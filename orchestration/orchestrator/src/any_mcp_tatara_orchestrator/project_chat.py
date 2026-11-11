"""The project room tools: ``project_chat_send`` and ``project_chat_read``.

A project room is the per-project channel between hive leaders, relayed by the orchestrator under
``/agent/v1/project/messages``. Leader only; a superleader session names the project.
"""

from __future__ import annotations

from any_mcp.types import HttpBinding, ToolSpec

from .common import HANDLED

__all__ = ["PROJECT_CHAT_READ_LIMIT", "project_chat_tools"]

_BASE = "/agent/v1"
PROJECT_CHAT_READ_LIMIT = 20
_PROJECT = {
    "type": "string",
    "description": "Project slug. Required for a superleader session; a leader's own project.",
}
_SEND = (
    "Post to your project's room. Leader only. The post is pushed as a turn to the leader of "
    "every other session of your project (never to a superleader); to narrows the push to those "
    "session slugs, but every leader of the project can still read it with "
    "orchestrator_project_chat_read. Asynchronous: returns post_id, delivered and skipped at "
    "once, never waits for answers. Post only what other hives need: what you take, what you "
    "finished, a question for the hive that owns something. Never post an acknowledgement or "
    "thanks. For one peer use orchestrator_session_send. reply_to names the post you answer "
    "(p<id> in the trailer). handled lists ids of incoming session messages this post answers "
    "or that need no answer. A superleader session must give project."
)
_READ = (
    "Read your project's room, newest first, limit posts per page (default 20, at most 100); "
    "next_before_id pages back, after_id reads posts newer than an id. Leader only. A "
    "superleader session must give project and may read any project."
)


def project_chat_tools(base_url: str) -> list[ToolSpec]:
    path = f"{_BASE}/project/messages"
    return [
        ToolSpec(
            name="project_chat_send",
            description=_SEND,
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "to": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 20,
                        "description": "Session slugs to push to; omitted: every other leader.",
                    },
                    "reply_to": {"type": "integer", "minimum": 1},
                    "handled": HANDLED,
                    "project": _PROJECT,
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            annotations={
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": False,
            },
            http=HttpBinding("POST", path, body="*", base_url=base_url),
        ),
        ToolSpec(
            name="project_chat_read",
            description=_READ,
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
