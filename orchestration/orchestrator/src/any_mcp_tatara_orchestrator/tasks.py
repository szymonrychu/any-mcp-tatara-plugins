"""Task board tools: tasks (plan steps too), decisions, todo lists and session labels.

Agents call the orchestrator's agent API, which relays ``/tasks/...`` to the taskboard service
and injects the verified session identity. ``/session/labels`` is answered by the orchestrator
itself. Every tool is a declarative row turned into a ``ToolSpec`` by ``task_tools``; ``todo``
and ``session_labels`` are merged tools (``merged.py``).
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

from .descriptions import DESCRIPTIONS
from .merged import Merged, Route
from .specs import SPEC_KEY_PATTERN
from .views import TASK_VIEWS

__all__ = [
    "SUMMARY_EVENTS",
    "TASK_LIST_LIMIT",
    "TASK_LIST_MAX",
    "TASK_WINDOW_DEFAULT",
    "TASK_WINDOW_MAX",
    "TASK_MERGED",
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
_MODELS = ["haiku", "sonnet", "opus", "fable"]
_REF = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["pr", "commit", "branch", "tag", "url"]},
        "value": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "required": ["kind", "value"],
    "additionalProperties": False,
}
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
    "Project slug; see project. Agents work in their own projects only (home plus any "
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
            "description": "todo: the items as [id, text cut to 60 chars], in order.",
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
_APPROVAL_ACK_OUT = {
    "type": "object",
    "description": "A short confirmation; the verdict arrives as a chat message.",
    "properties": {
        "id": {"type": ["integer", "string"]},
        "task_key": {"type": "string"},
        "status": {"type": "string", "description": "open, answered, withdrawn or expired."},
        "url": {"type": "string", "description": "Absolute link to the task."},
        "expires_at": {"type": "string", "description": "When the approval expires."},
    },
    "required": ["id", "task_key", "status", "url"],
}
_APPROVAL_LIST_OUT = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": ["integer", "string"]},
                    "task_key": {"type": "string"},
                    "title": {"type": "string"},
                    "status": {"type": "string"},
                    "verdict": {"type": ["string", "null"], "description": "Answer label."},
                    "note": {"type": ["string", "null"]},
                    "expires_at": {"type": ["string", "null"]},
                    "url": {"type": "string"},
                },
            },
        },
    },
}
_LIST_OUT = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "Link to the task board."},
        "items": {"type": "array", "items": {"type": "object"}},
        "next_cursor": {"type": ["string", "null"]},
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
_LABELS_OUT = {
    "type": "object",
    "properties": {
        "labels": {"type": "array", "items": {"type": "string"}},
        "url": {"type": "string"},
    },
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
            DESCRIPTIONS["task_create"],
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
            DESCRIPTIONS["task_update"],
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
                    "model": {
                        "type": ["string", "null"],
                        "enum": [*_MODELS, None],
                        "description": "Plan step model; null clears it.",
                    },
                    "refs": {"type": "array", "maxItems": 20, "items": _REF},
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
            DESCRIPTIONS["task_list"],
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
            DESCRIPTIONS["task_get"],
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
            DESCRIPTIONS["task_comment"],
            _schema({"key": _KEY, "body": {"type": "string"}}, ("key", "body")),
            _ACK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "decision_ask",
            "POST",
            "/tasks/{key}/decisions",
            DESCRIPTIONS["decision_ask"],
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
            DESCRIPTIONS["decision_withdraw"],
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
            "approval_ask",
            "POST",
            "/tasks/{key}/approvals",
            DESCRIPTIONS["approval_ask"],
            _schema(
                {
                    "key": _KEY,
                    "title": {
                        "type": "string",
                        "maxLength": 200,
                        "description": "What you want to do, one line.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Why, and what happens on Go (markdown).",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Optional link or ref, e.g. the PR URL.",
                    },
                    "expires_in_seconds": {
                        "type": "integer",
                        "minimum": 60,
                        "maximum": 604800,
                        "description": "Default 259200 (72 hours), at most 604800 (7 days).",
                    },
                    "client_key": {"type": "string", "description": "Idempotency key."},
                },
                ("key", "title"),
            ),
            _APPROVAL_ACK_OUT,
            _hints(),
            "*",
        ),
        spec(
            "approval_list",
            "GET",
            "/approvals",
            DESCRIPTIONS["approval_list"],
            _schema(
                {
                    "status": {
                        "type": "string",
                        "enum": ["open", "answered", "withdrawn", "expired", "all"],
                    }
                }
            ),
            _APPROVAL_LIST_OUT,
            _hints(read_only=True, idempotent=True),
            query=("status",),
        ),
    ]


TASK_MERGED = (
    Merged(
        "todo",
        DESCRIPTIONS["todo"],
        {
            "op": {"type": "string", "enum": ["set", "update"]},
            "key": _KEY,
            "items": {"type": "array", "maxItems": 100, "items": _TODO_ITEM},
            "item_id": {"type": "string"},
            "status": {"type": "string", "enum": _TODO_STATUS},
            "text": {"type": "string", "maxLength": 200},
        },
        {
            "set": Route("PUT", "/tasks/{key}/todo", ("key", "items"), body=True),
            "update": Route(
                "PATCH",
                "/tasks/{key}/todo/{item_id}",
                ("key", "item_id"),
                ("status", "text"),
                body=True,
            ),
        },
        selectors=("op",),
        required=("op", "key"),
        annotations=_hints(idempotent=True),
        output_schema=_ACK_OUT,
    ),
    Merged(
        "session_labels",
        DESCRIPTIONS["session_labels"],
        {
            "op": {"type": "string", "enum": ["set", "update"]},
            "slug": {"type": "string", "description": "Session slug (op update)."},
            "labels": _labels("The new labels (replaces all)."),
        },
        {
            "set": Route("PUT", "/session/labels", ("labels",), body=True),
            "update": Route("PUT", "/admin/sessions/{slug}/labels", ("slug", "labels"), body=True),
        },
        selectors=("op",),
        defaults={"op": "set"},
        required=("labels",),
        annotations=_hints(idempotent=True),
        output_schema=_LABELS_OUT,
    ),
)
