"""T-124 P1: the two room memory tools (chat notes; chat skills were removed)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp._fastmcp_adapter import ProtocolError
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.memory import SECTIONS, memory_tools

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
TOKEN = "tok-for-tests"
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": TOKEN,
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
    "hive": True,
}
NAMES = [
    "chat_notes_get",
    "chat_notes_update",
]


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


async def test_chat_memory_tools_call_agent_routes() -> None:
    seen: list[tuple[str, str, Any, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append(
            (
                request.method,
                request.url.path,
                body,
                request.headers.get("authorization", ""),
                request.headers.get("x-orch-session", ""),
            )
        )
        return httpx.Response(200, json={"ok": True})

    calls: list[tuple[str, dict[str, Any], tuple[str, str, Any]]] = [
        ("chat_notes_get", {}, ("GET", "/agent/v1/chat/notes", None)),
        ("chat_notes_get", {"section": "requests"}, ("GET", "/agent/v1/chat/notes", None)),
        (
            "chat_notes_update",
            {"section": "preferences", "body": "- terse", "expected_version": 2},
            (
                "PUT",
                "/agent/v1/chat/notes/sections/preferences",
                {"body": "- terse", "expected_version": 2},
            ),
        ),
    ]
    async with harness(handler) as h:
        for name, args, expected in calls:
            seen.clear()
            result = await h.call(f"orchestrator_{name}", args)
            assert not result.is_error, (name, result.text)
            assert len(seen) == 1, name
            method, path, body, auth, session = seen[0]
            assert (method, path, body) == expected, name
            assert auth == f"Bearer {TOKEN}"
            assert session == "s-1"


async def test_chat_memory_errors_surface_code() -> None:
    errors = {
        "chat_notes_update": (
            409,
            "version_conflict",
            {"section": "requests", "body": "x", "expected_version": 1},
        ),
        "chat_notes_get": (409, "room_unbound", {}),
    }

    async def run(status: int, code: str, name: str, args: dict[str, Any]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, json={"error": {"code": code, "message": f"refused, token {TOKEN}"}}
            )

        async with harness(handler) as h:
            result = await h.call(f"orchestrator_{name}", args)
            assert result.is_error, name
            assert code in result.text
            assert f"HTTP {status}" in result.text
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status
            assert TOKEN not in result.text
            assert TOKEN not in json.dumps(result.structured)

    for name, (status, code, args) in errors.items():
        await run(status, code, name, args)
    for code in ("section_too_large", "notes_too_large"):
        await run(
            413,
            code,
            "chat_notes_update",
            {"section": "requests", "body": "x", "expected_version": 1},
        )


async def test_chat_memory_schemas_and_annotations() -> None:
    specs = {s.name: s for s in memory_tools("http://orch.test:8081")}
    assert list(specs) == NAMES
    ann = {
        "chat_notes_get": (True, False, True),
        "chat_notes_update": (False, False, True),
    }
    for name, (ro, destructive, idem) in ann.items():
        spec = specs[name]
        assert spec.annotations == {
            "readOnlyHint": ro,
            "destructiveHint": destructive,
            "idempotentHint": idem,
            "openWorldHint": False,
        }, name
        assert spec.input_schema["additionalProperties"] is False, name
        assert "Leader only" in spec.description, name

    assert set(specs["chat_notes_get"].input_schema["properties"]) == {"section"}
    upd = specs["chat_notes_update"].input_schema
    assert set(upd["required"]) == {"section", "body", "expected_version"}
    assert upd["properties"]["expected_version"]["minimum"] == 0

    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with harness(record) as h:
        for name, args in (
            ("chat_notes_get", {"room": "!r:x"}),
            (
                "chat_notes_update",
                {"section": "requests", "body": "x", "expected_version": 0, "room": "!r:x"},
            ),
            ("chat_notes_get", {"section": "other"}),
            ("chat_notes_update", {"section": "notes", "body": "x", "expected_version": 0}),
        ):
            result = await h.call(f"orchestrator_{name}", args)
            assert result.is_error, (name, args)
            assert not sent, (name, args)


@pytest.mark.parametrize("name", ["chat_notes_get", "chat_notes_update"])
def test_notes_tools_section_enum_is_the_five_sections(name: str) -> None:
    spec = {s.name: s for s in memory_tools("http://orch.test:8081")}[name]
    assert spec.input_schema["properties"]["section"]["enum"] == SECTIONS


async def test_notes_get_sends_section_as_query() -> None:
    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with harness(record) as h:
        assert not (await h.call("orchestrator_chat_notes_get", {"section": "requests"})).is_error
    assert sent[0].url.query.decode() == "section=requests"


async def test_chat_memory_routes_exist_in_the_orchestrator_openapi() -> None:
    def norm(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path)

    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    for spec in memory_tools("http://orch.test:8081"):
        assert spec.http is not None
        assert (spec.http.method, norm(spec.http.path)) in known, spec.name


async def test_no_chat_skills_tools() -> None:
    """The chat skills feature is removed (T-505): no tool, no route."""
    assert not [s.name for s in memory_tools("http://orch.test:8081") if "skills" in s.name]
    async with harness(lambda request: httpx.Response(200, json={})) as h:
        with pytest.raises(ProtocolError, match="unknown tool"):
            await h.call("orchestrator_chat_skills_list", {})
