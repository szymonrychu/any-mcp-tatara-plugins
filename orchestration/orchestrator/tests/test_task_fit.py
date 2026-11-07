"""T-61: task answers over the any-mcp output budget are trimmed, never refused."""

from __future__ import annotations

import copy
import json
from typing import Any

from any_mcp.response import DEFAULT_BUDGET_CHARS
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
