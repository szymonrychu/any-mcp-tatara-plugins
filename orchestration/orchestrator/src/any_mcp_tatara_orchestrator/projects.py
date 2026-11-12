"""The ``project_list`` and ``project_summary`` tools: the projects a session may use.

A project is a named bucket every preset, session, task and spec belongs to. The orchestrator
answers with the caller's own projects, home first then any extra projects granted to the
session (archived or not); a superleader session also gets archived ones. The slugs are what
the ``project`` parameter of the task tools takes.
"""

from __future__ import annotations

from any_mcp.types import HttpBinding, ToolSpec

__all__ = ["project_tools"]

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

_BASE = "/agent/v1"


def project_tools(base_url: str) -> list[ToolSpec]:
    return [
        ToolSpec(
            name="project_list",
            description=(
                "List the projects you can use: your home project, then any extra projects "
                "granted to your session, in which you have the same access as their own "
                "members (archived or not); a superleader session gets every project, archived "
                "ones too. Each entry has slug, name, description and archived. The slug is the "
                "project parameter of task_create, task_update and task_list."
            ),
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            annotations=_READ_ONLY,
            http=HttpBinding("GET", f"{_BASE}/projects", base_url=base_url),
        ),
        ToolSpec(
            name="project_summary",
            description=(
                "Counts of tasks and specs per project within your project scope (your home "
                "project and extra projects; a superleader session sees every project): a "
                "quick overview before project_list and task_list. project narrows it to one "
                "project slug."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "project": {
                        "type": "string",
                        "description": "Project slug (see project_list); omitted: all in scope.",
                    }
                },
                "additionalProperties": False,
            },
            annotations=_READ_ONLY,
            http=HttpBinding(
                "GET", f"{_BASE}/projects/summary", query=("project",), base_url=base_url
            ),
        ),
    ]
