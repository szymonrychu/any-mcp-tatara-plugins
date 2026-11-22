from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.context import PluginContext
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.admin import ADMIN_TOOLS

OLD_UNIT = "clus" + "ter"
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
ALL_TOOLS = {
    "orchestrator_me",
    "orchestrator_leadership_handover",
    "orchestrator_handoff_submit",
    "orchestrator_lock_claim",
    "orchestrator_lock_release",
    "orchestrator_lock_list",
    "orchestrator_lock_reassign",
    "orchestrator_scale",
    "orchestrator_init_scripts_get",
    "orchestrator_init_scripts_edit",
    "orchestrator_sessions_list",
    "orchestrator_chat_rooms",
    "orchestrator_chat_history",
    "orchestrator_chat_attachment_fetch",
    "orchestrator_chat_attachment_status",
    "orchestrator_self_close",
    "orchestrator_activity",
    "orchestrator_project_list",
    "orchestrator_project_summary",
    "orchestrator_project_chat_read",
    "orchestrator_chat_notes_get",
    "orchestrator_chat_notes_update",
}
SPEC_TOOLS = {
    "orchestrator_spec_list",
    "orchestrator_spec_get",
    "orchestrator_spec_create",
    "orchestrator_spec_update",
    "orchestrator_spec_versions",
    "orchestrator_spec_comment",
    "orchestrator_spec_comments",
    "orchestrator_spec_comment_resolve",
    "orchestrator_spec_queue_list",
    "orchestrator_spec_queue_move",
    "orchestrator_spec_pull",
    "orchestrator_spec_push",
}
MINDMAP_TOOLS = {
    f"orchestrator_mindmap_{n}"
    for n in (
        "map_list", "map_get", "map_create", "map_update", "node_get", "node_history", "search",
        "traverse", "node_create", "node_update", "note_put", "growth_area_update",
        "edge_create", "edge_delete", "link", "task_nodes", "direction_list",
        "direction_create", "direction_update",
    )
}  # fmt: skip
PLAN_TOOLS = {
    "orchestrator_plan_get",
    "orchestrator_plan_set",
    "orchestrator_plan_steps",
    "orchestrator_plan_step_get",
    "orchestrator_plan_step_add",
    "orchestrator_plan_step_edit",
    "orchestrator_plan_step_deps",
    "orchestrator_plan_steps_order",
    "orchestrator_plan_step_next",
    "orchestrator_plan_step_update",
    "orchestrator_plan_gate",
    "orchestrator_spec_section",
    "orchestrator_plan_steps_push",
    "orchestrator_plan_export",
    "orchestrator_plan_questions_ask",
}
TASK_TOOLS = {
    "orchestrator_task_create",
    "orchestrator_task_update",
    "orchestrator_task_list",
    "orchestrator_task_get",
    "orchestrator_task_comment",
    "orchestrator_decision_ask",
    "orchestrator_decision_withdraw",
    "orchestrator_approval_ask",
    "orchestrator_approval_list",
    "orchestrator_todo_set",
    "orchestrator_todo_update",
    "orchestrator_session_labels_set",
}


def harness(handler: Any, **overrides: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config={**CONFIG, **overrides},
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


async def descriptions(**overrides: Any) -> dict[str, str]:
    async with harness(ok, **overrides) as h:
        return {t["name"]: t["description"] for t in await h.list_tools()}


async def test_lists_all_tools() -> None:
    every = (
        ALL_TOOLS
        | TASK_TOOLS
        | SPEC_TOOLS
        | PLAN_TOOLS
        | MINDMAP_TOOLS
        | {f"orchestrator_{r.name}" for r in ADMIN_TOOLS}
    )
    assert set(await descriptions()) == every
    assert set(await descriptions(hive=False)) == every


async def test_tools_call_agent_routes() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"echo": request.url.path})

    calls: list[tuple[str, dict[str, Any], tuple[str, str, Any]]] = [
        ("me", {}, ("GET", "/agent/v1/me", None)),
        (
            "leadership_handover",
            {"reason": "ctx", "epoch": 4, "to": "agent-1"},
            (
                "POST",
                "/agent/v1/leadership/handover",
                {"reason": "ctx", "epoch": 4, "to": "agent-1"},
            ),
        ),
        (
            "leadership_handover",
            {"reason": "ctx", "epoch": 4},
            ("POST", "/agent/v1/leadership/handover", {"reason": "ctx", "epoch": 4}),
        ),
        ("lock_claim", {"paths": ["a.py"]}, ("POST", "/agent/v1/locks/claim", {"paths": ["a.py"]})),
        (
            "lock_release",
            {"paths": ["a.py"]},
            ("POST", "/agent/v1/locks/release", {"paths": ["a.py"]}),
        ),
        ("lock_list", {}, ("GET", "/agent/v1/locks", None)),
        (
            "lock_reassign",
            {"path": "a.py", "to": None},
            ("POST", "/agent/v1/locks/reassign", {"path": "a.py", "to": None}),
        ),
        ("scale", {"replicas": 2}, ("POST", "/agent/v1/scale", {"replicas": 2})),
        ("init_scripts_get", {}, ("GET", "/agent/v1/init-scripts", None)),
        (
            "init_scripts_edit",
            {"scripts": ["echo hi"]},
            ("PUT", "/agent/v1/init-scripts", {"scripts": ["echo hi"]}),
        ),
        ("sessions_list", {}, ("GET", "/agent/v1/sessions", None)),
    ]
    async with harness(handler) as h:
        for tool, args, expected in calls:
            seen.clear()
            result = await h.call(f"orchestrator_{tool}", args)
            assert not result.is_error, result.text
            assert seen == [expected]
            assert json.loads(result.text) == {"echo": expected[1]}


