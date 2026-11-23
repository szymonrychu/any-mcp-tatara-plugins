"""The room memory tool ``chat_notes``: the notes of a chat room.

The orchestrator relays the bound room's notes under ``/agent/v1/chat/notes`` to the chat
connector, which resolves the room from the caller session's active binding; another room's
notes go by room id under ``/agent/v1/admin/chats/rooms``. ``chat_notes`` is a merged tool
(``merged.py``).
"""

from __future__ import annotations

from .descriptions import DESCRIPTIONS
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
        DESCRIPTIONS["chat_notes"],
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
