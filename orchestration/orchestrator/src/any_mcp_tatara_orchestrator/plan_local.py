"""Local plan tools: ``plan_steps_push``, ``plan_export`` and ``plan_questions_ask``.

``plan_steps_push`` writes a whole step set from a JSON file in the working copy, so step bodies
never pass through the model's context. ``plan_export`` writes the plan header and every step as
one markdown file for reading (a view, never pushed back). ``plan_questions_ask`` posts the gate
questions of a spec as decisions on its gate task, idempotent per question: a question that is
already open with the same options, or already answered, is not posted again, and a revised open
question is replaced. Paths are confined like ``spec_push``; results carry keys, ids and counts,
never a body.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

import httpx

from any_mcp.errors import UpstreamError
from any_mcp.types import HttpBinding, ToolSpec

from .specs import SPEC_PARAM, _hints, _schema
from .specs_local import (
    MAX_REQUEST_BYTES,
    SpecLocalError,
    _default_dir,
    _request,
    _sha,
    _spec_key,
    _workdir,
    _write_text,
    confine,
)

__all__ = ["local_plan_specs", "plan_export", "plan_questions_ask", "plan_steps_push"]

_BASE = "/agent/v1"
EXPORT_FILE = "plan-steps.md"
_NO_GATE = "link the improvement task to the spec first (task_update spec)"
_REVISED = "question revised"
_QUESTION_OPTION = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "minLength": 1, "maxLength": 200},
        "consequence": {
            "type": "string",
            "maxLength": 2000,
            "description": "What choosing it means.",
        },
        "recommended": {"type": "boolean"},
    },
    "required": ["label", "consequence"],
    "additionalProperties": False,
}
_QUESTION = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "minLength": 1, "maxLength": 2000},
        "context": {"type": "string"},
        "options": {"type": "array", "minItems": 2, "maxItems": 8, "items": _QUESTION_OPTION},
    },
    "required": ["question", "options"],
    "additionalProperties": False,
}


def local_plan_specs(base_url: str) -> list[ToolSpec]:
    # The HTTP bindings name the routes the tools use so the OpenAPI conformance test holds;
    # OrchestratorPlugin.call runs these tools itself and never sends the binding.
    return [
        ToolSpec(
            name="plan_steps_push",
            description=(
                "Write the whole step set of a spec's plan from a JSON file in your working copy: "
                '{"steps": [{"n", "title", "body", "depends_on": [n], "model"?, "labels"?, '
                '"todo"?: [text]}]}. n must run 1..N without gaps or repeats (checked before any '
                "request). The current steps_version is read first and sent with the set, so a "
                "plan changed meanwhile is refused (stale_version). Steps are keyed by n: new "
                "ones are created, changed ones updated, unstarted ones missing from the file "
                "are dropped; a started or done one missing is refused (step_in_progress). Each "
                "body is up to 32 KiB, the file up to 256 KiB encoded. Answers the step keys "
                "only, never a body. Write the file with your file tools; the header is written "
                "with plan_set."
            ),
            input_schema=_schema(
                {
                    "spec": SPEC_PARAM,
                    "file": {
                        "type": "string",
                        "description": "JSON file inside the workdir.",
                    },
                },
                ("spec", "file"),
            ),
            annotations=_hints(idempotent=True),
            http=HttpBinding("PUT", f"{_BASE}/specs/{{spec}}/steps", base_url=base_url),
        ),
        ToolSpec(
            name="plan_export",
            description=(
                "Write the plan header and every step, in order, with bodies and todo, as one "
                f"markdown file {EXPORT_FILE} under dir (default .scratchpad/<spec name>/spec/, "
                "inside the workdir) for reading with your file tools. It is a view: never push "
                "it back; edit steps with plan_step_edit or plan_steps_push. Answers the path "
                "and the step count, never the text."
            ),
            input_schema=_schema(
                {
                    "spec": SPEC_PARAM,
                    "dir": {"type": "string", "description": "Directory inside the workdir."},
                },
                ("spec",),
            ),
            annotations=_hints(idempotent=True),
            http=HttpBinding("GET", f"{_BASE}/specs/{{spec}}/steps", base_url=base_url),
        ),
        ToolSpec(
            name="plan_questions_ask",
            description=(
                "Ask the gate questions of a spec as decisions on its gate task (the "
                "improvement's task linked to the spec; task overrides it). Every question has "
                "2 to 8 options with a consequence each and exactly one recommended (mark the "
                "MVP option in its label); a call that breaks this is refused before any "
                "request. Safe to repeat, also from another session: a question already open "
                "with the same options is skipped, an answered one is never asked again "
                "(answered, not re-asked), an open one whose options changed is posted anew and "
                "the old one withdrawn as superseded. Answers a row per question: question, "
                "decision_id, action (posted, skipped, revised or answered). The user answers in "
                "the web UI; read the outcome with plan_gate."
            ),
            input_schema=_schema(
                {
                    "spec": SPEC_PARAM,
                    "task": {
                        "type": "string",
                        "description": "Gate task key; default the gate task of the spec.",
                    },
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": _QUESTION,
                    },
                },
                ("spec", "questions"),
            ),
            annotations=_hints(),
            http=HttpBinding("POST", f"{_BASE}/tasks/{{key}}/decisions", base_url=base_url),
        ),
    ]


def _task_key(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"T-[1-9][0-9]{0,17}", value):
        raise SpecLocalError("task_invalid")
    return value


def _load_steps(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, UnicodeDecodeError, ValueError):
        raise SpecLocalError("file_unreadable") from None
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        raise SpecLocalError('file_invalid: expected {"steps": [step objects]}')
    numbers = [s.get("n") for s in steps]
    if numbers != list(range(1, len(steps) + 1)) or not all(type(n) is int for n in numbers):
        raise SpecLocalError("steps_not_dense: n must run 1..N in order without gaps or repeats")
    return steps


def _encode(body: Mapping[str, Any]) -> bytes:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_REQUEST_BYTES:
        raise SpecLocalError("payload_too_large: split the plan or shorten the step bodies")
    return data


async def plan_steps_push(
    http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]
) -> dict[str, Any]:
    root = _workdir(cfg)
    key = _spec_key(args.get("spec"))
    steps = _load_steps(confine(args.get("file"), root))  # before any request
    _encode({"steps_version": 10**12, "steps": steps})  # an upper bound, before any request
    listed = (await _request(http, cfg, "GET", f"/specs/{key}/steps")).json()
    data = _encode({"steps_version": int(listed["steps_version"]), "steps": steps})
    raw = await _request(http, cfg, "PUT", f"/specs/{key}/steps", content=data)
    return {"spec": key, "keys": raw.json()["keys"]}


def _line(text: object) -> str:
    return " ".join(str(text).split())


def _render_header(plan: Mapping[str, Any]) -> list[str]:
    out = ["# Plan", "", "## Goal", "", str(plan.get("goal", "")), ""]
    out += ["## Architecture", "", str(plan.get("architecture", "")), ""]
    out += ["## Tech stack", "", str(plan.get("tech_stack", "")), ""]
    for title, field in (("Global constraints", "constraints"), ("Review focus", "review_focus")):
        out += [f"## {title}", ""]
        out += [f"- {_line(item)}" for item in plan.get(field, [])] or ["(none)"]
        out.append("")
    return out


def _render_step(item: Mapping[str, Any], task: Mapping[str, Any]) -> list[str]:
    out = [f"## Step {item['step']}: {_line(item['title'])}", ""]
    facts = [f"key {item['key']}", f"status {item['status']}"]
    if item.get("model"):
        facts.append(f"model {item['model']}")
    if item.get("deps"):
        facts.append("depends on " + ", ".join(item["deps"]))
    for ref in task.get("refs") or []:
        facts.append(f"{ref.get('kind')} {ref.get('value')}")
    out += ["; ".join(facts), "", str(task.get("body", "")).rstrip(), ""]
    todo = task.get("todo") or []
    if todo:
        out += [f"- [{'x' if i.get('status') == 'done' else ' '}] {_line(i['text'])}" for i in todo]
        out.append("")
    return out


async def plan_export(http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    root = _workdir(cfg)
    key = _spec_key(args.get("spec"))
    explicit = args.get("dir")
    directory = confine(explicit, root) if explicit else None  # before any request
    if directory is None:
        name = str((await _request(http, cfg, "GET", f"/specs/{key}")).json()["name"])
        directory = confine(_default_dir(name), root)
    plan = (await _request(http, cfg, "GET", f"/specs/{key}/plan")).json()
    items = (await _request(http, cfg, "GET", f"/specs/{key}/steps")).json()["items"]
    lines = _render_header(plan)
    for item in items:
        task = (
            await _request(
                http,
                cfg,
                "GET",
                f"/tasks/{_task_key(item['key'])}",
                params={"events_limit": 0, "comments_limit": 0},
            )
        ).json()
        lines += _render_step(item, task)
    text = "\n".join(lines).rstrip() + "\n"
    target = confine(os.path.join(directory, EXPORT_FILE), root)
    _write_text(target, text)
    return {"spec": key, "path": target, "steps": len(items), "size": len(text.encode())}


def _normalized(options: list[Mapping[str, Any]], *, consequence: str) -> list[dict[str, Any]]:
    return [
        {
            "label": o["label"],
            "description": o.get(consequence) or "",
            "recommended": bool(o.get("recommended")),
        }
        for o in options
    ]


def _check_questions(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise SpecLocalError("questions_required")
    out: list[dict[str, Any]] = []
    for item in raw:
        text = item.get("question") if isinstance(item, dict) else None
        options = item.get("options") if isinstance(item, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise SpecLocalError("question_invalid: every question needs text")
        if not isinstance(options, list) or not 2 <= len(options) <= 8:
            raise SpecLocalError(f"options_invalid: {text[:60]!r} needs 2 to 8 options")
        if not all(isinstance(o, dict) and o.get("label") for o in options):
            raise SpecLocalError(f"options_invalid: {text[:60]!r} has an option without a label")
        if sum(o.get("recommended") is True for o in options) != 1:
            raise SpecLocalError(
                f"options_invalid: {text[:60]!r} needs exactly one recommended option"
            )
        out.append(item)
    return out


def _digest(question: str, options: list[dict[str, Any]]) -> str:
    return _sha(question + json.dumps(options, sort_keys=True, ensure_ascii=False))[:12]


async def _gate_task(http: httpx.AsyncClient, cfg: Any, key: str, task: object) -> str:
    if task:
        return _task_key(task)
    gate = (await _request(http, cfg, "GET", f"/specs/{key}/gate")).json().get("gate_task")
    if not gate:
        raise SpecLocalError(_NO_GATE)
    return _task_key(gate)


def _row(question: str, decision_id: object, action: str) -> dict[str, Any]:
    row = {"question": question, "decision_id": decision_id, "action": action}
    if action == "answered":
        row["note"] = "answered, not re-asked"
    return row


async def plan_questions_ask(
    http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]
) -> list[dict[str, Any]]:
    key = _spec_key(args.get("spec"))
    questions = _check_questions(args.get("questions"))  # before any request
    gate = await _gate_task(http, cfg, key, args.get("task"))
    spec_number = key.split("-", 1)[1]
    task = (
        await _request(
            http, cfg, "GET", f"/tasks/{gate}", params={"events_limit": 0, "comments_limit": 0}
        )
    ).json()
    existing = [d for d in task.get("decisions", []) if d.get("status") != "withdrawn"]
    rows: list[dict[str, Any]] = []
    for item in questions:
        text = item["question"]
        options = _normalized(item["options"], consequence="consequence")
        same = [d for d in existing if d.get("question") == text]
        answered = next((d for d in same if d.get("status") == "answered"), None)
        open_ = [d for d in same if d.get("status") == "open"]
        if answered is not None:
            rows.append(_row(text, answered["id"], "answered"))
            continue
        current = next(
            (d for d in open_ if _normalized(d["options"], consequence="description") == options),
            None,
        )
        if current is not None:
            rows.append(_row(text, current["id"], "skipped"))
            continue
        body: dict[str, Any] = {
            "question": text,
            "options": options,
            "client_key": f"gate-{spec_number}-{_digest(text, options)}",
        }
        if item.get("context"):
            body["context"] = item["context"]
        created = (
            await _request(
                http, cfg, "POST", f"/tasks/{gate}/decisions", content=json.dumps(body).encode()
            )
        ).json()
        action = "posted"
        for old in open_:
            withdraw = {"reason": _REVISED, "superseded_by": created["id"]}
            try:
                await _request(
                    http,
                    cfg,
                    "POST",
                    f"/tasks/{gate}/decisions/{old['id']}/withdraw",
                    content=json.dumps(withdraw).encode(),
                )
                action = "revised"
            except UpstreamError as exc:
                if exc.response.status != 409:  # answered meanwhile: nothing to withdraw
                    raise
        rows.append(_row(text, created["id"], action))
    return rows