async def test_session_tools_call_agent_routes() -> None:
    seen: list[tuple[str, str, Any, httpx.Headers]] = []
    listing = {
        "sessions": [
            {"slug": "boss", "preset_name": "p", "superleader": True, "leader_present": True}
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers))
        if request.method == "GET":
            return httpx.Response(200, json=listing)
        return httpx.Response(202, json={"message_id": "m-1"})

    async with harness(handler) as h:
        listed = await h.call("orchestrator_sessions_list")
    assert not listed.is_error, listed.text
    assert json.loads(listed.text) == listing
    assert [(m, p, b) for m, p, b, _ in seen] == [
        ("GET", "/agent/v1/sessions", None),
    ]
    for _, _, _, headers in seen:
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert headers["x-orch-session"] == "s-1"


async def test_chat_tools_call_agent_routes() -> None:
    seen: list[tuple[str, str, Any, httpx.Headers]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers))
        return httpx.Response(200, json={"echo": request.url.path})

    calls: list[tuple[str, dict[str, Any], tuple[str, str, Any]]] = [
        ("chat_rooms", {}, ("GET", "/agent/v1/chat/rooms", None)),
        (
            "chat_attachment_fetch",
            {"id": "m3"},
            ("POST", "/agent/v1/chat/attachments", {"id": "m3"}),
        ),
        (
            "chat_attachment_status",
            {"request_id": "a4"},
            ("GET", "/agent/v1/chat/attachments/a4", None),
        ),
    ]
    async with harness(handler) as h:
        for tool, args, expected in calls:
            seen.clear()
            result = await h.call(f"orchestrator_{tool}", args)
            assert not result.is_error, result.text
            assert [(m, p, b) for m, p, b, _ in seen] == [expected]
            assert seen[0][3]["authorization"] == f"Bearer {TOKEN}"
            assert seen[0][3]["x-orch-session"] == "s-1"
            assert json.loads(result.text) == {"echo": expected[1]}


async def test_self_close_calls_route() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(202, json={"close_requested_at": "2026-11-02T10:00:00Z"})

    async with harness(handler) as h:
        result = await h.call("orchestrator_self_close", {"reason": "done"})
    assert not result.is_error, result.text
    assert seen == [("POST", "/agent/v1/close", {"reason": "done"})]


async def test_self_close_errors_surface_code() -> None:
    errors = {"not_leader": 403, "own_session": 422, "session_deleting": 409}

    def handler(request: httpx.Request) -> httpx.Response:
        code = json.loads(request.content)["reason"]
        return httpx.Response(
            errors[code], json={"error": {"code": code, "message": f"refused, token {TOKEN}"}}
        )

    async with harness(handler) as h:
        for code, status in errors.items():
            result = await h.call("orchestrator_self_close", {"reason": code})
            assert result.is_error
            assert code in result.text
            assert f"HTTP {status}" in result.text
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status
            assert TOKEN not in result.text
            assert TOKEN not in json.dumps(result.structured)


async def test_self_close_description() -> None:
    d = (await descriptions())["orchestrator_self_close"]
    assert "Leader only" in d
    assert "last action of your turn" in d
    assert "cancel it from the web UI" in d
    assert "Superleader sessions cannot close themselves" in d


async def test_self_close_schema_bounds() -> None:
    async with harness(ok) as h:
        tool = next(t for t in await h.list_tools() if t["name"] == "orchestrator_self_close")
    schema = tool["inputSchema"]
    assert schema["properties"]["reason"]["minLength"] == 1
    assert schema["properties"]["reason"]["maxLength"] == 500
    assert schema["required"] == ["reason"]
    assert schema["additionalProperties"] is False


async def test_chat_descriptions() -> None:
    on = await descriptions()
    rooms = on["orchestrator_chat_rooms"]
    assert "bound room" in rooms
    assert "unbound" in rooms
    assert "Leader only" in rooms
    for text in (await descriptions(hive=False)).values():
        assert "You are agent" not in text


