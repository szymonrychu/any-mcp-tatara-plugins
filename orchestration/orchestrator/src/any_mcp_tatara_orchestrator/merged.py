"""Merged tools: one tool whose arguments pick one of several orchestrator routes.

A ``Merged`` tool holds ``ops``, an op key mapped to a ``Route`` (method, path, required and
optional arguments). The op key is built from the call: the values of the tool's ``selectors``
(``action`` "claim") and then the names of its ``scopes`` that are set (``slug``), joined with
``+``: ``list``, ``list+slug``, ``meta``, or the empty key when nothing is set. ``resolve`` refuses
an unknown key, a missing required argument and an argument the op does not take, renames the
arguments the route names differently, and binds the call to the route. A scope argument the
route does not take (a flag such as ``meta``) only picks the op and is not sent. There is no
per-tool code: every merged tool is a declaration.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from any_mcp.errors import ToolInputError
from any_mcp.types import HttpBinding, ToolCall, ToolSpec

__all__ = ["Merged", "Route", "op_key"]

_BASE = "/agent/v1"


@dataclass(frozen=True)
class Route:
    """One op of a merged tool: an HTTP route below ``/agent/v1`` and the arguments it takes.

    ``query`` names route arguments sent as query parameters; ``body`` sends the arguments left
    after the path and the query as a JSON object. ``rename`` maps a tool argument to the name
    the route uses for it (``spec`` to ``key``). ``fill`` gives an argument the route takes a
    default when the call omits it (a lowered page limit, a graph depth)."""

    method: str
    path: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    query: tuple[str, ...] = ()
    body: bool = False
    rename: Mapping[str, str] = field(default_factory=dict)
    fill: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not set(self.fill) <= set(self.takes):
            raise ValueError(f"{self.path}: fill names an argument the route does not take")

    @property
    def takes(self) -> tuple[str, ...]:
        return self.required + self.optional


def _set(value: Any) -> bool:
    return value is not None and value is not False


def op_key(selectors: tuple[str, ...], scopes: tuple[str, ...], args: Mapping[str, Any]) -> str:
    """The op key of a call: selector values, then the names of the scopes that are set."""
    parts = [str(args[s]) for s in selectors if _set(args.get(s))]
    parts += [s for s in scopes if _set(args.get(s))]
    return "+".join(parts)


@dataclass(frozen=True)
class Merged:
    """A merged tool. ``defaults`` fills omitted selectors (``op`` "get") before the key is
    built; ``properties`` is the whole argument schema of the tool."""

    name: str
    description: str
    properties: Mapping[str, Any]
    ops: Mapping[str, Route]
    selectors: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    defaults: Mapping[str, str] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    annotations: Mapping[str, Any] | None = None
    output_schema: Mapping[str, Any] | None = None

    def spec(self, description: str | None = None) -> ToolSpec:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": dict(self.properties),
            "additionalProperties": False,
        }
        if self.required:
            schema["required"] = list(self.required)
        return ToolSpec(
            name=self.name,
            description=description or self.description,
            input_schema=schema,
            output_schema=self.output_schema,
            annotations=self.annotations,
        )

    def _valid(self) -> str:
        return ", ".join(k or "(none)" for k in self.ops)

    def key_of(self, arguments: Mapping[str, Any]) -> str:
        """The op key the arguments pick."""
        return op_key(self.selectors, self.scopes, {**self.defaults, **arguments})

    def resolve(self, call: ToolCall, base_url: str) -> ToolCall:
        """The call bound to the route its arguments pick, with the route's argument names."""
        args = {**self.defaults, **call.arguments}
        key = op_key(self.selectors, self.scopes, args)
        route = self.ops.get(key)
        if route is None:
            raise ToolInputError(
                [("", f"{self.name}: no op {key or '(none)'!r}; valid ops: {self._valid()}")]
            )
        missing = [(a, f"required by {self.name} op {key!r}") for a in route.required]
        missing = [m for m in missing if args.get(m[0]) is None]
        picking = set(self.selectors) | set(self.scopes)
        foreign = [
            (a, f"not taken by {self.name} op {key!r}")
            for a, v in args.items()
            if a not in route.takes and a not in picking and v is not None
        ]
        if missing or foreign:
            raise ToolInputError(missing + foreign)
        sent = {route.rename.get(a, a): v for a, v in args.items() if a in route.takes}
        sent |= {a: v for a, v in route.fill.items() if sent.get(a) is None}
        binding = HttpBinding(
            route.method,
            f"{_BASE}{route.path}",
            query=route.query,
            body="*" if route.body else None,
            base_url=base_url,
        )
        spec = dataclasses.replace(call.spec, http=binding)
        return dataclasses.replace(call, spec=spec, arguments=sent)
