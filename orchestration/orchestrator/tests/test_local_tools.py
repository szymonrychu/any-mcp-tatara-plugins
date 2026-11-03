"""PL2: the local tools wired into the real plugin, against a fake orchestrator."""

from __future__ import annotations

import json
import logging
import os
import secrets
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin

CANARY = "canary-" + secrets.token_hex(12)
TOKEN = "tok-" + secrets.token_hex(8)
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": TOKEN,
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}
OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
CRED_TOOLS = ("webhook_create", "webhook_update", "webhook_clone", "webhook_rotate")
ARGS: dict[str, dict[str, Any]] = {
    "webhook_create": {"type": "github", "preset_id": "p"},
    "webhook_update": {"id": "w1", "auth_mode": "bearer"},
    "webhook_clone": {"id": "w1"},
    "webhook_rotate": {"id": "w1"},
}


def harness(handler: Any, root: Path | None = None) -> PluginHarness:
    config = dict(CONFIG)
    if root is not None:
        config["secrets_dir"] = str(root)
    return PluginHarness(
        OrchestratorPlugin,
        config=config,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def blob(res: Any) -> str:
    return res.text + json.dumps(res.structured)


def fake(seen: list[httpx.Request], *, with_credential: bool = True) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT" and "/secrets/" in request.url.path:
            return httpx.Response(200, json={"name": "TOK", "set": True, "version": 3})
        body: dict[str, Any] = {"id": "w1", "password_secret": "webhook-w1-password"}
        body["key"] = "password"
        if with_credential and request.headers.get("x-orch-tool") == "local":
            body["credential"] = {"key": "password", "value": CANARY}
        return httpx.Response(201, json=body)

    return handler


async def test_secret_set_full_call(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    f = tmp_path / "v"
    f.write_text(CANARY + "\n")
    seen: list[httpx.Request] = []
    async with harness(fake(seen)) as h:
        names = {t["name"] for t in await h.list_tools()}
        assert "orchestrator_secret_set" in names
        res = await h.call("secret_set", {"preset": "web", "name": "TOK", "from_file": str(f)})
    assert not res.is_error
    assert res.structured == {"name": "TOK", "set": True, "version": 3, "bytes": len(CANARY)}
    assert CANARY not in blob(res) and CANARY not in caplog.text
    (req,) = seen
    assert (req.method, req.url.path) == ("PUT", "/agent/v1/admin/presets/web/secrets/TOK")
    assert json.loads(req.content) == {"value": CANARY}
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    known = {(m.upper(), p) for p, ms in OPENAPI["paths"].items() for m in ms}
    assert ("PUT", "/agent/v1/admin/presets/{ref}/secrets/{name}") in known


async def test_secret_set_refused_for_a_non_protected_target(tmp_path: Path) -> None:
    f = tmp_path / "v"
    f.write_text(CANARY)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422, json={"error": {"code": "not_protected", "message": "echo " + CANARY}}
        )

    async with harness(handler) as h:
        res = await h.call("secret_set", {"preset": "web", "name": "PLAIN", "from_file": str(f)})
    assert res.is_error and "422" in res.text
    assert CANARY not in blob(res)


async def test_secret_resolve_errors_route_to_a_fixed_class_text() -> None:
    async with harness(fake([])) as h:
        res = await h.call(
            "secret_set", {"preset": "p", "name": "TOK", "from_env": "NOPE_" + CANARY}
        )
    assert res.is_error and "env_missing" in res.text
    assert CANARY not in blob(res)


@pytest.mark.parametrize("tool", CRED_TOOLS)
async def test_to_file_writes_the_credential_and_the_result_is_metadata_only(
    tool: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    target = tmp_path / "root" / "cred"
    seen: list[httpx.Request] = []
    async with harness(fake(seen), tmp_path / "root") as h:
        res = await h.call(tool, {**ARGS[tool], "to_file": str(target)})
    assert not res.is_error, res.text
    assert target.read_text() == CANARY
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert CANARY not in blob(res) and CANARY not in caplog.text
    assert res.structured["id"] == "w1" and "credential" not in res.structured
    assert res.structured["to_file"] == {"written": True, "key": "password", "bytes": len(CANARY)}
    (req,) = seen
    assert req.headers["x-orch-tool"] == "local"
    assert b"to_file" not in req.content and CANARY.encode() not in req.content


async def test_without_to_file_the_local_header_is_not_sent_and_a_credential_is_dropped() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"id": "w1", "credential": {"key": "k", "value": CANARY}})

    async with harness(handler) as h:
        res = await h.call("webhook_create", ARGS["webhook_create"])
    assert not res.is_error
    assert "x-orch-tool" not in seen[0].headers
    assert CANARY not in blob(res)