async def test_activity_tool_calls_route() -> None:
    seen: list[tuple[str, str, dict[str, str], Any, httpx.Headers]] = []
    answer = {"lines": [{"id": 4, "agent": "pod-1", "text": "pod-1 ran: ls (ok)", "ts": "t"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append(
            (request.method, request.url.path, dict(request.url.params), body, request.headers)
        )
        return httpx.Response(200, json=answer)

    async with harness(handler) as h:
        both = await h.call("orchestrator_activity", {"agent": "pod-1", "limit": 20})
        bare = await h.call("orchestrator_activity")
        limit_only = await h.call("orchestrator_activity", {"limit": 200})
    for result in (both, bare, limit_only):
        assert not result.is_error, result.text
        assert json.loads(result.text) == answer
    assert [(m, p, q, b) for m, p, q, b, _ in seen] == [
        ("GET", "/agent/v1/activity", {"agent": "pod-1", "limit": "20"}, None),
        ("GET", "/agent/v1/activity", {}, None),
        ("GET", "/agent/v1/activity", {"limit": "200"}, None),
    ]
    for *_, headers in seen:
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert headers["x-orch-session"] == "s-1"


async def test_activity_tool_schema_and_description() -> None:
    async with harness(ok) as h:
        tool = next(t for t in await h.list_tools() if t["name"] == "orchestrator_activity")
    props = tool["inputSchema"]["properties"]
    assert set(props) == {"agent", "limit"}
    assert props["agent"]["type"] == "string"
    assert props["limit"]["type"] == "integer"
    assert props["limit"]["minimum"] == 1
    assert props["limit"]["maximum"] == 200
    assert not tool["inputSchema"].get("required")
    text = tool["description"]
    assert text.startswith(
        "You are agent agent-0, one of up to 3 agents in hive my-hive; the workdir is shared."
    )
    assert (
        "What the other agents did recently (commands with exit status, files written, "
        "finished turns): newest last. Call it when you need it; nothing is streamed to you."
    ) in text
    off = (await descriptions(hive=False))["orchestrator_activity"]
    assert "You are agent" not in off
    assert off.startswith("What the other agents did recently")


async def test_handover_description_protocol() -> None:
    text = (await descriptions())["orchestrator_leadership_handover"]
    assert "handover_due" in text
    assert "2000" in text
    assert "pending" in text
    assert "transcript" in text
    assert "mcp__ccw__send_message" not in text
    assert '"ready"' not in text
    assert "leadership-request note" in text
    assert "orchestrator_me" in text
    assert "outcome arrives as a hive-state note" not in text
    # every handover has a deadline, and a pending one can be withdrawn
    assert "deadline" in text and "expired" in text
    # the deadline is the orchestrator's setting: no literal that drifts from it
    assert "minutes" not in text
    assert "orchestrator_leadership_withdraw" in text


async def test_handover_reason_schema() -> None:
    async with harness(ok) as h:
        tool = next(
            t for t in await h.list_tools() if t["name"] == "orchestrator_leadership_handover"
        )
    reason = tool["inputSchema"]["properties"]["reason"]
    assert reason["minLength"] == 1
    assert reason["maxLength"] == 2000


async def test_leadership_handover_has_no_extend() -> None:
    async with harness(ok) as h:
        tool = next(
            t for t in await h.list_tools() if t["name"] == "orchestrator_leadership_handover"
        )
    schema = tool["inputSchema"]
    assert "extend" not in schema["properties"]
    assert "reason" not in schema.get("required", [])
    text = tool["description"]
    for word in ("extend", "solo", "countdown"):
        assert word not in text
    assert "hive leader" in text


async def test_handoff_submit_row_routes_to_post_handoff() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(202, json={"handoff_id": 1, "phase": "submitted"})

    args = {
        "doing": "wiring the tool",
        "done": ["a"],
        "abandon": [{"item": "/tmp/claude-x", "why": "scratch, not needed"}],
        "handled": [3],
        "to": None,
        "epoch": 2,
    }
    async with harness(handler) as h:
        res = await h.call("orchestrator_handoff_submit", args)
    assert not res.is_error, res.text
    assert seen == [("POST", "/agent/v1/handoff", args)]


async def test_handoff_submit_schema_matches_openapi() -> None:
    # Mirrors HandoffSubmitIn (agents-orchestrator api/schemas.py): extra forbidden, no required.
    async with harness(ok) as h:
        tool = next(t for t in await h.list_tools() if t["name"] == "orchestrator_handoff_submit")
    schema = tool["inputSchema"]
    props = schema["properties"]
    assert set(props) == {
        "doing", "done", "in_flight", "next", "open_requests", "results", "gotchas",
        "abandon", "handled", "to", "epoch",
    }  # fmt: skip
    assert "required" not in schema
    assert schema["additionalProperties"] is False
    assert props["doing"]["type"] == "string"
    assert props["doing"]["maxLength"] == 4000
    for name in ("done", "in_flight", "next", "open_requests", "results", "gotchas"):
        assert props[name]["type"] == "array"
        assert props[name]["maxItems"] == 50
        assert props[name]["items"]["type"] == "string"
        assert props[name]["items"]["maxLength"] == 2000
    abandon = props["abandon"]
    assert abandon["maxItems"] == 50
    assert abandon["items"]["required"] == ["item", "why"]
    assert abandon["items"]["properties"]["why"]["minLength"] == 10
    assert props["handled"]["items"]["type"] == "integer"
    assert props["handled"]["maxItems"] == 50
    assert props["to"]["type"] == ["string", "null"]
    assert props["epoch"]["type"] == "integer"
    openapi = json.loads(
        (Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text()
    )
    assert "post" in openapi["paths"]["/agent/v1/handoff"]


async def test_handoff_submit_description_mentions_shared_scratchpad() -> None:
    text = (await descriptions())["orchestrator_handoff_submit"]
    assert "/tmp/claude-*" in text
    assert ".scratchpad/<name>/" in text
    assert "abandon it with a reason" in text
    assert "422" in text and "409 in_flight" in text
    assert "never post the handoff to the room" in text


async def test_descriptions_have_no_time_words() -> None:
    found = await descriptions()
    for name in ("orchestrator_handoff_submit", "orchestrator_leadership_handover"):
        text = found[name].lower()
        for word in ("minute", "second", "hour", "countdown", "in n "):
            assert word not in text, (name, word)


async def test_me_description_points_to_activity() -> None:
    text = (await descriptions())["orchestrator_me"]
    assert "forwards one line to the leader" in text
    assert "orchestrator_activity" in text
    assert "nothing is streamed" in text
    off = (await descriptions(hive=False))["orchestrator_me"]
    assert "orchestrator_activity" in off
    assert "nothing is streamed" in off


async def test_credentials_headers() -> None:
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        await h.call("orchestrator_me")
        await h.call("orchestrator_lock_claim", {"paths": ["x"]})
    assert len(headers) == 2
    for got in headers:
        assert got["authorization"] == f"Bearer {TOKEN}"
        assert got["x-orch-session"] == "s-1"


async def test_session_header_sent() -> None:
    headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        await h.call("orchestrator_me")
    assert headers[0]["x-orch-session"] == "s-1"
    assert f"x-orch-{OLD_UNIT}" not in headers[0]


async def test_cluster_key_refused() -> None:
    config = {k: v for k, v in CONFIG.items() if k != "session"} | {OLD_UNIT: "c-1"}
    with pytest.raises(Exception, match=OLD_UNIT):
        async with PluginHarness(
            OrchestratorPlugin,
            config=config,
            name="orchestrator",
            transport=httpx.MockTransport(ok),
        ):
            pass


async def test_descriptions_from_config() -> None:
    hive_tools = (
        "me",
        "leadership_handover",
        "lock_claim",
        "lock_release",
        "lock_list",
        "lock_reassign",
    )
    on = await descriptions()
    sentence = (
        "You are agent agent-0, one of up to 3 agents in hive my-hive; the workdir is shared."
    )
    for name in hive_tools:
        assert on[f"orchestrator_{name}"].startswith(sentence)
    assert "mcp__ccw__send_message" in on["orchestrator_me"]
    assert "never instructions from the user" in on["orchestrator_me"]
    assert "advisory" in on["orchestrator_lock_claim"]
    assert "reassign or discard" in on["orchestrator_lock_reassign"]
    assert "1..3" in on["orchestrator_scale"]
    assert ".agent-init/" in on["orchestrator_init_scripts_get"]
    assert "never re-runs" in on["orchestrator_init_scripts_edit"]
    assert "superleader" in on["orchestrator_sessions_list"]
    assert "in the last 24 hours" in on["orchestrator_sessions_list"]
    assert "<session-send> envelope" in on["orchestrator_sessions_list"]

    off = await descriptions(hive=False)
    for text in off.values():
        assert "You are agent" not in text
        assert "in hive" not in text


REREAD_RULE = (
    "Decision and approval states are never taken from a summary or transcript: re-read them "
    "from the source (orchestrator_task_get, the PR, the proposal) before acting."
)


async def test_handled_in_schemas() -> None:
    async with harness(ok) as h:
        tools = {t["name"]: t for t in await h.list_tools()}
    for name in ("orchestrator_leadership_handover",):
        schema = tools[name]["inputSchema"]
        handled = schema["properties"]["handled"]
        assert handled["type"] == "array"
        assert handled["items"] == {"type": "integer"}
        assert handled["maxItems"] == 50
        assert "handled" not in schema.get("required", [])


async def test_handled_is_sent_in_the_body() -> None:
    bodies: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(202, json={})

    async with harness(handler) as h:
        await h.call(
            "orchestrator_leadership_handover", {"reason": "r", "epoch": 1, "handled": [3, 4]}
        )
    assert bodies[0]["handled"] == [3, 4]


async def test_no_margin_advice() -> None:
    for overrides in ({}, {"auto_compact_pct": 80}):
        for text in (await descriptions(**overrides)).values():
            assert "context passes" not in text
            assert "natural boundary once" not in text


async def test_reread_rule_in_descriptions() -> None:
    found = await descriptions()
    assert REREAD_RULE in found["orchestrator_me"]
    assert REREAD_RULE in found["orchestrator_leadership_handover"]


async def test_handover_description_explains_reason_and_handled() -> None:
    text = (await descriptions())["orchestrator_leadership_handover"]
    assert "judgment for the successor" in text
    assert "generated by the orchestrator" in text
    assert "handover_due" in text
    assert "handled" in text
    assert "<message-in>" in text
    assert "session-message" not in text


async def test_me_description_mentions_state() -> None:
    found = await descriptions()
    assert "leader_state" in found["orchestrator_me"]


async def test_error_mapping_redacts_token(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "stale_epoch", "message": f"epoch is 5, token {TOKEN}"}},
        )

    caplog.set_level(logging.DEBUG)
    async with harness(handler) as h:
        result = await h.call("orchestrator_leadership_handover", {"reason": "r", "epoch": 1})
    assert result.is_error
    assert "stale_epoch" in result.text
    assert "epoch is 5" in result.text
    assert TOKEN not in result.text
    assert TOKEN not in json.dumps(result.structured)
    assert TOKEN not in caplog.text

    async with harness(lambda r: httpx.Response(500, text=f"boom {TOKEN}")) as h:
        result = await h.call("orchestrator_me")
    assert result.is_error
    assert "500" in result.text
    assert TOKEN not in result.text


async def test_no_io_at_load() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request {request.url}")

    plugin = OrchestratorPlugin()
    plugin.ctx = PluginContext(
        name="orchestrator",
        namespace="orchestrator",
        config=OrchestratorPlugin.Config.model_validate(CONFIG),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        log=logging.getLogger("test"),
        response_budget_chars=40000,
        config_dir=None,
    )
    await plugin.setup()
    specs = await plugin.load_tools()
    assert len(specs) == (
        len(ALL_TOOLS)
        + len(TASK_TOOLS)
        + len(SPEC_TOOLS)
        + len(PLAN_TOOLS)
        + len(MINDMAP_TOOLS)
        + len(ADMIN_TOOLS)
    )


async def test_scale_and_init_tools_match_contracts() -> None:
    accepted = {"status": "accepted", "from": 3, "to": 2, "removed": ["agent-2"]}
    scripts = {"scripts": ["echo a"], "log_dir": ".agent-init/", "applies": "next_session"}
    seen: list[tuple[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, body))
        if request.url.path == "/agent/v1/scale":
            if body["replicas"] == 9:
                return httpx.Response(
                    422, json={"error": {"code": "out_of_range", "message": "1..3"}}
                )
            if body["replicas"] == 0:
                return httpx.Response(
                    409, json={"error": {"code": "static_session", "message": "static"}}
                )
            if body["replicas"] == 1:
                return httpx.Response(
                    409, json={"error": {"code": "leader_would_leave", "message": "leader"}}
                )
            if body["replicas"] == 5:
                return httpx.Response(
                    403, json={"error": {"code": "not_leader", "message": "not the leader"}}
                )
            return httpx.Response(202, json=accepted)
        return httpx.Response(200, json=scripts)

    async with harness(handler) as h:
        r = await h.call("orchestrator_scale", {"replicas": 2})
        assert json.loads(r.text) == accepted
        for n, code in (
            (9, "out_of_range"),
            (0, "static_session"),
            (1, "leader_would_leave"),
            (5, "not_leader"),
        ):
            r = await h.call("orchestrator_scale", {"replicas": n})
            assert r.is_error and code in r.text
        r = await h.call("orchestrator_init_scripts_get")
        assert json.loads(r.text) == scripts
        seen.clear()
        r = await h.call("orchestrator_init_scripts_edit", {"scripts": ["echo b"]})
        assert seen == [("PUT", {"scripts": ["echo b"]})]


async def test_refused_edit_surfaces_proposable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        proposable = request.url.path == "/agent/v1/scale"
        error = {"code": "human_only_field", "message": "only a human can change: x"}
        if proposable:
            error["proposable"] = True
            error["fields"] = ["x"]
        return httpx.Response(403, json={"error": error})

    async with harness(handler) as h:
        r = await h.call("orchestrator_scale", {"replicas": 2})
        assert r.is_error and r.structured["error"]["proposable"] is True
        assert r.structured["error"]["fields"] == ["x"]
        assert "proposable: true" in r.text
        r = await h.call("orchestrator_init_scripts_get")
        assert r.is_error and "proposable" not in r.text
        assert "proposable" not in r.structured["error"]


async def tools_by_name() -> dict[str, dict[str, Any]]:
    async with harness(ok) as h:
        return {t["name"]: t for t in await h.list_tools()}


async def test_task_tools_registered_with_names() -> None:
    tools = await tools_by_name()
    assert set(tools) >= TASK_TOOLS
    for name in TASK_TOOLS:
        assert tools[name]["description"].strip()


async def test_schemas_strict_and_enums() -> None:
    tools = await tools_by_name()
    for name in TASK_TOOLS:
        schema = tools[name]["inputSchema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False, name
        out = tools[name]["outputSchema"]
        assert out["type"] == "object", name
        if name != "orchestrator_approval_list":  # rows carry their own url; no top-level one
            assert "url" in out["properties"], name
    statuses = ["idea", "open", "in_progress", "done", "dropped"]
    update = tools["orchestrator_task_update"]["inputSchema"]["properties"]
    assert update["status"]["enum"] == statuses
    listing = tools["orchestrator_task_list"]["inputSchema"]["properties"]
    assert listing["status"]["enum"] == ["active", "done", "dropped", "all"]
    assert tools["orchestrator_task_create"]["inputSchema"]["properties"]["kind"]["enum"] == [
        "idea",
        "task",
    ]
    create = tools["orchestrator_task_create"]["inputSchema"]
    assert create["required"] == ["title", "client_key"]
    todo = tools["orchestrator_todo_update"]["inputSchema"]["properties"]
    assert todo["status"]["enum"] == ["todo", "inprogress", "done"]
    items = tools["orchestrator_todo_set"]["inputSchema"]["properties"]["items"]
    assert items["maxItems"] == 100
    assert items["items"]["additionalProperties"] is False
    assert items["items"]["properties"]["status"]["enum"] == ["todo", "inprogress", "done"]


async def test_task_tools_accept_project() -> None:
    tools = await tools_by_name()
    for name in ("task_create", "task_update", "task_list"):
        props = tools[f"orchestrator_{name}"]["inputSchema"]["properties"]
        assert props["project"]["type"] == "string", name
        assert "project_list" in props["project"]["description"], name
    assert "project" not in tools["orchestrator_task_create"]["inputSchema"]["required"]
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"key": "T-5", "url": "https://x/tasks/T-5"})

    async with harness(handler) as h:
        await h.call("orchestrator_task_create", {"title": "t", "client_key": "k", "project": "p"})
        await h.call("orchestrator_task_update", {"key": "T-5", "version": 1, "project": "q"})
        await h.call("orchestrator_task_list", {"project": "p"})
    assert seen == [
        ("POST", "/agent/v1/tasks", "", {"title": "t", "client_key": "k", "project": "p"}),
        ("PATCH", "/agent/v1/tasks/T-5", "", {"version": 1, "project": "q"}),
        ("GET", "/agent/v1/tasks", "project=p&limit=10", None),
    ]


async def test_project_list_maps_to_agent_projects() -> None:
    seen: list[tuple[str, str, str]] = []
    projects = [{"slug": "default", "name": "Default", "description": "", "archived": False}]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json=projects)

    async with harness(handler) as h:
        tool = {t["name"]: t for t in await h.list_tools()}["orchestrator_project_list"]
        res = await h.call("orchestrator_project_list")
    assert tool["inputSchema"]["properties"] == {}
    assert tool["annotations"]["readOnlyHint"] is True
    assert not res.is_error, res.text
    assert json.loads(res.text) == projects
    assert seen == [("GET", "/agent/v1/projects", "")]


