"""Spec tools: specs (named sets of markdown documents with versions and comments) on the task
board, relayed by the orchestrator under ``/agent/v1/specs...``.

Document bodies never pass through these rows: they move through the local ``spec_pull`` and
``spec_push`` tools (``specs_local.py``), which read and write files in the working copy so a body
is never in the model's context. Deleting a spec and dismissing a suggestion are superleader admin
tools (``spec_delete``, ``spec_suggestion_dismiss``); accept is the user's in the web UI.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["SPEC_KEY_PATTERN", "SPEC_PARAM", "spec_tools"]

_BASE = "/agent/v1"
SPEC_KEY_PATTERN = "^S-[1-9][0-9]{0,17}$"
_STATUS = ["draft", "review", "approved", "archived"]
_LABEL_RULE = (
    "Labels are trimmed and lowercased by the server and must then match ^[a-z0-9-_.:]{1,40}$."
)
_PROJECT_RULE = (
    "Project slug; see project_list. Agents work in their own projects only (home plus any "
    "extra projects granted to the session); "
    "a superleader may use any project."
)
SPEC_PARAM = {
    "type": "string",
    "pattern": SPEC_KEY_PATTERN,
    "description": "Spec key such as S-3 (see spec_get).",
}
_PATH = {
    "type": "string",
    "pattern": r"^[a-z0-9][a-z0-9._/-]{0,119}\.md$",
    "description": "Document path inside the spec, such as plan.md or research/topic.md.",
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


def _hints(*, read_only: bool = False, idempotent: bool = False) -> dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "destructiveHint": False,
        "idempotentHint": idempotent,
        "openWorldHint": False,
    }


def _labels(text: str) -> dict[str, Any]:
    return {
        "type": "array",
        "maxItems": 20,
        "items": {"type": "string", "maxLength": 40},
        "description": f"{text} At most 20. {_LABEL_RULE}",
    }


def spec_tools(base_url: str) -> list[ToolSpec]:
    def row(
        name: str,
        method: str,
        path: str,
        description: str,
        schema: dict[str, Any],
        hints: dict[str, bool],
        body: str | None = None,
        query: tuple[str, ...] = (),
    ) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            input_schema=schema,
            annotations=hints,
            http=HttpBinding(method, f"{_BASE}{path}", query=query, body=body, base_url=base_url),
        )

    return [
        row(
            "spec_list",
            "GET",
            "/specs",
            "List specs, newest first. A spec is a named set of markdown documents (plan, "
            "research, notes) with versions and comments. Filter by project, status, label or "
            "exact name. Pages of limit specs; pass next_cursor as cursor for the next page. "
            "Document bodies are not listed: spec_get shows the documents of one spec and "
            "spec_pull copies them to files.",
            _schema(
                {
                    "project": {"type": "string", "description": _PROJECT_RULE},
                    "status": {"type": "string", "enum": _STATUS},
                    "label": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 40},
                        "description": f"Match any of these labels. {_LABEL_RULE}",
                    },
                    "name": {"type": "string", "description": "Exact spec name."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "cursor": {"type": "string", "description": "next_cursor of the last page."},
                }
            ),
            _hints(read_only=True, idempotent=True),
            query=("project", "status", "label", "name", "limit", "cursor"),
        ),
        row(
            "spec_get",
            "GET",
            "/specs/{spec}",
            "Read one spec: key, project, name, title, status, labels, version, url, its "
            "documents (path, version, size, note, author; never the bodies), linked task keys "
            "and the open comment count. Use spec_pull to copy the document bodies to files.",
            _schema({"spec": SPEC_PARAM}, ("spec",)),
            _hints(read_only=True, idempotent=True),
        ),
        row(
            "spec_create",
            "POST",
            "/specs",
            "Create an empty spec. name is a stable slug (a-z 0-9 and hyphen, at most 80, "
            "unique in the project), title is for people. Documents are added with spec_push, "
            "which also creates a spec when its name is new. Link the improvement's task with "
            "task_update spec.",
            _schema(
                {
                    "project": {
                        "type": "string",
                        "description": _PROJECT_RULE
                        + " Omitted: your home project (a superleader: default).",
                    },
                    "name": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,79}$"},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "labels": _labels("Labels of the spec."),
                    "client_key": {
                        "type": "string",
                        "description": "Idempotency key chosen by you, stable across retries.",
                    },
                },
                ("name", "title"),
            ),
            _hints(),
            "*",
        ),
        row(
            "spec_update",
            "PATCH",
            "/specs/{spec}",
            "Edit a spec: title, status, labels, project, or assign it to your session with "
            "assignee_self. version is the version you last read (spec_get); a stale one is "
            "refused (HTTP 412 stale_version): read it again and retry. Status moves between "
            "draft, review, approved and archived. An agent sets approved once plan_gate says "
            "approvable (no open decision on the spec's open or in_progress tasks); otherwise it "
            "is refused 409 decisions_open listing the open decisions (answer or withdraw them "
            "first). A superleader and the user may always approve. Archive the spec after its "
            "retro. Document edits go through spec_push.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "version": {"type": "integer", "description": "Version last read."},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "status": {"type": "string", "enum": _STATUS},
                    "labels": _labels("Replaces the labels of the spec."),
                    "assignee_self": {"type": "boolean"},
                    "project": {
                        "type": "string",
                        "description": _PROJECT_RULE + " Moves the spec to that project.",
                    },
                },
                ("spec", "version"),
            ),
            _hints(idempotent=True),
            "*",
        ),
        row(
            "spec_versions",
            "GET",
            "/specs/{spec}/docs/{path}/versions",
            "List the versions of one document, newest first: version, size, note, author, "
            "time (never the bodies). Pages of limit; pass next_cursor as cursor for older "
            "ones. Read a body with spec_pull and its version argument.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "path": _PATH,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    "cursor": {"type": "integer", "description": "next_cursor of the last page."},
                },
                ("spec", "path"),
            ),
            _hints(read_only=True, idempotent=True),
            query=("limit", "cursor"),
        ),
        row(
            "spec_comment",
            "POST",
            "/specs/{spec}/comments",
            "Comment on a spec, on one document (path, optionally a version and an anchor "
            "inside it), or give a replacement text as suggestion; the user accepts or "
            "dismisses a suggestion in the web UI. Safe to retry with the same client_key.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "body": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "maxLength": 200},
                    "version": {"type": "integer", "minimum": 1},
                    "anchor": {"type": "string", "minLength": 1, "maxLength": 200},
                    "suggestion": {"type": "string", "description": "Replacement text."},
                    "client_key": {"type": "string", "description": "Idempotency key."},
                },
                ("spec", "body"),
            ),
            _hints(),
            "*",
        ),
        row(
            "spec_comments",
            "GET",
            "/specs/{spec}/comments",
            "List the comments of a spec, oldest first: id, path, version, anchor, body, "
            "suggestion and its status, author, resolved or not. path limits them to one "
            "document, open to the unresolved ones.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "path": {"type": "string", "maxLength": 200},
                    "open": {"type": "boolean", "description": "Only unresolved comments."},
                },
                ("spec",),
            ),
            _hints(read_only=True, idempotent=True),
            query=("path", "open"),
        ),
        row(
            "spec_comment_resolve",
            "POST",
            "/specs/{spec}/comments/{id}/resolve",
            "Mark a comment resolved once it is handled.",
            _schema({"spec": SPEC_PARAM, "id": {"type": "integer", "minimum": 1}}, ("spec", "id")),
            _hints(idempotent=True),
        ),
        row(
            "spec_queue_list",
            "GET",
            "/specs/queue",
            "List the implementation start queue of approved specs per project (FIFO by "
            "position, no expiry): the project's autostart limits, state (off = autostart off "
            "until the user sets both limits, idle, usage_unknown, usage_refused, at_limit, "
            "usage_high, orchestrator_unavailable, ready) and queued specs. position is a dense "
            "1-based rank. Any agent may read it; only a superleader or the user may reorder; "
            "ask a superleader to rearrange.",
            _schema({"project": {"type": "string", "description": _PROJECT_RULE}}),
            _hints(read_only=True, idempotent=True),
            query=("project",),
        ),
        row(
            "spec_queue_move",
            "POST",
            "/specs/queue/{id}/move",
            "Superleader only: move one queued spec in the start queue, to a 1-based position "
            "or before another queue item (before is that item's id); give exactly one of "
            "position and before. The server answers 403 superleader_only to other sessions and "
            "409 not_queued once a start began. Removing an item is the user's, in the web UI.",
            {
                **_schema(
                    {
                        "id": {"type": "integer", "minimum": 1, "description": "Queue item id."},
                        "position": {"type": "integer", "minimum": 1},
                        "before": {"type": "integer", "minimum": 1},
                    },
                    ("id",),
                ),
                "oneOf": [{"required": ["position"]}, {"required": ["before"]}],
            },
            _hints(idempotent=True),
            "*",
        ),
    ]
