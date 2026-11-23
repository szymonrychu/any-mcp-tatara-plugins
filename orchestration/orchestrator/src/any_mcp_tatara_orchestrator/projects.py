"""The ``project`` tool: the projects a session may use, and the superleader project writes.

A project is a named bucket every preset, session, task and spec belongs to. The orchestrator
answers with the caller's own projects, home first then any extra projects granted to the
session (archived or not); a superleader session also gets archived ones. The slugs are what
the ``project`` parameter of the task tools takes. ``project`` is a merged tool (``merged.py``).
"""

from __future__ import annotations

from .descriptions import DESCRIPTIONS
from .merged import Merged, Route

__all__ = ["PROJECT_MERGED"]

_SLUG = {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9-]{0,39}$",
    "description": "Project slug of an existing project (create: the new slug: lowercase "
    "letters, digits and hyphens, 1 to 40 characters, starting with a letter or digit).",
}
_GRANT = ("ref", "project", "reason")
PROJECT_MERGED = (
    Merged(
        "project",
        DESCRIPTIONS["project"],
        {
            "action": {
                "type": "string",
                "enum": ["list", "summary", "create", "update", "grant", "revoke"],
            },
            "target": {"type": "string", "enum": ["session", "preset"]},
            "ref": {"type": "string", "description": "Session slug, or preset id or name."},
            "project": _SLUG,
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
            "description": {"type": "string", "maxLength": 4000},
            "archived": {"type": "boolean"},
            "reason": {
                "type": "string",
                "minLength": 1,
                "maxLength": 4000,
                "description": "Why the grant is needed, shown to the user who approves it.",
            },
            "client_key": {"type": "string", "maxLength": 200},
        },
        {
            "list": Route("GET", "/projects"),
            "summary": Route("GET", "/projects/summary", (), ("project",), query=("project",)),
            "create": Route(
                "POST",
                "/admin/projects",
                ("project", "name"),
                ("description",),
                body=True,
                rename={"project": "slug"},
            ),
            "update": Route(
                "PATCH",
                "/admin/projects/{slug}",
                ("project",),
                ("name", "description", "archived"),
                body=True,
                rename={"project": "slug"},
            ),
            "grant+session": Route(
                "POST",
                "/admin/sessions/{slug}/projects",
                _GRANT,
                ("client_key",),
                body=True,
                rename={"ref": "slug"},
            ),
            "grant+preset": Route(
                "POST", "/admin/presets/{ref}/projects", _GRANT, ("client_key",), body=True
            ),
            "revoke+session": Route(
                "DELETE",
                "/admin/sessions/{slug}/projects/{project}",
                ("ref", "project"),
                rename={"ref": "slug"},
            ),
            "revoke+preset": Route(
                "DELETE", "/admin/presets/{ref}/projects/{project}", ("ref", "project")
            ),
        },
        selectors=("action", "target"),
        defaults={"action": "list"},
        annotations={"openWorldHint": False},
    ),
)
