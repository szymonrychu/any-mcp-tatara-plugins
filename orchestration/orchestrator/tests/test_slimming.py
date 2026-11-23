"""S-83 slimming (925): lowered read defaults, shaped read answers, every route in the fixture."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.context import PluginContext
from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import _MERGED, OrchestratorPlugin
from any_mcp_tatara_orchestrator.descriptions import DESCRIPTIONS

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
_ID = "00000000-0000-0000-0000-000000000001"


def _harness(handler: Any) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config=CONFIG,
        name="orchestrator",
        transport=httpx.MockTransport(handler),
    )


async def _call(tool: str, args: dict[str, Any], answer: Any) -> tuple[httpx.Request, Any]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=answer)

    async with _harness(handler) as h:
        result = await h.call(f"orchestrator_{tool}", args)
    assert not result.is_error, result.text
    return seen[0], json.loads(result.text)


# (tool, arguments, query parameter, default)
DEFAULTS = [
    ("activity", {}, "limit", "20"),
    ("mindmap_search", {"q": "x"}, "limit", "20"),
    ("spec_comments", {"spec": "S-1"}, "open", "true"),
    ("mindmap_map", {"op": "get", "key": "M-1"}, "depth", "3"),
    ("webhook", {"action": "events"}, "limit", "20"),
    ("schedule", {"action": "runs_all"}, "limit", "20"),
    ("handoffs", {}, "limit", "20"),
    ("history_get", {"session_id": _ID, "records": True}, "limit", "20"),
]


@pytest.mark.parametrize(("tool", "args", "param", "default"), DEFAULTS)
async def test_read_defaults_lowered(tool: str, args: dict[str, Any], param: str, default: str):
    request, _ = await _call(tool, args, [])
    assert request.url.params.get(param) == default


async def test_a_given_limit_beats_the_default() -> None:
    request, _ = await _call("activity", {"limit": 200}, [])
    assert request.url.params["limit"] == "200"


_SESSION = {"slug": "s", "spec": {"a": 1}, "vault": {"E": {}}, "summary": {"state": "working"}}
_PROPOSAL = {"id": 4, "revisions": [{"revision": 1}, {"revision": 2}]}
_ME = {"session_id": "s", "leader": "a", "leader_state": "long text"}
_ROOMS = [{"room_id": f"!{i}", "name": "n", "alias": "a", "encrypted": True} for i in range(25)]
_VAULT = {
    "entries": [
        {"id": "e", "name": "n", "envs": [{"env": "X", "protected": False, "value": "v",
         "updated_at": "t"}], "updated_at": "t", "pinned_sessions_count": 2}
    ],
    "to_reattach": [],
}  # fmt: skip
_COMMENTS = {"items": [{"id": 1, "body": "b" * 1000, "suggestion": None}]}
# (tool, arguments, answer, check on the shaped answer)
SHAPES: list[tuple[str, dict[str, Any], Any, Any]] = [
    ("session_get", {"slug": "s"}, _SESSION, lambda a: "spec" not in a and "vault" not in a),
    ("session_get", {"slug": "s", "view": "spec"}, _SESSION, lambda a: "spec" in a),
    ("proposal_get", {"id": 4}, _PROPOSAL, lambda a: a["revisions"] == [{"revision": 2}]),
    (
        "proposal_get",
        {"id": 4, "all_revisions": True},
        _PROPOSAL,
        lambda a: len(a["revisions"]) == 2,
    ),
    ("me", {}, _ME, lambda a: "leader_state" not in a),
    ("me", {"leader_state": True}, _ME, lambda a: a["leader_state"] == "long text"),
    (
        "chat_rooms",
        {"scope": "projects"},
        _ROOMS,
        lambda a: len(a["rooms"]) == 20 and a["omitted"] == 5 and "alias" not in a["rooms"][0],
    ),
    (
        "vault_list",
        {},
        _VAULT,
        lambda a: (
            set(a["entries"][0]) == {"id", "name", "envs"}
            and set(a["entries"][0]["envs"][0]) == {"env", "protected", "value"}
        ),
    ),
    (
        "spec_comments",
        {"spec": "S-1"},
        _COMMENTS,
        lambda a: len(a["items"][0]["body"]) < 450 and "cut 600 chars" in a["items"][0]["body"],
    ),
    (
        "webhook",
        {"action": "events"},
        [{"id": 1, "title": "t", "dedup_key": "k", "payload_size": 9}],
        lambda a: a == [{"id": 1, "title": "t"}],
    ),
    (
        "schedule",
        {"action": "runs_all"},
        [{"id": "r", "status": "ok", "chain_depth": 0, "previous_name": "p"}],
        lambda a: a == [{"id": "r", "status": "ok"}],
    ),
]


@pytest.mark.parametrize(("tool", "args", "answer", "check"), SHAPES)
async def test_shaped_answers_drop_unused_fields(
    tool: str, args: dict[str, Any], answer: Any, check: Any
):
    _, shaped = await _call(tool, args, answer)
    assert check(shaped), shaped


async def test_history_records_page_is_cut_to_the_budget_with_a_continue_id() -> None:
    page = {
        "records": [{"id": i, "data": "x" * 4000} for i in range(1, 31)],
        "has_more_before": False,
        "next_after_id": None,
    }
    _, shaped = await _call("history_get", {"session_id": _ID, "records": True}, page)
    kept = shaped["records"]
    assert 0 < len(kept) < 30
    assert shaped["omitted"] == 30 - len(kept)
    assert shaped["next_after_id"] == kept[-1]["id"]


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


async def specs() -> list[Any]:
    plugin = OrchestratorPlugin()
    plugin.ctx = PluginContext(
        name="orchestrator",
        namespace="orchestrator",
        config=OrchestratorPlugin.Config.model_validate(CONFIG),
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        log=logging.getLogger("test"),
        response_budget_chars=40000,
        config_dir=None,
    )
    await plugin.setup()
    return list(await plugin.load_tools())


async def test_every_route_in_fixture() -> None:
    known = {(m.upper(), _norm(p)) for p, ms in OPENAPI["paths"].items() for m in ms}
    ours: set[tuple[str, str]] = set()
    for spec in await specs():
        if spec.http is not None:
            ours.add((spec.http.method.upper(), _norm(spec.http.path)))
    for tool in _MERGED.values():
        ours |= {(r.method.upper(), _norm(f"/agent/v1{r.path}")) for r in tool.ops.values()}
    # The relay catch-alls (/specs, /tasks) stand for their subpaths.
    missing = {
        (m, p)
        for m, p in ours
        if (m, p) not in known
        and not any(m == km and p.startswith(kp.removesuffix("{}") + "") for km, kp in known)
    }
    assert not missing, sorted(missing)


_CODE = re.compile(r"^(\d{3} )?[a-z_]+$")


@pytest.mark.parametrize("hive", [True, False])
async def test_descriptions_purpose_plus_codes(hive: bool) -> None:
    async with _harness_with(hive) as h:
        listed = {
            t["name"].removeprefix("orchestrator_"): t["description"] for t in await h.list_tools()
        }
    assert set(listed) == set(DESCRIPTIONS)
    for name, text in listed.items():
        assert "\n" not in text, name
        purpose, sep, codes = text.partition(" Errors: ")
        assert sep and purpose.strip(), name
        assert len(purpose) <= 330, name
        tokens = [c.strip() for c in codes.removesuffix(".").split(",")]
        assert tokens and all(_CODE.match(c) for c in tokens), (name, tokens)


def _harness_with(hive: bool) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config={**CONFIG, "hive": hive},
        name="orchestrator",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
    )
