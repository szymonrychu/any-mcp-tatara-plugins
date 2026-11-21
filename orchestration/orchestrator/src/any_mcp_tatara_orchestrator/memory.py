"""The room memory tools: the notes of the chat room bound to the session.

The orchestrator relays them under ``/agent/v1/chat/notes`` to the chat connector, which
resolves the room from the caller session's active binding.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["SECTIONS", "memory_tools"]

_BASE = "/agent/v1/chat"
_VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "The version of this section you last read; 0 to create.",
}

SECTIONS = (
    "preferences",
    "requests",
    "work-in-progress",
    "provided-information",
    "repeatable-workflows",
)
_SECTION = {
    "type": "string",
    "enum": list(SECTIONS),
    "description": "The notes section: " + ", ".join(SECTIONS) + ".",
}

_GET_NOTES = (
    "Read the notes of your chat room (the room bound to this session). The notes have five "
    "sections: preferences, requests, work-in-progress, provided-information, "
    "repeatable-workflows. Without section it answers the room totals (chars, max_chars, "
    "section_max_chars), the five sections each with version, chars and inject, the rendered "
    "body and rule, the standing instructions for room notes. provided-information and "
    "repeatable-workflows are not in the first prompt: read them with section, which answers "
    "that section in full. Leader only. 409 room_unbound "
    'when this session has no chat room yet (it gets one with the first <chat-send room="..."> '
    "envelope in its answer or the first chat message)."
)
_UPDATE_NOTES = (
    "Replace one section of your chat room notes with body (the whole new text of that "
    "section, not a delta). expected_version is the version of that section from the last "
    "orchestrator_chat_notes_get or update (0 to create). On 409 version_conflict re-read "
    "that section and merge. 413 section_too_large: condense that section. 413 "
    "notes_too_large: condense another section. An empty body forgets that section. Never "
    "store secrets. Leader only."
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
    def bind(
        method: str, path: str, body: str | None = None, query: tuple[str, ...] = ()
    ) -> HttpBinding:
        return HttpBinding(method, f"{_BASE}{path}", query=query, body=body, base_url=base_url)

    return [
        ToolSpec(
            name="chat_notes_get",
            description=_GET_NOTES,
            input_schema=_schema({"section": _SECTION}, []),
            annotations=_annotations(read_only=True),
            http=bind("GET", "/notes", query=("section",)),
        ),
        ToolSpec(
            name="chat_notes_update",
            description=_UPDATE_NOTES,
            input_schema=_schema(
                {
                    "section": _SECTION,
                    "body": {"type": "string"},
                    "expected_version": _VERSION,
                },
                ["section", "body", "expected_version"],
            ),
            annotations=_annotations(read_only=False),
            http=bind("PUT", "/notes/sections/{section}", body="*"),
        ),
    ]
