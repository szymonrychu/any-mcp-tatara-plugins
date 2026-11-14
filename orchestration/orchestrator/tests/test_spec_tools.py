"""T-116 C1: the spec relay rows and the spec parameter of the task tools."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.specs import spec_tools

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": "tok-for-tests",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
}
ROWS = {
    "spec_list": ("GET", "/agent/v1/specs"),
    "spec_get": ("GET", "/agent/v1/specs/{spec}"),
    "spec_create": ("POST", "/agent/v1/specs"),
    "spec_update": ("PATCH", "/agent/v1/specs/{spec}"),
    "spec_versions": ("GET", "/agent/v1/specs/{spec}/docs/{path}/versions"),
    "spec_comment": ("POST", "/agent/v1/specs/{spec}/comments"),
    "spec_comments": ("GET", "/agent/v1/specs/{spec}/comments"),
    "spec_comment_resolve": ("POST", "/agent/v1/specs/{spec}/comments/{id}/resolve"),
    "spec_queue_list": ("GET", "/agent/v1/specs/queue"),
    "spec_queue_move": ("POST", "/agent/v1/specs/queue/{id}/move"),
}


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def test_rows_map_method_and_path() -> None:
    rows = {s.name: s for s in spec_tools("http://x")}
    assert set(rows) == set(ROWS)
    for name, (method, path) in ROWS.items():
        http = rows[name].http
        assert http is not None
        assert (http.method, http.path) == (method, path), name


def test_rows_are_served_by_the_vendored_relay_routes() -> None:
    # The orchestrator relay is two catch-all routes: the collection and /specs/{path}.
    for name, (method, path) in ROWS.items():
        route = "/agent/v1/specs" if path == "/agent/v1/specs" else "/agent/v1/specs/{path}"
        assert method.lower() in OPENAPI["paths"][route], name


def test_rows_are_ascii_described_and_closed() -> None:
    for s in spec_tools("http://x"):
        assert s.description.isascii() and s.description, s.name
        assert s.input_schema["additionalProperties"] is False, s.name
        for prop in s.input_schema["properties"].values():
            assert str(prop).isascii(), s.name


def test_no_row_takes_a_document_body() -> None:
    for s in spec_tools("http://x"):
        assert "docs" not in s.input_schema["properties"], s.name
        assert "body" not in s.input_schema["properties"] or s.name == "spec_comment", s.name


def test_spec_update_description_says_when_agents_approve() -> None:
    rows = {s.name: s for s in spec_tools("http://x")}
    desc = rows["spec_update"].description
    for needle in ("approved", "plan_gate", "approvable", "409 decisions_open"):
        assert needle in desc
    assert "set by the user only" not in desc
    props = rows["spec_update"].input_schema["properties"]
    assert "approved" in props["status"]["enum"]
    assert list(rows["spec_update"].input_schema["required"]) == ["spec", "version"]


def test_read_rows_are_read_only() -> None:
    for s in spec_tools("http://x"):
        reads = s.http is not None and s.http.method == "GET"
        assert s.annotations["readOnlyHint"] is reads, s.name


async def test_rows_call_their_routes() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"key": "S-3", "items": []})

    calls: list[tuple[str, dict[str, Any]]] = [
        ("spec_list", {"project": "p", "status": "review", "name": "n", "limit": 5, "cursor": "c"}),
        ("spec_get", {"spec": "S-3"}),
        ("spec_create", {"project": "p", "name": "my-spec", "title": "T", "labels": ["a"]}),
        ("spec_update", {"spec": "S-3", "version": 2, "title": "N", "labels": ["b"]}),
        ("spec_versions", {"spec": "S-3", "path": "plan/a.md", "limit": 3}),
        ("spec_comment", {"spec": "S-3", "body": "hm", "path": "a.md", "version": 1}),
        ("spec_comments", {"spec": "S-3", "path": "a.md", "open": True}),
        ("spec_comment_resolve", {"spec": "S-3", "id": 7}),
    ]
    async with harness(handler) as h:
        for name, args in calls:
            res = await h.call(f"orchestrator_{name}", args)
            assert not res.is_error, (name, res.text)
    assert seen == [
        ("GET", "/agent/v1/specs", "project=p&status=review&name=n&limit=5&cursor=c", None),
        ("GET", "/agent/v1/specs/S-3", "", None),
        (
            "POST",
            "/agent/v1/specs",
            "",
            {"project": "p", "name": "my-spec", "title": "T", "labels": ["a"]},
        ),
        ("PATCH", "/agent/v1/specs/S-3", "", {"version": 2, "title": "N", "labels": ["b"]}),
        ("GET", "/agent/v1/specs/S-3/docs/plan/a.md/versions", "limit=3", None),
        (
            "POST",
            "/agent/v1/specs/S-3/comments",
            "",
            {"body": "hm", "path": "a.md", "version": 1},
        ),
        ("GET", "/agent/v1/specs/S-3/comments", "path=a.md&open=true", None),
        ("POST", "/agent/v1/specs/S-3/comments/7/resolve", "", None),
    ]


async def test_approved_is_sent_unchanged_and_the_409_is_surfaced() -> None:
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "decisions_open",
                    "message": "open decisions on linked tasks",
                    "decisions": [{"task": "T-5", "id": 9, "question": "Q?"}],
                }
            },
        )

    async with harness(handler) as h:
        res = await h.call(
            "orchestrator_spec_update", {"spec": "S-3", "version": 1, "status": "approved"}
        )
    assert seen == [{"version": 1, "status": "approved"}]
    assert res.is_error
    assert "decisions_open" in res.text
    assert "T-5" in res.text


@pytest.mark.parametrize("name", ["task_create", "task_update", "task_list"])
async def test_task_tools_declare_spec(name: str) -> None:
    async with harness(lambda r: httpx.Response(200, json={})) as h:
        tools = {t["name"]: t for t in await h.list_tools()}
    prop = tools[f"orchestrator_{name}"]["inputSchema"]["properties"]["spec"]
    assert "spec_get" in prop["description"]
    assert "spec" not in tools[f"orchestrator_{name}"]["inputSchema"].get("required", [])
    pattern = prop.get("pattern") or next(
        (o["pattern"] for o in prop.get("anyOf", []) if "pattern" in o), None
    )
    assert pattern and re.fullmatch(pattern, "S-12") and not re.fullmatch(pattern, "T-12")


async def test_task_tools_pass_spec() -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json={"key": "T-5", "url": "https://x/tasks/T-5"})

    async with harness(handler) as h:
        await h.call("orchestrator_task_create", {"title": "t", "client_key": "k", "spec": "S-3"})
        await h.call("orchestrator_task_update", {"key": "T-5", "version": 1, "spec": "S-3"})
        await h.call("orchestrator_task_update", {"key": "T-5", "version": 2, "spec": None})
        await h.call("orchestrator_task_list", {"spec": "S-3"})
    assert seen == [
        ("POST", "/agent/v1/tasks", "", {"title": "t", "client_key": "k", "spec": "S-3"}),
        ("PATCH", "/agent/v1/tasks/T-5", "", {"version": 1, "spec": "S-3"}),
        ("PATCH", "/agent/v1/tasks/T-5", "", {"version": 2, "spec": None}),
        ("GET", "/agent/v1/tasks", "spec=S-3&limit=10", None),
    ]


async def test_spec_queue_move_sends_body() -> None:
    seen: list[tuple[str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"items": []})

    async with harness(handler) as h:
        res = await h.call("orchestrator_spec_queue_move", {"id": 7, "position": 1})
        assert not res.is_error, res.text
        res = await h.call("orchestrator_spec_queue_move", {"id": 7, "before": 3})
        assert not res.is_error, res.text
    assert seen == [
        ("POST", "/agent/v1/specs/queue/7/move", {"position": 1}),
        ("POST", "/agent/v1/specs/queue/7/move", {"before": 3}),
    ]


async def test_spec_queue_list_passes_project_query() -> None:
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.url.query.decode()))
        return httpx.Response(200, json={"projects": []})

    async with harness(handler) as h:
        res = await h.call("orchestrator_spec_queue_list", {"project": "p"})
        assert not res.is_error, res.text
    assert seen == [("GET", "/agent/v1/specs/queue", "project=p")]


def test_spec_queue_move_schema_and_descriptions() -> None:
    rows = {s.name: s for s in spec_tools("http://x")}
    move = rows["spec_queue_move"]
    assert move.description.startswith("Superleader only:")
    assert "403 superleader_only" in move.description and "409 not_queued" in move.description
    assert list(move.input_schema["required"]) == ["id"]
    assert move.input_schema["properties"]["position"]["minimum"] == 1
    assert move.annotations["readOnlyHint"] is False and move.annotations["idempotentHint"] is True
    lst = rows["spec_queue_list"]
    for needle in ("off", "idle", "usage_refused", "orchestrator_unavailable", "at_limit"):
        assert needle in lst.description


def test_no_spec_queue_remove_tool() -> None:
    names = {s.name for s in spec_tools("http://x")}
    assert not {n for n in names if n.startswith("spec_queue")} - {
        "spec_queue_list",
        "spec_queue_move",
    }


def test_no_top_level_combinators_in_tool_schemas() -> None:
    # The Anthropic API rejects tool input schemas with oneOf/anyOf/allOf at the top level.
    for spec in spec_tools("http://x"):
        for combinator in ("oneOf", "anyOf", "allOf"):
            assert combinator not in spec.input_schema, (spec.name, combinator)
