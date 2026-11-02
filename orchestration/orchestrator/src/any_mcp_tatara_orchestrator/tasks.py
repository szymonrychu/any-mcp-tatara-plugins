"""Task board tools: tasks, decisions, todo lists and session labels.

Agents call the orchestrator's agent API, which relays ``/tasks/...`` to the taskboard service
and injects the verified session identity. ``/session/labels`` is answered by the orchestrator
itself. Every tool is a declarative row turned into a ``ToolSpec`` by ``task_tools``.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["task_tools"]

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
_KEY = {"type": "string", "description": "Task key such as T-12."}
_TASK_OUT = {
    "type": "object",
    "properties": {
        "key": {"type": "string"},
        "version": {"type": "integer"},
        "url": {"type": "string", "description": "Absolute link to the task."},
    },
    "required": ["url"],
}
_LIST_OUT = {
    "type": "object",
    "properties": {"url": {"type": "string", "description": "Link to the task board."}},
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
            "mine: tasks matching any label of your own session. " + _LABEL_RULE,
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
                }
            ),
            _LIST_OUT,
            _hints(read_only=True, idempotent=True),
            query=("status", "mine", "waiting", "session", "label", "labels"),
        ),
        spec(
            "task_get",
            "GET",
            "/tasks/{key}",
            "Read one task with its todo list, decisions (including answers), comments and "
            "events. Use it to read a user's answer after the chat message that links the task.",
            _schema({"key": _KEY}, ("key",)),
            _TASK_OUT,
            _hints(read_only=True, idempotent=True),
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
