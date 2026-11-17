"""The room memory tools: the notes of the chat room bound to the session.

The orchestrator relays them under ``/agent/v1/chat/notes`` to the chat connector, which
resolves the room from the caller session's active binding; the room is x
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["memory_tools"]

_BASE = "/agent/v1/chat"
_VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "The version you last read; 0 to create.",
}

_GET_NOTES = (
    "Read the notes of your chat room (the room bound to this session): requests, data the user "
    "gave, work in progress and repeatable workflows saved by earlier sessions of the room, plus "
    "rule, the standing instructions for room notes. Leader only. Answers room_id, "
    "body, version, chars, max_chars and rule; version 0 means no notes yet. 409 room_unbound "
    'when this session has no chat room yet (it gets one with the first <chat-send room="..."> '
    "envelope in its answer or the first chat message)."
)
_UPDATE_NOTES = (
    "Replace the notes of your chat room with body (the whole new text, not a delta). Pass "
    "expected_version from the last orchestrator_chat_notes_get or update (0 to create). On 409 "
    "version_conflict re-read, merge and retry. At most max_chars characters (413 "
    "notes_too_large): condense, keep sections Preferences, Requests, Work in progress, Provided "
    "information, Repeatable workflows. An empty body forgets everything. Never store secrets. "
    "Leader only."
)


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _annotations(*, read_only: bool, destructive: bool = False) -> dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "destructiveHint": destructive,
        "idempotentHint": True,
        "openWorldHint": False,
    }


def memory_tools(base_url: str) -> list[ToolSpec]:
    def bind(method: str, path: str, body: str | None = None) -> HttpBinding:
        return HttpBinding(method, f"{_BASE}{path}", body=body, base_url=base_url)

    return [
        ToolSpec(
            name="chat_notes_get",
            description=_GET_NOTES,
            input_schema=_schema({}, []),
            annotations=_annotations(read_only=True),
            http=bind("GET", "/notes"),
        ),
        ToolSpec(
            name="chat_notes_update",
            description=_UPDATE_NOTES,
            input_schema=_schema(
                {"body": {"type": "string"}, "expected_version": _VERSION},
                ["body", "expected_version"],
            ),
            annotations=_annotations(read_only=False),
            http=bind("PUT", "/notes", body="*"),
        ),
    ]
