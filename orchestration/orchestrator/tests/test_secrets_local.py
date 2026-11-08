from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp.types import ToolCall, ToolResult, ToolSpec
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.secrets_local import (
    MAX_BYTES,
    SecretResolveError,
    local_secret_tool,
    resolve_secret,
    secret_error_result,
    validate_to_file,
    write_credential,
)

CANARY = "canary-9f3a7c1e-value"
TOKEN = "s3cr3t-token-value"
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": TOKEN,
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}


class LocalPlugin(OrchestratorPlugin):
    """The orchestrator plugin with the local tool added, as the real registration will do."""

    async def load_tools(self) -> Sequence[ToolSpec]:
        return [local_secret_tool(self.cfg.base_url, self.cfg.token, self.cfg.session, self.ctx)]

    async def on_error(self, call: ToolCall, exc: Exception) -> ToolResult:
        if isinstance(exc, SecretResolveError):
            return secret_error_result(exc)
        return await super().on_error(call, exc)


def make_harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        LocalPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


# ---- resolver


async def test_exactly_one_source() -> None:
    for kwargs in (
        {},
        {"from_env": "A", "from_file": "/x"},
        {"from_env": "A", "from_command": "x"},
    ):
        with pytest.raises(SecretResolveError) as e:
            await resolve_secret(**kwargs)
        assert str(e.value) == "exactly_one_source"


async def test_from_file_strips_one_trailing_newline(tmp_path: Path) -> None:
    f = tmp_path / "s"
    f.write_text(CANARY + "\n\n")
    assert await resolve_secret(from_file=str(f)) == CANARY + "\n"


async def test_from_file_missing_error_has_no_path(tmp_path: Path) -> None:
    missing = tmp_path / "very-private-name"
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_file=str(missing))
    assert str(e.value) == "file_missing"
    assert "very-private-name" not in repr(e.value)


async def test_from_file_size_cap(tmp_path: Path) -> None:
    f = tmp_path / "big"
    f.write_bytes(b"a" * (MAX_BYTES + 1))
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_file=str(f))
    assert str(e.value) == "value_too_large"
    f.write_bytes(b"a" * MAX_BYTES)
    assert len(await resolve_secret(from_file=str(f))) == MAX_BYTES


async def test_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_SECRET_X", CANARY + "\n")
    assert await resolve_secret(from_env="MY_SECRET_X") == CANARY
    monkeypatch.delenv("MY_SECRET_X")
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_env="MY_SECRET_X")
    assert str(e.value) == "env_missing"


async def test_from_command_no_shell() -> None:
    cmd = f"{sys.executable} -c \"import sys; sys.stdout.write('{CANARY}\\n')\""
    assert await resolve_secret(from_command=cmd) == CANARY
    # a shell metacharacter is an argument, not an operator
    out = await resolve_secret(from_command=f"{sys.executable} -c 'print(1)' ; echo injected")
    assert out == "1"


async def test_from_command_nonzero_exit_reports_code_only() -> None:
    code = f"import sys; sys.stdout.write('{CANARY}'); sys.stderr.write('{CANARY}'); sys.exit(3)"
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command=f'{sys.executable} -c "{code}"')
    assert str(e.value) == "command_failed exit_code=3"
    assert CANARY not in repr(e.value)


async def test_from_command_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from any_mcp_tatara_orchestrator import secrets_local

    monkeypatch.setattr(secrets_local, "COMMAND_TIMEOUT", 0.3)
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command=f"{sys.executable} -c 'import time; time.sleep(5)'")
    assert str(e.value) == "command_timeout"


async def test_from_command_stdout_cap() -> None:
    code = f"import sys; sys.stdout.write('a' * {MAX_BYTES + 10})"
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command=f'{sys.executable} -c "{code}"')
    assert str(e.value) == "value_too_large"


async def test_from_command_not_found_and_bad_quoting() -> None:
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command="/nonexistent/binary-xyz --token=" + CANARY)
    assert str(e.value) == "command_not_found"
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command="echo 'unterminated " + CANARY)
    assert str(e.value) == "command_invalid"


