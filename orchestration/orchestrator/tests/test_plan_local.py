"""T-116 L5: plan_steps_push, plan_export and plan_questions_ask."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.plan_local import local_plan_specs

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
SECRET_BODY = "STEP-BODY-MARKER"


class Board:
    """A tiny fake of the orchestrator spec, step and task relay."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str, Any]] = []
        self.steps_version = 4
        self.gate_task: str | None = "T-9"
        self.decisions: list[dict[str, Any]] = []
        self.put_status = 200
        self.withdraw_status = 200
        self.post_fail_on: str | None = None

    def add_decision(
        self, question: str, options: list[dict[str, Any]], status: str = "open"
    ) -> dict[str, Any]:
        decision = {
            "id": len(self.decisions) + 100,
            "question": question,
            "options": options,
            "status": status,
        }
        self.decisions.append(decision)
        return decision

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, p, request.url.query.decode(), body))
        if p == "/agent/v1/specs/S-3":
            return httpx.Response(200, json={"key": "S-3", "name": "my-spec", "documents": []})
        if p == "/agent/v1/specs/S-3/gate":
            return httpx.Response(200, json={"key": "S-3", "gate_task": self.gate_task})
        if p == "/agent/v1/specs/S-3/plan":
            return httpx.Response(
                200,
                json={
                    "goal": "the goal",
                    "architecture": "arch",
                    "tech_stack": "py",
                    "constraints": ["c1", "c2"],
                    "review_focus": ["r1"],
                    "version": 3,
                },
            )
        if p == "/agent/v1/specs/S-3/steps" and request.method == "GET":
            items = [
                {"key": "T-11", "step": 1, "title": "first", "status": "done", "deps": []},
                {"key": "T-12", "step": 2, "title": "second", "status": "open", "deps": ["T-11"]},
            ]
            return httpx.Response(200, json={"items": items, "steps_version": self.steps_version})
        if p == "/agent/v1/specs/S-3/steps" and request.method == "PUT":
            if self.put_status != 200:
                return httpx.Response(
                    self.put_status,
                    json={
                        "error": {
                            "code": "stale_version",
                            "message": "x",
                            "current_steps_version": 9,
                        }
                    },
                )
            keys = [f"T-{20 + s['n']}" for s in body["steps"]]
            return httpx.Response(200, json={"keys": keys})
        if m := re.fullmatch(r"/agent/v1/tasks/(T-\d+)", p):
            if m[1] == "T-9" or m[1] == self.gate_task:
                return httpx.Response(
                    200, json={"key": m[1], "version": 1, "decisions": self.decisions}
                )
            n = int(m[1][2:])
            return httpx.Response(
                200,
                json={
                    "key": m[1],
                    "body": f"{SECRET_BODY} {n}",
                    "todo": [{"id": 1, "text": "sub one", "status": "done"}],
                    "refs": [{"kind": "pr", "value": "https://x/pr/1"}],
                    "model": "sonnet",
                    "depends_on": [],
                },
            )
        if m := re.fullmatch(r"/agent/v1/tasks/(T-\d+)/decisions", p):
            if body["question"] == self.post_fail_on:
                return httpx.Response(500, json={"error": {"code": "boom"}})
            replay = next(
                (d for d in self.decisions if d.get("client_key") == body["client_key"]), None
            )
            if replay is not None:  # the real relay replays by (task, session, client_key)
                return httpx.Response(200, json=replay)
            decision = self.add_decision(body["question"], body["options"])
            decision["client_key"] = body["client_key"]
            return httpx.Response(201, json=decision)
        if m := re.fullmatch(r"/agent/v1/tasks/(T-\d+)/decisions/(\d+)/withdraw", p):
            if self.withdraw_status != 200:
                return httpx.Response(self.withdraw_status, json={"error": {"code": "forbidden"}})
            for d in self.decisions:
                if d["id"] == int(m[2]):
                    d["status"] = "withdrawn"
            return httpx.Response(200, json={"id": int(m[2]), "status": "withdrawn"})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def writes(self, suffix: str = "") -> list[tuple[str, str, Any]]:
        return [(m, p, b) for m, p, _, b in self.requests if m != "GET" and p.endswith(suffix)]


def harness(board: Board, workdir: Path, session: str = "s-1") -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config={
            "base_url": "http://orch.test:8081",
            "token": "tok-for-tests",
            "session": session,
            "agent": "agent-0",
            "slug": "my-hive",
            "workdir": str(workdir),
        },
        name="orchestrator",
        transport=httpx.MockTransport(board),
    )


def opts(*pairs: tuple[str, bool]) -> list[dict[str, Any]]:
    return [
        {"label": label, "consequence": f"then {label}", "recommended": rec} for label, rec in pairs
    ]


