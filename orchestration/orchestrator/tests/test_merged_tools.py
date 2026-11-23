"""S-83 merges 886-918: each merged tool's op reaches its route with its arguments."""

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
# S-83 905-916: the merged admin tools.
_SID = "6f1c1c0e-0000-4000-8000-000000000001"
_EVERY_HOOK = {"type": "github", "preset_id": "p"}
_SCHED = {"name": "n", "cron": "* * * * *", "preset_id": "p", "prompt": "x"}
ADMIN_CASES: list[tuple[str, dict[str, Any], tuple[str, str, str, Any]]] = [
    ("volumes", {}, ("GET", f"{_V}/volumes", "", None)),
    ("volumes", {"scope": "all"}, ("GET", f"{_ADM}/volumes", "", None)),
    ("volumes", {"scope": "all", "volume_id": "v-1"}, ("GET", f"{_ADM}/volumes/v-1", "", None)),
    ("volumes", {"volume_id": "v-1"}, ("GET", f"{_ADM}/volumes/v-1", "", None)),
    (
        "vault_attach",
        {"ref": "web", "entry_id": "E1", "envs": {"TOK": "BOT_TOKEN"}, "replace": True},
        (
            "PUT",
            f"{_ADM}/presets/web/vault/E1",
            "",
            {"envs": {"TOK": "BOT_TOKEN"}, "replace": True},
        ),
    ),
    (
        "vault_attach",
        {"ref": "web", "entry_id": "E1", "detach": True, "env": "TOK"},
        ("DELETE", f"{_ADM}/presets/web/vault/E1", "env=TOK", None),
    ),
    (
        "volume_attach",
        {
            "ref": "dev",
            "volume_id": "v-1",
            "mode": "rw",
            "mount_path": "/d",
            "backup_restore": True,
        },
        (
            "POST",
            f"{_ADM}/presets/dev/volumes/v-1",
            "",
            {"mode": "rw", "mount_path": "/d", "backup_restore": True},
        ),
    ),
    (
        "volume_attach",
        {"ref": "dev", "volume_id": "v-1", "detach": True},
        ("DELETE", f"{_ADM}/presets/dev/volumes/v-1", "", None),
    ),
    (
        "proposal_put",
        {"op": "create", "kind": "preset", "target": "web", "patch": {"a": 1}, "reason": "r"},
        (
            "POST",
            f"{_ADM}/proposals",
            "",
            {"kind": "preset", "target": "web", "patch": {"a": 1}, "reason": "r"},
        ),
    ),
    (
        "proposal_put",
        {"op": "revise", "id": 5, "patch": {"a": 2}},
        ("POST", f"{_ADM}/proposals/5/revise", "", {"patch": {"a": 2}}),
    ),
    (
        "proposal_put",
        {"op": "withdraw", "id": 7},
        ("POST", f"{_ADM}/proposals/7/withdraw", "", None),
    ),
    (
        "proposal_get",
        {"status": "all", "limit": 5},
        ("GET", f"{_ADM}/proposals", "status=all&limit=5", None),
    ),
    ("proposal_get", {"id": 7}, ("GET", f"{_ADM}/proposals/7", "", None)),
    ("session_restart", {"slug": "s"}, ("POST", f"{_ADM}/sessions/s/restart", "", None)),
    (
        "session_restart",
        {"scope": "agent", "slug": "s", "pod": "p-0"},
        ("POST", f"{_ADM}/sessions/s/agents/p-0/restart", "", None),
    ),
    (
        "session_commands",
        {"slug": "s", "limit": 5},
        ("GET", f"{_ADM}/sessions/s/commands", "limit=5", None),
    ),
    ("session_commands", {"command_id": "c1"}, ("GET", f"{_ADM}/commands/c1", "", None)),
    ("usage", {}, ("GET", f"{_ADM}/usage", "", None)),
    (
        "usage",
        {"what": "history", "metric": "five_hour", "max_points": 10},
        ("GET", f"{_ADM}/usage/history", "metric=five_hour&max_points=10", None),
    ),
    (
        "usage",
        {"what": "sessions", "from": "2026-01-01", "limit": 3},
        ("GET", f"{_ADM}/usage/sessions", "from=2026-01-01&limit=3", None),
    ),
    ("webhook", {"action": "list"}, ("GET", f"{_ADM}/webhooks/endpoints", "", None)),
    ("webhook", {"action": "meta"}, ("GET", f"{_ADM}/webhooks/meta", "", None)),
    (
        "webhook",
        {"action": "create", **_EVERY_HOOK, "auth_mode": "hmac"},
        ("POST", f"{_ADM}/webhooks/endpoints", "", {**_EVERY_HOOK, "auth_mode": "hmac"}),
    ),
    (
        "webhook",
        {"action": "update", "id": "e1", "then_schedule_ids": None},
        ("PATCH", f"{_ADM}/webhooks/endpoints/e1", "", {"then_schedule_ids": None}),
    ),
    (
        "webhook",
        {"action": "clone", "id": "e1", "suffix": "b"},
        ("POST", f"{_ADM}/webhooks/endpoints/e1/clone", "", {"suffix": "b"}),
    ),
    (
        "webhook",
        {"action": "rotate", "id": "e1", "username": "u"},
        ("POST", f"{_ADM}/webhooks/endpoints/e1/rotate", "", {"username": "u"}),
    ),
    (
        "webhook",
        {"action": "delete", "id": "e1"},
        ("DELETE", f"{_ADM}/webhooks/endpoints/e1", "", None),
    ),
    (
        "webhook",
        {"action": "filters", "id": "e1", "allow": [], "deny": ["x"]},
        ("PUT", f"{_ADM}/webhooks/endpoints/e1/filters", "", {"allow": [], "deny": ["x"]}),
    ),
    (
        "webhook",
        {"action": "events", "status": "queued", "limit": 5},
        ("GET", f"{_ADM}/webhooks/events", "status=queued&limit=5", None),
    ),
    ("webhook", {"action": "event", "id": "v1"}, ("GET", f"{_ADM}/webhooks/events/v1", "", None)),
    (
        "webhook",
        {"action": "preview", "type": "github", "example": {"a": 1}},
        ("POST", f"{_ADM}/webhooks/templates/preview", "", {"type": "github", "example": {"a": 1}}),
    ),
    ("schedule", {"action": "list"}, ("GET", f"{_ADM}/schedules/schedules", "", None)),
    (
        "schedule",
        {"action": "get", "schedule_id": "7"},
        ("GET", f"{_ADM}/schedules/schedules/7", "", None),
    ),
    (
        "schedule",
        {"action": "create", **_SCHED, "enabled": True},
        ("POST", f"{_ADM}/schedules/schedules", "", {**_SCHED, "enabled": True}),
    ),
    (
        "schedule",
        {"action": "update", "schedule_id": "7", **_SCHED, "then_schedule_ids": []},
        ("PUT", f"{_ADM}/schedules/schedules/7", "", {**_SCHED, "then_schedule_ids": []}),
    ),
    (
        "schedule",
        {"action": "delete", "schedule_id": "7"},
        ("DELETE", f"{_ADM}/schedules/schedules/7", "", None),
    ),
    (
        "schedule",
        {"action": "run", "schedule_id": "7"},
        ("POST", f"{_ADM}/schedules/schedules/7/run", "", None),
    ),
    (
        "schedule",
        {"action": "runs", "schedule_id": "7", "limit": 3},
        ("GET", f"{_ADM}/schedules/schedules/7/runs", "limit=3", None),
    ),
    (
        "schedule",
        {"action": "runs_all", "status": "failed"},
        ("GET", f"{_ADM}/schedules/runs", "status=failed", None),
    ),
    (
        "schedule",
        {"action": "run_get", "run_id": "r1"},
        ("GET", f"{_ADM}/schedules/runs/r1", "", None),
    ),
    (
        "schedule",
        {"action": "preview", "cron": "* * * * *", "timezone": None},
        (
            "POST",
            f"{_ADM}/schedules/schedules/preview",
            "",
            {"cron": "* * * * *", "timezone": None},
        ),
    ),
    (
        "chat_connector",
        {"action": "settings", "room_id": "r1", "agent_aliases": []},
        ("PUT", f"{_ADM}/chats/rooms/r1/settings", "", {"agent_aliases": []}),
    ),
    (
        "chat_connector",
        {"action": "allow", "room_id": "r1", "trigger": "mention"},
        ("POST", f"{_ADM}/chats/rooms/r1/allow", "", {"trigger": "mention"}),
    ),
    (
        "chat_connector",
        {"action": "users", "q": "ann", "limit": 5},
        ("GET", f"{_ADM}/chats/users", "q=ann&limit=5", None),
    ),
    (
        "chat_connector",
        {"action": "open", "target": "ann", "task_key": "T-1"},
        (
            "POST",
            f"{_ADM}/chats/room-requests",
            "",
            {"action": "open", "target": "ann", "task_key": "T-1"},
        ),
    ),
    (
        "chat_connector",
        {"action": "invite", "target": "ann", "task_key": "T-1", "room_id": "r1"},
        (
            "POST",
            f"{_ADM}/chats/room-requests",
            "",
            {"action": "invite", "target": "ann", "task_key": "T-1", "room_id": "r1"},
        ),
    ),
    (
        "chat_connector",
        {"action": "open_status", "request_id": "7"},
        ("GET", f"{_ADM}/chats/room-requests/7", "", None),
    ),
    (
        "chat_connector",
        {"action": "open_confirm", "request_id": "7"},
        ("POST", f"{_ADM}/chats/room-requests/7/confirm", "", {}),
    ),
    ("chat_connector", {"action": "rules"}, ("GET", f"{_ADM}/chats/rules", "", None)),
    (
        "chat_connector",
        {"action": "rule_add", "list": "deny", "kind": "user", "value": "x"},
        ("POST", f"{_ADM}/chats/rules", "", {"list": "deny", "kind": "user", "value": "x"}),
    ),
    (
        "chat_connector",
        {"action": "rule_delete", "rule_id": "3"},
        ("DELETE", f"{_ADM}/chats/rules/3", "", None),
    ),
    ("history_get", {"session_id": _SID}, ("GET", f"{_ADM}/history/sessions/{_SID}", "", None)),
    (
        "history_get",
        {"session_id": _SID, "records": True, "kind": "event", "after_id": 0, "limit": 5},
        (
            "GET",
            f"{_ADM}/history/sessions/{_SID}/records",
            "kind=event&after_id=0&limit=5",
            None,
        ),
    ),
    (
        "handoffs",
        {"session": "mx-1", "limit": 3},
        ("GET", f"{_ADM}/handoffs", "session=mx-1&limit=3", None),
    ),
    ("handoffs", {"id": 4}, ("GET", f"{_ADM}/handoffs/4", "", None)),
    ("backups", {"q": "a"}, ("GET", f"{_ADM}/backups", "q=a", None)),
    (
        "backups",
        {"name": "a", "limit": 5},
        ("GET", f"{_ADM}/backups/a/versions", "limit=5", None),
    ),
    (
        "backups",
        {"name": "a", "version": "v1"},
        ("GET", f"{_ADM}/backups/a/versions/v1/manifest", "", None),
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


async def _routed(tool: str, args: dict[str, Any], expected: tuple) -> None:
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


@pytest.mark.parametrize(("tool", "args", "expected"), CASES)
async def test_merged_op_routes(tool: str, args: dict[str, Any], expected: tuple) -> None:
    await _routed(tool, args, expected)


@pytest.mark.parametrize(("tool", "args", "expected"), ADMIN_CASES)
async def test_merged_admin_op_routes(tool: str, args: dict[str, Any], expected: tuple) -> None:
    await _routed(tool, args, expected)


_MISSING = [
    ("lock", {"action": "claim"}, "paths"),
    ("todo", {"op": "update", "key": "T-1"}, "item_id"),
    ("session_labels", {"op": "update", "labels": []}, "slug"),
    ("project", {"action": "create", "name": "P"}, "project"),
    ("spec_get", {"spec": "S-1", "versions": True}, "path"),
    ("spec_comment", {"op": "resolve", "spec": "S-1"}, "id"),
    ("chat_notes", {"op": "update", "section": "requests", "body": "b"}, "expected_version"),
    ("chat_attachment", {"op": "status"}, "request_id"),
    ("schedule", {"action": "runs"}, "schedule_id"),
    ("chat_connector", {"action": "invite", "target": "a", "task_key": "T-1"}, "room_id"),
    ("mindmap_node", {"op": "update", "key": "N-1", "notes": "x"}, "version"),
]
_FOREIGN = [
    ("lock", {"action": "claim", "paths": ["a"], "slug": "x"}, "claim+slug"),
    ("scale", {"replicas": 2, "to": "x"}, "to"),
    ("todo", {"op": "set", "key": "T-1", "items": [], "text": "x"}, "text"),
    ("project", {"action": "grant", "ref": "x", "project": "p", "reason": "r"}, "grant"),
    ("webhook", {"action": "delete", "id": "e1", "to_file": "/tmp/x"}, "to_file"),
    ("backups", {"version": "v1"}, "version"),
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
