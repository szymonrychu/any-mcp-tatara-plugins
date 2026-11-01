"""PL1: declarative admin rows pinned to the orchestrator OpenAPI."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from any_mcp.context import PluginContext
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.admin import ADMIN_TOOLS

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": "tok-for-tests",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}
EXPECTED = {
    "presets_list", "preset_get", "preset_create", "preset_replace", "preset_delete",
    "preset_copy", "session_get", "session_create", "session_discard", "session_restart",
    "agent_restart", "command_get", "webhooks_list", "webhook_get_meta", "webhook_create",
    "webhook_update", "webhook_clone", "webhook_rotate", "webhook_delete", "webhook_filters_set",
    "webhook_events", "webhook_event", "webhook_template_preview", "schedules_list",
    "schedule_get", "schedule_create", "schedule_update", "schedule_delete", "schedule_run",
    "schedule_runs", "schedule_runs_all", "schedule_run_get", "schedule_preview",
    "chat_admin_rooms", "chat_room_settings", "chat_rules", "chat_rule_add", "chat_rule_delete",
}  # fmt: skip


def norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


async def specs() -> list[Any]:
    plugin = OrchestratorPlugin()
    plugin.ctx = PluginContext(
        name="orchestrator",
        namespace="orchestrator",
        config=OrchestratorPlugin.Config.model_validate(CONFIG),
        http=httpx.AsyncClient(transport=httpx.MockTransport(ok)),
        log=logging.getLogger("test"),
        response_budget_chars=40000,
        config_dir=None,
    )
    await plugin.setup()
    return list(await plugin.load_tools())


def test_admin_tool_names_are_the_planned_set() -> None:
    assert {r.name for r in ADMIN_TOOLS} == EXPECTED


async def test_every_http_row_exists_in_the_orchestrator_openapi() -> None:
    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    rows = [s for s in await specs() if s.http is not None]
    assert len(rows) >= len(EXPECTED)
    for s in rows:
        assert s.http is not None
        assert (s.http.method, norm(s.http.path)) in known, (s.name, s.http.method, s.http.path)


def test_every_admin_row_has_a_description_and_a_closed_schema() -> None:
    for r in ADMIN_TOOLS:
        assert r.description.strip(), r.name
        assert r.schema["type"] == "object" and r.schema["additionalProperties"] is False, r.name
        for token in re.findall(r"\{([^}]+)\}", r.path):
            assert token in r.schema["properties"], (r.name, token)
            assert token in r.schema.get("required", []), (r.name, token)
        for q in r.query:
            assert q in r.schema["properties"], (r.name, q)


async def test_no_row_takes_a_secret_value_from_the_model() -> None:
    for r in ADMIN_TOOLS:
        assert "password" not in r.schema["properties"], r.name
        if "preset" in r.name:
            assert "value" not in r.schema["properties"], r.name


async def test_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"ok": True})

    calls = [
        ("presets_list", {}, ("GET", "/agent/v1/admin/presets", "", None)),
        ("preset_get", {"ref": "web"}, ("GET", "/agent/v1/admin/presets/web", "", None)),
        (
            "preset_create",
            {"name": "web", "spec": {"image": "x"}},
            ("POST", "/agent/v1/admin/presets", "", {"name": "web", "spec": {"image": "x"}}),
        ),
        (
            "session_restart",
            {"slug": "s"},
            ("POST", "/agent/v1/admin/sessions/s/restart", "", None),
        ),
        (
            "agent_restart",
            {"slug": "s", "pod": "p-0"},
            ("POST", "/agent/v1/admin/sessions/s/agents/p-0/restart", "", None),
        ),
        (
            "webhook_events",
            {"status": "queued", "limit": 5},
            ("GET", "/agent/v1/admin/webhooks/events", "status=queued&limit=5", None),
        ),
        (
            "schedule_update",
            {"schedule_id": "7", "name": "n", "cron": "* * * * *", "preset_id": "p", "prompt": "x"},
            (
                "PUT",
                "/agent/v1/admin/schedules/schedules/7",
                "",
                {"name": "n", "cron": "* * * * *", "preset_id": "p", "prompt": "x"},
            ),
        ),
        (
            "chat_room_settings",
            {"room_id": "!r:x", "trigger": "mention"},
            ("PUT", "/agent/v1/admin/chats/rooms/!r:x/settings", "", {"trigger": "mention"}),
        ),
        (
            "chat_rule_delete",
            {"rule_id": "3"},
            ("DELETE", "/agent/v1/admin/chats/rules/3", "", None),
        ),
    ]
    async with harness(handler) as h:
        for name, args, want in calls:
            seen.clear()
            res = await h.call(name, args)
            assert not res.is_error, (name, res.text)
            assert seen == [want], name


async def test_unknown_field_is_rejected() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        res = await h.call("preset_create", {"name": "a", "spec": {}, "password": "x"})
        assert res.is_error
    assert seen == []