async def test_project_summary_tool() -> None:
    seen: list[tuple[str, str, str]] = []
    summary = [{"project": "default", "tasks": {"open": 2}, "specs": 1}]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json=summary)

    openapi = json.loads(
        (Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text()
    )
    known = {(m.upper(), p) for p, ms in openapi["paths"].items() for m in ms}
    assert ("GET", "/agent/v1/projects/summary") in known
    async with harness(handler) as h:
        tool = {t["name"]: t for t in await h.list_tools()}["orchestrator_project_summary"]
        res = await h.call("orchestrator_project_summary")
        await h.call("orchestrator_project_summary", {"project": "default"})
    assert set(tool["inputSchema"]["properties"]) == {"project"}
    assert tool["inputSchema"]["additionalProperties"] is False
    assert "required" not in tool["inputSchema"]
    assert tool["annotations"]["readOnlyHint"] is True
    assert "project_list" in tool["inputSchema"]["properties"]["project"]["description"]
    assert not res.is_error, res.text
    assert json.loads(res.text) == summary
    assert seen == [
        ("GET", "/agent/v1/projects/summary", ""),
        ("GET", "/agent/v1/projects/summary", "project=default"),
    ]


async def test_annotations_per_tool() -> None:
    tools = await tools_by_name()

    def hints(name: str) -> dict[str, Any]:
        return tools[f"orchestrator_{name}"]["annotations"]

    for name in ("task_list", "task_get"):
        assert hints(name)["readOnlyHint"] is True
        assert hints(name)["openWorldHint"] is False
    for name in ("task_update", "todo_set", "todo_update", "decision_withdraw"):
        assert hints(name)["idempotentHint"] is True, name
        assert hints(name)["destructiveHint"] is False, name
        assert hints(name).get("readOnlyHint") is not True, name
    for name in ("task_create", "task_comment", "decision_ask"):
        assert hints(name)["idempotentHint"] is False, name
        assert hints(name)["readOnlyHint"] is False, name
    for name in TASK_TOOLS:
        assert tools[name]["annotations"]["openWorldHint"] is False, name


async def test_task_get_path_and_method() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        task = {"key": "T-5", "version": 3, "url": "https://x/tasks/T-5"}
        decision = {"id": 7, "task_key": "T-5", "status": "open", "url": "https://x/tasks/T-5"}
        return httpx.Response(200, json=decision if "decisions" in request.url.path else task)

    options = [
        {"label": "a", "recommended": True},
        {"label": "b", "description": "d", "recommended": False},
    ]
    calls: list[tuple[str, dict[str, Any], tuple[str, str, str, Any]]] = [
        (
            "task_get",
            {"key": "T-5"},
            ("GET", "/agent/v1/tasks/T-5", "events_limit=10&comments_limit=0", None),
        ),
        (
            "task_create",
            {"title": "t", "client_key": "k1", "assignee_self": True},
            (
                "POST",
                "/agent/v1/tasks",
                "",
                {"title": "t", "client_key": "k1", "assignee_self": True},
            ),
        ),
        (
            "task_update",
            {"key": "T-5", "version": 3, "status": "done"},
            ("PATCH", "/agent/v1/tasks/T-5", "", {"version": 3, "status": "done"}),
        ),
        (
            "task_list",
            {"status": "active", "mine": True, "label": ["a", "b"]},
            ("GET", "/agent/v1/tasks", "status=active&mine=true&label=a&label=b&limit=10", None),
        ),
        (
            "task_comment",
            {"key": "T-5", "body": "hi"},
            ("POST", "/agent/v1/tasks/T-5/comments", "", {"body": "hi"}),
        ),
        (
            "decision_ask",
            {"key": "T-5", "question": "q?", "options": options, "client_key": "d1"},
            (
                "POST",
                "/agent/v1/tasks/T-5/decisions",
                "",
                {"question": "q?", "options": options, "client_key": "d1"},
            ),
        ),
        (
            "decision_withdraw",
            {"key": "T-5", "decision_id": "7", "reason": "moot", "superseded_by": 9},
            (
                "POST",
                "/agent/v1/tasks/T-5/decisions/7/withdraw",
                "",
                {"reason": "moot", "superseded_by": 9},
            ),
        ),
        (
            "decision_withdraw",
            {"key": "T-5", "decision_id": "7", "reason": "moot", "answered_elsewhere": "in chat"},
            (
                "POST",
                "/agent/v1/tasks/T-5/decisions/7/withdraw",
                "",
                {"reason": "moot", "answered_elsewhere": "in chat"},
            ),
        ),
        (
            "todo_set",
            {"key": "T-5", "items": [{"text": "x"}]},
            ("PUT", "/agent/v1/tasks/T-5/todo", "", {"items": [{"text": "x"}]}),
        ),
        (
            "todo_update",
            {"key": "T-5", "item_id": "2", "status": "done"},
            ("PATCH", "/agent/v1/tasks/T-5/todo/2", "", {"status": "done"}),
        ),
        (
            "session_labels_set",
            {"labels": ["a", "b"]},
            ("PUT", "/agent/v1/session/labels", "", {"labels": ["a", "b"]}),
        ),
    ]
    async with harness(handler) as h:
        for tool, args, expected in calls:
            seen.clear()
            result = await h.call(f"orchestrator_{tool}", args)
            assert not result.is_error, (tool, result.text)
            assert seen == [expected], tool


async def test_decision_ask_requires_one_recommended_schema_hint() -> None:
    tools = await tools_by_name()
    ask = tools["orchestrator_decision_ask"]
    options = ask["inputSchema"]["properties"]["options"]
    assert options["minItems"] == 2 and options["maxItems"] == 8
    assert options["items"]["required"] == ["label", "recommended"]
    assert options["items"]["additionalProperties"] is False
    assert "exactly one" in options["description"].lower()
    assert "recommended" in options["description"]
    assert ask["inputSchema"]["required"] == ["key", "question", "options"]


async def test_decision_withdraw_schema_and_texts() -> None:
    tools = await tools_by_name()
    w = tools["orchestrator_decision_withdraw"]
    props = w["inputSchema"]["properties"]
    assert w["inputSchema"]["required"] == ["key", "decision_id", "reason"]
    assert props["reason"]["type"] == "string"
    assert props["superseded_by"]["type"] == "integer"
    assert props["answered_elsewhere"]["type"] == "string"
    text = w["description"].lower()
    assert "exactly one" in text and "422" in text
    ask = tools["orchestrator_decision_ask"]["description"].lower()
    assert "only channel" in ask and "never ask" in ask
    assert "blocked by a tool, permission or sandbox" in ask and "fix" in ask


async def test_409_maps_to_is_error() -> None:
    cases = {
        ("task_update", 412): "stale_version",
        ("decision_withdraw", 409): "already_answered",
        ("task_get", 404): "not_found",
    }

    by_path = {
        "/agent/v1/tasks/T-1": {"PATCH": (412, "stale_version"), "GET": (404, "not_found")},
        "/agent/v1/tasks/T-1/decisions/3/withdraw": {"POST": (409, "already_answered")},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        status, code = by_path[request.url.path][request.method]
        return httpx.Response(status, json={"error": {"code": code, "message": "no"}})

    args = {
        "task_update": {"key": "T-1", "version": 1},
        "decision_withdraw": {"key": "T-1", "decision_id": "3", "reason": "r", "superseded_by": 4},
        "task_get": {"key": "T-1"},
    }
    async with harness(handler) as h:
        for (tool, status), code in cases.items():
            result = await h.call(f"orchestrator_{tool}", args[tool])
            assert result.is_error, tool
            assert code in result.text and f"HTTP {status}" in result.text, tool
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status


async def test_description_mentions_lifecycle() -> None:
    tools = await tools_by_name()
    for name in ("task_create", "task_update", "todo_set", "todo_update"):
        text = tools[f"orchestrator_{name}"]["description"].lower()
        assert "create or claim" in text and "todo" in text and "close" in text, name
    ask = tools["orchestrator_decision_ask"]["description"].lower()
    assert "chat message" in ask and "link" in ask and "task_get" in ask


async def test_labels_param_schema_pattern_and_max() -> None:
    tools = await tools_by_name()
    pattern = "^[a-z0-9-_.:]{1,40}$"
    for name in ("task_create", "task_update", "session_labels_set"):
        labels = tools[f"orchestrator_{name}"]["inputSchema"]["properties"]["labels"]
        assert labels["type"] == "array" and labels["maxItems"] == 20, name
        assert labels["items"]["type"] == "string" and labels["items"]["maxLength"] == 40, name
        assert "pattern" not in labels["items"], name
        assert pattern in labels["description"], name
    assert tools["orchestrator_session_labels_set"]["inputSchema"]["required"] == ["labels"]
    one = tools["orchestrator_task_list"]["inputSchema"]["properties"]["label"]
    assert one["items"]["maxLength"] == 40 and pattern in one["description"]


async def test_task_list_labels_mine_enum() -> None:
    tools = await tools_by_name()
    labels = tools["orchestrator_task_list"]["inputSchema"]["properties"]["labels"]
    assert labels["enum"] == ["mine"]


async def test_session_labels_set_tool_path_method_and_annotations() -> None:
    tools = await tools_by_name()
    tool = tools["orchestrator_session_labels_set"]
    assert tool["annotations"]["idempotentHint"] is True
    assert tool["annotations"]["destructiveHint"] is False
    assert tool["annotations"]["openWorldHint"] is False
    assert "own session" in tool["description"].lower()
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"session_id": "s-1", "labels": ["a"]})

    async with harness(handler) as h:
        result = await h.call("orchestrator_session_labels_set", {"labels": ["A "]})
    assert not result.is_error, result.text
    assert seen == [("PUT", "/agent/v1/session/labels", {"labels": ["A "]})]