async def test_empty_value_refused(tmp_path: Path) -> None:
    f = tmp_path / "e"
    f.write_text("\n")
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_file=str(f))
    assert str(e.value) == "value_empty"


# ---- the tool through the real any-mcp server


def put_handler(seen: list[httpx.Request], status: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json={"name": "TOK", "set": True, "version": 4})

    return handler


async def test_tool_puts_value_and_returns_metadata_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    f = tmp_path / "s"
    f.write_text(CANARY + "\n")
    seen: list[httpx.Request] = []
    caplog.set_level(logging.DEBUG)
    async with make_harness(put_handler(seen)) as h:
        res = await h.call("secret_set", {"preset": "pr", "name": "TOK", "from_file": str(f)})
    assert not res.is_error
    assert res.structured == {"name": "TOK", "set": True, "version": 4, "bytes": len(CANARY)}
    assert CANARY not in res.text
    assert CANARY not in json.dumps(res.structured)
    assert CANARY not in caplog.text
    (req,) = seen
    assert req.method == "PUT"
    assert req.url.path == "/agent/v1/admin/presets/pr/secrets/TOK"
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-orch-session"] == "s-1"
    assert json.loads(req.content) == {"value": CANARY}
    for k, v in req.headers.items():
        assert CANARY not in v, k
    assert CANARY not in str(req.url)


async def test_tool_errors_never_contain_value(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    f = tmp_path / "s"
    f.write_text(CANARY)
    caplog.set_level(logging.DEBUG)

    def echo_400(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="bad input " + request.content.decode())

    async with make_harness(echo_400) as h:
        res = await h.call("secret_set", {"preset": "p", "name": "TOK", "from_file": str(f)})
        assert res.is_error
        assert CANARY not in res.text and CANARY not in json.dumps(res.structured)
        assert "422" in res.text
        bad = await h.call("secret_set", {"preset": "p", "name": "TOK", "from_env": "NOPE_X"})
        assert bad.is_error and "env_missing" in bad.text
        two = await h.call(
            "secret_set", {"preset": "p", "name": "TOK", "from_env": "A", "from_file": "/x"}
        )
        assert two.is_error and "exactly_one_source" in two.text

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused " + request.content.decode())

    async with make_harness(boom) as h:
        res = await h.call("secret_set", {"preset": "p", "name": "TOK", "from_file": str(f)})
        assert res.is_error
        assert CANARY not in res.text and CANARY not in json.dumps(res.structured)
    assert CANARY not in caplog.text


async def test_tool_rejects_bad_names() -> None:
    async with make_harness(put_handler([])) as h:
        for preset, name in (("p/../x", "TOK"), ("p", "bad name"), ("p", "1X"), ("", "TOK")):
            res = await h.call("secret_set", {"preset": preset, "name": name, "from_env": "X"})
            assert res.is_error


# ---- review follow-up


async def test_not_utf8_error_keeps_no_raw_bytes(tmp_path: Path) -> None:
    f = tmp_path / "b"
    f.write_bytes(b"\xff\xfe" + CANARY.encode())
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_file=str(f))
    assert str(e.value) == "value_not_utf8"
    assert e.value.__cause__ is None
    assert e.value.__context__ is None


async def test_fifo_is_rejected_without_blocking(tmp_path: Path) -> None:
    import os

    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(SecretResolveError) as e:
        await asyncio.wait_for(resolve_secret(from_file=str(fifo)), 2)
    assert str(e.value) == "file_not_regular"


async def test_non_string_args_rejected() -> None:
    async with make_harness(put_handler([])) as h:
        for key in ("from_file", "from_env", "from_command"):
            res = await h.call("secret_set", {"preset": "p", "name": "TOK", key: 123})
            assert res.is_error
    for kwargs in ({"from_file": 3}, {"from_env": 3}, {"from_command": 3}):
        with pytest.raises(SecretResolveError) as e:
            await resolve_secret(**kwargs)  # type: ignore[arg-type]
        assert str(e.value) == "invalid_source"


