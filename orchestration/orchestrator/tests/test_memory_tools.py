"""T-124 P1: the room memory tool chat_notes (chat skills were removed)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from any_mcp._fastmcp_adapter import ProtocolError
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.memory import MEMORY_MERGED

TOKEN = "tok-for-tests"
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": TOKEN,
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
    "hive": True,
}
UPDATE = {"op": "update", "section": "requests", "body": "x", "expected_version": 1}


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


async def test_chat_memory_errors_surface_code() -> None:
    errors = [(409, "version_conflict", UPDATE), (409, "room_unbound", {})]

    async def run(status: int, code: str, args: dict[str, Any]) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                status, json={"error": {"code": code, "message": f"refused, token {TOKEN}"}}
            )

        async with harness(handler) as h:
            result = await h.call("orchestrator_chat_notes", args)
            assert result.is_error, code
            assert code in result.text
            assert f"HTTP {status}" in result.text
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status
            assert TOKEN not in result.text
            assert TOKEN not in json.dumps(result.structured)

    for status, code, args in errors:
        await run(status, code, args)
    for code in ("section_too_large", "notes_too_large"):
        await run(413, code, UPDATE)


async def test_chat_notes_schema_and_annotations() -> None:
    (spec,) = (m.spec() for m in MEMORY_MERGED)
    assert spec.annotations == {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert spec.input_schema["additionalProperties"] is False
    assert "leader only" in spec.description.lower()
    assert spec.input_schema["properties"]["expected_version"]["minimum"] == 0

    sent: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with harness(record) as h:
        for args in (
            {"room": "!r:x"},
            {**UPDATE, "room": "!r:x"},
            {"section": "other"},
            {**UPDATE, "section": "notes"},
        ):
            result = await h.call("orchestrator_chat_notes", args)
            assert result.is_error, args
            assert not sent, args


def test_notes_update_413_advice_condenses_that_section_for_both_caps() -> None:
    """The connector room memory rule: on either 413, condense the written section."""
    (spec,) = (m.spec() for m in MEMORY_MERGED)
    assert "notes_too_large: condense that section" in spec.description
    assert "another section" not in spec.description


async def test_no_chat_skills_tools() -> None:
    """The chat skills feature is removed (T-505): no tool, no route."""
    assert not [m.name for m in MEMORY_MERGED if "skills" in m.name]
    async with harness(lambda request: httpx.Response(200, json={})) as h:
        with pytest.raises(ProtocolError, match="unknown tool"):
            await h.call("orchestrator_chat_skills_list", {})