async def test_removed_chat_tools_are_gone() -> None:
    names = set(await tools_by_name())
    assert not names & {
        "orchestrator_chat_send",
        "orchestrator_chat_react",
        "orchestrator_chat_status",
        "orchestrator_session_send",
        "orchestrator_project_chat_send",
    }


def test_no_text_names_a_removed_chat_tool() -> None:
    src = Path(__file__).resolve().parents[1] / "src" / "any_mcp_tatara_orchestrator"
    pattern = re.compile(
        r"orchestrator_chat_(send|react|status)\b|\bchat_(send|react|status)\b"
        r"|\b(orchestrator_)?(session_send|project_chat_send)\b|<session-message"
    )
    hits = [
        f"{p.name}:{i}"
        for p in src.glob("*.py")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if pattern.search(line)
    ]
    assert not hits, hits


async def test_chat_texts_point_to_envelopes() -> None:
    on = await descriptions()
    assert "<chat-send" in on["orchestrator_chat_rooms"]


async def test_chat_rooms_description_mentions_agents_may_send_files() -> None:
    on = await descriptions()
    assert "agents_may_send_files" in on["orchestrator_chat_rooms"]


async def test_approval_tools_relay_paths() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        if request.method == "GET":
            return httpx.Response(200, json={"items": []})
        return httpx.Response(
            201,
            json={
                "id": 7,
                "task_key": "T-3",
                "status": "open",
                "url": "u",
                "expires_at": "2026-11-18T00:00:00Z",
                "context": "x" * 100000,
            },
        )

    openapi = json.loads(
        (Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text()
    )
    known = {(m.upper(), p) for p, ms in openapi["paths"].items() for m in ms}
    assert ("GET", "/agent/v1/approvals") in known
    async with harness(handler) as h:
        ask = await h.call(
            "orchestrator_approval_ask", {"key": "T-3", "title": "deploy?", "client_key": "k"}
        )
        await h.call("orchestrator_approval_list", {"status": "open"})
    assert json.loads(ask.text) == {
        "id": 7,
        "task_key": "T-3",
        "status": "open",
        "url": "u",
        "expires_at": "2026-11-18T00:00:00Z",
    }
    assert seen == [
        ("POST", "/agent/v1/tasks/T-3/approvals", "", {"title": "deploy?", "client_key": "k"}),
        ("GET", "/agent/v1/approvals", "status=open", None),
    ]


async def test_approval_texts_route_permissions() -> None:
    tools = await tools_by_name()
    ask = tools["orchestrator_approval_ask"]
    assert ask["inputSchema"]["required"] == ["key", "title"]
    assert ask["inputSchema"]["properties"]["expires_in_seconds"]["maximum"] == 604800
    text = ask["description"].lower()
    assert "never a numbered list in chat" in text and "task_get" in text and "expired" in text
    assert "approval_ask instead" in tools["orchestrator_decision_ask"]["description"]
    assert tools["orchestrator_approval_list"]["annotations"]["readOnlyHint"] is True


async def _history_calls(args: dict[str, Any], page: Any = None) -> list[tuple[str, str, str]]:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json=page or {"messages": []})

    async with harness(handler) as h:
        res = await h.call("orchestrator_chat_history", args)
        assert not res.is_error, res.text
    return seen