async def test_existing_file_is_refused_unless_overwrite(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "cred"
    target.write_text("old")
    seen: list[httpx.Request] = []
    async with harness(fake(seen), root) as h:
        res = await h.call("webhook_rotate", {"id": "w1", "to_file": str(target)})
        assert res.is_error and "to_file_exists" in res.text
        assert target.read_text() == "old" and seen == []
        res = await h.call(
            "webhook_rotate", {"id": "w1", "to_file": str(target), "overwrite": True}
        )
    assert not res.is_error and target.read_text() == CANARY
    assert sorted(p.name for p in root.iterdir()) == ["cred"]
    assert b"overwrite" not in seen[0].content


async def test_default_root_is_created_0700_under_home(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / ".agent-secrets" / "cred"
    async with harness(fake([])) as h:
        res = await h.call("webhook_create", {**ARGS["webhook_create"], "to_file": str(target)})
    assert not res.is_error, res.text
    assert stat.S_IMODE(os.stat(target.parent).st_mode) == 0o700
    assert target.read_text() == CANARY


async def test_paths_outside_the_root_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    (root / "filelink").symlink_to(outside / "victim")
    bad = [
        tmp_path / "hooks.json",
        root / ".." / "hooks.json",
        root / "link" / "cred",
        root / "filelink",
        root / "sub" / "cred",
        "relative/cred",
        "",
        root,
    ]
    seen: list[httpx.Request] = []
    async with harness(fake(seen), root) as h:
        for path in bad:
            res = await h.call(
                "webhook_create",
                {**ARGS["webhook_create"], "to_file": str(path), "overwrite": True},
            )
            assert res.is_error and "to_file_invalid" in res.text, path
    assert seen == [] and list(outside.iterdir()) == []


async def test_to_file_without_a_credential_in_the_answer_reports_not_written(
    tmp_path: Path,
) -> None:
    target = tmp_path / "root" / "cred"
    async with harness(fake([], with_credential=False), tmp_path / "root") as h:
        res = await h.call("webhook_update", {"id": "w1", "to_file": str(target)})
    assert not res.is_error
    assert res.structured["to_file"] == {"written": False}
    assert not target.exists()


async def test_a_failed_file_write_leaks_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    from any_mcp_tatara_orchestrator import secrets_local

    def boom(*a: Any, **k: Any) -> None:
        raise OSError(CANARY)

    monkeypatch.setattr(secrets_local.os, "fsync", boom)
    root = tmp_path / "root"
    async with harness(fake([]), root) as h:
        res = await h.call("webhook_create", {**ARGS["webhook_create"], "to_file": str(root / "c")})
    assert res.is_error and "to_file_failed" in res.text
    assert CANARY not in blob(res)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize(
    "tool", ["preset_create", "preset_replace", "preset_patch", "session_create"]
)
async def test_protected_env_value_is_refused(tool: str) -> None:
    seen: list[httpx.Request] = []
    args: dict[str, Any] = {
        "preset_create": {"name": "n"},
        "preset_replace": {"ref": "n", "name": "n"},
        "preset_patch": {"ref": "n"},
        "session_create": {},
    }[tool]
    spec = {"env": {"TOK": {"protected": True, "value": CANARY}}}
    async with harness(fake(seen)) as h:
        res = await h.call(tool, {**args, "spec": spec})
        assert res.is_error and "secret_set" in res.text and "TOK" in res.text
        assert CANARY not in blob(res)
        ok = {"env": {"TOK": {"protected": True}, "PLAIN": {"value": "x", "protected": False}}}
        res = await h.call(tool, {**args, "spec": ok})
    assert not res.is_error
    assert len(seen) == 1 and CANARY.encode() not in seen[0].content


async def test_upstream_errors_of_credential_tools_keep_their_code(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503, json={"error": {"code": "credential_not_stored", "message": "x"}}
        )

    async with harness(handler, tmp_path / "root") as h:
        res = await h.call(
            "webhook_create", {**ARGS["webhook_create"], "to_file": str(tmp_path / "root" / "c")}
        )
    assert res.is_error and "credential_not_stored" in res.text


async def test_credential_tools_offer_to_file_and_others_do_not() -> None:
    async with harness(fake([])) as h:
        tools = {t["name"]: t for t in await h.list_tools()}
    for name in CRED_TOOLS:
        assert "to_file" in tools[f"orchestrator_{name}"]["inputSchema"]["properties"]
    assert "to_file" not in tools["orchestrator_webhook_delete"]["inputSchema"]["properties"]
