"""The ``project`` tool: the projects a session may use, and the superleader project writes.

A project is a named bucket every preset, session, task and spec belongs to. The orchestrator
answers with the caller's own projects, home first then any extra projects granted to the
session (archived or not); a superleader session also gets archived ones. The slugs are what
the ``project`` parameter of the task tools takes. ``project`` is a merged tool (``merged.py``).
"""

from __future__ import annotations

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
        "Projects. action list (default): the projects you can use: your home project, then the "
        "extra projects granted to your session, in which you have the same access as their "
        "own members (archived or not); a superleader gets every project. Each entry has slug, "
        "name, description and archived; the slug is the project parameter of the task tools. "
        "action summary: counts of tasks and specs per project in your scope (project narrows "
        "it to one). Superleader only: create (project, name, description; projects are never "
        "deleted, only archived; 409 project_exists), update (rename, describe, archive or "
        "unarchive; archiving default is 409 default_project), grant and revoke an extra "
        "project (target session or preset, ref its slug or preset id or name). A grant is a "
        "change proposal the user approves on the Proposals page (202 with the proposal, "
        "follow it with proposal_get; 200 with the first proposal on a client_key replay); "
        "withdraw it with proposal_put op withdraw and grant again. At most 9 extra projects. "
        "Grant errors: own_project, already_granted, unknown_project, project_archived, "
        "too_many_projects, not_found. A revoke applies at once; running sessions keep the "
        "extra projects a preset revoke removes. Writes on a target that is not manageable "
        "answer 403 not_manageable.",
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
