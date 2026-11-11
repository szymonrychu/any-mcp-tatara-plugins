"""Task board tools: tasks, decisions, todo lists and session labels.

Agents call the orchestrator's agent API, which relays ``/tasks/...`` to the taskboard service
and injects the verified session identity. ``/session/labels`` is answered by the orchestrator
itself. Every tool is a declarative row turned into a ``ToolSpec`` by ``task_tools``.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

from .specs import SPEC_KEY_PATTERN
from .views import TASK_VIEWS

__all__ = [
    "SUMMARY_EVENTS",
    "TASK_LIST_LIMIT",
    "TASK_LIST_MAX",
    "TASK_WINDOW_DEFAULT",
    "TASK_WINDOW_MAX",
    "task_tools",
]

TASK_LIST_LIMIT = 10
# 20 bounded summaries stay under the 40000 char budget (fit.summarize_tasks).
TASK_LIST_MAX = 20
TASK_WINDOW_DEFAULT = 20
TASK_WINDOW_MAX = 200
# Events the summary view of task_get shows unless events_limit says otherwise.
SUMMARY_EVENTS = 10
_TRIMMED = (
    "A task too large for the tool output budget comes back trimmed: the oldest events, then "
    "the oldest comments are left out and long texts are cut (marked ...[cut N chars]); "
    "omitted counts what was left out. The whole task is at url in the web UI."
)
_OMITTED = {
    "type": "object",
    "description": "Present only when the answer was trimmed to fit the tool output budget.",
    "properties": {
        "events": {"type": "integer", "description": "Oldest events left out."},
        "comments": {"type": "integer", "description": "Oldest comments left out."},
        "decisions": {"type": "integer", "description": "Oldest closed decisions left out."},
        "cut": {"type": "integer", "description": "Long texts cut."},
        "skeleton": {"type": "boolean", "description": "Only the core fields are shown."},
    },
}

_BASE = "/agent/v1"
_TASK_STATUS = ["idea", "open", "in_progress", "done", "dropped"]
_TODO_STATUS = ["todo", "inprogress", "done"]
_LABEL_PATTERN = "^[a-z0-9-_.:]{1,40}$"
_LABEL_RULE = (
    f"Labels are trimmed and lowercased by the server and must then match {_LABEL_PATTERN} "
    "(1 to 40 characters of a-z 0-9 - _ . :)."
)
_ACKED = " Answers a short confirmation, not the task; read the task with task_get."
_LIFECYCLE = (
    "Lifecycle: create or claim a task at the start of work, keep its todo list current as you "
    "go, and close the task (status done, or dropped) when the work is finished." + _ACKED
)
_ANSWER = (
    "The user answers in the web UI; the answer arrives as a chat message with a link to the "
    "task, then read it with task_get."
)
_PROJECT_RULE = (
    "Project slug; see project_list. Agents work in their own projects only (home plus any "
    "extra projects granted to the session); "
    "a superleader may use any project."
)
_SPEC_RULE = "Spec key such as S-3 (see spec_get)."
_KEY = {"type": "string", "description": "Task key such as T-12."}
_TASK_OUT = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "version": {"type": "integer"},
        "url": {"type": "string", "description": "Absolute link to the task."},
        "events_total": {"type": "integer", "description": "How many events the task has."},
        "comments_total": {"type": "integer", "description": "How many comments the task has."},
        "omitted": _OMITTED,
    },
    "required": ["url"],
}
_ACK_OUT = {
    "type": "object",
    "description": "A short confirmation; read the task with task_get.",
    "properties": {
        "key": {"type": "string"},
        "version": {"type": "integer", "description": "Task version, valid for task_update."},
        "url": {"type": "string", "description": "Absolute link to the task."},
        "comment_id": {"type": ["integer", "null"], "description": "task_comment: newest comment."},
        "comments_total": {"type": "integer", "description": "task_comment: comments on the task."},
        "todo": {
            "type": "array",
            "description": "todo_set: the new items as [id, text cut to 60 chars], in order.",
            "items": {"type": "array"},
        },
    },
    "required": ["key", "version", "url"],
}
_DECISION_ACK_OUT = {
    "type": "object",
    "description": "A short confirmation; the answer arrives as a chat message.",
    "properties": {
        "id": {"type": ["integer", "string"]},
        "task_key": {"type": "string"},
        "status": {"type": "string", "description": "open, answered or withdrawn."},
        "url": {"type": "string", "description": "Absolute link to the task."},
    },
    "required": ["id", "task_key", "status", "url"],
}
_LIST_OUT = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "Link to the task board."},
        "items": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"type": ["string", "null"]},
    },
}
_LABELS_OUT = {
    "type": "object",
    "properties": {
        "labels": {"type": "array", "items": {"type": "string"}},
        "url": {"type": "string"},
    },
}


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


def _window(text: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": 0, "maximum": TASK_WINDOW_MAX, "description": text}


_BEFORE = {
    "type": "integer",
    "minimum": 1,
    "description": "Only items with a smaller id: pass the smallest id you hold to page back.",
}


def _labels(text: str) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string", "maxLength": 40},
        "description": f"{text} At most 20. {_LABEL_RULE}",
    }


def _hints(*, read_only: bool = False, idempotent: bool = False) -> dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": idempotent,
        "openWorldHint": False,
    }


_OPTION = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "description": {"type": "string"},
        "recommended": {"type": "boolean"},
    },
    "required": ["label", "recommended"],
    "additionalProperties": False,
}
_TODO_ITEM = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "maxLength": 200, "description": "One line."},
        "status": {"type": "string", "enum": _TODO_STATUS},
    },
    "required": ["text"],
    "additionalProperties": False,
}


def task_tools(base_url: str) -> list[ToolSpec]:
    def spec(
        name: str,
        method: str,
        path: str,
        description: str,
        schema: dict[str, Any],
        output: dict[str, Any],
        hints: dict[str, bool],
        body: str | None = None,
        query: tuple[str, ...] = (),
    ) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            input_schema=schema,
            output_schema=output,
            annotations=hints,
            http=HttpBinding(method, f"{_BASE}{path}", query=query, body=body, base_url=base_url),
        )

    return [
        spec(
            "task_create",
            "POST",
            "/tasks",
            "Create a task on the task board (kind idea or task). Safe to retry: the same "
            "client_key from your session returns the existing task instead of a second one. "
            "assignee_self assigns it to your session. " + _LIFECYCLE,
            _schema(
                {
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "kind": {"type": "string", "enum": ["idea", "task"]},
                    "labels": _labels("Labels of the task."),
                    "assignee_self": {"type": "boolean"},
                    "project": {
                        "type": "string",
                        "description": _PROJECT_RULE
                        + " Omitted: your home project (a superleader: default).",
                    },
                    "spec": {
                        "type": "string",
                        "pattern": SPEC_KEY_PATTERN,
                        "description": _SPEC_RULE + " Links the task to that spec.",
                    },
                    "client_key": {
                        "type": "string",
                        "description": "Idempotency key chosen by you, stable across retries.",
                    },
                },
                ("title", "client_key"),
            ),
            _ACK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "task_update",
            "PATCH",
            "/tasks/{key}",
            "Edit a task. version is the version you last read (task_get); a stale one is "
            "refused (HTTP 412 stale_version): read the task again and retry. " + _LIFECYCLE,
            _schema(
                {
                    "key": _KEY,
                    "version": {"type": "integer", "description": "Version last read."},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "status": {"type": "string", "enum": _TASK_STATUS},
                    "labels": _labels("Replaces the labels of the task."),
                    "assignee_self": {"type": "boolean"},
                    "project": {
                        "type": "string",
                        "description": _PROJECT_RULE + " Moves the task to that project.",
                    },
                    "spec": {
                        "type": ["string", "null"],
                        "pattern": SPEC_KEY_PATTERN,
                        "description": _SPEC_RULE + " Links the task to that spec; null unlinks.",
                    },
                },
                ("key", "version"),
            ),
            _ACK_OUT,
            _hints(idempotent=True),
            "*",
        ),
        spec(
            "task_list",
            "GET",
            "/tasks",
            "List tasks, blocked ones first. status defaults to active (idea, open, "
            "in_progress). mine: your tasks; waiting: tasks with an open decision; session: "
            "tasks assigned to a session id; label: tasks carrying any of the labels; labels "
            "mine: tasks matching any label of your own session. " + _LABEL_RULE + " "
            "Each task is a summary with counts of its todo items, decisions, comments and "
            "events; read one in full with task_get. Pages of limit tasks (default 10, at most "
            "20); pass next_cursor as cursor for the next page.",
            _schema(
                {
                    "status": {"type": "string", "enum": ["active", "done", "dropped", "all"]},
                    "mine": {"type": "boolean"},
                    "waiting": {"type": "boolean"},
                    "session": {"type": "string"},
                    "label": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "description": f"Match any of these labels. {_LABEL_RULE}",
                    },
                    "labels": {"type": "string", "enum": ["mine"]},
                    "project": {
                        "type": "string",
                        "description": _PROJECT_RULE + " Only tasks of that project.",
                    },
                    "spec": {
                        "type": "string",
                        "pattern": SPEC_KEY_PATTERN,
                        "description": _SPEC_RULE + " Only tasks linked to that spec.",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": TASK_LIST_MAX},
                    "cursor": {
                        "type": "string",
                        "description": "next_cursor of the previous page.",
                    },
                }
            ),
            _LIST_OUT,
            _hints(read_only=True, idempotent=True),
            query=(
                "status",
                "mine",
                "waiting",
                "session",
                "label",
                "labels",
                "project",
                "spec",
                "limit",
                "cursor",
            ),
        ),
        spec(
            "task_get",
            "GET",
            "/tasks/{key}",
            "Read one task. The view picks what comes back; every view carries key, project, "
            "title, kind, status, labels, assignee_session, version, blocked, open_decisions, "
            "url, events_total, comments_total and updated_at. summary (default): the body, "
            "open decisions in full, answered decisions as id, question, answer and note, "
            "withdrawn ones only as a count, the todo items still todo or inprogress plus "
            "todo_done, and the newest 10 events (no comments). open_decisions: the open "
            "decisions in full. todo: every todo item. events: the newest 20 events. comments: "
            "the newest 20 comments. Use it to read a user's answer after the chat message that "
            "links the task. events_limit (events and summary) and comments_limit (comments) "
            "resize the window; page back with events_before / comments_before. " + _TRIMMED,
            _schema(
                {
                    "key": _KEY,
                    "view": {
                        "type": "string",
                        "enum": list(TASK_VIEWS),
                        "description": "What to read; default summary. See the tool description.",
                    },
                    "events_limit": _window("Newest N events (summary, events); 0 = none."),
                    "comments_limit": _window("Newest N comments (comments view)."),
                    "events_before": _BEFORE,
                    "comments_before": _BEFORE,
                },
                ("key",),
            ),
            _TASK_OUT,
            _hints(read_only=True, idempotent=True),
            query=("events_limit", "events_before", "comments_limit", "comments_before"),
        ),
        spec(
            "task_comment",
            "POST",
            "/tasks/{key}/comments",
            "Add a comment to a task. Comments never notify the user. " + _LIFECYCLE,
            _schema({"key": _KEY, "body": {"type": "string"}}, ("key", "body")),
            _ACK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "decision_ask",
            "POST",
            "/tasks/{key}/decisions",
            "Ask the user a decision on a task, with 2 to 8 options, exactly one marked "
            "recommended. This is the only channel for a question to the user: never ask a "
            "question in chat (orchestrator_chat_send) or in your final answer. When the action "
            "the decision unblocks was blocked by a tool, permission or sandbox block, the same "
            "decision also asks for the fix of that block, so one answer unblocks it. "
            "Not blocking: continue other work. " + _ANSWER + " The task counts "
            "as blocked while a decision is open. A repeated client_key returns the same "
            "decision. " + _LIFECYCLE,
            _schema(
                {
                    "key": _KEY,
                    "question": {"type": "string"},
                    "context": {"type": "string"},
                    "options": {
                        "type": "array",
                        "minItems": 2,
                        "maxItems": 8,
                        "items": _OPTION,
                        "description": "2 to 8 options; exactly one has recommended true.",
                    },
                    "client_key": {"type": "string", "description": "Idempotency key."},
                },
                ("key", "question", "options"),
            ),
            _DECISION_ACK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "decision_withdraw",
            "POST",
            "/tasks/{key}/decisions/{decision_id}/withdraw",
            "Withdraw a decision you asked while it is still open (HTTP 409 once answered). "
            "A withdrawal needs an outcome: reason plus exactly one of superseded_by (the id "
            "of the decision that replaces it) or answered_elsewhere (where the answer came "
            "from); HTTP 422 when reason is missing, both or neither outcome is given, or "
            "superseded_by is not a decision you can see. " + _ANSWER + _ACKED,
            _schema(
                {
                    "key": _KEY,
                    "decision_id": {"type": "string"},
                    "reason": {
                        "type": "string",
                        "description": "Why the decision is withdrawn (1 to 2000 characters).",
                    },
                    "superseded_by": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Id of the decision that replaces this one.",
                    },
                    "answered_elsewhere": {
                        "type": "string",
                        "description": (
                            "Where the user answered instead (1 to 4000 characters); "
                            "give this or superseded_by, not both."
                        ),
                    },
                },
                ("key", "decision_id", "reason"),
            ),
            _DECISION_ACK_OUT,
            _hints(idempotent=True),
            "*",
        ),
        spec(
            "todo_set",
            "PUT",
            "/tasks/{key}/todo",
            "Replace the todo list of a task (max 100 items, one line of at most 200 "
            "characters each). " + _LIFECYCLE,
            _schema(
                {
                    "key": _KEY,
                    "items": {"type": "array", "maxItems": 100, "items": _TODO_ITEM},
                },
                ("key", "items"),
            ),
            _ACK_OUT,
            _hints(idempotent=True),
            "*",
        ),
        spec(
            "todo_update",
            "PATCH",
            "/tasks/{key}/todo/{item_id}",
            "Change the status or text of one todo item (item_id from task_get). " + _LIFECYCLE,
            _schema(
                {
                    "key": _KEY,
                    "item_id": {"type": "string"},
                    "status": {"type": "string", "enum": _TODO_STATUS},
                    "text": {"type": "string", "maxLength": 200},
                },
                ("key", "item_id"),
            ),
            _ACK_OUT,
            _hints(idempotent=True),
            "*",
        ),
        spec(
            "session_labels_set",
            "PUT",
            "/session/labels",
            "Replace the labels of your own session (never another's). A task matches a "
            "session when they share a label. Idempotent. " + _LABEL_RULE,
            _schema({"labels": _labels("The new labels of your session.")}, ("labels",)),
            _LABELS_OUT,
            _hints(idempotent=True),
            "*",
        ),
    ]
