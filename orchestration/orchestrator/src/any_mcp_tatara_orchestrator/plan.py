"""Plan tools: the plan header and the ordered steps of a spec on the task board, relayed by the
orchestrator under ``/agent/v1/specs/{spec}/plan|steps|gate`` and ``/agent/v1/tasks/{key}``.

A plan step is a task of the spec, so one step is read, edited and updated through the task
routes. The bulk write of a whole step set, the markdown export and the gate questions are local
tools (``plan_local.py``): their bodies would exceed the tool output budget or need several calls.
"""

from __future__ import annotations

from typing import Any

from any_mcp.types import HttpBinding, ToolSpec

from .specs import SPEC_PARAM, _hints, _schema
from .tasks import _ACK_OUT, _BEFORE, _KEY, _TASK_OUT, _TRIMMED, TASK_WINDOW_MAX
from .views import TASK_VIEWS

__all__ = ["PLAN_ROW_NAMES", "plan_tools"]

_BASE = "/agent/v1"
_MODELS = ["haiku", "sonnet", "opus", "fable"]
_STATUS = ["idea", "open", "in_progress", "done", "dropped"]
_STALE_PLAN = (
    "version is the plan version you last read (plan_get; 0 before the first write); a stale "
    "one is refused (HTTP 412 stale_version with current_version): read again and retry."
)
_STALE_STEPS = (
    "steps_version is the value you last read (plan_steps); a stale one is refused (HTTP 412 "
    "stale_version with current_steps_version): read again and retry."
)
_STEP_ACK = " Answers a short confirmation (key, version, url); read the step with plan_step_get."
_STRINGS = {
    "type": "array",
    "maxItems": 50,
    "items": {"type": "string", "maxLength": 1000},
}
_REF = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["pr", "commit", "branch", "tag", "url"]},
        "value": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "required": ["kind", "value"],
    "additionalProperties": False,
}
PLAN_ROW_NAMES = (
    "plan_get",
    "plan_set",
    "plan_steps",
    "plan_step_get",
    "plan_step_add",
    "plan_step_edit",
    "plan_step_deps",
    "plan_steps_order",
    "plan_step_next",
    "plan_step_update",
    "plan_gate",
    "spec_section",
)


