"""Plan tools: the plan header and the ordered steps of a spec on the task board, relayed by the
orchestrator under ``/agent/v1/specs/{spec}/plan|steps|gate``.

A plan step is a task of the spec, so one step is read, edited and updated with the task tools
(task_get, task_update). ``plan_get`` is a merged tool (``merged.py``): its view picks the header,
the steps or the gate. The bulk write of a whole step set, the markdown export and the gate
questions are local tools (``plan_local.py``): their bodies would exceed the tool output budget
or need several calls.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

from .descriptions import DESCRIPTIONS
from .merged import Merged, Route
from .specs import SPEC_PARAM, _hints, _schema

__all__ = ["PLAN_MERGED", "plan_tools"]

_BASE = "/agent/v1"
_STALE_PLAN = (
    "version is the plan version you last read (plan_get; 0 before the first write); a stale "
    "one is refused (HTTP 412 stale_version with current_version): read again and retry."
)
_STRINGS = {
    "type": "array",
    "maxItems": 50,
    "items": {"type": "string", "maxLength": 1000},
}


def plan_tools(base_url: str) -> list[ToolSpec]:
    def row(
        name: str,
        method: str,
        path: str,
        description: str,
        schema: dict[str, Any],
        hints: dict[str, bool],
        body: str | None = None,
        query: tuple[str, ...] = (),
        output: dict[str, Any] | None = None,
    ) -> ToolSpec:
        return ToolSpec(
            name=name,
            description=description,
            input_schema=schema,
            output_schema=output,
            annotations=hints,
            http=HttpBinding(method, f"{_BASE}{path}", query=query, body=body, base_url=base_url),
        )

    return [
        row(
            "plan_set",
            "PUT",
            "/specs/{spec}/plan",
            DESCRIPTIONS["plan_set"],
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "version": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Version last read.",
                    },
                    "goal": {"type": "string", "maxLength": 500},
                    "architecture": {"type": "string", "maxLength": 4000},
                    "tech_stack": {"type": "string", "maxLength": 1000},
                    "constraints": {**_STRINGS, "description": "Global constraints."},
                    "review_focus": {**_STRINGS, "description": "What the final review checks."},
                },
                ("spec", "version"),
            ),
            _hints(idempotent=True),
            "*",
        ),
        row(
            "plan_step_next",
            "POST",
            "/specs/{spec}/steps/next",
            DESCRIPTIONS["plan_step_next"],
            _schema(
                {"spec": SPEC_PARAM, "claim": {"type": "boolean", "description": "Default false."}},
                ("spec",),
            ),
            _hints(),
            "*",
        ),
    ]


PLAN_MERGED = (
    Merged(
        "plan_get",
        DESCRIPTIONS["plan_get"],
        {
            "spec": SPEC_PARAM,
            "view": {"type": "string", "enum": ["header", "steps", "gate"]},
        },
        {
            "header": Route("GET", "/specs/{spec}/plan", ("spec",)),
            "steps": Route("GET", "/specs/{spec}/steps", ("spec",)),
            "gate": Route("GET", "/specs/{spec}/gate", ("spec",)),
        },
        selectors=("view",),
        defaults={"view": "header"},
        required=("spec",),
        annotations=_hints(read_only=True, idempotent=True),
    ),
)
