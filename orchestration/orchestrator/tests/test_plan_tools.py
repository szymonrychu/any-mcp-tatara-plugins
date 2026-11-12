"""T-116 L4: the plan relay rows (header, steps, next step, gate, section)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.plan import plan_tools

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": "tok-for-tests",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}
ROWS = {
    "plan_get": ("GET", "/agent/v1/specs/{spec}/plan"),
    "plan_set": ("PUT", "/agent/v1/specs/{spec}/plan"),
    "plan_steps": ("GET", "/agent/v1/specs/{spec}/steps"),
    "plan_step_get": ("GET", "/agent/v1/tasks/{key}"),
    "plan_step_add": ("POST", "/agent/v1/specs/{spec}/steps"),
    "plan_step_edit": ("PATCH", "/agent/v1/tasks/{key}"),
    "plan_step_deps": ("PUT", "/agent/v1/tasks/{key}/deps"),
    "plan_steps_order": ("PUT", "/agent/v1/specs/{spec}/steps/order"),
    "plan_step_next": ("POST", "/agent/v1/specs/{spec}/steps/next"),
    "plan_step_update": ("PATCH", "/agent/v1/tasks/{key}"),
    "plan_gate": ("GET", "/agent/v1/specs/{spec}/gate"),
    "spec_section": ("GET", "/agent/v1/specs/{spec}/docs/{path}/section"),
}


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def test_rows_map_method_and_path() -> None:
    rows = {s.name: s for s in plan_tools("http://x")}
    assert set(rows) == set(ROWS)
    for name, (method, path) in ROWS.items():
        http = rows[name].http
        assert http is not None
        assert (http.method, http.path) == (method, path), name


def test_rows_are_served_by_the_vendored_relay_routes() -> None:
    for name, (method, path) in ROWS.items():
        route = "/agent/v1/tasks/{path}" if "/tasks" in path else "/agent/v1/specs/{path}"
        assert method.lower() in OPENAPI["paths"][route], name


def test_rows_are_ascii_described_and_closed() -> None:
    for s in plan_tools("http://x"):
        assert s.description.isascii() and s.description, s.name
        assert s.input_schema["additionalProperties"] is False, s.name
        for prop in s.input_schema["properties"].values():
            assert str(prop).isascii(), s.name


def test_read_rows_are_read_only() -> None:
    for s in plan_tools("http://x"):
        reads = s.http is not None and s.http.method == "GET"
        assert s.annotations["readOnlyHint"] is reads, s.name


def test_descriptions_state_the_stale_and_cap_rules() -> None:
    rows = {s.name: s for s in plan_tools("http://x")}
    assert "current_version" in rows["plan_set"].description
    assert "current_steps_version" in rows["plan_step_add"].description
    assert "32 KiB" in rows["plan_step_add"].description
    assert "no ready step" in rows["plan_step_next"].description
    assert "plan_steps_push" in rows["plan_step_add"].description


async def test_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"key": "T-5", "version": 2, "url": "u", "items": []})

    calls: list[tuple[str, dict[str, Any]]] = [
        ("plan_get", {"spec": "S-3"}),
        ("plan_set", {"spec": "S-3", "version": 1, "goal": "g", "constraints": ["c"]}),
        ("plan_steps", {"spec": "S-3"}),
        ("plan_step_get", {"key": "T-5"}),
        ("plan_step_add", {"spec": "S-3", "steps_version": 4, "after": 1, "title": "t"}),
        ("plan_step_edit", {"key": "T-5", "version": 2, "title": "n", "model": "sonnet"}),
        ("plan_step_deps", {"key": "T-5", "depends_on": ["T-4"]}),
        ("plan_steps_order", {"spec": "S-3", "steps_version": 4, "order": ["T-5", "T-4"]}),
        ("plan_step_next", {"spec": "S-3", "claim": True}),
        (
            "plan_step_update",
            {
                "key": "T-5",
                "version": 3,
                "status": "in_progress",
                "refs": [{"kind": "pr", "value": "x"}],
            },
        ),
        ("plan_gate", {"spec": "S-3"}),
        ("spec_section", {"spec": "S-3", "path": "plan/a.md", "heading": "goal", "version": 2}),
    ]
    async with harness(handler) as h:
        for name, args in calls:
            res = await h.call(f"orchestrator_{name}", args)
            assert not res.is_error, (name, res.text)
    assert [(m, p, q) for m, p, q, _ in seen] == [
        ("GET", "/agent/v1/specs/S-3/plan", ""),
        ("PUT", "/agent/v1/specs/S-3/plan", ""),
        ("GET", "/agent/v1/specs/S-3/steps", ""),
        ("GET", "/agent/v1/tasks/T-5", "events_limit=10&comments_limit=0"),
        ("POST", "/agent/v1/specs/S-3/steps", ""),
        ("PATCH", "/agent/v1/tasks/T-5", ""),
        ("PUT", "/agent/v1/tasks/T-5/deps", ""),
        ("PUT", "/agent/v1/specs/S-3/steps/order", ""),
        ("POST", "/agent/v1/specs/S-3/steps/next", ""),
        ("PATCH", "/agent/v1/tasks/T-5", ""),
        ("GET", "/agent/v1/specs/S-3/gate", ""),
        ("GET", "/agent/v1/specs/S-3/docs/plan/a.md/section", "heading=goal&version=2"),
    ]
    bodies = [b for _, _, _, b in seen]
    assert bodies[1] == {"version": 1, "goal": "g", "constraints": ["c"]}
    assert bodies[4] == {"steps_version": 4, "after": 1, "title": "t"}
    assert bodies[5] == {"version": 2, "title": "n", "model": "sonnet"}
    assert bodies[6] == {"depends_on": ["T-4"]}
    assert bodies[7] == {"steps_version": 4, "order": ["T-5", "T-4"]}
    assert bodies[8] == {"claim": True}
    assert bodies[9] == {
        "version": 3,
        "status": "in_progress",
        "refs": [{"kind": "pr", "value": "x"}],
    }


async def test_plan_step_next_passes_claim_false_by_default() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"key": "T-5", "step": 1})

    async with harness(handler) as h:
        res = await h.call("orchestrator_plan_step_next", {"spec": "S-3"})
    assert not res.is_error, res.text
    assert seen == [{"claim": False}]


async def test_plan_step_next_204_means_no_ready_step() -> None:
    async with harness(lambda r: httpx.Response(204)) as h:
        res = await h.call("orchestrator_plan_step_next", {"spec": "S-3", "claim": True})
    assert not res.is_error, res.text
    assert "no ready step" in res.text


async def test_plan_step_get_goes_through_the_task_window_fit() -> None:
    seen: list[str] = []
    task = {
        "key": "T-5",
        "title": "t",
        "status": "open",
        "version": 2,
        "url": "u",
        "body": "step body",
        "step": 2,
        "refs": [{"kind": "pr", "value": "x"}],
        "model": "sonnet",
        "comments": [{"id": 1, "body": "c"}],
        "events": [{"id": 1}],
        "events_total": 1,
        "comments_total": 1,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.query.decode())
        return httpx.Response(200, json=task)

    async with harness(handler) as h:
        res = await h.call("orchestrator_plan_step_get", {"key": "T-5"})
    assert not res.is_error, res.text
    out = json.loads(res.text)
    assert out["body"] == "step body" and "comments" not in out
    assert out["step"] == 2 and out["model"] == "sonnet" and out["refs"] == task["refs"]
    assert "events_limit" in seen[0] and "comments_limit=0" in seen[0]


async def test_step_writes_answer_a_short_confirmation() -> None:
    task = {"key": "T-5", "version": 3, "url": "u", "body": "x" * 5000, "comments": []}
    async with harness(lambda r: httpx.Response(200, json=task)) as h:
        for name, args in (
            ("plan_step_edit", {"key": "T-5", "version": 2, "title": "n"}),
            ("plan_step_update", {"key": "T-5", "version": 2, "status": "done"}),
            ("plan_step_deps", {"key": "T-5", "depends_on": []}),
        ):
            res = await h.call(f"orchestrator_{name}", args)
            assert json.loads(res.text) == {"key": "T-5", "version": 3, "url": "u"}, name


async def test_stale_plan_and_stale_steps_are_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        field = (
            "current_steps_version" if request.url.path.endswith("/steps") else "current_version"
        )
        return httpx.Response(
            412,
            json={"error": {"code": "stale_version", "message": "changed", field: 7}},
        )

    async with harness(handler) as h:
        plan = await h.call("orchestrator_plan_set", {"spec": "S-3", "version": 1})
        step = await h.call(
            "orchestrator_plan_step_add", {"spec": "S-3", "steps_version": 1, "title": "t"}
        )
    assert plan.is_error and "stale_version" in plan.text
    assert step.is_error and "stale_version" in step.text


def test_plan_gate_description_names_approvable() -> None:
    rows = {s.name: s for s in plan_tools("http://x")}
    desc = rows["plan_gate"].description
    assert "approvable" in desc
    assert "open_decisions" in desc