async def test_chat_history_calls_route_with_query() -> None:
    seen = await _history_calls({"before": "m5", "from": "Ann", "limit": 20})
    assert seen == [("GET", "/agent/v1/chat/history", "before=m5&from=Ann&limit=20")]


async def test_chat_history_default_limit_20() -> None:
    seen = await _history_calls({})
    assert seen == [("GET", "/agent/v1/chat/history", "limit=20")]
    seen = await _history_calls({"limit": 7})
    assert seen == [("GET", "/agent/v1/chat/history", "limit=7")]


async def test_chat_history_keeps_newest_within_budget() -> None:
    messages = [{"id": f"m{i}", "text": "x" * 3000} for i in range(100)]
    page = {
        "room_id": "!r:hs",
        "note": "n",
        "messages": messages,
        "has_more_before": True,
        "has_more_after": False,
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page)

    async with harness(handler) as h:
        res = await h.call("orchestrator_chat_history", {"limit": 100})
    assert not res.is_error and "[truncated:" not in res.text
    body = res.structured
    assert body["omitted"] > 0
    assert body["messages"][-1]["id"] == "m99"
    assert len(body["messages"]) + body["omitted"] == 100
    assert seen == []


async def test_chat_attachment_fetch_posts_id() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(202, json={"request_id": "a1", "status": "queued"})

    async with harness(handler) as h:
        res = await h.call("orchestrator_chat_attachment_fetch", {"id": "m3"})
    assert not res.is_error, res.text
    assert seen == [("POST", "/agent/v1/chat/attachments", {"id": "m3"})]