async def test_timeout_is_one_overall_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from any_mcp_tatara_orchestrator import secrets_local

    monkeypatch.setattr(secrets_local, "COMMAND_TIMEOUT", 0.6)
    # prints at 0.4 s, exits at 0.8 s: each phase is under 0.6 s, the total is not
    code = "import sys,time; time.sleep(.4); print('x', flush=True); time.sleep(.4)"
    with pytest.raises(SecretResolveError) as e:
        await resolve_secret(from_command=f'{sys.executable} -c "{code}"')
    assert str(e.value) == "command_timeout"


def test_no_overwrite_create_is_atomic_against_a_racing_file(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    validated = validate_to_file(str(target), str(tmp_path), overwrite=False)
    target.write_text("racer")  # appears between the check and the create
    with pytest.raises(SecretResolveError, match="to_file_exists"):
        write_credential(validated, "new-value", overwrite=False)
    assert target.read_text() == "racer"


def test_no_overwrite_create_refuses_a_racing_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    target = tmp_path / "cred"
    validated = validate_to_file(str(target), str(tmp_path), overwrite=False)
    target.symlink_to(victim)
    with pytest.raises(SecretResolveError):
        write_credential(validated, "new-value", overwrite=False)
    assert victim.read_text() == "keep"


def test_no_overwrite_create_writes_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    write_credential(str(target), "v", overwrite=False)
    assert target.read_text() == "v"
    assert target.stat().st_mode & 0o777 == 0o600


def test_overwrite_replaces_existing(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    target.write_text("old")
    write_credential(str(target), "new", overwrite=True)
    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o600


# ---- proposal_create / proposal_revise through the real plugin registration

VIEW = {"id": 5, "kind": "preset", "status": "open", "revision": 1, "revisions": []}


def proposal_harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def blob(res: ToolResult) -> str:
    return res.text + json.dumps(res.structured)


async def test_proposal_create_sends_the_env_secret_only_in_the_body(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PROPOSAL_SRC_X", CANARY + "\n")
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=VIEW)

    args = {
        "kind": "preset",
        "target": "web",
        "patch": {"env": {"TOKEN": {"protected": True}}},
        "reason": "rotate",
        "client_key": "k1",
        "secrets": {"TOKEN": {"from_env": "PROPOSAL_SRC_X"}},
    }
    async with proposal_harness(handler) as h:
        res = await h.call("proposal_create", args)
    assert not res.is_error, res.text
    assert res.structured == VIEW
    assert CANARY not in blob(res) and CANARY not in caplog.text
    (req,) = seen
    assert (req.method, req.url.path) == ("POST", "/agent/v1/admin/proposals")
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert req.headers["x-orch-session"] == "s-1"
    body = json.loads(req.content)
    assert body == {
        "kind": "preset",
        "target": "web",
        "patch": {"env": {"TOKEN": {"protected": True}}},
        "reason": "rotate",
        "client_key": "k1",
        "secrets": {"TOKEN": CANARY},
    }
    assert CANARY not in str(req.url)
    for k, v in req.headers.items():
        assert CANARY not in v, k


async def test_proposal_revise_posts_to_the_revise_route(tmp_path: Path) -> None:
    f = tmp_path / "s"
    f.write_text(CANARY + "\n")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={**VIEW, "revision": 2})

    async with proposal_harness(handler) as h:
        res = await h.call(
            "proposal_revise",
            {"id": 5, "patch": {"docker": True}, "secrets": {"TOK": {"from_file": str(f)}}},
        )
        bare = await h.call("proposal_revise", {"id": 5, "patch": {"docker": True}})
    assert not res.is_error and not bare.is_error
    assert CANARY not in blob(res)
    first, second = seen
    assert (first.method, first.url.path) == ("POST", "/agent/v1/admin/proposals/5/revise")
    assert json.loads(first.content) == {"patch": {"docker": True}, "secrets": {"TOK": CANARY}}
    assert json.loads(second.content) == {"patch": {"docker": True}}


async def test_proposal_upstream_error_keeps_its_code_and_hides_the_value(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("PROPOSAL_SRC_X", CANARY)
    caplog.set_level(logging.DEBUG)

    def echo(request: httpx.Request) -> httpx.Response:
        text = "echo " + request.content.decode()
        return httpx.Response(422, json={"error": {"code": "nothing_changed", "message": text}})

    def raw_echo(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom " + request.content.decode())

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused " + request.content.decode())

    args = {
        "kind": "preset",
        "target": "web",
        "patch": {},
        "reason": "r",
        "secrets": {"TOKEN": {"from_env": "PROPOSAL_SRC_X"}},
    }
    async with proposal_harness(echo) as h:
        res = await h.call("proposal_create", args)
    assert res.is_error and "nothing_changed" in res.text and "422" in res.text
    assert CANARY not in blob(res)
    async with proposal_harness(raw_echo) as h:
        res = await h.call("proposal_create", args)
    assert res.is_error and "500" in res.text
    assert CANARY not in blob(res) and TOKEN not in blob(res)
    async with proposal_harness(boom) as h:
        res = await h.call("proposal_create", args)
    assert res.is_error and "request_failed" in res.text
    assert CANARY not in blob(res)
    assert CANARY not in caplog.text


ESCAPED = 'line1-AAAA\nline2-"q"-BBBB\\back\ttab-\u00e9'


async def test_proposal_upstream_error_hides_json_escaped_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROPOSAL_SRC_ESC", ESCAPED)

    def coded(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)["secrets"]["TOKEN"]
        return httpx.Response(
            422, json={"error": {"code": "invalid", "message": "got " + sent, "detail": [sent]}}
        )

    def uncoded(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)["secrets"]["TOKEN"]
        return httpx.Response(400, json={"detail": sent, sent: 1})

    def plain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom " + request.content.decode())

    def ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": 1, "echo": json.loads(request.content)["secrets"]})

    args = {
        "kind": "preset",
        "target": "web",
        "patch": {},
        "reason": "r",
        "secrets": {"TOKEN": {"from_env": "PROPOSAL_SRC_ESC"}},
    }
    pieces = ["line1-AAAA", "line2-", "BBBB", "tab-", json.dumps(ESCAPED)[1:-1]]
    for handler in (coded, uncoded, plain, ok):
        async with proposal_harness(handler) as h:
            res = await h.call("proposal_create", args)
        text = blob(res)
        assert not any(p in text for p in pieces), handler.__name__
    assert "invalid" in (await _call_text(coded, args))


async def _call_text(handler: Any, args: dict[str, Any]) -> str:
    async with proposal_harness(handler) as h:
        return (await h.call("proposal_create", args)).text


@pytest.mark.parametrize("tool", ["proposal_create", "proposal_revise"])
async def test_proposal_protected_value_in_patch_is_refused_before_any_request(
    tool: str,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=VIEW)

    patch = {"env": {"TOK": {"protected": True, "value": CANARY}}}
    args: dict[str, Any] = {
        "proposal_create": {"kind": "preset", "target": "web", "patch": patch, "reason": "r"},
        "proposal_revise": {"id": 5, "patch": patch},
    }[tool]
    async with proposal_harness(handler) as h:
        res = await h.call(tool, args)
    assert res.is_error and "protected_value_refused" in res.text and "TOK" in res.text
    assert CANARY not in blob(res)
    assert seen == []


async def test_proposal_unreadable_file_gives_the_class_name_only(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []
    missing = tmp_path / "very-private-name"
    unreadable = tmp_path / "locked"
    unreadable.write_text(CANARY)
    unreadable.chmod(0)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=VIEW)

    base = {"kind": "preset", "target": "web", "patch": {}, "reason": "r"}
    async with proposal_harness(handler) as h:
        res = await h.call(
            "proposal_create", {**base, "secrets": {"TOK": {"from_file": str(missing)}}}
        )
        assert res.is_error
        assert res.text == "proposal_create failed: file_missing"
        assert "very-private-name" not in blob(res)
        if os.geteuid() != 0:  # root reads a mode-0 file
            res = await h.call(
                "proposal_create", {**base, "secrets": {"TOK": {"from_file": str(unreadable)}}}
            )
            assert res.is_error and res.text.startswith("proposal_create failed: file_")
            assert CANARY not in blob(res) and "locked" not in blob(res)
        bad = await h.call("proposal_create", {**base, "secrets": {"bad name": {"from_env": "X"}}})
        assert bad.is_error and "invalid_name" in bad.text
    assert seen == []
