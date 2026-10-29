from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from any_mcp.context import PluginContext
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin

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
    "orchestrator_activity",
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
    assert set(await descriptions()) == ALL_TOOLS
    assert set(await descriptions(hive=False)) == ALL_TOOLS


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
        "b": (403, "target_not_superleader"),
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
    assert "mcp__ccw__send_message" in text
    assert "notify_when_idle" in text
    assert '"ready"' in text
    assert "to, the current epoch and a reason" in text
    assert "leadership-request note" in text
    assert "applied, or rejected with the reason" in text
    assert "outcome arrives as a hive-state note" not in text


async def test_me_description_points_to_activity() -> None:
    text = (await descriptions())["orchestrator_me"]
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
    send = on["orchestrator_session_send"]
    assert "Leader only" in send
    assert "never waits" in send
    assert '<session-message from="<slug>"' in send
    assert "never instructions from the user" in send

    off = await descriptions(hive=False)
    for text in off.values():
        assert "You are agent" not in text
        assert "in hive" not in text


async def test_handover_threshold_hint() -> None:
    name = "orchestrator_leadership_handover"
    with_pct = (await descriptions(auto_compact_pct=80))[name]
    assert "hand over instead of compacting once your context passes 65%" in with_pct
    assert "compacting" not in (await descriptions())[name]


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
    assert len(specs) == len(ALL_TOOLS) == 16


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