def stored(*pairs: tuple[str, bool]) -> list[dict[str, Any]]:
    return [
        {"label": label, "description": f"then {label}", "recommended": rec} for label, rec in pairs
    ]


A = (("A MVP", True), ("B", False))


def test_rows_have_http_bindings_in_the_vendored_openapi() -> None:
    rows = {s.name: s for s in local_plan_specs("http://x")}
    assert set(rows) == {"plan_steps_push", "plan_export", "plan_questions_ask"}
    expected = {
        "plan_steps_push": ("PUT", "/agent/v1/specs/{spec}/steps"),
        "plan_export": ("GET", "/agent/v1/specs/{spec}/steps"),
        "plan_questions_ask": ("POST", "/agent/v1/tasks/{key}/decisions"),
    }
    for name, (method, path) in expected.items():
        http = rows[name].http
        assert http is not None
        assert (http.method, http.path) == (method, path)
        route = "/agent/v1/tasks/{path}" if "/tasks" in path else "/agent/v1/specs/{path}"
        assert method.lower() in OPENAPI["paths"][route]
        assert rows[name].description.isascii()
        assert rows[name].input_schema["additionalProperties"] is False


# plan_steps_push


def write_steps(tmp_path: Path, steps: list[dict[str, Any]], name: str = "steps.json") -> str:
    (tmp_path / name).write_text(json.dumps({"steps": steps}))
    return name


async def test_push_sends_the_steps_version_given_by_the_caller(tmp_path: Path) -> None:
    board = Board()
    steps = [
        {"n": 1, "title": "a", "body": "x", "depends_on": []},
        {"n": 2, "title": "b", "body": "y", "depends_on": [1], "model": "sonnet"},
    ]
    file = write_steps(tmp_path, steps)
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_plan_steps_push", {"spec": "S-3", "file": file, "steps_version": 2}
        )
    assert not res.is_error, res.text
    assert json.loads(res.text) == {"spec": "S-3", "keys": ["T-21", "T-22"]}
    assert board.writes() == [
        ("PUT", "/agent/v1/specs/S-3/steps", {"steps_version": 2, "steps": steps})
    ]
    assert not any(m == "GET" for m, _, _, _ in board.requests)


async def test_push_uses_the_version_recorded_by_plan_export(tmp_path: Path) -> None:
    board = Board()
    board.steps_version = 4
    steps = [{"n": 1, "title": "a", "body": "x", "depends_on": []}]
    async with harness(board, tmp_path) as h:
        exported = await h.call("orchestrator_plan_export", {"spec": "S-3"})
        assert json.loads(exported.text)["steps_version"] == 4
        board.steps_version = 7  # someone edited meanwhile
        spec_dir = tmp_path / ".scratchpad" / "my-spec" / "spec"
        (spec_dir / "steps.json").write_text(json.dumps({"steps": steps}))
        res = await h.call(
            "orchestrator_plan_steps_push",
            {"spec": "S-3", "file": ".scratchpad/my-spec/spec/steps.json"},
        )
    assert not res.is_error, res.text
    assert board.writes() == [
        ("PUT", "/agent/v1/specs/S-3/steps", {"steps_version": 4, "steps": steps})
    ]


async def test_push_without_a_known_base_version_is_refused(tmp_path: Path) -> None:
    board = Board()
    file = write_steps(tmp_path, [{"n": 1, "title": "t", "body": "", "depends_on": []}])
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": file})
    assert res.is_error and "steps_version_unknown" in res.text
    assert board.writes() == []


@pytest.mark.parametrize("bad", ["/etc/steps.json", "../steps.json", "a/../../steps.json"])
async def test_push_file_outside_workdir_refused(tmp_path: Path, bad: str) -> None:
    board = Board()
    (tmp_path / "w").mkdir()
    async with harness(board, tmp_path / "w") as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": bad})
    assert res.is_error
    assert board.requests == []


async def test_push_symlink_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"steps": []}))
    work = tmp_path / "work"
    work.mkdir()
    (work / "steps.json").symlink_to(outside)
    board = Board()
    async with harness(board, work) as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": "steps.json"})
    assert res.is_error
    assert board.requests == []


@pytest.mark.parametrize(
    "ns",
    [[1, 3], [2, 3], [1, 1], [0, 1], [2, 1]],
    ids=["gap", "from-two", "repeat", "zero", "unordered"],
)
async def test_push_refuses_numbering_that_is_not_dense_from_one(
    tmp_path: Path, ns: list[int]
) -> None:
    board = Board()
    file = write_steps(tmp_path, [{"n": n, "title": "t", "body": "", "depends_on": []} for n in ns])
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": file})
    assert res.is_error and "steps_not_dense" in res.text
    assert board.requests == []


