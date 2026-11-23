"""Spec tools: specs (named sets of markdown documents with versions and comments) on the task
board, relayed by the orchestrator under ``/agent/v1/specs...``.

Document bodies never pass through these rows: they move through the local ``spec_pull`` and
``spec_push`` tools (``specs_local.py``), which read and write files in the working copy so a body
is never in the model's context. ``spec_get``, ``spec_comment`` and ``spec_queue`` are merged tools
(``merged.py``). Deleting a spec (``spec_delete``) and dismissing a suggestion (``spec_comment`` op
dismiss) are superleader only; accept is the user's in the web UI.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

from .descriptions import DESCRIPTIONS
from .merged import Merged, Route

__all__ = ["SPEC_KEY_PATTERN", "SPEC_MERGED", "SPEC_PARAM", "spec_tools"]

_BASE = "/agent/v1"
SPEC_KEY_PATTERN = "^S-[1-9][0-9]{0,17}$"
_STATUS = ["draft", "review", "approved", "archived"]
_LABEL_RULE = (
    "Labels are trimmed and lowercased by the server and must then match ^[a-z0-9-_.:]{1,40}$."
)
_PROJECT_RULE = (
    "Project slug; see project. Agents work in their own projects only (home plus any "
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
            DESCRIPTIONS["spec_list"],
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
            "spec_create",
            "POST",
            "/specs",
            DESCRIPTIONS["spec_create"],
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
            DESCRIPTIONS["spec_update"],
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
            "spec_comments",
            "GET",
            "/specs/{spec}/comments",
            DESCRIPTIONS["spec_comments"],
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
    ]


_READ = _hints(read_only=True, idempotent=True)
SPEC_MERGED = (
    Merged(
        "spec_get",
        DESCRIPTIONS["spec_get"],
        {
            "spec": SPEC_PARAM,
            "path": _PATH,
            "versions": {"type": "boolean", "description": "List the versions of path."},
            "section": {"type": "string", "description": "Heading slug of a section of path."},
            "version": {"type": "integer", "minimum": 1, "description": "Document version."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "cursor": {"type": "integer", "description": "next_cursor of the last page."},
        },
        {
            "": Route("GET", "/specs/{spec}", ("spec",)),
            "versions": Route(
                "GET",
                "/specs/{spec}/docs/{path}/versions",
                ("spec", "path"),
                ("limit", "cursor"),
                query=("limit", "cursor"),
            ),
            "section": Route(
                "GET",
                "/specs/{spec}/docs/{path}/section",
                ("spec", "path", "section"),
                ("version",),
                query=("heading", "version"),
                rename={"section": "heading"},
            ),
        },
        scopes=("versions", "section"),
        required=("spec",),
        annotations=_READ,
    ),
    Merged(
        "spec_comment",
        DESCRIPTIONS["spec_comment"],
        {
            "op": {"type": "string", "enum": ["comment", "resolve", "dismiss"]},
            "spec": SPEC_PARAM,
            "id": {"type": "integer", "minimum": 1, "description": "Comment id."},
            "body": {"type": "string", "minLength": 1},
            "path": {"type": "string", "maxLength": 200},
            "version": {"type": "integer", "minimum": 1},
            "anchor": {"type": "string", "minLength": 1, "maxLength": 200},
            "suggestion": {"type": "string", "description": "Replacement text."},
            "client_key": {"type": "string", "description": "Idempotency key."},
        },
        {
            "comment": Route(
                "POST",
                "/specs/{spec}/comments",
                ("spec", "body"),
                ("path", "version", "anchor", "suggestion", "client_key"),
                body=True,
            ),
            "resolve": Route("POST", "/specs/{spec}/comments/{id}/resolve", ("spec", "id")),
            "dismiss": Route(
                "POST",
                "/admin/specs/{key}/comments/{comment_id}/dismiss",
                ("spec", "id"),
                rename={"spec": "key", "id": "comment_id"},
            ),
        },
        selectors=("op",),
        defaults={"op": "comment"},
        required=("spec",),
        annotations=_hints(),
    ),
    Merged(
        "spec_queue",
        DESCRIPTIONS["spec_queue"],
        {
            "project": {"type": "string", "description": _PROJECT_RULE},
            "move": {"type": "integer", "minimum": 1, "description": "Queue item id to move."},
            "position": {"type": "integer", "minimum": 1},
            "before": {"type": "integer", "minimum": 1},
        },
        {
            "": Route("GET", "/specs/queue", (), ("project",), query=("project",)),
            "move": Route(
                "POST",
                "/specs/queue/{id}/move",
                ("move",),
                ("position", "before"),
                body=True,
                rename={"move": "id"},
            ),
        },
        scopes=("move",),
        annotations=_hints(idempotent=True),
    ),
)
