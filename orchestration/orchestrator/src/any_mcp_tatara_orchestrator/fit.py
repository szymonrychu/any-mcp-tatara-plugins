"""Fit task answers into the any-mcp tool output budget while keeping them schema valid.

any-mcp answers is_error for a structured value longer than ``response_budget_chars`` when the
tool declares an output schema (any_mcp.response.shape_default), and every task tool declares
one. A long-lived task outgrows the budget, so the plugin trims the answer before any-mcp
measures it: oldest events, oldest comments, long texts, long decision answers, oldest closed
decisions, then a skeleton. key, url, title, status and version are never changed.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from typing import Any

__all__ = ["MIN_KEEP", "fit_task", "measure", "summarize_tasks"]

MIN_KEEP = 500
# Room for the cut marker (at most 24 chars) and an omitted counter gaining a digit.
_SLACK = 32
_PROTECTED = frozenset({"key", "url", "title", "status", "version", "omitted"})
_SKELETON = (
    "id",
    "task_key",
    "key",
    "title",
    "kind",
    "status",
    "labels",
    "version",
    "blocked",
    "open_decisions",
    "url",
)
_SUMMARY = (
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
)
_SUMMARY_CHARS = 200
_MAX_LABELS = 20
_LABEL_CHARS = 40
_TODO_STATUS = ("todo", "inprogress", "done")


def measure(value: Any) -> int:
    """Length of the text any-mcp sends for ``value`` (any_mcp.response._describe)."""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _cut(text: str, keep: int) -> str:
    return f"{text[:keep]}...[cut {len(text) - keep} chars]"


def _drop(
    out: dict[str, Any],
    field: str,
    budget: int,
    omitted: dict[str, Any],
    droppable: Callable[[Any], bool],
) -> None:
    """Drop the fewest oldest droppable items of ``out[field]`` that make ``out`` fit (all of
    them when nothing smaller does). Lists are oldest first, so the first ones go."""
    items = out.get(field)
    if not isinstance(items, list):
        return
    order = [i for i, item in enumerate(items) if droppable(item)]

    def size(k: int) -> int:
        gone = set(order[:k])
        out[field] = [item for i, item in enumerate(items) if i not in gone]
        omitted[field] = k
        return measure(out)

    lo, hi = 0, len(order)
    if size(hi) > budget:
        return
    while lo < hi:
        mid = (lo + hi) // 2
        if size(mid) <= budget:
            hi = mid
        else:
            lo = mid + 1
    size(lo)


def _long_strings(out: dict[str, Any], answers: bool) -> list[tuple[Any, Any]]:
    """(container, key) of every string over MIN_KEEP chars, in document order: only inside
    decision answers when ``answers``, otherwise only outside them. Top-level protected keys
    are never listed."""
    found: list[tuple[Any, Any]] = []

    def walk(node: Any, inside: bool, top: bool) -> None:
        pairs = list(node.items()) if isinstance(node, dict) else list(enumerate(node))
        for key, value in pairs:
            if top and key in _PROTECTED:
                continue
            here = inside or key == "answer"
            if isinstance(value, str):
                if here == answers and len(value) > MIN_KEEP:
                    found.append((node, key))
            elif isinstance(value, (dict, list)):
                walk(value, here, False)

    walk(out, False, True)
    return found


def _cut_long(out: dict[str, Any], budget: int, omitted: dict[str, Any], answers: bool) -> None:
    """Cut long strings, longest first (ties in document order), each once, just enough to fit
    or down to MIN_KEEP chars."""
    refs = sorted(_long_strings(out, answers), key=lambda ref: -len(ref[0][ref[1]]))
    for node, key in refs:
        excess = measure(out) - budget
        if excess <= 0:
            return
        text = node[key]
        node[key] = _cut(text, max(MIN_KEEP, len(text) - excess - _SLACK))
        omitted["cut"] += 1


def _any(_: Any) -> bool:
    return True


def _closed(decision: Any) -> bool:
    return not (isinstance(decision, dict) and decision.get("status") == "open")


def fit_task(value: Any, budget: int) -> Any:
    """``value`` when it fits ``budget`` (or is not an object), else a trimmed copy carrying
    ``omitted``: counts of dropped events, comments and closed decisions, of cut texts, and
    whether only the skeleton is left."""
    if not isinstance(value, dict) or measure(value) <= budget:
        return value
    out = copy.deepcopy(value)
    omitted: dict[str, Any] = {
        "events": 0,
        "comments": 0,
        "decisions": 0,
        "cut": 0,
        "skeleton": False,
    }
    out["omitted"] = omitted
    steps: tuple[Callable[[], None], ...] = (
        lambda: _drop(out, "events", budget, omitted, _any),
        lambda: _drop(out, "comments", budget, omitted, _any),
        lambda: _cut_long(out, budget, omitted, answers=False),
        lambda: _cut_long(out, budget, omitted, answers=True),
        lambda: _drop(out, "decisions", budget, omitted, _closed),
    )
    for step in steps:
        if measure(out) <= budget:
            return out
        step()
    if measure(out) <= budget:
        return out
    omitted["skeleton"] = True
    return {**{f: value[f] for f in _SKELETON if f in value}, "omitted": omitted}


def _short(value: Any, limit: int) -> Any:
    return _cut(value, limit) if isinstance(value, str) and len(value) > limit else value


def _summary(task: Any) -> Any:
    if not isinstance(task, dict):
        return task
    row = {
        f: task[f] if f in ("key", "url") else _short(task[f], _SUMMARY_CHARS)
        for f in _SUMMARY
        if f in task
    }
    labels = task.get("labels")
    if isinstance(labels, list):
        row["labels"] = [_short(label, _LABEL_CHARS) for label in labels[:_MAX_LABELS]]
    todo = task.get("todo") if isinstance(task.get("todo"), list) else []
    row["todo"] = {
        s: sum(1 for t in todo if isinstance(t, dict) and t.get("status") == s)
        for s in _TODO_STATUS
    }
    for field in ("decisions", "comments", "events"):
        items = task.get(field)
        row[field] = len(items) if isinstance(items, list) else 0
    return row


def summarize_tasks(page: Any) -> Any:
    """Compact task_list page: each task becomes a bounded summary with child counts, so a page
    of TASK_LIST_MAX rows always fits the budget; task_get shows one task in full."""
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        return page
    return {**page, "items": [_summary(t) for t in page["items"]]}
