from __future__ import annotations

import json
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
    assert {"orchestrator_project_chat_send", "orchestrator_project_chat_read"} <= set(tools)


async def test_project_chat_send_posts_body() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(202, json={"post_id": 1, "delivered": ["a"], "skipped": []})

    body = {
        "text": "I take the lock file",
        "to": ["a"],
        "reply_to": 3,
        "handled": [5],
        "project": "p1",
    }
    async with harness(handler) as h:
        res = await h.call("orchestrator_project_chat_send", body)
    assert not res.is_error, res.text
    assert seen == [("POST", "/agent/v1/project/messages", body)]


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
    send = tools["orchestrator_project_chat_send"]["inputSchema"]
    read = tools["orchestrator_project_chat_read"]["inputSchema"]
    assert send["additionalProperties"] is False
    assert read["additionalProperties"] is False
    assert send["required"] == ["text"]
    assert send["properties"]["text"]["minLength"] == 1
    assert send["properties"]["text"]["maxLength"] == 4000
    assert send["properties"]["to"]["maxItems"] == 20
    assert send["properties"]["reply_to"]["minimum"] == 1
    assert read["properties"]["limit"]["minimum"] == 1
    assert read["properties"]["limit"]["maximum"] == 100
    assert read["properties"]["before_id"]["minimum"] == 1
    assert read["properties"]["after_id"]["minimum"] == 1
    assert tools["orchestrator_project_chat_read"]["annotations"]["readOnlyHint"] is True
    assert not tools["orchestrator_project_chat_send"]["annotations"].get("readOnlyHint")


async def test_project_chat_descriptions_forbid_ack() -> None:
    tools = await _tools()
    send = tools["orchestrator_project_chat_send"]["description"]
    read = tools["orchestrator_project_chat_read"]["description"]
    assert "Never post an acknowledgement" in send
    assert "never to a superleader" in send
    assert "Leader only" in read


async def test_project_chat_errors_surface_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409, json={"error": {"code": "too_many_recipients", "message": f"token {TOKEN}"}}
        )

    async with harness(handler) as h:
        res = await h.call("orchestrator_project_chat_send", {"text": "hi"})
    assert res.is_error
    assert "too_many_recipients" in res.text
    assert "HTTP 409" in res.text
    assert TOKEN not in res.text
