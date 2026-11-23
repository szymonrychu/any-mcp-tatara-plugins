"""S-83 merges 886-902: each merged tool's op reaches its route with its arguments."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
CONFIG: dict[str, Any] = {
    "base_url": "http://orch.test:8081",
    "token": "tok-for-tests",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
    "max_replicas": 3,
    "hive": True,
}
_V = "/agent/v1"
_SPEC = f"{_V}/specs/S-1"
_ADM = f"{_V}/admin"
# (tool, arguments, (method, path, query, JSON body or None))
CASES: list[tuple[str, dict[str, Any], tuple[str, str, str, Any]]] = [
    ("me", {}, ("GET", f"{_V}/me", "", None)),
    ("me", {"meta": True}, ("GET", f"{_ADM}/meta", "", None)),
    (
        "handoff_submit",
        {"doing": "x", "next": ["y"], "handled": [3], "epoch": 2},
        ("POST", f"{_V}/handoff", "", {"doing": "x", "next": ["y"], "handled": [3], "epoch": 2}),
    ),
    (
        "handoff_submit",
        {"handover": True, "reason": "ctx", "epoch": 4, "to": "agent-1"},
        ("POST", f"{_V}/leadership/handover", "", {"reason": "ctx", "epoch": 4, "to": "agent-1"}),
    ),
    (
        "lock",
        {"action": "claim", "paths": ["a"]},
        ("POST", f"{_V}/locks/claim", "", {"paths": ["a"]}),
    ),
    (
        "lock",
        {"action": "release", "paths": ["a"]},
        ("POST", f"{_V}/locks/release", "", {"paths": ["a"]}),
    ),
    ("lock", {"action": "list"}, ("GET", f"{_V}/locks", "", None)),
    ("lock", {"action": "list", "slug": "x"}, ("GET", f"{_ADM}/sessions/x/locks", "", None)),
    (
        "lock",
        {"action": "reassign", "path": "a", "to": None},
        ("POST", f"{_V}/locks/reassign", "", {"path": "a", "to": None}),
    ),
    (
        "lock",
        {"action": "reassign", "slug": "x", "path": "a", "to": "p-1"},
        ("POST", f"{_ADM}/sessions/x/locks/reassign", "", {"path": "a", "to": "p-1"}),
    ),
    ("scale", {"replicas": 2}, ("POST", f"{_V}/scale", "", {"replicas": 2})),
    (
        "scale",
        {"replicas": 2, "slug": "x"},
        ("POST", f"{_ADM}/sessions/x/scale", "", {"replicas": 2}),
    ),
    ("init_scripts", {}, ("GET", f"{_V}/init-scripts", "", None)),
    (
        "init_scripts",
        {"action": "edit", "scripts": ["echo"]},
        ("PUT", f"{_V}/init-scripts", "", {"scripts": ["echo"]}),
    ),
    (
        "session_labels",
        {"labels": ["a"]},
        ("PUT", f"{_V}/session/labels", "", {"labels": ["a"]}),
    ),
    (
        "session_labels",
        {"op": "update", "slug": "x", "labels": ["a"]},
        ("PUT", f"{_ADM}/sessions/x/labels", "", {"labels": ["a"]}),
    ),
    ("chat_rooms", {}, ("GET", f"{_V}/chat/rooms", "", None)),
    (
        "chat_rooms",
        {"scope": "projects", "kind": "group"},
        ("GET", f"{_ADM}/chats/rooms", "kind=group", None),
    ),
    ("chat_history", {"before": "m5"}, ("GET", f"{_V}/chat/history", "before=m5&limit=20", None)),
    (
        "chat_history",
        {"room_id": "r1", "limit": 5},
        ("GET", f"{_ADM}/chats/rooms/r1/messages", "limit=5", None),
    ),
    ("chat_notes", {"section": "requests"}, ("GET", f"{_V}/chat/notes", "section=requests", None)),
    ("chat_notes", {"room_id": "r1"}, ("GET", f"{_ADM}/chats/rooms/r1/notes", "", None)),
    (
        "chat_notes",
        {"op": "update", "section": "requests", "body": "b", "expected_version": 2},
        ("PUT", f"{_V}/chat/notes/sections/requests", "", {"body": "b", "expected_version": 2}),
    ),
    (
        "chat_notes",
        {
            "op": "update",
            "room_id": "r1",
            "section": "requests",
            "body": "b",
            "expected_version": 0,
        },
        (
            "PUT",
            f"{_ADM}/chats/rooms/r1/notes/sections/requests",
            "",
            {"body": "b", "expected_version": 0},
        ),
    ),
    (
        "chat_attachment",
        {"op": "fetch", "id": "m3"},
        ("POST", f"{_V}/chat/attachments", "", {"id": "m3"}),
    ),
    (
        "chat_attachment",
        {"op": "status", "request_id": "a4"},
        ("GET", f"{_V}/chat/attachments/a4", "", None),
    ),
    (
        "todo",
        {"op": "set", "key": "T-1", "items": [{"text": "a"}]},
        ("PUT", f"{_V}/tasks/T-1/todo", "", {"items": [{"text": "a"}]}),
    ),
    (
        "todo",
        {"op": "update", "key": "T-1", "item_id": "i1", "status": "done"},
        ("PATCH", f"{_V}/tasks/T-1/todo/i1", "", {"status": "done"}),
    ),
    ("project", {}, ("GET", f"{_V}/projects", "", None)),
    (
        "project",
        {"action": "summary", "project": "p"},
        ("GET", f"{_V}/projects/summary", "project=p", None),
    ),
    (
        "project",
        {"action": "create", "project": "p", "name": "P"},
        ("POST", f"{_ADM}/projects", "", {"slug": "p", "name": "P"}),
    ),
    (
        "project",
        {"action": "update", "project": "p", "archived": True},
        ("PATCH", f"{_ADM}/projects/p", "", {"archived": True}),
    ),
    (
        "project",
        {"action": "grant", "target": "session", "ref": "x", "project": "p", "reason": "r"},
        ("POST", f"{_ADM}/sessions/x/projects", "", {"project": "p", "reason": "r"}),
    ),
    (
        "project",
        {"action": "grant", "target": "preset", "ref": "pr", "project": "p", "reason": "r"},
        ("POST", f"{_ADM}/presets/pr/projects", "", {"project": "p", "reason": "r"}),
    ),
    (
        "project",
        {"action": "revoke", "target": "session", "ref": "x", "project": "p"},
        ("DELETE", f"{_ADM}/sessions/x/projects/p", "", None),
    ),
    (
        "project",
        {"action": "revoke", "target": "preset", "ref": "pr", "project": "p"},
        ("DELETE", f"{_ADM}/presets/pr/projects/p", "", None),
    ),
    ("spec_get", {"spec": "S-1"}, ("GET", _SPEC, "", None)),
    (
        "spec_get",
        {"spec": "S-1", "path": "plan.md", "versions": True, "limit": 5},
        ("GET", f"{_SPEC}/docs/plan.md/versions", "limit=5", None),
    ),
    (
        "spec_get",
        {"spec": "S-1", "path": "plan.md", "section": "goal", "version": 2},
        ("GET", f"{_SPEC}/docs/plan.md/section", "heading=goal&version=2", None),
    ),
    (
        "spec_comment",
        {"spec": "S-1", "body": "b", "path": "plan.md"},
        ("POST", f"{_SPEC}/comments", "", {"body": "b", "path": "plan.md"}),
    ),
    (
        "spec_comment",
        {"op": "resolve", "spec": "S-1", "id": 7},
        ("POST", f"{_SPEC}/comments/7/resolve", "", None),
    ),
    (
        "spec_comment",
        {"op": "dismiss", "spec": "S-1", "id": 7},
        ("POST", f"{_ADM}/specs/S-1/comments/7/dismiss", "", None),
    ),
    ("spec_queue", {"project": "p"}, ("GET", f"{_V}/specs/queue", "project=p", None)),
    (
        "spec_queue",
        {"move": 4, "position": 1},
        ("POST", f"{_V}/specs/queue/4/move", "", {"position": 1}),
    ),
    ("plan_get", {"spec": "S-1"}, ("GET", f"{_SPEC}/plan", "", None)),
    ("plan_get", {"spec": "S-1", "view": "steps"}, ("GET", f"{_SPEC}/steps", "", None)),
    ("plan_get", {"spec": "S-1", "view": "gate"}, ("GET", f"{_SPEC}/gate", "", None)),
    (
        "task_update",
        {"key": "T-2", "version": 3, "model": None, "refs": [{"kind": "pr", "value": "u"}]},
        (
            "PATCH",
            f"{_V}/tasks/T-2",
            "",
            {"version": 3, "model": None, "refs": [{"kind": "pr", "value": "u"}]},
        ),
    ),
]
# Routes the orchestrator relays through a catch-all (``/specs/{path}``, ``/tasks/{path}``).
_RELAYED = re.compile(r"^/agent/v1/(specs|tasks)/")


def _known(method: str, path: str) -> bool:
    def norm(p: str) -> str:
        return re.sub(r"\{[^}]*\}", "{}", p)

    known = {(m.upper(), norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    pattern = {
        (m, re.compile("^" + re.sub(r"\\\{\\\}", "[^/]+", re.escape(p)) + "$")) for m, p in known
    }
    if _RELAYED.match(path):
        return True
    return any(m == method and rx.match(path) for m, rx in pattern)


def harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


def _task() -> dict[str, Any]:
    return {"key": "T-1", "version": 1, "url": "u", "todo": [], "comments": []}


@pytest.mark.parametrize(("tool", "args", "expected"), CASES)
async def test_merged_op_routes(tool: str, args: dict[str, Any], expected: tuple) -> None:
    seen: list[tuple[str, str, str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, request.url.query.decode(), body))
        return httpx.Response(200, json=_task())

    async with harness(handler) as h:
        result = await h.call(f"orchestrator_{tool}", args)
    assert not result.is_error, result.text
    assert seen == [expected]
    assert _known(expected[0], expected[1]), expected


_MISSING = [
    ("lock", {"action": "claim"}, "paths"),
    ("todo", {"op": "update", "key": "T-1"}, "item_id"),
    ("session_labels", {"op": "update", "labels": []}, "slug"),
    ("project", {"action": "create", "name": "P"}, "project"),
    ("spec_get", {"spec": "S-1", "versions": True}, "path"),
    ("spec_comment", {"op": "resolve", "spec": "S-1"}, "id"),
    ("chat_notes", {"op": "update", "section": "requests", "body": "b"}, "expected_version"),
    ("chat_attachment", {"op": "status"}, "request_id"),
]
_FOREIGN = [
    ("lock", {"action": "claim", "paths": ["a"], "slug": "x"}, "claim+slug"),
    ("scale", {"replicas": 2, "to": "x"}, "to"),
    ("todo", {"op": "set", "key": "T-1", "items": [], "text": "x"}, "text"),
    ("project", {"action": "grant", "ref": "x", "project": "p", "reason": "r"}, "grant"),
]


async def _refused(tool: str, args: dict[str, Any], named: str) -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async with harness(handler) as h:
        result = await h.call(f"orchestrator_{tool}", args)
    assert result.is_error
    assert named in result.text
    assert not sent


@pytest.mark.parametrize(("tool", "args", "named"), _MISSING)
async def test_merged_op_missing_required_arg_refused(
    tool: str, args: dict[str, Any], named: str
) -> None:
    await _refused(tool, args, named)


@pytest.mark.parametrize(("tool", "args", "named"), _FOREIGN)
async def test_merged_op_unknown_op_or_foreign_arg_refused(
    tool: str, args: dict[str, Any], named: str
) -> None:
    await _refused(tool, args, named)