def _window(text: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": 0, "maximum": TASK_WINDOW_MAX, "description": text}


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
            "plan_get",
            "GET",
            "/specs/{spec}/plan",
            "Read the plan header of a spec: goal, architecture, tech_stack, constraints, "
            "review_focus and version (0 and empty fields before the first plan_set). The "
            "steps are listed with plan_steps.",
            _schema({"spec": SPEC_PARAM}, ("spec",)),
            _hints(read_only=True, idempotent=True),
        ),
        row(
            "plan_set",
            "PUT",
            "/specs/{spec}/plan",
            "Write the plan header of a spec (replaces it whole): goal up to 500 characters, "
            "architecture up to 4000, tech_stack up to 1000, constraints and review_focus up to "
            "50 strings of up to 1000 each. " + _STALE_PLAN + " The steps are written with "
            "plan_steps_push.",
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
            "plan_steps",
            "GET",
            "/specs/{spec}/steps",
            "List the steps of a spec's plan in order, without bodies: key, step number, title, "
            "status, assignee_session, model, refs, version, ready (open and every dependency "
            "done or dropped), todo_total, todo_done and deps (task keys); plus steps_version. "
            "Read one step in full with plan_step_get.",
            _schema({"spec": SPEC_PARAM}, ("spec",)),
            _hints(read_only=True, idempotent=True),
        ),
        row(
            "plan_step_get",
            "GET",
            "/tasks/{key}",
            "Read one plan step (a task of the spec) with its body, step number, model, refs, "
            "depends_on and todo (the sub-steps). The view and the event and comment windows "
            "work as in task_get: summary (default) has the body, todo and the newest 10 "
            "events; todo, events, comments and open_decisions are the other views. " + _TRIMMED,
            _schema(
                {
                    "key": _KEY,
                    "view": {
                        "type": "string",
                        "enum": list(TASK_VIEWS),
                        "description": "What to read; default summary.",
                    },
                    "events_limit": _window("Newest N events (summary, events); 0 = none."),
                    "comments_limit": _window("Newest N comments (comments view)."),
                    "events_before": _BEFORE,
                    "comments_before": _BEFORE,
                },
                ("key",),
            ),
            _hints(read_only=True, idempotent=True),
            query=("events_limit", "events_before", "comments_limit", "comments_before"),
            output=_TASK_OUT,
        ),
        row(
            "plan_step_add",
            "POST",
            "/specs/{spec}/steps",
            "Insert one step after step number after (0: first; omitted: last); later steps "
            "are renumbered. body is markdown up to 32 KiB (a larger one is refused, HTTP 413 "
            "payload_too_large), depends_on lists step numbers. " + _STALE_STEPS + " To write "
            "many steps at once use plan_steps_push.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "steps_version": {"type": "integer", "minimum": 0},
                    "after": {"type": "integer", "minimum": 0, "description": "Step number."},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "body": {"type": "string", "description": "Markdown, up to 32 KiB."},
                    "depends_on": {"type": "array", "items": {"type": "integer", "minimum": 1}},
                    "model": {"type": "string", "enum": _MODELS},
                },
                ("spec", "steps_version", "title"),
            ),
            _hints(),
            "*",
        ),
        row(
            "plan_step_edit",
            "PATCH",
            "/tasks/{key}",
            "Edit one step: title, body (up to 32 KiB; HTTP 413 payload_too_large above it) and "
            "model (null clears it). version is the step version you last read (plan_step_get); "
            "a stale one is refused (HTTP 412 stale_version). Status and refs go through "
            "plan_step_update." + _STEP_ACK,
            _schema(
                {
                    "key": _KEY,
                    "version": {"type": "integer", "description": "Version last read."},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "body": {"type": "string", "description": "Markdown, up to 32 KiB."},
                    "model": {"type": ["string", "null"], "enum": [*_MODELS, None]},
                },
                ("key", "version"),
            ),
            _hints(idempotent=True),
            "*",
            output=_ACK_OUT,
        ),
        row(
            "plan_step_deps",
            "PUT",
            "/tasks/{key}/deps",
            "Set the dependencies of one step (replaces them): task keys of steps of the same "
            "spec. A set that forms a cycle is refused (HTTP 422 dependency_cycle)." + _STEP_ACK,
            _schema(
                {
                    "key": _KEY,
                    "depends_on": {
                        "type": "array",
                        "maxItems": 200,
                        "items": {"type": "string", "description": "Task key such as T-12."},
                    },
                },
                ("key", "depends_on"),
            ),
            _hints(idempotent=True),
            "*",
            output=_ACK_OUT,
        ),
        row(
            "plan_steps_order",
            "PUT",
            "/specs/{spec}/steps/order",
            "Reorder the steps: order lists every live step key exactly once (else HTTP 422). "
            "Answers the new steps_version. " + _STALE_STEPS,
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "steps_version": {"type": "integer", "minimum": 0},
                    "order": {"type": "array", "items": {"type": "string"}},
                },
                ("spec", "steps_version", "order"),
            ),
            _hints(idempotent=True),
            "*",
        ),
        row(
            "plan_step_next",
            "POST",
            "/specs/{spec}/steps/next",
            "Take the first ready step in order (open, every dependency done or dropped). With "
            "claim true the step is assigned to your session and set in_progress in one guarded "
            "update, so two implementers never get the same step; without claim it only shows "
            "it. When none is ready the answer says no ready step (HTTP 204 upstream). Read the "
            "step in full with plan_step_get.",
            _schema(
                {"spec": SPEC_PARAM, "claim": {"type": "boolean", "description": "Default false."}},
                ("spec",),
            ),
            _hints(),
            "*",
        ),
        row(
            "plan_step_update",
            "PATCH",
            "/tasks/{key}",
            "Record progress on one step: status, and refs (what implements it, replacing the "
            "list: up to 20 of kind pr, commit, branch, tag or url). version is the step "
            "version you last read; a stale one is refused (HTTP 412 stale_version)." + _STEP_ACK,
            _schema(
                {
                    "key": _KEY,
                    "version": {"type": "integer", "description": "Version last read."},
                    "status": {"type": "string", "enum": _STATUS},
                    "refs": {"type": "array", "maxItems": 20, "items": _REF},
                },
                ("key", "version"),
            ),
            _hints(idempotent=True),
            "*",
            output=_ACK_OUT,
        ),
        row(
            "plan_gate",
            "GET",
            "/specs/{spec}/gate",
            "Read the gate of a spec: its status, the approved document versions, the gate task "
            "(the improvement's task linked to the spec) and its open and answered decisions, and "
            "gate: none, waiting (a decision is open) or open (every gate decision answered). "
            "Ask the gate questions with plan_questions_ask.",
            _schema({"spec": SPEC_PARAM}, ("spec",)),
            _hints(read_only=True, idempotent=True),
        ),
        row(
            "spec_section",
            "GET",
            "/specs/{spec}/docs/{path}/section",
            "Read one section of a spec document by heading slug, under the tool output budget "
            "(truncated true when cut). version picks an older version of the document. Use "
            "spec_pull to copy a whole document to a file instead.",
            _schema(
                {
                    "spec": SPEC_PARAM,
                    "path": {
                        "type": "string",
                        "pattern": r"^[a-z0-9][a-z0-9._/-]{0,119}\.md$",
                        "description": "Document path inside the spec.",
                    },
                    "heading": {"type": "string", "description": "Heading slug."},
                    "version": {"type": "integer", "minimum": 1},
                },
                ("spec", "path", "heading"),
            ),
            _hints(read_only=True, idempotent=True),
            query=("heading", "version"),
        ),
    ]
