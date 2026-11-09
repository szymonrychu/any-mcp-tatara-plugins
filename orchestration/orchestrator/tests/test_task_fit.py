"""T-61: task answers over the any-mcp output budget are trimmed, never refused."""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx
import jsonschema
import pytest

from any_mcp.response import DEFAULT_BUDGET_CHARS
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.fit import MIN_KEEP, fit_task, measure, summarize_tasks
from any_mcp_tatara_orchestrator.views import (
    TASK_VIEWS,
    comment_ack,
    decision_ack,
    task_ack,
    task_view,
    todo_ack,
)

URL = "https://lab.example.test/tasks/T-19"
AT = "2026-11-07T10:00:00+00:00"
T19_CHARS = 40313  # the size reported by the failing task_get T-19


def event(i: int) -> dict[str, Any]:
    return {
        "id": i,
        "actor_kind": "agent",
        "actor_session": "s-1",
        "actor_slug": "mx-1",
        "kind": "todo_updated",
        "detail": {"item": i, "status": "done"},
        "at": AT,
    }


def comment(i: int, size: int = 1000) -> dict[str, Any]:
    return {
        "id": i,
        "author_kind": "agent",
        "author_session": "s-1",
        "author_slug": "mx-1",
        "body": f"c{i} " + "x" * size,
        "created_at": AT,
    }


def decision(
    i: int, *, status: str = "answered", context: int = 10_000, answer: Any = "a"
) -> dict[str, Any]:
    return {
        "id": i,
        "asking_session": "s-1",
        "asking_slug": "mx-1",
        "question": f"q{i}?",
        "context": "y" * context,
        "options": [
            {"label": "a", "description": "d", "recommended": True},
            {"label": "b", "recommended": False},
        ],
        "status": status,
        "answer": answer if status == "answered" else None,
        "note": None,
        "created_at": AT,
        "answered_at": AT if status == "answered" else None,
    }


def task(
    *, events: int = 0, comments: list | None = None, decisions: list | None = None, body: str = ""
) -> dict[str, Any]:
    decisions = decisions or []
    open_count = sum(d["status"] == "open" for d in decisions)
    return {
        "key": "T-19",
        "title": "Big task",
        "body": body,
        "kind": "task",
        "status": "in_progress",
        "labels": ["infra"],
        "assignee_session": "s-1",
        "version": 57,
        "created_at": AT,
        "updated_at": AT,
        "blocked": open_count > 0,
        "open_decisions": open_count,
        "waiting_sessions": [],
        "todo": [
            {"id": i, "text": f"step {i}", "status": "done", "position": i} for i in range(30)
        ],
        "decisions": decisions,
        "comments": comments or [],
        "events": [event(i) for i in range(1, events + 1)],
        "url": URL,
    }


def big_task() -> dict[str, Any]:
    """~265k chars: over the old 160 KB read cap as well as the budget."""
    comments = [comment(i) for i in range(1, 41)] + [comment(41, 60_000)]
    decisions = [decision(i) for i in range(1, 5)] + [decision(5, answer="z" * 30_000)]
    return task(events=400, comments=comments, decisions=decisions, body="b" * 5000)


def t19_like() -> dict[str, Any]:
    """Just over T-19's reported size, grown by events only (its likely shape)."""
    out = task(comments=[comment(i, 300) for i in range(1, 6)])
    i = 0
    while measure(out) <= T19_CHARS:
        i += 1
        out["events"].append(event(i))
    return out


PROTECTED = ("key", "url", "title", "status", "version")


def test_measure_matches_the_any_mcp_text() -> None:
    value = {"a": "é\n", "b": [1, 2]}
    assert measure(value) == len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def test_small_answer_is_returned_unchanged() -> None:
    small = task(events=3)
    assert fit_task(small, DEFAULT_BUDGET_CHARS) is small


def test_non_dict_passes_through() -> None:
    assert fit_task([1, 2], 1) == [1, 2]


