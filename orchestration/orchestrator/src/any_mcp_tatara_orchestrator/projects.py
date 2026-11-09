"""The ``project_list`` tool: the projects a session may use, from ``GET /agent/v1/projects``.

A project is a named bucket every preset, session, task and spec belongs to. The orchestrator
answers with the caller's own project (archived or not); a superleader session gets every active
project. The slugs are what the ``project`` parameter of the task tools takes.
"""

from __future__ import annotations

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["project_tools"]

_BASE = "/agent/v1"


def project_tools(base_url: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="project_list",
            description=(
                "List the projects you can use: your own project (archived or not); a "
                "superleader session gets every active project. Each entry has slug, name, "
                "description and archived. The slug is the project parameter of task_create, "
                "task_update and task_list."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            annotations={
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
            http=HttpBinding("GET", f"{_BASE}/projects", base_url=base_url),
        )
    ]
