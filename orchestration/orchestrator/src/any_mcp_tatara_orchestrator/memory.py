"""The room memory tool ``chat_notes``: the notes of a chat room.

The orchestrator relays the bound room's notes under ``/agent/v1/chat/notes`` to the chat
connector, which resolves the room from the caller session's active binding; another room's
notes go by room id under ``/agent/v1/admin/chats/rooms``. ``chat_notes`` is a merged tool
(``merged.py``).
"""

from __future__ import annotations

from .merged import Merged, Route

__all__ = ["MEMORY_MERGED", "SECTIONS"]

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

_ROOM_ID = {
    "type": "string",
    "description": "Chat room id of another room: one of your own or granted projects (a "
    "superleader: any room). Omitted: your bound room.",
}
_NOTE_ARGS = ("section", "body", "expected_version")

MEMORY_MERGED = (
    Merged(
        "chat_notes",
        "The notes of a chat room: your bound room, or room_id. Five sections: preferences, "
        "requests, work-in-progress, provided-information, repeatable-workflows. op get "
        "(default): without section the room totals (chars, max_chars, section_max_chars), "
        "each section's version, chars and inject, the rendered body and rule (the standing "
        "instructions for room notes); with section that section in full "
        "(provided-information and repeatable-workflows are not in the first prompt). Your bound "
        "room is leader only. op "
        "update (leader only): replace one section with body (the whole new text, not a "
        "delta; empty forgets it); expected_version is that section's version from your last "
        "read (0 to create). 409 version_conflict: re-read that section and merge. 413 "
        "section_too_large or notes_too_large: condense that section. 409 room_unbound when "
        'you have no bound room yet (it comes with the first <chat-send room="..."> '
        "envelope or chat message). Rules files in the notes apply from the next session of "
        "the room. Never store secrets.",
        {
            "op": {"type": "string", "enum": ["get", "update"]},
            "room_id": _ROOM_ID,
            "section": _SECTION,
            "body": {"type": "string"},
            "expected_version": _VERSION,
        },
        {
            "get": Route("GET", "/chat/notes", (), ("section",), query=("section",)),
            "get+room_id": Route(
                "GET",
                "/admin/chats/rooms/{room_id}/notes",
                ("room_id",),
                ("section",),
                query=("section",),
            ),
            "update": Route("PUT", "/chat/notes/sections/{section}", _NOTE_ARGS, body=True),
            "update+room_id": Route(
                "PUT",
                "/admin/chats/rooms/{room_id}/notes/sections/{section}",
                ("room_id", *_NOTE_ARGS),
                body=True,
            ),
        },
        selectors=("op",),
        scopes=("room_id",),
        defaults={"op": "get"},
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    ),
)