@pytest.mark.parametrize("content", ["not json", '{"steps": "x"}', '["a"]', '{"steps": [1]}'])
async def test_push_refuses_a_malformed_file(tmp_path: Path, content: str) -> None:
    board = Board()
    (tmp_path / "s.json").write_text(content)
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": "s.json"})
    assert res.is_error
    assert board.requests == []


async def test_push_stale_steps_version_is_surfaced(tmp_path: Path) -> None:
    board = Board()
    board.put_status = 412
    file = write_steps(tmp_path, [{"n": 1, "title": "t", "body": "", "depends_on": []}])
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_plan_steps_push", {"spec": "S-3", "file": file, "steps_version": 1}
        )
    assert res.is_error and "stale_version" in res.text


async def test_push_refuses_a_request_over_the_relay_cap(tmp_path: Path) -> None:
    board = Board()
    file = write_steps(
        tmp_path,
        [{"n": i, "title": "t", "body": "x" * 30000, "depends_on": []} for i in range(1, 12)],
    )
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_plan_steps_push", {"spec": "S-3", "file": file})
    assert res.is_error and "payload_too_large" in res.text
    assert board.requests == []


async def test_push_refuses_an_oversized_file_before_reading_it(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "big.json").write_text(" " * (300 * 1024) + '{"steps": []}')
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_plan_steps_push",
            {"spec": "S-3", "file": "big.json", "steps_version": 1},
        )
    assert res.is_error and "payload_too_large" in res.text
    assert board.requests == []


async def test_push_result_has_no_step_body(tmp_path: Path) -> None:
    board = Board()
    file = write_steps(tmp_path, [{"n": 1, "title": "t", "body": SECRET_BODY, "depends_on": []}])
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_plan_steps_push", {"spec": "S-3", "file": file, "steps_version": 1}
        )
    assert SECRET_BODY not in res.text


# plan_export


async def test_export_writes_header_and_every_step_in_order(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_plan_export", {"spec": "S-3"})
    assert not res.is_error, res.text
    target = tmp_path / ".scratchpad" / "my-spec" / "spec" / "plan-steps.md"
    text = target.read_text()
    assert "the goal" in text and "arch" in text and "c1" in text and "r1" in text
    assert text.index("first") < text.index(f"{SECRET_BODY} 11") < text.index("second")
    assert f"{SECRET_BODY} 12" in text and "sub one" in text
    out = json.loads(res.text)
    assert out["path"] == str(target) and out["steps"] == 2
    assert SECRET_BODY not in res.text
    assert board.writes() == []


async def test_export_dir_is_confined(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "w").mkdir()
    async with harness(board, tmp_path / "w") as h:
        res = await h.call("orchestrator_plan_export", {"spec": "S-3", "dir": "../out"})
        ok = await h.call("orchestrator_plan_export", {"spec": "S-3", "dir": "out/plan"})
    assert res.is_error
    assert not ok.is_error, ok.text
    assert (tmp_path / "w" / "out" / "plan" / "plan-steps.md").exists()
    assert not (tmp_path / "out").exists()


# plan_questions_ask


def question(text: str = "Which db?", options: Any = None) -> dict[str, Any]:
    return {"question": text, "options": options or opts(*A)}


async def ask(h: PluginHarness, questions: list[dict[str, Any]], **extra: Any) -> Any:
    return await h.call(
        "orchestrator_plan_questions_ask", {"spec": "S-3", "questions": questions, **extra}
    )


async def test_new_question_is_posted_with_a_client_key(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question()])
    assert not res.is_error, res.text
    [(method, path, body)] = board.writes("/decisions")
    assert (method, path) == ("POST", "/agent/v1/tasks/T-9/decisions")
    assert body["question"] == "Which db?"
    assert body["options"] == stored(*A)
    assert re.fullmatch(r"gate-3-[0-9a-f]{12}", body["client_key"])
    out = json.loads(res.text)
    assert out == [{"question": "Which db?", "decision_id": 100, "action": "posted"}]


async def test_replay_posts_no_second_decision(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path) as h:
        await ask(h, [question()])
        res = await ask(h, [question()])
    assert len(board.writes("/decisions")) == 1
    assert "skipped" in res.text


async def test_new_leader_session_asking_the_same_question_posts_nothing(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path, "s-1") as h:
        await ask(h, [question()])
    async with harness(board, tmp_path, "s-2") as h:
        res = await ask(h, [question()])
    assert len(board.writes("/decisions")) == 1
    assert "skipped" in res.text


