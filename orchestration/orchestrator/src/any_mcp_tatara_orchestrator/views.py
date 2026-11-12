"""Compact task answers: task_get views and short confirmations for the write tools.

The taskboard answers every task call with the whole task. An agent that wrote one line should
not pay ~40k chars of context for it, and a task_get should not carry what the agent did not ask
for. These pure functions shape the taskboard answer; fit.fit_task stays the net for task_get.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .fit import _cut

__all__ = [
    "ACKS",
    "TASK_VIEWS",
    "comment_ack",
    "decision_ack",
    "task_ack",
    "task_view",
    "todo_ack",
]

TASK_VIEWS = ("summary", "open_decisions", "todo", "events", "comments")
_CORE = (
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
    "spec",
    "step",
    "depends_on",
    "refs",
    "model",
)
_OPEN_DECISION = ("id", "asking_slug", "question", "context", "options", "created_at")
_COLLAPSED_CHARS = 200
_TODO_CHARS = 60


def _text(text: str, keep: int) -> str:
    return text if len(text) <= keep else _cut(text, keep)


def _answer(decision: dict[str, Any]) -> str:
    answer = decision.get("answer")
    if not isinstance(answer, dict):
        return ""
    label = answer.get("label")
    return label if isinstance(label, str) else _text(str(answer.get("text", "")), _COLLAPSED_CHARS)


def _decision(decision: dict[str, Any]) -> dict[str, Any]:
    if decision.get("status") == "open":
        return {k: decision[k] for k in _OPEN_DECISION if k in decision}
    out = {"id": decision["id"], "question": decision.get("question"), "answer": _answer(decision)}
    if decision.get("note"):
        out["note"] = _text(str(decision["note"]), _COLLAPSED_CHARS)
    return out


def _items(task: dict[str, Any], *, open_only: bool) -> list[dict[str, Any]]:
    return [
        {"id": i["id"], "text": i["text"], "status": i["status"]}
        for i in task.get("todo", [])
        if not open_only or i["status"] != "done"
    ]


def task_view(task: dict[str, Any], view: str) -> dict[str, Any]:
    """The part of a taskboard task one task_get view shows; core fields are in every view."""
    out = {k: task[k] for k in _CORE if k in task}
    decisions = task.get("decisions", [])
    if view == "summary":
        out["body"] = task.get("body", "")
        out["waiting_sessions"] = task.get("waiting_sessions", [])
        out["decisions"] = [_decision(d) for d in decisions if d.get("status") != "withdrawn"]
        withdrawn = sum(d.get("status") == "withdrawn" for d in decisions)
        if withdrawn:
            out["decisions_withdrawn"] = withdrawn
        out["todo"] = _items(task, open_only=True)
        out["todo_done"] = sum(i["status"] == "done" for i in task.get("todo", []))
        out["events"] = task.get("events", [])
    elif view == "open_decisions":
        out["decisions"] = [_decision(d) for d in decisions if d.get("status") == "open"]
    elif view == "todo":
        out["todo"] = _items(task, open_only=False)
        out["todo_done"] = sum(i["status"] == "done" for i in task.get("todo", []))
    elif view in ("events", "comments"):
        out[view] = task.get(view, [])
    else:
        raise ValueError(f"unknown task view {view!r}; expected one of {', '.join(TASK_VIEWS)}")
    return out


def task_ack(task: dict[str, Any]) -> dict[str, Any]:
    return {"key": task["key"], "version": task["version"], "url": task["url"]}


def comment_ack(task: dict[str, Any]) -> dict[str, Any]:
    comments = task.get("comments", [])
    return {
        **task_ack(task),
        "comment_id": max((c["id"] for c in comments), default=None),
        "comments_total": task.get("comments_total", len(comments)),
    }


def todo_ack(task: dict[str, Any]) -> dict[str, Any]:
    """A replace changes every item id, so the new ids come back with a cut of each text."""
    todo = [[i["id"], _text(i["text"], _TODO_CHARS)] for i in task.get("todo", [])]
    return {**task_ack(task), "todo": todo}


def decision_ack(decision: dict[str, Any]) -> dict[str, Any]:
    return {k: decision[k] for k in ("id", "task_key", "status", "url")}


# Write tools and the short confirmation each answers instead of the whole task.
ACKS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "task_create": task_ack,
    "task_update": task_ack,
    "task_comment": comment_ack,
    "todo_set": todo_ack,
    "todo_update": task_ack,
    "plan_step_edit": task_ack,
    "plan_step_update": task_ack,
    "plan_step_deps": task_ack,
    "decision_ask": decision_ack,
    "decision_withdraw": decision_ack,
}
