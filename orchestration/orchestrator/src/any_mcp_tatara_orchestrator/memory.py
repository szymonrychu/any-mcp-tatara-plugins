"""The room memory tools: notes and skills of the chat room bound to the session.

The orchestrator relays them under ``/agent/v1/chat/notes`` and ``/agent/v1/chat/skills`` to the
chat connector, which resolves the room from the caller session's active binding; the room is
never a parameter. Leader only. Hand-written ToolSpecs (as ``projects.py``) because the
annotations differ per tool.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["memory_tools"]

_BASE = "/agent/v1/chat"
_NAME = {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9-]{0,63}$",
    "description": "Skill name: lowercase letters, digits and hyphens (a SKILL.md directory name).",
}
_VERSION = {
    "type": "integer",
    "minimum": 0,
    "description": "The version you last read; 0 to create.",
}

_GET_NOTES = (
    "Read the notes of your chat room (the room bound to this session): requests, data the user "
    "gave, work in progress and repeatable workflows saved by earlier sessions of the room, plus "
    "rule, the standing instructions for room notes and skills. Leader only. Answers room_id, "
    "body, version, chars, max_chars and rule; version 0 means no notes yet. 409 room_unbound "
    "when this session has no chat room yet (it gets one with its first orchestrator_chat_send "
    "or the first chat message)."
)
_UPDATE_NOTES = (
    "Replace the notes of your chat room with body (the whole new text, not a delta). Pass "
    "expected_version from the last orchestrator_chat_notes_get or update (0 to create). On 409 "
    "version_conflict re-read, merge and retry. At most max_chars characters (413 "
    "notes_too_large): condense, keep sections Preferences, Requests, Work in progress, Provided "
    "information, Repeatable workflows. An empty body forgets everything. Never store secrets. "
    "Leader only."
)
_LIST_SKILLS = (
    "List the skills saved for your chat room: name, description, version, updated_at and chars "
    "of each, plus max_skills and max_chars. Leader only. 409 room_unbound when this session has "
    "no chat room yet."
)
_GET_SKILL = (
    "Read one skill of your chat room by name: description and body (the steps). Write it to "
    "$CLAUDE_CONFIG_DIR/skills/<name>/SKILL.md (frontmatter name and description, then the "
    "body), never into the working directory. Leader only. 404 skill_not_found."
)
_SAVE_SKILL = (
    "Create or replace a skill of your chat room: a workflow that was repeated or is clearly "
    "reusable. name is lowercase letters, digits and hyphens; description says when to use it "
    "(at most 1024 characters); body holds the steps with their inputs (at most max_chars). "
    "expected_version is 0 to create, else the version you read (409 version_conflict: re-read "
    "and merge). 409 skill_limit when the room has max_skills skills. Tell the user in the chat "
    "when you save one. Never store secrets. Leader only."
)
_DELETE_SKILL = (
    "Delete a skill of your chat room by name, when the user asks or it is obsolete; tell the "
    "user in the chat. Also remove its SKILL.md from $CLAUDE_CONFIG_DIR/skills. Leader only. "
    "404 skill_not_found."
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
        ToolSpec(
            name="chat_skills_list",
            description=_LIST_SKILLS,
            input_schema=_schema({}, []),
            annotations=_annotations(read_only=True),
            http=bind("GET", "/skills"),
        ),
        ToolSpec(
            name="chat_skills_get",
            description=_GET_SKILL,
            input_schema=_schema({"name": _NAME}, ["name"]),
            annotations=_annotations(read_only=True),
            http=bind("GET", "/skills/{name}"),
        ),
        ToolSpec(
            name="chat_skills_save",
            description=_SAVE_SKILL,
            input_schema=_schema(
                {
                    "name": _NAME,
                    "description": {"type": "string", "minLength": 1, "maxLength": 1024},
                    "body": {"type": "string", "minLength": 1},
                    "expected_version": _VERSION,
                },
                ["name", "description", "body", "expected_version"],
            ),
            annotations=_annotations(read_only=False),
            http=bind("PUT", "/skills/{name}", body="*"),
        ),
        ToolSpec(
            name="chat_skills_delete",
            description=_DELETE_SKILL,
            input_schema=_schema({"name": _NAME}, ["name"]),
            annotations=_annotations(read_only=False, destructive=True),
            http=bind("DELETE", "/skills/{name}"),
        ),
    ]
