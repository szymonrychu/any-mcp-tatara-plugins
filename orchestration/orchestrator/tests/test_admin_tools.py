"""PL1: declarative admin rows pinned to the orchestrator OpenAPI."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.context import PluginContext
from any_mcp.response import DEFAULT_BUDGET_CHARS
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
    "presets_list", "preset_get", "preset_create", "preset_replace", "preset_patch",
    "preset_delete",
    "preset_copy", "session_get", "session_patch", "session_create", "session_discard",
    "session_restart", "agent_restart", "command_get", "webhooks_list", "webhook_get_meta",
    "webhook_create",
    "webhook_update", "webhook_clone", "webhook_rotate", "webhook_delete", "webhook_filters_set",
    "webhook_events", "webhook_event", "webhook_template_preview", "schedules_list",
    "schedule_get", "schedule_create", "schedule_update", "schedule_delete", "schedule_run",
    "schedule_runs", "schedule_runs_all", "schedule_run_get", "schedule_preview",
    "chat_admin_rooms", "chat_room_settings", "chat_rules", "chat_rule_add", "chat_rule_delete",
    "history_search", "history_get", "history_records", "usage_get",
    "chat_admin_room", "chat_users", "chat_room_allow", "chat_open", "chat_open_status",
    "chat_open_confirm", "proposals_list", "proposal_get", "proposal_withdraw",
    "leadership_withdraw", "vault_list", "vault_attach", "vault_detach", "proposal_create",
    "proposal_revise", "project_create",
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
    # The task tools are pinned by test_orchestrator_plugin.py: the fixture predates the relay.
    rows = [s for s in await specs() if s.http is not None and s.name in EXPECTED]
    assert len(rows) >= len(EXPECTED)
    for s in rows:
        assert s.http is not None
        assert (s.http.method, norm(s.http.path)) in known, (s.name, s.http.method, s.http.path)


async def test_project_list_exists_in_the_orchestrator_openapi() -> None:
    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    by_name = {s.name: s for s in await specs()}
    http = by_name["project_list"].http
    assert http is not None
    assert (http.method, norm(http.path)) in known


async def test_project_create_is_pinned_and_described() -> None:
    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    row = rows_by_name()["project_create"]
    route = (row.method, "/agent/v1" + norm(row.path))
    assert route == ("POST", "/agent/v1/admin/projects")
    assert route in known
    assert row.schema["required"] == ["slug", "name"]
    props = row.schema["properties"]
    assert props["slug"]["pattern"] == "^[a-z0-9][a-z0-9-]{0,39}$"
    assert (props["name"]["minLength"], props["name"]["maxLength"]) == (1, 80)
    assert props["description"]["maxLength"] == 4000
    assert "superleader" in row.description
    assert "never deleted" in row.description
    assert "project_exists" in row.description


async def test_preset_rows_take_a_project_to_move_a_preset() -> None:
    rows = rows_by_name()
    for name, method in (("preset_patch", "PATCH"), ("preset_replace", "PUT")):
        row = rows[name]
        assert row.method == method and row.path == "/admin/presets/{ref}"
        assert row.schema["properties"]["project"]["type"] == "string"
        assert "project" not in row.schema["required"]
    desc = rows["preset_patch"].description
    for word in ("project", "unknown_project", "vault_label_mismatch", "privileged_preset_move"):
        assert word in desc


async def test_vault_rows_are_pinned_to_the_orchestrator_openapi() -> None:
    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    want = {
        "vault_list": ("GET", "/agent/v1/admin/vault"),
        "vault_attach": ("PUT", "/agent/v1/admin/presets/{}/vault/{}"),
        "vault_detach": ("DELETE", "/agent/v1/admin/presets/{}/vault/{}"),
        "proposal_create": ("POST", "/agent/v1/admin/proposals"),
        "proposal_revise": ("POST", "/agent/v1/admin/proposals/{}/revise"),
    }
    by_name = {s.name: s for s in await specs()}
    for name, route in want.items():
        http = by_name[name].http
        assert http is not None and by_name[name].handler is None, name
        assert (http.method, norm(http.path)) == route and route in known, name


async def test_secret_set_is_gone() -> None:
    names = {s.name for s in await specs()}
    assert "secret_set" not in names
    assert not any("/secrets/" in s.http.path for s in await specs() if s.http is not None)
    assert not any("/secrets/" in p for p in OPENAPI["paths"])


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
            "preset_patch",
            {"ref": "web", "spec": {"usage_quota": {"five_hour_pct": 80}}},
            (
                "PATCH",
                "/agent/v1/admin/presets/web",
                "",
                {"spec": {"usage_quota": {"five_hour_pct": 80}}},
            ),
        ),
        (
            "preset_patch",
            {"ref": "web", "project": "acme"},
            ("PATCH", "/agent/v1/admin/presets/web", "", {"project": "acme"}),
        ),
        (
            "preset_replace",
            {"ref": "web", "name": "web", "spec": {}, "project": "acme"},
            (
                "PUT",
                "/agent/v1/admin/presets/web",
                "",
                {"name": "web", "spec": {}, "project": "acme"},
            ),
        ),
        (
            "session_patch",
            {"slug": "s", "spec": {"inactivity": {"close_seconds": 3600}, "labels": None}},
            (
                "PATCH",
                "/agent/v1/admin/sessions/s",
                "",
                {"spec": {"inactivity": {"close_seconds": 3600}, "labels": None}},
            ),
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
            "leadership_withdraw",
            {"slug": "s", "request_id": 7},
            ("POST", "/agent/v1/admin/sessions/s/leadership/withdraw", "", {"request_id": 7}),
        ),
        (
            "leadership_withdraw",
            {"slug": "s"},
            ("POST", "/agent/v1/admin/sessions/s/leadership/withdraw", "", {}),
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
        (
            "proposals_list",
            {"status": "all", "limit": 5},
            ("GET", "/agent/v1/admin/proposals", "status=all&limit=5", None),
        ),
        ("proposal_get", {"proposal_id": 7}, ("GET", "/agent/v1/admin/proposals/7", "", None)),
        (
            "proposal_withdraw",
            {"proposal_id": 7},
            ("POST", "/agent/v1/admin/proposals/7/withdraw", "", None),
        ),
        (
            "project_create",
            {"slug": "acme", "name": "Acme", "description": "d"},
            (
                "POST",
                "/agent/v1/admin/projects",
                "",
                {"slug": "acme", "name": "Acme", "description": "d"},
            ),
        ),
        ("vault_list", {}, ("GET", "/agent/v1/admin/vault", "", None)),
        ("vault_list", {"q": "gh"}, ("GET", "/agent/v1/admin/vault", "q=gh", None)),
        (
            "vault_attach",
            {"ref": "web", "entry_id": "E1", "envs": {"TOK": "BOT_TOKEN"}, "replace": True},
            (
                "PUT",
                "/agent/v1/admin/presets/web/vault/E1",
                "",
                {"envs": {"TOK": "BOT_TOKEN"}, "replace": True},
            ),
        ),
        (
            "vault_detach",
            {"ref": "web", "entry_id": "E1", "env": "TOK"},
            ("DELETE", "/agent/v1/admin/presets/web/vault/E1", "env=TOK", None),
        ),
        (
            "proposal_create",
            {"kind": "preset", "target": "web", "patch": {"a": 1}, "reason": "r"},
            (
                "POST",
                "/agent/v1/admin/proposals",
                "",
                {"kind": "preset", "target": "web", "patch": {"a": 1}, "reason": "r"},
            ),
        ),
        (
            "proposal_revise",
            {"id": 5, "patch": {"a": 2}},
            ("POST", "/agent/v1/admin/proposals/5/revise", "", {"patch": {"a": 2}}),
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


def rows_by_name() -> dict[str, Any]:
    return {r.name: r for r in ADMIN_TOOLS}


def test_history_rows_present() -> None:
    assert {"history_search", "history_get", "history_records"} <= set(rows_by_name())


def test_history_has_no_delete() -> None:
    # Q6: history is read and search only; deleting a stored session is for the user.
    assert "history_delete" not in rows_by_name()
    for r in ADMIN_TOOLS:
        if r.path.startswith("/admin/history"):
            assert r.method == "GET", r.name
    for path, methods in OPENAPI["paths"].items():
        if "/admin/history" in path:
            assert set(methods) == {"get"}, path


def test_history_search_schema() -> None:
    row = rows_by_name()["history_search"]
    props = row.schema["properties"]
    assert set(props) == {"q", "regex", "from", "to", "preset", "session", "limit", "before_id"}
    assert row.schema["additionalProperties"] is False
    assert props["q"]["minLength"] == 3
    assert props["regex"]["type"] == "boolean"
    assert props["limit"] == {"type": "integer", "minimum": 1, "maximum": 200, "default": 20}
    assert row.query == ("q", "regex", "from", "to", "preset", "session", "limit", "before_id")
    assert "case-insensitive" in row.description
    assert "Postgres regex" in row.description
    assert "next_before_id" in row.description


def test_history_records_schema() -> None:
    row = rows_by_name()["history_records"]
    props = row.schema["properties"]
    assert set(props) == {"session_id", "kind", "q", "regex", "before_id", "after_id", "limit"}
    assert row.schema["additionalProperties"] is False
    assert props["q"]["minLength"] == 3
    assert props["limit"] == {"type": "integer", "minimum": 1, "maximum": 200}
    assert row.query == ("kind", "q", "regex", "before_id", "after_id", "limit")


def test_history_get_requires_id() -> None:
    for name in ("history_get", "history_records"):
        row = rows_by_name()[name]
        assert row.schema["required"][0] == "session_id", name
        assert row.schema["properties"]["session_id"]["description"] == (
            "Session id (uuid) of the discarded session."
        )


def test_leadership_withdraw_description_excludes_rollout_requests() -> None:
    desc = rows_by_name()["leadership_withdraw"].description
    assert "rollout" in desc and "409 rollout_request" in desc


def test_history_descriptions_scope_results_to_manageable_presets() -> None:
    for name in ("history_search", "history_get", "history_records"):
        assert "may manage" in rows_by_name()[name].description, name


async def test_history_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json=[])

    sid = "6f1c1c0e-0000-4000-8000-000000000001"
    calls = [
        (
            "history_search",
            {"q": "deploy", "regex": False, "preset": "web", "limit": 20, "before_id": 9},
            (
                "GET",
                "/agent/v1/admin/history/sessions",
                "q=deploy&regex=false&preset=web&limit=20&before_id=9",
            ),
        ),
        (
            "history_get",
            {"session_id": sid},
            ("GET", f"/agent/v1/admin/history/sessions/{sid}", ""),
        ),
        (
            "history_records",
            {"session_id": sid, "kind": "event", "after_id": 0, "limit": 5},
            (
                "GET",
                f"/agent/v1/admin/history/sessions/{sid}/records",
                "kind=event&after_id=0&limit=5",
            ),
        ),
    ]
    async with harness(handler) as h:
        for name, args, want in calls:
            seen.clear()
            res = await h.call(name, args)
            assert not res.is_error, (name, res.text)
            assert seen == [want], name


async def test_history_search_exposes_the_next_cursor() -> None:
    # Ruling S-R1: the relay sends the upstream cursor in X-Next-Before-Id, also for a short or
    # empty page; the tool shows it as next_before_id so the caller can keep paging.
    pages = [
        ([{"id": 40, "slug": "a"}], {"X-Next-Before-Id": "37"}),
        ([], {"X-Next-Before-Id": "12"}),
        ([{"id": 9, "slug": "b"}], {}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        body, headers = pages.pop(0)
        return httpx.Response(200, json=body, headers=headers)

    async with harness(handler) as h:
        first = await h.call("history_search", {"q": "abc"})
        empty = await h.call("history_search", {"q": "abc", "before_id": 37})
        last = await h.call("history_search", {"q": "abc", "before_id": 12})
    assert first.structured == {"sessions": [{"id": 40, "slug": "a"}], "next_before_id": 37}
    assert empty.structured == {"sessions": [], "next_before_id": 12}
    assert last.structured == {"sessions": [{"id": 9, "slug": "b"}], "next_before_id": None}


def _large_hit(i: int) -> dict[str, Any]:
    """A realistic search hit: a ~420 char session summary and 3 snippets of ~250 chars."""
    return {
        "id": 1000 - i,
        "slug": f"fix-traceback-{i:04d}",
        "preset": "superleader",
        "summary": f"session {i}: " + "investigated a Traceback in the reconciler loop " * 8,
        "snippets": [f"snippet {i}.{n}: " + "Traceback (most recent call) " * 8 for n in range(3)],
    }


async def test_history_search_cursor_survives_the_output_budget() -> None:
    # any-mcp cuts a tool text at DEFAULT_BUDGET_CHARS (any_mcp.response, 40000 chars, the
    # `response_budget_chars` default of the config) and keeps the head, so the cursor must come
    # first. A 50-hit page of ~1.2k chars per hit is ~60k chars and loses its tail.
    page = [_large_hit(i) for i in range(50)]
    assert len(json.dumps(page, separators=(",", ":"))) > DEFAULT_BUDGET_CHARS

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page, headers={"X-Next-Before-Id": "951"})

    async with harness(handler) as h:
        res = await h.call("history_search", {"q": "Traceback", "limit": 50})
    assert "[truncated:" in res.text
    assert res.text.startswith('{"next_before_id":951,"sessions":[')


async def test_history_search_default_page_fits_the_output_budget() -> None:
    seen: list[str] = []
    page = [_large_hit(i) for i in range(20)]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(200, json=page, headers={"X-Next-Before-Id": "981"})

    async with harness(handler) as h:
        res = await h.call("history_search", {"q": "Traceback"})
        explicit = await h.call("history_search", {"q": "Traceback", "limit": 7})
    assert seen == ["q=Traceback&limit=20", "q=Traceback&limit=7"]
    assert "[truncated:" not in res.text
    assert res.structured is not None and res.structured["next_before_id"] == 981
    assert len(res.structured["sessions"]) == 20
    assert explicit.structured is not None


async def test_history_search_ignores_a_malformed_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-Next-Before-Id": "nope"})

    async with harness(handler) as h:
        res = await h.call("history_search", {})
    assert res.structured == {"sessions": [], "next_before_id": None}


async def test_history_get_and_records_keep_their_answer_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/records"):
            return httpx.Response(200, json=[{"id": 1, "kind": "event"}])
        return httpx.Response(200, json={"slug": "s"})

    sid = "6f1c1c0e-0000-4000-8000-000000000001"
    async with harness(handler) as h:
        got = await h.call("history_get", {"session_id": sid})
        recs = await h.call("history_records", {"session_id": sid})
    assert got.structured == {"slug": "s"}
    assert recs.structured == [{"id": 1, "kind": "event"}]


def _big_preset(i: int) -> dict[str, Any]:
    return {
        "id": f"00000000-0000-0000-0000-{i:012d}",
        "name": f"preset-{i}",
        "is_default": False,
        "superleader_manageable": i % 2 == 0,
        "labels": ["team", f"n{i}"],
        "spec": {
            "image": "registry.example/agent:1.2.3",
            "init_scripts": ["echo " + "x" * 300 for _ in range(3)],
            "env": {f"VAR_{j}": {"value": "v" * 40} for j in range(8)},
            "start_prompt": "p" * 400,
            **({"kube_access": True} if i % 3 == 0 else {}),
            **({"docker": True} if i % 5 == 0 else {}),
            **({"session_messaging": True} if i % 7 == 0 else {}),
            **({"inactivity": {"reminder_seconds": 60, "close_seconds": 600}} if i == 4 else {}),
        },
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


async def test_presets_list_is_a_compact_summary_within_the_output_budget() -> None:
    presets = [_big_preset(i) for i in range(100)]
    assert len(json.dumps(presets, separators=(",", ":"))) > DEFAULT_BUDGET_CHARS

    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.method, request.url.path) == ("GET", "/agent/v1/admin/presets")
        return httpx.Response(200, json=presets)

    async with harness(handler) as h:
        res = await h.call("presets_list", {})
    assert not res.is_error
    assert "[truncated:" not in res.text
    assert len(res.text) < DEFAULT_BUDGET_CHARS // 2
    rows = res.structured
    assert isinstance(rows, list) and len(rows) == 100
    assert [r["name"] for r in rows] == [f"preset-{i}" for i in range(100)]
    for i, row in enumerate(rows):
        assert row["labels"] == ["team", f"n{i}"]
        assert row["superleader_manageable"] is (i % 2 == 0)
        assert row["kube_access"] is (i % 3 == 0)
        assert row["docker"] is (i % 5 == 0)
        assert row["session_messaging"] is (i % 7 == 0)
        assert ("inactivity" in row) is (i == 4)
        assert set(row) - {"inactivity"} == {
            "name", "labels", "superleader_manageable", "kube_access", "docker",
            "session_messaging",
        }  # fmt: skip
    assert rows[4]["inactivity"] == {"reminder_seconds": 60, "close_seconds": 600}


async def test_presets_list_omits_superleader_manageable_when_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "a", "labels": [], "spec": {}}])

    async with harness(handler) as h:
        res = await h.call("presets_list", {})
    assert res.structured == [
        {
            "name": "a",
            "labels": [],
            "kube_access": False,
            "session_messaging": False,
            "docker": False,
        }
    ]


async def test_presets_list_description_points_to_preset_get() -> None:
    desc = rows_by_name()["presets_list"].description
    assert desc == (
        "List the presets: name, labels and privilege flags; use preset_get for the full spec."
    )


def test_session_discard_schema_lists_force() -> None:
    row = rows_by_name()["session_discard"]
    assert row.schema["properties"]["force"]["type"] == "boolean"
    assert row.schema["required"] == ["slug"]
    assert "stuck in phase deleting" in row.description
    assert "history export may be lost" in row.description
    assert "workspace PVC is kept" in row.description


async def test_session_discard_sends_force_only_when_true() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json={})

    path = "/agent/v1/admin/sessions/s1"
    async with harness(handler) as h:
        for args, query in (
            ({"slug": "s1"}, ""),
            ({"slug": "s1", "force": False}, ""),
            ({"slug": "s1", "force": True}, "force=true"),
        ):
            seen.clear()
            res = await h.call("session_discard", args)
            assert not res.is_error, res.text
            assert seen == [("DELETE", path, query)], args


async def test_usage_get_is_a_read_only_superleader_tool() -> None:
    row = rows_by_name()["usage_get"]
    assert (row.method, row.path) == ("GET", "/admin/usage")
    assert row.schema["properties"] == {}
    text = row.description.lower()
    assert "read-only" in text and "superleader" in text
    assert "five_hour" in text and "seven_day" in text


async def test_usage_get_calls_the_admin_usage_route() -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"available": True, "five_hour": {"utilization_pct": 3.0}})

    async with harness(handler) as h:
        res = await h.call("usage_get", {})
    assert not res.is_error, res.text
    assert seen == [("GET", "/agent/v1/admin/usage")]


async def test_usage_get_refusal_surfaces_the_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": "not_superleader", "message": "admin routes need a superleader"}}
        return httpx.Response(403, json=body)

    async with harness(handler) as h:
        res = await h.call("usage_get", {})
    assert res.is_error
    assert "not_superleader" in res.text


def test_chat_open_requires_target_and_task() -> None:
    row = rows_by_name()["chat_open"]
    assert (row.method, row.path, row.body) == ("POST", "/admin/chats/room-requests", "*")
    assert set(row.schema["required"]) == {"target", "task_key"}
    assert row.schema["properties"]["action"]["enum"] == ["open", "invite"]
    assert "room_id" in row.schema["properties"]


def test_chat_open_description_mentions_confirm_flow() -> None:
    text = rows_by_name()["chat_open"].description
    for word in ("chat_open_confirm", "chat_open_status", "chat_room_allow", "session_create",
                 "session_send", "chat_send", "candidates", "vanity"):  # fmt: skip
        assert word in text, word


def test_chat_admin_rooms_description_mentions_pending() -> None:
    assert "pending" in rows_by_name()["chat_admin_rooms"].description


async def test_chat_admin_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"ok": True})

    base = "/agent/v1/admin/chats"
    calls = [
        ("chat_admin_room", {"room_id": "!a:x"}, ("GET", f"{base}/rooms/!a:x", "", None)),
        ("chat_users", {"q": "ann", "limit": 5}, ("GET", f"{base}/users", "q=ann&limit=5", None)),
        (
            "chat_room_allow",
            {"room_id": "!a:x", "preset_id": "p", "trigger": "mention"},
            ("POST", f"{base}/rooms/!a:x/allow", "", {"preset_id": "p", "trigger": "mention"}),
        ),
        (
            "chat_open",
            {"target": "ann", "task_key": "T-1", "action": "open"},
            (
                "POST",
                f"{base}/room-requests",
                "",
                {"target": "ann", "task_key": "T-1", "action": "open"},
            ),
        ),
        ("chat_open_status", {"request_id": "7"}, ("GET", f"{base}/room-requests/7", "", None)),
        (
            "chat_open_confirm",
            {"request_id": "7"},
            ("POST", f"{base}/room-requests/7/confirm", "", {}),
        ),
    ]
    async with harness(handler) as h:
        for name, args, _ in calls:
            res = await h.call(name, args)
            assert not res.is_error, (name, res.text)
    for (name, _, want), got in zip(calls, seen, strict=True):
        assert got[:3] == want[:3], name
        assert (got[3] or {}) == (want[3] or {}), name


@pytest.mark.parametrize(
    "name,args",
    [
        (
            "proposal_create",
            {"kind": "preset", "target": "w", "patch": {}, "reason": "r", "secrets": {}},
        ),
        ("proposal_revise", {"id": 1, "patch": {}, "secrets": {"T": {"from_env": "X"}}}),
    ],
)
async def test_proposal_with_secrets_is_refused_before_any_request(
    name: str, args: dict[str, Any]
) -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        res = await h.call(name, args)
    assert res.is_error
    assert seen == []
    assert "secret" in res.text.lower(), res.text


def test_vault_descriptions_mark_privileged_attachments_without_refusing() -> None:
    rows = rows_by_name()
    assert "privileged" in rows["vault_list"].description
    attach = rows["vault_attach"].description
    assert "attachment_privileged" in attach
    assert "read every Secret in the namespace" in attach
    assert "nothing is refused" in attach


@pytest.mark.parametrize(
    "envs",
    [["TOK"], {}, {"tok": "BOT"}, {"TOK": "bot"}, {"TOK": 1}],
)
async def test_vault_attach_rejects_bad_envs(envs: Any) -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        res = await h.call("vault_attach", {"ref": "web", "entry_id": "E1", "envs": envs})
        assert res.is_error
    assert seen == []


def test_vault_attach_envs_maps_entry_env_to_preset_env() -> None:
    envs = rows_by_name()["vault_attach"].schema["properties"]["envs"]
    assert envs["type"] == "object" and envs["minProperties"] == 1
    assert "propertyNames" in envs and "additionalProperties" in envs
