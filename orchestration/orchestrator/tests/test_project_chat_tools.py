from __future__ import annotations

from typing import Any

import httpx

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin

TOKEN = "s3cr3t-token-value"
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": TOKEN,
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
    "max_replicas": 3,
    "hive": True,
}


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


async def _tools() -> dict[str, Any]:
    async with harness(ok) as h:
        return {t["name"]: t for t in await h.list_tools()}


async def test_project_chat_tools_registered() -> None:
    tools = await _tools()
    assert "orchestrator_project_chat_read" in tools
    assert "orchestrator_project_chat_send" not in tools


async def test_project_chat_read_fills_limit_20() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json={"posts": [], "next_before_id": None})

    async with harness(handler) as h:
        await h.call("orchestrator_project_chat_read", {"project": "p1"})
        await h.call("orchestrator_project_chat_read", {"limit": 5, "before_id": 9})
    assert seen[0].path == "/agent/v1/project/messages"
    assert dict(seen[0].params) == {"project": "p1", "limit": "20"}
    assert dict(seen[1].params) == {"limit": "5", "before_id": "9"}


async def test_project_chat_schemas_strict() -> None:
    tools = await _tools()
    read = tools["orchestrator_project_chat_read"]["inputSchema"]
    assert read["additionalProperties"] is False
    assert read["properties"]["limit"]["minimum"] == 1
    assert read["properties"]["limit"]["maximum"] == 100
    assert read["properties"]["before_id"]["minimum"] == 1
    assert read["properties"]["after_id"]["minimum"] == 1
    assert tools["orchestrator_project_chat_read"]["annotations"]["readOnlyHint"] is True


async def test_project_chat_errors_surface_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "too_many_recipients", "message": f"token {TOKEN}"}}
        )

    async with harness(handler) as h:
        res = await h.call("orchestrator_project_chat_read", {})
    assert res.is_error
    assert "too_many_recipients" in res.text
    assert "HTTP 409" in res.text
    assert TOKEN not in res.text
