"""The mindmap tools: maps, nodes, edges, notes, links and directions relayed by the orchestrator.

The orchestrator relays ``/agent/v1/mindmap/{path}`` to the mindmap service (GET, POST, PATCH and
DELETE, an allowlisted route surface; anything else is 404). The plugin registers all 10 tools
statically, with no ``/meta`` lookup: the orchestrator's rendered ``allow`` list
(``orchestrator_allow`` in ``k8s/on_start.py``, ``MINDMAP_TOOLS``) decides whether a session sees
them, and it lists them only when the orchestrator has a mindmap upstream.

``mindmap_map``, ``mindmap_node``, ``mindmap_edge`` and ``mindmap_direction`` are merged tools
(``merged.py``): ``op`` picks the route. Two more tools cover more than one service route;
``route`` picks the binding from the arguments: ``mindmap_growth_area_update`` (add to a node by
``N-n`` key, patch one by ``G-n`` key) and ``mindmap_link`` (add to a map or node, remove by link
id). Their nominal binding is the first route.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from any_mcp.errors import ToolInputError
from any_mcp.types import HttpBinding, ToolCall, ToolSpec

from .descriptions import DESCRIPTIONS
from .merged import Merged, Route
from .slim import MAP_DEPTH

__all__ = ["MINDMAP_MERGED", "mindmap_tools", "route"]

_BASE = "/agent/v1/mindmap"
_M = {"type": "string", "pattern": "^M-[1-9][0-9]{0,17}$", "description": "Map key M-n."}
_N = {"type": "string", "pattern": "^N-[1-9][0-9]{0,17}$", "description": "Node key N-n."}
_T = {"type": "string", "pattern": "^T-[1-9][0-9]{0,17}$", "description": "Task key T-n."}
_PROJECT = {
    "type": "string",
    "pattern": "^[a-z0-9][a-z0-9-]{0,39}$",
    "description": "Project slug; omitted: your own project.",
}
_VERSION = {
    "type": "integer",
    "minimum": 1,
    "description": "The version you read; a stale one is refused 409 with the current version.",
}
_CLIENT_KEY = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "description": "Idempotency key: a retry with the same key returns the first result.",
}
_NODE_STATUS = ["seed", "growing", "settled", "stale", "archived"]
_EDGE_KINDS = ["relates_to", "builds_on", "derived_from", "contradicts", "supersedes", "see_also"]
_RELATIONS = ["researched_by", "informs", "grown_by", "implements", "cites", "uses"]
_LINK = {
    "type": "object",
    "properties": {
        "ref_key": {"type": "string", "pattern": "^(T|S)-[1-9][0-9]{0,17}$"},
        "relation": {"enum": _RELATIONS},
        "note": {"type": "string", "maxLength": 500},
    },
    "required": ["ref_key"],
    "additionalProperties": False,
}
_EDGE = {
    "type": "object",
    "properties": {
        "dst": _N,
        "kind": {"enum": _EDGE_KINDS},
        "note": {"type": "string", "maxLength": 500},
    },
    "required": ["dst"],
    "additionalProperties": False,
}
_NODE_FIELDS: dict[str, Any] = {
    "topic": {"type": "string", "minLength": 1, "maxLength": 200},
    "notes": {"type": "string", "maxLength": 65536, "description": "Markdown notes."},
    "summary": {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
        "description": "Change summary, kept in the node history.",
    },
    "parent": {**_N, "description": "Parent node N-n."},
}
_READ = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        out["required"] = required
    return out


def _tool(
    name: str,
    description: str,
    schema: dict[str, Any],
    http: HttpBinding,
    *,
    read: bool = False,
    destructive: bool = False,
) -> ToolSpec:
    hints = {**(_READ if read else _WRITE), "openWorldHint": False}
    if destructive:
        hints["destructiveHint"] = True
    return ToolSpec(
        name=name,
        description=description,
        input_schema=schema,
        annotations=hints,
        http=http,
    )


def mindmap_tools(base_url: str) -> list[ToolSpec]:
    def bind(
        method: str, path: str, *, query: tuple[str, ...] = (), body: str | None = None
    ) -> HttpBinding:
        return HttpBinding(method, f"{_BASE}{path}", query=query, body=body, base_url=base_url)

    return [
        _tool(
            "mindmap_search",
            DESCRIPTIONS["mindmap_search"],
            _schema(
                {
                    "q": {"type": "string", "minLength": 1},
                    "project": _PROJECT,
                    "map": _M,
                    "status": {"enum": _NODE_STATUS},
                    "scope": {"enum": ["own", "all"]},
                    "mode": {"enum": ["hybrid", "lexical", "semantic"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                ["q"],
            ),
            bind(
                "GET", "/search", query=("q", "project", "map", "status", "scope", "mode", "limit")
            ),
            read=True,
        ),
        _tool(
            "mindmap_traverse",
            DESCRIPTIONS["mindmap_traverse"],
            _schema(
                {
                    "key": _N,
                    "depth": {"type": "integer", "minimum": 0},
                    "kinds": {"type": "string", "maxLength": 200},
                    "follow": {"enum": ["out", "in", "both"]},
                    "tree": {"type": "boolean"},
                    "scope": {"enum": ["own", "all"]},
                },
                ["key"],
            ),
            bind(
                "GET", "/nodes/{key}/traverse", query=("depth", "kinds", "follow", "tree", "scope")
            ),
            read=True,
        ),
        _tool(
            "mindmap_note_put",
            DESCRIPTIONS["mindmap_note_put"],
            _schema(
                {
                    **_NODE_FIELDS,
                    "project": _PROJECT,
                    "links": {"type": "array", "items": _LINK, "maxItems": 20},
                    "edges": {"type": "array", "items": _EDGE, "maxItems": 20},
                    "client_key": _CLIENT_KEY,
                },
                ["topic", "notes", "summary"],
            ),
            bind("POST", "/knowledge/notes", body="*"),
        ),
        _tool(
            "mindmap_growth_area_update",
            DESCRIPTIONS["mindmap_growth_area_update"],
            _schema(
                {
                    "key": {
                        "type": "string",
                        "pattern": "^[NG]-[1-9][0-9]{0,17}$",
                        "description": "N-n to add a growth area, G-n to change one.",
                    },
                    "text": {"type": "string", "minLength": 1, "maxLength": 500},
                    "version": _VERSION,
                    "status": {"enum": ["open", "taken", "grown", "dropped"]},
                    "task_key": _T,
                    "grown_node": _N,
                    "position": {"type": "integer", "minimum": 0, "maximum": 10000},
                },
                ["key"],
            ),
            bind("POST", "/nodes/{key}/growth-areas", body="*"),
        ),
        _tool(
            "mindmap_link",
            DESCRIPTIONS["mindmap_link"],
            _schema(
                {
                    "action": {"enum": ["add", "remove"]},
                    "target": {"type": "string", "pattern": "^[MN]-[1-9][0-9]{0,17}$"},
                    "ref": {"type": "string", "pattern": "^(T|S)-[1-9][0-9]{0,17}$"},
                    "relation": {"enum": _RELATIONS},
                    "note": {"type": "string", "maxLength": 500},
                    "link_id": {"type": "integer", "minimum": 1},
                },
                ["action"],
            ),
            bind("POST", "/maps/{target}/links", body="*"),
            destructive=True,
        ),
        _tool(
            "mindmap_task_nodes",
            DESCRIPTIONS["mindmap_task_nodes"],
            _schema({"key": {"type": "string", "pattern": "^(T|S)-[1-9][0-9]{0,17}$"}}, ["key"]),
            bind("GET", "/links", query=("key",)),
            read=True,
        ),
    ]


def _need(arguments: dict[str, Any], *names: str) -> None:
    missing = [(f"/{n}", "required here") for n in names if arguments.get(n) is None]
    if missing:
        raise ToolInputError(missing)


def route(call: ToolCall) -> ToolCall:
    """Pick the service route of a tool that has more than one, from its arguments."""
    name = call.spec.name
    http = call.spec.http
    if http is None or name not in _ROUTED:
        return call
    args = dict(call.arguments)

    def to(method: str, path: str, *, body: bool = True, **rest: Any) -> ToolCall:
        spec = dataclasses.replace(
            call.spec,
            http=HttpBinding(
                method, f"{_BASE}{path}", body="*" if body else None, base_url=http.base_url
            ),
        )
        return dataclasses.replace(call, spec=spec, arguments=rest)

    if name == "mindmap_growth_area_update":
        key = args.pop("key")
        if key.startswith("N-"):
            _need(args, "text")
            return to("POST", "/nodes/{key}/growth-areas", key=key, text=args["text"])
        _need(args, "version")
        return to("PATCH", "/growth-areas/{key}", key=key, **args)
    # mindmap_link
    action = args.pop("action")
    if action == "remove":
        _need(args, "link_id")
        return to("DELETE", "/links/{link_id}", body=False, link_id=args["link_id"])
    _need(args, "target", "ref")
    target = args.pop("target")
    rest = {"ref_key": args.pop("ref"), **{k: v for k, v in args.items() if k != "link_id"}}
    path = "/maps/{target}/links" if target.startswith("M-") else "/nodes/{target}/links"
    return to("POST", path, target=target, **rest)


_ROUTED = frozenset({"mindmap_growth_area_update", "mindmap_link"})


_MM = "/mindmap"
_OP_WRITE = {**_WRITE, "openWorldHint": False}
_DIRECTION_STATUS = ["open", "acknowledged", "resolved", "withdrawn"]
_SEEDS = {
    "type": "array",
    "items": {"type": "string", "minLength": 1, "maxLength": 500},
    "maxItems": 50,
}
_NODE_SET = ("notes", "summary", "parent", "status")
_NODE_UPDATE = Route(
    "PATCH", f"{_MM}/nodes/{{key}}", ("key", "version"), ("topic", *_NODE_SET), body=True
)
_NODE_VERSION = Route("GET", f"{_MM}/nodes/{{key}}/versions/{{version}}", ("key", "version"))
MINDMAP_MERGED: tuple[Merged, ...] = (
    Merged(
        "mindmap_map",
        DESCRIPTIONS["mindmap_map"],
        {
            "op": {"type": "string", "enum": ["list", "get", "create", "update"]},
            "key": _M,
            "project": _PROJECT,
            "status": {"enum": ["active", "archived"]},
            "kind": {"enum": ["research", "knowledge"]},
            "depth": {"type": "integer", "minimum": 0},
            "slug": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{0,63}$"},
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string", "maxLength": 8192},
            "root_topic": {"type": "string", "minLength": 1, "maxLength": 200},
            "version": _VERSION,
            "client_key": _CLIENT_KEY,
        },
        {
            "list": Route(
                "GET",
                f"{_MM}/maps",
                (),
                ("project", "status", "kind"),
                query=("project", "status", "kind"),
            ),
            "get": Route(
                "GET",
                f"{_MM}/maps/{{key}}/graph",
                ("key",),
                ("depth",),
                query=("depth",),
                fill={"depth": MAP_DEPTH},
            ),
            "create": Route(
                "POST",
                f"{_MM}/maps",
                ("project", "slug", "title"),
                ("kind", "description", "root_topic", "client_key"),
                body=True,
            ),
            "update": Route(
                "PATCH",
                f"{_MM}/maps/{{key}}",
                ("key", "version"),
                ("title", "description", "status"),
                body=True,
            ),
        },
        selectors=("op",),
        required=("op",),
        annotations=_OP_WRITE,
    ),
    Merged(
        "mindmap_node",
        DESCRIPTIONS["mindmap_node"],
        {
            "op": {"type": "string", "enum": ["get", "history", "create", "update"]},
            "key": {
                "type": "string",
                "pattern": "^[MN]-[1-9][0-9]{0,17}$",
                "description": "Node key N-n (op create: the map key M-n).",
            },
            "version": {
                "type": "integer",
                "minimum": 1,
                "description": "op history: the version to read; op update: the version you "
                "read (a stale one is refused 409 with the current version).",
            },
            **_NODE_FIELDS,
            "status": {"enum": _NODE_STATUS},
            "growth_areas": _SEEDS,
            "links": {"type": "array", "items": _LINK, "maxItems": 20},
            "edges": {"type": "array", "items": _EDGE, "maxItems": 20},
            "client_key": _CLIENT_KEY,
        },
        {
            "get": Route("GET", f"{_MM}/nodes/{{key}}", ("key",)),
            "history": Route("GET", f"{_MM}/nodes/{{key}}/versions", ("key",)),
            "history+version": _NODE_VERSION,
            "create": Route(
                "POST",
                f"{_MM}/maps/{{key}}/nodes",
                ("key", "topic"),
                (*_NODE_SET, "growth_areas", "links", "edges", "client_key"),
                body=True,
            ),
            # Without version the update op still resolves, to refuse the missing version.
            "update": _NODE_UPDATE,
            "update+version": _NODE_UPDATE,
        },
        selectors=("op",),
        scopes=("version",),
        required=("op", "key"),
        annotations=_OP_WRITE,
    ),
    Merged(
        "mindmap_edge",
        DESCRIPTIONS["mindmap_edge"],
        {
            "op": {"type": "string", "enum": ["create", "delete"]},
            "src": _N,
            "dst": _N,
            "kind": {"enum": _EDGE_KINDS},
            "note": {"type": "string", "maxLength": 500},
            "key": {"type": "string", "pattern": "^E-[1-9][0-9]{0,17}$"},
        },
        {
            "create": Route("POST", f"{_MM}/edges", ("src", "dst"), ("kind", "note"), body=True),
            "delete": Route("DELETE", f"{_MM}/edges/{{key}}", ("key",)),
        },
        selectors=("op",),
        required=("op",),
        annotations={**_OP_WRITE, "destructiveHint": True},
    ),
    Merged(
        "mindmap_direction",
        DESCRIPTIONS["mindmap_direction"],
        {
            "op": {"type": "string", "enum": ["list", "create", "update"]},
            "key": {
                "type": "string",
                "pattern": "^[MD]-[1-9][0-9]{0,17}$",
                "description": "op create: map key M-n; op update: direction key D-n.",
            },
            "project": _PROJECT,
            "map": _M,
            "status": {"enum": _DIRECTION_STATUS},
            "text": {"type": "string", "minLength": 1, "maxLength": 8192},
            "node": _N,
            "task": _T,
            "growth_areas": {**_SEEDS, "maxItems": 20},
            "version": _VERSION,
            "action": {"enum": ["acknowledge", "resolve", "withdraw"]},
            "resolution": {"type": "string", "maxLength": 4096},
        },
        {
            "list": Route(
                "GET",
                f"{_MM}/directions",
                (),
                ("project", "map", "status"),
                query=("project", "map", "status"),
            ),
            "create": Route(
                "POST",
                f"{_MM}/maps/{{key}}/directions",
                ("key", "text"),
                ("node", "task", "growth_areas"),
                body=True,
            ),
            "update": Route(
                "PATCH",
                f"{_MM}/directions/{{key}}",
                ("key", "version", "action"),
                ("resolution",),
                body=True,
            ),
        },
        selectors=("op",),
        required=("op",),
        annotations=_OP_WRITE,
    ),
)