def test_t19_drops_only_oldest_events() -> None:
    upstream = t19_like()
    assert measure(upstream) > T19_CHARS
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    assert measure(out) <= DEFAULT_BUDGET_CHARS
    kept = [e["id"] for e in out["events"]]
    ids = [e["id"] for e in upstream["events"]]
    assert kept == ids[len(ids) - len(kept) :]
    assert out["comments"] == upstream["comments"]
    assert out["omitted"] == {
        "events": len(ids) - len(kept),
        "comments": 0,
        "decisions": 0,
        "cut": 0,
        "skeleton": False,
    }
    assert all(out[f] == upstream[f] for f in PROTECTED)


def test_minimal_drop() -> None:
    upstream = t19_like()
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    start = len(upstream["events"]) - len(out["events"]) - 1
    one_more = dict(out, events=upstream["events"][start:])
    one_more["omitted"] = dict(out["omitted"], events=out["omitted"]["events"] - 1)
    assert measure(one_more) > DEFAULT_BUDGET_CHARS


def test_events_then_comments_then_texts() -> None:
    upstream = big_task()
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    assert measure(out) <= DEFAULT_BUDGET_CHARS
    assert out["events"] == []
    assert out["omitted"]["events"] == 400
    assert out["omitted"]["comments"] == 41 - len(out["comments"])
    assert out["omitted"]["cut"] >= 1
    assert all(out[f] == upstream[f] for f in PROTECTED)


def test_answers_survive_until_other_texts_are_cut() -> None:
    upstream = task(events=400, decisions=[decision(1, context=20_000, answer="k" * 2000)])
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    assert out["decisions"][0]["answer"] == "k" * 2000


def test_long_text_is_cut_with_marker() -> None:
    upstream = task(decisions=[decision(1, status="open", context=100_000)])
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    context = out["decisions"][0]["context"]
    assert context.startswith("y" * MIN_KEEP) and context.endswith(" chars]")
    assert "...[cut " in context
    assert out["omitted"]["cut"] == 1


def test_open_decisions_never_dropped_then_skeleton() -> None:
    many = [decision(i, status="open", context=0) for i in range(1, 400)]
    for d in many:
        d["question"] = "q" * 2000
    upstream = task(decisions=many)
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    assert measure(out) <= DEFAULT_BUDGET_CHARS
    assert out["omitted"]["skeleton"] is True
    assert out["omitted"]["decisions"] == 0
    assert {k: out[k] for k in PROTECTED} == {k: upstream[k] for k in PROTECTED}
    assert "decisions" not in out


def test_closed_decisions_dropped_oldest_first() -> None:
    closed = [decision(i, context=0, answer="a" * 400) for i in range(1, 200)]
    upstream = task(decisions=closed)
    out = fit_task(upstream, DEFAULT_BUDGET_CHARS)
    ids = [d["id"] for d in out["decisions"]]
    assert ids == list(range(200 - len(ids), 200))
    assert out["omitted"]["decisions"] == 199 - len(ids)


def test_deterministic_and_input_untouched() -> None:
    upstream = big_task()
    before = copy.deepcopy(upstream)
    assert fit_task(upstream, DEFAULT_BUDGET_CHARS) == fit_task(upstream, DEFAULT_BUDGET_CHARS)
    assert upstream == before


def test_terminates_on_a_small_budget() -> None:
    assert measure(fit_task(big_task(), 3000)) <= 3000


def test_summaries_are_bounded() -> None:
    worst = dict(
        big_task(),
        title="t" * 200,
        labels=[f"{i:02d}" + "l" * 38 for i in range(20)],
        assignee_session="s" * 5000,
    )
    page = summarize_tasks({"items": [worst] * 20, "next_cursor": "abc"})
    assert measure(page) <= DEFAULT_BUDGET_CHARS
    row = page["items"][0]
    assert set(row) == {
        "key",
        "title",
        "kind",
        "status",
        "labels",
        "assignee_session",
        "version",
        "blocked",
        "open_decisions",
        "updated_at",
        "url",
        "todo",
        "decisions",
        "comments",
        "events",
    }
    assert row["todo"] == {"todo": 0, "inprogress": 0, "done": 30}
    assert (row["decisions"], row["comments"], row["events"]) == (5, 41, 400)
    assert row["title"] == "t" * 200
    assert row["assignee_session"].endswith("chars]")
    assert page["next_cursor"] == "abc"


