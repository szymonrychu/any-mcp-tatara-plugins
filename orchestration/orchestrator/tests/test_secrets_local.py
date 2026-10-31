from __future__ import annotations

import json
import logging
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
