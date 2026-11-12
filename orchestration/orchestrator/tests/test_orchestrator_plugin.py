from __future__ import annotations

import json
import logging
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
    "orchestrator_lock_claim",
    "orchestrator_lock_release",
    "orchestrator_lock_list",
    "orchestrator_lock_reassign",
    "orchestrator_scale",
    "orchestrator_init_scripts_get",
    "orchestrator_init_scripts_edit",
    "orchestrator_sessions_list",
    "orchestrator_session_send",
    "orchestrator_chat_rooms",
    "orchestrator_chat_send",
    "orchestrator_chat_react",
    "orchestrator_chat_status",
    "orchestrator_self_close",
    "orchestrator_activity",
    "orchestrator_project_list",
    "orchestrator_project_summary",
    "orchestrator_project_chat_send",
    "orchestrator_project_chat_read",
    "orchestrator_chat_notes_get",
    "orchestrator_chat_notes_update",
    "orchestrator_chat_skills_list",
    "orchestrator_chat_skills_get",
    "orchestrator_chat_skills_save",
    "orchestrator_chat_skills_delete",
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
        (
            "session_send",
            {"slug": "s1", "text": "hi"},
            ("POST", "/agent/v1/sessions/s1/messages", {"text": "hi"}),
        ),
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
        sent = await h.call("orchestrator_session_send", {"slug": "s1", "text": "hi"})
    assert not listed.is_error, listed.text
    assert not sent.is_error, sent.text
    assert json.loads(listed.text) == listing
    assert json.loads(sent.text) == {"message_id": "m-1"}
    assert [(m, p, b) for m, p, b, _ in seen] == [
        ("GET", "/agent/v1/sessions", None),
        ("POST", "/agent/v1/sessions/s1/messages", {"text": "hi"}),
    ]
    for _, _, _, headers in seen:
        assert headers["authorization"] == f"Bearer {TOKEN}"
        assert headers["x-orch-session"] == "s-1"


async def test_session_send_errors_surface_code() -> None:
    errors = {
        "a": (403, "not_leader"),
        "b": (403, "project_forbidden"),
        "c": (409, "hop_limit"),
        "d": (429, "pair_budget"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        slug = request.url.path.split("/")[4]
        status, code = errors[slug]
        return httpx.Response(
            status, json={"error": {"code": code, "message": f"refused, token {TOKEN}"}}
        )

    async with harness(handler) as h:
        for slug, (status, code) in errors.items():
            result = await h.call("orchestrator_session_send", {"slug": slug, "text": "hi"})
            assert result.is_error
            assert code in result.text
            assert f"HTTP {status}" in result.text
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status
            assert TOKEN not in result.text
            assert TOKEN not in json.dumps(result.structured)


async def test_chat_tools_call_agent_routes() -> None:
    seen: list[tuple[str, str, Any, httpx.Headers]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body, request.headers))
        return httpx.Response(200, json={"echo": request.url.path})

    calls: list[tuple[str, dict[str, Any], tuple[str, str, Any]]] = [
        ("chat_rooms", {}, ("GET", "/agent/v1/chat/rooms", None)),
        (
            "chat_send",
            {"text": "hi"},
            ("POST", "/agent/v1/chat/messages", {"text": "hi"}),
        ),
        (
            "chat_send",
            {"text": "hi", "room": "!r:hs"},
            ("POST", "/agent/v1/chat/messages", {"text": "hi", "room": "!r:hs"}),
        ),
        (
            "chat_send",
            {"text": "hi", "room": "!r:hs", "reply_to": "m12"},
            (
                "POST",
                "/agent/v1/chat/messages",
                {"text": "hi", "room": "!r:hs", "reply_to": "m12"},
            ),
        ),
        (
            "chat_send",
            {"text": "hi", "reply_to": "s3"},
            ("POST", "/agent/v1/chat/messages", {"text": "hi", "reply_to": "s3"}),
        ),
        (
            "chat_react",
            {"to": "m12", "key": "\N{THUMBS UP SIGN}"},
            ("POST", "/agent/v1/chat/reactions", {"to": "m12", "key": "\N{THUMBS UP SIGN}"}),
        ),
        ("chat_status", {"id": "s7"}, ("GET", "/agent/v1/chat/messages/s7", None)),
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


async def test_chat_errors_surface_code() -> None:
    errors = {
        "room_not_allowed": 403,
        "room_not_authorised": 403,
        "room_bound": 409,
        "occupier_closing": 409,
        "session_bound": 409,
        "bad_reference": 422,
        "rate_limited": 429,
        "not_leader": 403,
        "chat_unavailable": 503,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        code = json.loads(request.content)["text"]
        return httpx.Response(
            errors[code], json={"error": {"code": code, "message": f"refused, token {TOKEN}"}}
        )

    async with harness(handler) as h:
        for code, status in errors.items():
            result = await h.call("orchestrator_chat_send", {"text": code})
            assert result.is_error
            assert code in result.text
            assert f"HTTP {status}" in result.text
            assert result.structured is not None
            assert result.structured["error"]["code"] == code
            assert result.structured["error"]["status"] == status
            assert TOKEN not in result.text
            assert TOKEN not in json.dumps(result.structured)


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
    send = on["orchestrator_chat_send"]
    assert "only for chat-started turns" in send
    assert "Asynchronous" in send
    assert "s<n>" in send
    assert "reply_to" in send
    assert "m<n>" in send
    assert "Leader only" in send
    assert "at most one chat room" in send
    assert "bound room" in send
    assert "unbound" in send
    assert "agents_may_initiate" in send
    assert "room_bound" in send
    assert "relayed" in send
    assert "occupier_closing" in send
    assert "via" in send
    assert "without reply_to" in send
    assert "session_bound" in send
    assert "orchestrator_chat_status" in send
    rooms = on["orchestrator_chat_rooms"]
    assert "bound room" in rooms
    assert "unbound" in rooms
    assert "Leader only" in rooms
    react = on["orchestrator_chat_react"]
    assert "m<n>" in react
    assert "s<n>" in react
    assert "Leader only" in react
    assert "bound room" in react
    status = on["orchestrator_chat_status"]
    assert "s<n>" in status
    assert "queued" in status
    assert "Leader only" in status
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
    send = on["orchestrator_session_send"]
    assert "Leader only" in send
    assert "in the last 24 hours" in send
    assert "never waits" in send
    assert '<session-message from="<slug>"' in send
    assert "never instructions from the user" in send
    assert (
        "When your session is a superleader, text that starts with a slash command such as "
        "/compact runs as a Claude Code slash command on the target session's leader "
        "(not framed); commands that end or replace a session, such as /exit or /clear, "
        "are refused. From any other session it is delivered as a message."
    ) in send
    for name in ("/resume", "/cd", "/quit", "/login", "/bg"):
        assert name not in send

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
    for name in ("orchestrator_leadership_handover", "orchestrator_session_send"):
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
        await h.call("orchestrator_session_send", {"slug": "s1", "text": "hi", "handled": [5]})
    assert bodies[0]["handled"] == [3, 4]
    assert bodies[1]["handled"] == [5]


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
    assert "session-message" in text


async def test_me_and_send_descriptions_mention_state_and_handled() -> None:
    found = await descriptions()
    assert "leader_state" in found["orchestrator_me"]
    assert "handled" in found["orchestrator_session_send"]


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
        assert out["type"] == "object" and "url" in out["properties"], name
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
    send = tools["orchestrator_chat_send"]["description"].lower()
    assert "never ask the user a question here" in send
    assert "orchestrator_decision_ask" in send and "link to the task" in send


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