async def test_revised_question_posts_then_withdraws_with_superseded_by(tmp_path: Path) -> None:
    board = Board()
    old = board.add_decision("Which db?", stored(("A MVP", True), ("B", False)))
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question(options=opts(("A MVP", True), ("C", False)))])
    assert not res.is_error, res.text
    writes = board.writes()
    assert [(m, p) for m, p, _ in writes] == [
        ("POST", "/agent/v1/tasks/T-9/decisions"),
        ("POST", f"/agent/v1/tasks/T-9/decisions/{old['id']}/withdraw"),
    ]
    assert writes[1][2] == {"reason": "question revised", "superseded_by": old["id"] + 1}
    assert "revised" in res.text


async def test_answered_question_is_never_re_asked(tmp_path: Path) -> None:
    board = Board()
    board.add_decision("Which db?", stored(*A), "answered")
    async with harness(board, tmp_path) as h:
        same = await ask(h, [question()])
        changed = await ask(h, [question(options=opts(("A MVP", True), ("C", False)))])
    assert board.writes() == []
    assert "answered" in same.text and "not re-asked" in same.text
    assert "answered" in changed.text and "not re-asked" in changed.text


async def test_explicit_task_skips_the_gate_lookup(tmp_path: Path) -> None:
    board = Board()
    board.gate_task = None
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question()], task="T-9")
    assert not res.is_error, res.text
    assert not any(p.endswith("/gate") for _, p, _, _ in board.requests)


async def test_no_gate_task_is_a_local_error(tmp_path: Path) -> None:
    board = Board()
    board.gate_task = None
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question()])
    assert res.is_error
    assert "link the improvement task to the spec first (task_update spec)" in res.text
    assert board.writes() == []


async def test_two_recommended_options_refused_before_any_request(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question(options=opts(("A", True), ("B", True)))])
        none = await ask(h, [question(options=opts(("A", False), ("B", False)))])
    assert res.is_error and none.is_error
    assert board.requests == []


async def test_one_bad_question_refuses_the_whole_call_before_any_post(tmp_path: Path) -> None:
    board = Board()
    bad = question("Second?", opts(("A", True), ("B", True)))
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question(), bad])
    assert res.is_error
    assert board.requests == []


async def test_result_lists_every_question_without_options_or_context(tmp_path: Path) -> None:
    board = Board()
    board.add_decision("Old?", stored(*A), "answered")
    qs = [question("Old?"), question("New?")]
    async with harness(board, tmp_path) as h:
        res = await ask(h, qs)
    text = res.text
    assert "then A MVP" not in text
    rows = json.loads(text)
    assert [(r["question"], r["action"]) for r in rows] == [
        ("Old?", "answered"),
        ("New?", "posted"),
    ]


async def test_reverting_a_question_asks_it_again(tmp_path: Path) -> None:
    board = Board()
    a = opts(*A)
    b = opts(("A MVP", True), ("C", False))
    async with harness(board, tmp_path) as h:
        await ask(h, [question(options=a)])
        await ask(h, [question(options=b)])
        res = await ask(h, [question(options=a)])
    assert not res.is_error, res.text
    open_ = [d for d in board.decisions if d["status"] == "open"]
    assert [d["options"] for d in open_] == [stored(*A)]
    row = json.loads(res.text)[0]
    assert row["decision_id"] == open_[0]["id"] and row["action"] == "revised"


async def test_withdraw_forbidden_for_another_sessions_decision_is_reported(
    tmp_path: Path,
) -> None:
    board = Board()
    old = board.add_decision("Which db?", stored(*A))
    board.withdraw_status = 403
    qs = [question(options=opts(("A MVP", True), ("C", False))), question("Second?")]
    async with harness(board, tmp_path, "s-2") as h:
        res = await ask(h, qs)
    assert not res.is_error, res.text
    rows = json.loads(res.text)
    assert [r["question"] for r in rows] == ["Which db?", "Second?"]
    assert rows[0]["action"] == "posted" and rows[0]["decision_id"] != old["id"]
    assert "another session" in rows[0]["note"]
    assert rows[1]["action"] == "posted"


async def test_post_failure_keeps_earlier_rows_and_continues(tmp_path: Path) -> None:
    board = Board()
    board.post_fail_on = "Second?"
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question(), question("Second?"), question("Third?")])
    assert not res.is_error, res.text
    rows = json.loads(res.text)
    assert [(r["question"], r["action"]) for r in rows] == [
        ("Which db?", "posted"),
        ("Second?", "failed"),
        ("Third?", "posted"),
    ]
    assert "500" in rows[1]["note"]


async def test_withdraw_answered_meanwhile_is_noted(tmp_path: Path) -> None:
    board = Board()
    board.add_decision("Which db?", stored(*A))
    board.withdraw_status = 409
    async with harness(board, tmp_path) as h:
        res = await ask(h, [question(options=opts(("A MVP", True), ("C", False)))])
    row = json.loads(res.text)[0]
    assert row["action"] == "posted" and "answered meanwhile" in row["note"]
