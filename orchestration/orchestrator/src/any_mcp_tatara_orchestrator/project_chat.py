"""The project room read tool: ``project_chat_read``.

A project room is the per-project channel between hive leaders, relayed by the orchestrator under
``/agent/v1/project/messages``. Leader only; a superleader session names the project. Posting is
a ``<project-post>`` envelope, not a tool.
"""

from __future__ import annotations

from any_mcp.types import HttpBinding, ToolSpec

from .descriptions import DESCRIPTIONS

__all__ = ["PROJECT_CHAT_READ_LIMIT", "project_chat_tools"]

_BASE = "/agent/v1"
PROJECT_CHAT_READ_LIMIT = 20
_PROJECT = {
    "type": "string",
    "description": "Project slug. Required for a superleader session; a leader may name any of its "
    "projects (home or granted), default its home project.",
}


def project_chat_tools(base_url: str) -> list[ToolSpec]:
    path = f"{_BASE}/project/messages"
    return [
        ToolSpec(
            name="project_chat_read",
            description=DESCRIPTIONS["project_chat_read"],
            input_schema={
                "type": "object",
                "properties": {
                    "project": _PROJECT,
                    "before_id": {"type": "integer", "minimum": 1},
                    "after_id": {"type": "integer", "minimum": 1},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            http=HttpBinding(
                "GET",
                path,
                query=("project", "before_id", "after_id", "limit"),
                base_url=base_url,
            ),
        ),
    ]
