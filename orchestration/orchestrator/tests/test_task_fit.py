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


@pytest.mark.parametrize("make", [big_task, t19_like], ids=["265k", "t19"])
@pytest.mark.parametrize(("tool", "args"), TASK_CALLS, ids=[c[0] for c in TASK_CALLS])
async def test_every_task_tool_fits_a_task_over_40k(make: Any, tool: str, args: dict) -> None:
    upstream = make()
    assert measure(upstream) > T19_CHARS
    async with harness(upstream) as h:
        out = await call_checked(h, tool, args)
    assert all(out[f] == upstream[f] for f in PROTECTED)
    kept = [e["id"] for e in out["events"]]
    ids = [e["id"] for e in upstream["events"]]
    assert kept == ids[len(ids) - len(kept) :]
    assert out["omitted"]["events"] == len(ids) - len(kept)
    assert out["omitted"]["comments"] == len(upstream["comments"]) - len(out["comments"])


async def test_body_over_the_default_read_cap_is_read_whole() -> None:
    upstream = big_task()
    assert len(json.dumps(upstream).encode()) > READ_CAP
    async with harness(upstream) as h:
        out = await call_checked(h, "task_get", {"key": "T-19"})
    assert out["omitted"]["events"] == 400


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
        ("decision_withdraw", {"key": "T-19", "decision_id": "7"}),
    ],
)
async def test_decision_tools_fit_a_huge_context(tool: str, args: dict) -> None:
    upstream = dict(decision(7, status="open", context=100_000), task_key="T-19", url=URL)
    async with harness(upstream) as h:
        out = await call_checked(h, tool, args)
    assert out["omitted"]["cut"] == 1
    assert out["url"] == URL and out["task_key"] == "T-19"


async def test_small_task_answer_is_unchanged() -> None:
    upstream = task(events=3)
    async with harness(upstream) as h:
        out = await call_checked(h, "task_get", {"key": "T-19"})
    assert out == upstream


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
                "events_limit": 0,
                "comments_limit": 5,
                "events_before": 90,
                "comments_before": 7,
            },
        )
    assert seen == [
        "events_limit=20&comments_limit=20",
        "events_limit=0&events_before=90&comments_limit=5&comments_before=7",
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
        out = await call_checked(h, "task_get", {"key": "T-19"})
        tools = {t["name"]: t for t in await h.list_tools() if "outputSchema" in t}
    assert out["events_total"] == 3
    assert out["comments_total"] == 0
    props = tools["orchestrator_task_get"]["outputSchema"]["properties"]
    assert props["events_total"]["type"] == "integer"
    assert props["comments_total"]["type"] == "integer"
