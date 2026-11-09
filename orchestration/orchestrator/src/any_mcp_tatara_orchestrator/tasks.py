"""Task board tools: tasks, decisions, todo lists and session labels.

Agents call the orchestrator's agent API, which relays ``/tasks/...`` to the taskboard service
and injects the verified session identity. ``/session/labels`` is answered by the orchestrator
itself. Every tool is a declarative row turned into a ``ToolSpec`` by ``task_tools``.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = [
    "FIT_TOOLS",
    "TASK_LIST_LIMIT",
    "TASK_LIST_MAX",
    "TASK_WINDOW_DEFAULT",
    "TASK_WINDOW_MAX",
    "task_tools",
]

# Tools answering one task (or one decision) that fit.fit_task trims to the output budget.
FIT_TOOLS = frozenset(
    {
        "task_create",
        "task_update",
        "task_get",
        "task_comment",
        "decision_ask",
        "decision_withdraw",
        "todo_set",
        "todo_update",
    }
)
TASK_LIST_LIMIT = 10
# 20 bounded summaries stay under the 40000 char budget (fit.summarize_tasks).
TASK_LIST_MAX = 20
TASK_WINDOW_DEFAULT = 20
TASK_WINDOW_MAX = 200
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
_LIFECYCLE = (
    "Lifecycle: create or claim a task at the start of work, keep its todo list current as you "
    "go, and close the task (status done, or dropped) when the work is finished."
)
_ANSWER = (
    "The user answers in the web UI; the answer arrives as a chat message with a link to the "
    "task, then read it with task_get."
)
_PROJECT_RULE = "Project slug; see project_list. Agents work in their own project only."
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
                        "description": _PROJECT_RULE + " Omitted: your own project.",
                    },
                    "client_key": {
                        "type": "string",
                        "description": "Idempotency key chosen by you, stable across retries.",
                    },
                },
                ("title", "client_key"),
            ),
            _TASK_OUT,
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
                },
                ("key", "version"),
            ),
            _TASK_OUT,
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
                "limit",
                "cursor",
            ),
        ),
        spec(
            "task_get",
            "GET",
            "/tasks/{key}",
            "Read one task with its todo list, decisions (including answers), comments and "
            "events. Use it to read a user's answer after the chat message that links the task. "
            "Returns the newest 20 events and comments by default; events_total and "
            "comments_total say how many exist; page back with events_before / comments_before; "
            "events_limit=0 and comments_limit=0 give the decisions and todo only. " + _TRIMMED,
            _schema(
                {
                    "key": _KEY,
                    "events_limit": _window("Newest N events; default 20; 0 = none."),
                    "comments_limit": _window("Newest N comments; default 20; 0 = none."),
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
            _TASK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "decision_ask",
            "POST",
            "/tasks/{key}/decisions",
            "Ask the user a decision on a task, with 2 to 8 options, exactly one marked "
            "recommended. Not blocking: continue other work. " + _ANSWER + " The task counts "
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
            _TASK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "decision_withdraw",
            "POST",
            "/tasks/{key}/decisions/{decision_id}/withdraw",
            "Withdraw a decision you asked while it is still open (HTTP 409 once answered). "
            + _ANSWER,
            _schema({"key": _KEY, "decision_id": {"type": "string"}}, ("key", "decision_id")),
            _TASK_OUT,
            _hints(idempotent=True),
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
            _TASK_OUT,
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
            _TASK_OUT,
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
