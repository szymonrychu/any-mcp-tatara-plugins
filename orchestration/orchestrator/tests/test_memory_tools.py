"""T-124 P1: the six room memory tools (chat notes and skills)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.memory import memory_tools

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
    "chat_skills_list",
    "chat_skills_get",
    "chat_skills_save",
    "chat_skills_delete",
]
NAME_PATTERN = "^[a-z0-9][a-z0-9-]{0,63}$"


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
        (
            "chat_notes_update",
            {"body": "## Preferences\n- terse", "expected_version": 2},
            (
                "PUT",
                "/agent/v1/chat/notes",
                {"body": "## Preferences\n- terse", "expected_version": 2},
            ),
        ),
        ("chat_skills_list", {}, ("GET", "/agent/v1/chat/skills", None)),
        (
            "chat_skills_get",
            {"name": "deck-tune"},
            ("GET", "/agent/v1/chat/skills/deck-tune", None),
        ),
        (
            "chat_skills_save",
            {
                "name": "deck-tune",
                "description": "Tune a deck",
                "body": "1. open",
                "expected_version": 0,
            },
            (
                "PUT",
                "/agent/v1/chat/skills/deck-tune",
                {"description": "Tune a deck", "body": "1. open", "expected_version": 0},
            ),
        ),
        (
            "chat_skills_delete",
            {"name": "deck-tune"},
            ("DELETE", "/agent/v1/chat/skills/deck-tune", None),
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
        "chat_notes_update": (409, "version_conflict", {"body": "x", "expected_version": 1}),
        "chat_skills_save": (
            409,
            "skill_limit",
            {"name": "a", "description": "d", "body": "b", "expected_version": 0},
        ),
        "chat_skills_get": (404, "skill_not_found", {"name": "a"}),
        "chat_skills_delete": (404, "skill_not_found", {"name": "a"}),
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
    await run(413, "notes_too_large", "chat_notes_update", {"body": "x", "expected_version": 1})


async def test_chat_memory_schemas_and_annotations() -> None:
    specs = {s.name: s for s in memory_tools("http://orch.test:8081")}
    assert list(specs) == NAMES
    ann = {
        "chat_notes_get": (True, False, True),
        "chat_notes_update": (False, False, True),
        "chat_skills_list": (True, False, True),
        "chat_skills_get": (True, False, True),
        "chat_skills_save": (False, False, True),
        "chat_skills_delete": (False, True, True),
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

    assert specs["chat_notes_get"].input_schema["properties"] == {}
    upd = specs["chat_notes_update"].input_schema
    assert set(upd["required"]) == {"body", "expected_version"}
    assert upd["properties"]["expected_version"]["minimum"] == 0
    for name in ("chat_skills_get", "chat_skills_save", "chat_skills_delete"):
        schema = specs[name].input_schema
        assert "name" in schema["required"], name
        assert schema["properties"]["name"]["pattern"] == NAME_PATTERN, name
    save = specs["chat_skills_save"].input_schema
    assert set(save["required"]) == {"name", "description", "body", "expected_version"}
    assert (
        save["properties"]["description"]["minLength"],
        save["properties"]["description"]["maxLength"],
    ) == (1, 1024)
    assert save["properties"]["body"]["minLength"] == 1
    assert save["properties"]["expected_version"]["minimum"] == 0

    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with harness(record) as h:
        for name, args in (
            ("chat_notes_get", {"room": "!r:x"}),
            ("chat_notes_update", {"body": "x", "expected_version": 0, "room": "!r:x"}),
            ("chat_skills_get", {"name": "Bad_Name"}),
            ("chat_skills_get", {"name": "-lead"}),
            ("chat_skills_delete", {"name": "../x"}),
            ("chat_skills_save", {"name": "a", "description": "d", "body": "b"}),
        ):
            result = await h.call(f"orchestrator_{name}", args)
            assert result.is_error, (name, args)
            assert not sent, (name, args)


async def test_chat_memory_routes_exist_in_the_orchestrator_openapi() -> None:
    def norm(path: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", path)

    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    for spec in memory_tools("http://orch.test:8081"):
        assert spec.http is not None
        assert (spec.http.method, norm(spec.http.path)) in known, spec.name