def test_summaries_pass_other_shapes_through() -> None:
    assert summarize_tasks({"key": "T-5", "url": URL}) == {"key": "T-5", "url": URL}


CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": "s3cr3t-token-value",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}
READ_CAP = DEFAULT_BUDGET_CHARS * 4 + 1  # any-mcp's default HTTP read cap in bytes
TASK_CALLS = [
    ("task_get", {"key": "T-19"}),
    ("task_create", {"title": "t", "client_key": "k"}),
    ("task_update", {"key": "T-19", "version": 57, "status": "done"}),
    ("task_comment", {"key": "T-19", "body": "hi"}),
    ("todo_set", {"key": "T-19", "items": [{"text": "x"}]}),
    ("todo_update", {"key": "T-19", "item_id": "2", "status": "done"}),
]


def harness(answer: Any, seen: list[str] | None = None) -> PluginHarness:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.query.decode())
        return httpx.Response(200, json=answer)

    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


async def call_checked(h: PluginHarness, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call through the real pipeline and assert the answer fits and validates."""
    schemas = {t["name"]: t["outputSchema"] for t in await h.list_tools() if "outputSchema" in t}
    res = await h.call(f"orchestrator_{tool}", args)
    assert not res.is_error, res.text[:300]
    assert len(res.text) <= DEFAULT_BUDGET_CHARS
    assert json.loads(res.text) == res.structured
    jsonschema.validate(res.structured, schemas[f"orchestrator_{tool}"])
    return res.structured


WITHDRAW_ARGS = {"key": "T-19", "decision_id": "7", "reason": "moot", "superseded_by": 9}


@pytest.mark.parametrize(
    ("make", "view"),
    [(big_task, "events"), (big_task, "comments"), (t19_like, "events")],
    ids=["265k-events", "265k-comments", "t19-events"],
)
async def test_task_get_window_views_fit_a_task_over_40k(make: Any, view: str) -> None:
    upstream = make()
    assert measure(upstream) > T19_CHARS
    async with harness(upstream) as h:
        out = await call_checked(h, "task_get", {"key": "T-19", "view": view})
    assert all(out[f] == upstream[f] for f in PROTECTED)
    kept = [e["id"] for e in out[view]]
    ids = [e["id"] for e in upstream[view]]
    assert kept == ids[len(ids) - len(kept) :]
    assert out.get("omitted", {}).get(view, 0) == len(ids) - len(kept)


async def test_body_over_the_default_read_cap_is_read_whole() -> None:
    upstream = big_task()
    assert len(json.dumps(upstream).encode()) > READ_CAP
    async with harness(upstream) as h:
        out = await call_checked(
            h, "task_get", {"key": "T-19", "view": "events", "events_limit": 200}
        )
    assert out["omitted"]["events"] == 163  # the events view is events only, so 237 fit


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "decision_ask",
            {
                "key": "T-19",
                "question": "q?",
                "options": [
                    {"label": "a", "recommended": True},
                    {"label": "b", "recommended": False},
                ],
            },
        ),
        ("decision_withdraw", WITHDRAW_ARGS),
    ],
)
async def test_decision_tools_answer_compactly_for_a_huge_context(tool: str, args: dict) -> None:
    upstream = dict(decision(7, status="open", context=100_000), task_key="T-19", url=URL)
    async with harness(upstream) as h:
        out = await call_checked(h, tool, args)
    assert out == {"id": 7, "task_key": "T-19", "status": "open", "url": URL}


async def test_small_task_summary_is_the_view_of_the_task() -> None:
    upstream = task(events=3)
    async with harness(upstream) as h:
        out = await call_checked(h, "task_get", {"key": "T-19"})
    assert out == task_view(upstream, "summary")
    assert "omitted" not in out


async def test_task_list_is_compact_limited_and_pages() -> None:
    seen: list[str] = []
    page = {"items": [dict(big_task(), key=f"T-{i}") for i in range(20)], "next_cursor": "c2"}
    async with harness(page, seen) as h:
        out = await call_checked(h, "task_list", {})
        await call_checked(h, "task_list", {"limit": 20, "cursor": "c1", "mine": True})
    assert seen == ["limit=10", "mine=true&limit=20&cursor=c1"]
    assert out["next_cursor"] == "c2"
    assert [r["key"] for r in out["items"]] == [f"T-{i}" for i in range(20)]
    assert out["items"][0]["events"] == 400


async def test_task_list_limit_schema() -> None:
    async with harness({}) as h:
        tools = {t["name"]: t for t in await h.list_tools() if "outputSchema" in t}
    props = tools["orchestrator_task_list"]["inputSchema"]["properties"]
    assert (props["limit"]["minimum"], props["limit"]["maximum"]) == (1, 20)
    assert props["cursor"]["type"] == "string"


async def test_task_get_sends_window_defaults_and_values() -> None:
    seen: list[str] = []
    async with harness(task(events=3), seen) as h:
        await call_checked(h, "task_get", {"key": "T-19"})
        await call_checked(
            h,
            "task_get",
            {
                "key": "T-19",
                "view": "comments",
                "events_limit": 0,
                "comments_limit": 5,
                "events_before": 90,
                "comments_before": 7,
            },
        )
    assert seen == [
        "events_limit=10&comments_limit=0",
        "events_limit=0&comments_limit=5&comments_before=7",
    ]


async def test_task_get_window_schema() -> None:
    async with harness({}) as h:
        tools = {t["name"]: t for t in await h.list_tools()}
    props = tools["orchestrator_task_get"]["inputSchema"]["properties"]
    for name in ("events_limit", "comments_limit"):
        assert (props[name]["minimum"], props[name]["maximum"]) == (0, 200)
    for name in ("events_before", "comments_before"):
        assert props[name]["minimum"] == 1


async def test_task_get_totals_validate_and_are_declared() -> None:
    answer = task(events=3) | {"events_total": 3, "comments_total": 0}
    async with harness(answer) as h:
        tools = {t["name"]: t for t in await h.list_tools() if "outputSchema" in t}
        for view in TASK_VIEWS:
            out = await call_checked(h, "task_get", {"key": "T-19", "view": view})
            assert (out["events_total"], out["comments_total"]) == (3, 0), view
    props = tools["orchestrator_task_get"]["outputSchema"]["properties"]
    assert props["events_total"]["type"] == "integer"
    assert props["comments_total"]["type"] == "integer"


# T-101: task_get views and short confirmations for the write tools.
CORE = {
    "key",
    "project",
    "title",
    "kind",
    "status",
    "labels",
    "assignee_session",
    "version",
    "blocked",
    "open_decisions",
    "url",
    "events_total",
    "comments_total",
    "updated_at",
}


def rich() -> dict[str, Any]:
    open_d = decision(1, status="open", context=50)
    chosen = decision(2, answer={"choice": 0, "label": "a"})
    chosen["note"] = "n" * 300
    typed = decision(3, answer={"text": "t" * 300})
    plain = decision(4, answer={"choice": 1, "label": "b"})
    withdrawn = decision(5, status="withdrawn")
    out = task(
        events=30,
        comments=[comment(1, 5), comment(2, 5)],
        decisions=[open_d, chosen, typed, plain, withdrawn],
        body="the body",
    )
    out["project"] = "default"
    out["events_total"] = 30
    out["comments_total"] = 2
    out["waiting_sessions"] = ["s-9"]
    out["todo"] = [
        {"id": 1, "text": "a", "status": "todo", "position": 0},
        {"id": 2, "text": "b", "status": "inprogress", "position": 1},
        {"id": 3, "text": "c", "status": "done", "position": 2},
        {"id": 4, "text": "d", "status": "done", "position": 3},
    ]
    return out


def test_summary_shape() -> None:
    src = rich()
    before = copy.deepcopy(src)
    out = task_view(src, "summary")
    assert src == before
    assert set(out) == CORE | {
        "body",
        "waiting_sessions",
        "decisions",
        "decisions_withdrawn",
        "todo",
        "todo_done",
        "events",
    }
    assert out["body"] == "the body"
    assert out["todo"] == [
        {"id": 1, "text": "a", "status": "todo"},
        {"id": 2, "text": "b", "status": "inprogress"},
    ]
    assert out["todo_done"] == 2
    assert out["decisions_withdrawn"] == 1
    assert out["events"] == src["events"]
    assert "comments" not in out


def test_summary_decisions_open_full_answered_collapsed() -> None:
    d_open, d_choice, d_text, d_plain = task_view(rich(), "summary")["decisions"]
    assert set(d_open) == {"id", "asking_slug", "question", "context", "options", "created_at"}
    assert d_open["id"] == 1 and d_open["context"] == "y" * 50
    assert set(d_choice) == {"id", "question", "answer", "note"}
    assert d_choice["answer"] == "a"
    assert d_choice["note"] == "n" * 200 + "...[cut 100 chars]"
    assert d_text["answer"] == "t" * 200 + "...[cut 100 chars]"
    assert "note" not in d_text
    assert d_plain == {"id": 4, "question": "q4?", "answer": "b"}


def test_summary_omits_withdrawn_count_when_none() -> None:
    src = task(decisions=[decision(1, status="open")])
    out = task_view(src, "summary")
    assert "decisions_withdrawn" not in out
    assert out["todo"] == [] and out["todo_done"] == 30


def test_open_decisions_view() -> None:
    out = task_view(rich(), "open_decisions")
    assert set(out) == CORE | {"decisions"}
    assert [d["id"] for d in out["decisions"]] == [1]
    assert "context" in out["decisions"][0]


def test_todo_view_keeps_every_status() -> None:
    out = task_view(rich(), "todo")
    assert set(out) == CORE | {"todo", "todo_done"}
    assert [i["id"] for i in out["todo"]] == [1, 2, 3, 4]
    assert set(out["todo"][0]) == {"id", "text", "status"}
    assert out["todo_done"] == 2


def test_events_and_comments_views() -> None:
    src = rich()
    events = task_view(src, "events")
    assert set(events) == CORE | {"events"} and events["events"] == src["events"]
    comments = task_view(src, "comments")
    assert set(comments) == CORE | {"comments"} and comments["comments"] == src["comments"]


def test_core_only_carries_fields_the_upstream_has() -> None:
    out = task_view(task(), "open_decisions")
    assert "events_total" not in out and out["key"] == "T-19"


def test_acks() -> None:
    src = rich()
    assert task_ack(src) == {"key": "T-19", "version": 57, "url": URL}
    assert comment_ack(src) == {
        "key": "T-19",
        "version": 57,
        "url": URL,
        "comment_id": 2,
        "comments_total": 2,
    }
    src["todo"] = [{"id": 9, "text": "x" * 80, "status": "todo", "position": 0}]
    assert todo_ack(src)["todo"] == [[9, "x" * 60 + "...[cut 20 chars]"]]
    assert set(todo_ack(src)) == {"key", "version", "url", "todo"}
    d = dict(decision(7, status="open"), task_key="T-19", url=URL)
    assert decision_ack(d) == {"id": 7, "task_key": "T-19", "status": "open", "url": URL}


async def test_task_get_default_is_summary_with_window() -> None:
    seen: list[str] = []
    async with harness(rich(), seen) as h:
        out = await call_checked(h, "task_get", {"key": "T-19"})
    assert seen == ["events_limit=10&comments_limit=0"]
    assert "comments" not in out and out["todo_done"] == 2


@pytest.mark.parametrize(
    ("args", "query"),
    [
        (
            {"view": "summary", "events_before": 9, "comments_before": 3},
            "events_limit=10&events_before=9&comments_limit=0",
        ),
        ({"view": "summary", "events_limit": 4}, "events_limit=4&comments_limit=0"),
        ({"view": "open_decisions", "events_before": 9}, "events_limit=0&comments_limit=0"),
        ({"view": "todo", "events_limit": 50}, "events_limit=0&comments_limit=0"),
        (
            {"view": "events", "events_before": 9},
            "events_limit=20&events_before=9&comments_limit=0",
        ),
        ({"view": "events", "events_limit": 200}, "events_limit=200&comments_limit=0"),
        (
            {"view": "comments", "comments_before": 3},
            "events_limit=0&comments_limit=20&comments_before=3",
        ),
        (
            {"view": "comments", "comments_limit": 5, "events_limit": 9},
            "events_limit=0&comments_limit=5",
        ),
    ],
)
async def test_task_get_view_sets_the_upstream_window(args: dict, query: str) -> None:
    seen: list[str] = []
    async with harness(rich(), seen) as h:
        await call_checked(h, "task_get", {"key": "T-19", **args})
    assert seen == [query]


async def test_big_task_views_fit() -> None:
    summary_upstream = big_task()
    summary_upstream["events"] = summary_upstream["events"][-10:]  # the taskboard honours the limit
    async with harness(summary_upstream) as h:
        summary = await call_checked(h, "task_get", {"key": "T-19"})
    async with harness(big_task()) as h:
        events = await call_checked(h, "task_get", {"key": "T-19", "view": "events"})
        comments = await call_checked(h, "task_get", {"key": "T-19", "view": "comments"})
    # The summary shows only 10 events and no comments, so it is a small fraction of the
    # budget even for a 265k task; 25% is a generous bound.
    assert measure(summary) < DEFAULT_BUDGET_CHARS // 4
    assert "omitted" in events and "omitted" in comments


WRITES = [
    ("task_create", {"title": "t", "client_key": "k"}, {"key", "version", "url"}),
    ("task_update", {"key": "T-19", "version": 57, "status": "done"}, {"key", "version", "url"}),
    (
        "task_comment",
        {"key": "T-19", "body": "hi"},
        {"key", "version", "url", "comment_id", "comments_total"},
    ),
    ("todo_set", {"key": "T-19", "items": [{"text": "x"}]}, {"key", "version", "url", "todo"}),
    (
        "todo_update",
        {"key": "T-19", "item_id": "2", "status": "done"},
        {"key", "version", "url"},
    ),
]


@pytest.mark.parametrize(("tool", "args", "keys"), WRITES, ids=[w[0] for w in WRITES])
async def test_write_tools_answer_a_short_confirmation(tool: str, args: dict, keys: set) -> None:
    upstream = big_task()
    upstream["events"].append(event(401))
    async with harness(upstream) as h:
        out = await call_checked(h, tool, args)
    assert set(out) == keys
    assert out["key"] == "T-19" and out["version"] == 57 and out["url"] == URL


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        (
            "decision_ask",
            {
                "key": "T-19",
                "question": "q?",
                "options": [
                    {"label": "a", "recommended": True},
                    {"label": "b", "recommended": False},
                ],
            },
        ),
        ("decision_withdraw", WITHDRAW_ARGS),
    ],
)
async def test_decision_tools_answer_a_short_confirmation(tool: str, args: dict) -> None:
    upstream = dict(decision(7, status="open", context=100_000), task_key="T-19", url=URL)
    async with harness(upstream) as h:
        out = await call_checked(h, tool, args)
    assert out == {"id": 7, "task_key": "T-19", "status": "open", "url": URL}


async def test_error_answers_pass_through() -> None:
    transport = httpx.MockTransport(
        lambda r: httpx.Response(409, json={"error": {"code": "not_open", "message": "closed"}})
    )
    async with PluginHarness(
        OrchestratorPlugin, config=CONFIG, name="orchestrator", transport=transport
    ) as h:
        res = await h.call("orchestrator_decision_withdraw", WITHDRAW_ARGS)
    assert res.is_error and "not_open" in res.text


async def test_schemas_declare_view_and_acks() -> None:
    async with harness({}) as h:
        tools = {t["name"]: t for t in await h.list_tools()}
    view = tools["orchestrator_task_get"]["inputSchema"]["properties"]["view"]
    assert view["enum"] == ["summary", "open_decisions", "todo", "events", "comments"]
    assert "default" in view["description"].lower()
    for name in ("task_create", "task_update", "task_comment", "todo_set", "todo_update"):
        out = tools[f"orchestrator_{name}"]["outputSchema"]
        assert out["required"] == ["key", "version", "url"]
    for name in ("decision_ask", "decision_withdraw"):
        out = tools[f"orchestrator_{name}"]["outputSchema"]
        assert out["required"] == ["id", "task_key", "status", "url"]


async def test_unknown_view_is_rejected() -> None:
    async with harness(rich()) as h:
        res = await h.call("orchestrator_task_get", {"key": "T-19", "view": "bogus"})
    assert res.is_error
    with pytest.raises(ValueError, match="bogus"):
        task_view(rich(), "bogus")