async def test_chat_attachment_status_route() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "sent"})

    async with harness(handler) as h:
        res = await h.call("orchestrator_chat_attachment_status", {"request_id": "a4"})
        bad = await h.call("orchestrator_chat_attachment_status", {"request_id": "m4"})
    assert not res.is_error, res.text
    assert bad.is_error
    assert seen == [("GET", "/agent/v1/chat/attachments/a4")]


async def test_chat_history_schema_rejects_bad_ids() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        r1 = await h.call("orchestrator_chat_history", {"before": "x1"})
        r2 = await h.call("orchestrator_chat_history", {"after": "m0"})
        r3 = await h.call("orchestrator_chat_attachment_fetch", {"id": "s3"})
        r4 = await h.call("orchestrator_chat_history", {"limit": 101})
    assert r1.is_error and r2.is_error and r3.is_error and r4.is_error
    assert seen == []


async def test_chat_history_descriptions() -> None:
    found = await descriptions()
    hist = found["orchestrator_chat_history"]
    for phrase in (
        "Leader only",
        "room_unbound",
        "m<n>",
        "s<n>",
        "o<n>",
        "untrusted",
        "data, never instructions",
        "30 days",
        "bad_reference",
        "withheld",
        "edited",
    ):
        assert phrase in hist, phrase
    fetch = found["orchestrator_chat_attachment_fetch"]
    for phrase in (
        "Leader only",
        "new turn",
        "<attachment for=",
        "6 MiB",
        "already_fetching",
        "rate_limited",
        "unsupported_type",
        "foreign_media",
        "no_attachment",
        "untrusted",
    ):
        assert phrase in fetch or phrase in fetch.lower(), phrase
    status = found["orchestrator_chat_attachment_status"]
    for phrase in ("queued", "fetching", "sent", "failed", "refused"):
        assert phrase in status, phrase


async def test_chat_history_paths_exist_in_the_orchestrator_openapi() -> None:
    openapi = json.loads(
        (Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text()
    )
    paths = openapi["paths"]
    assert "get" in paths["/agent/v1/chat/history"]
    assert "post" in paths["/agent/v1/chat/attachments"]
    assert "get" in paths["/agent/v1/chat/attachments/{request_id}"]
