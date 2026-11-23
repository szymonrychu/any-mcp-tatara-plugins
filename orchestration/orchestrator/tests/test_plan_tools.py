"""T-116 L4: the plan relay rows (plan_set, next step) and the merged plan_get."""

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
    "plan_set": ("PUT", "/agent/v1/specs/{spec}/plan"),
    "plan_step_next": ("POST", "/agent/v1/specs/{spec}/steps/next"),
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


async def test_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"key": "T-5", "version": 2})

    async with harness(handler) as h:
        for name, args in (
            ("plan_set", {"spec": "S-3", "version": 1, "goal": "g", "constraints": ["c"]}),
            ("plan_step_next", {"spec": "S-3", "claim": True}),
        ):
            res = await h.call(f"orchestrator_{name}", args)
            assert not res.is_error, (name, res.text)
    assert seen == [
        ("PUT", "/agent/v1/specs/S-3/plan", {"version": 1, "goal": "g", "constraints": ["c"]}),
        ("POST", "/agent/v1/specs/S-3/steps/next", {"claim": True}),
    ]


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


async def test_task_get_of_a_step_shows_the_step_fields() -> None:
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
        res = await h.call("orchestrator_task_get", {"key": "T-5"})
    assert not res.is_error, res.text
    out = json.loads(res.text)
    assert out["body"] == "step body" and "comments" not in out
    assert out["step"] == 2 and out["model"] == "sonnet" and out["refs"] == task["refs"]
    assert "events_limit" in seen[0] and "comments_limit=0" in seen[0]


async def test_stale_plan_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            412,
            json={"error": {"code": "stale_version", "message": "changed", "current_version": 7}},
        )

    async with harness(handler) as h:
        plan = await h.call("orchestrator_plan_set", {"spec": "S-3", "version": 1})
    assert plan.is_error and "stale_version" in plan.text
