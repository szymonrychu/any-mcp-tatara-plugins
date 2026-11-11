"""T-116 C2: spec_pull and spec_push move document bodies between the task board and files."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from any_mcp.testing import PluginHarness
from any_mcp_tatara_orchestrator import OrchestratorPlugin
from any_mcp_tatara_orchestrator.specs_local import local_spec_specs

OPENAPI = json.loads((Path(__file__).parent / "fixtures" / "orchestrator_openapi.json").read_text())
BODY = "# Plan\n\nSecret-looking-but-fine body text.\n"
CAP = 256 * 1024


class Board:
    """A tiny fake of the orchestrator spec relay."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str, bytes]] = []
        self.specs: dict[str, dict[str, Any]] = {
            "S-3": {"key": "S-3", "name": "my-spec", "project": "p", "docs": {}}
        }
        self.stale: set[str] = set()

    def add(self, path: str, body: str, version: int = 1) -> None:
        self.specs["S-3"]["docs"][path] = {"version": version, "body": body}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        self.requests.append((request.method, p, request.url.query.decode(), request.content))
        if m := re.fullmatch(r"/agent/v1/specs/(S-\d+)", p):
            spec = self.specs.get(m[1])
            if spec is None:
                return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})
            docs = [
                {"path": k, "version": v["version"], "size": len(v["body"]), "sha256": "a"}
                for k, v in spec["docs"].items()
            ]
            return httpx.Response(
                200, json={**{k: spec[k] for k in ("key", "name")}} | {"documents": docs}
            )
        if m := re.fullmatch(r"/agent/v1/specs/(S-\d+)/docs/(.+)", p):
            spec = self.specs[m[1]]
            if request.method == "GET":
                doc = spec["docs"].get(m[2])
                if doc is None:
                    return httpx.Response(404, json={"error": {"code": "not_found"}})
                want = request.url.params.get("version")
                if want and int(want) != doc["version"]:
                    return httpx.Response(404, json={"error": {"code": "not_found"}})
                return httpx.Response(
                    200, json={"path": m[2], "version": doc["version"], "body": doc["body"]}
                )
            put = json.loads(request.content)
            current = spec["docs"].get(m[2], {"version": 0})["version"]
            if m[2] in self.stale or put["base_version"] != current:
                return httpx.Response(
                    412,
                    json={
                        "error": {
                            "code": "stale_version",
                            "message": "changed",
                            "current_version": current + 1,
                        }
                    },
                )
            spec["docs"][m[2]] = {"version": current + 1, "body": put["body"]}
            return httpx.Response(201, json={"path": m[2], "version": current + 1})
        if p == "/agent/v1/specs" and request.method == "GET":
            name = request.url.params.get("name")
            items = [s for s in self.specs.values() if s["name"] == name]
            return httpx.Response(
                200,
                json={
                    "items": [{"key": s["key"], "name": s["name"]} for s in items],
                    "next_cursor": None,
                },
            )
        if p == "/agent/v1/specs" and request.method == "POST":
            body = json.loads(request.content)
            key = f"S-{len(self.specs) + 10}"
            self.specs[key] = {"key": key, "name": body["name"], "project": "p", "docs": {}}
            return httpx.Response(201, json={"key": key, "name": body["name"], "documents": []})
        return httpx.Response(404, json={"error": {"code": "not_found"}})

    def puts(self) -> list[Any]:
        return [json.loads(c) for m, _, _, c in self.requests if m == "PUT"]


def harness(board: Board, workdir: Path) -> PluginHarness:
    return PluginHarness(
        OrchestratorPlugin,
        config={
            "base_url": "http://orch.test:8081",
            "token": "tok-for-tests",
            "session": "s-1",
            "agent": "agent-0",
            "slug": "my-hive",
            "workdir": str(workdir),
        },
        name="orchestrator",
        transport=httpx.MockTransport(board),
    )


def test_rows_have_http_bindings_in_the_vendored_openapi() -> None:
    rows = {s.name: s for s in local_spec_specs("http://x")}
    assert set(rows) == {"spec_pull", "spec_push"}
    assert rows["spec_pull"].http is not None and rows["spec_push"].http is not None
    assert (rows["spec_pull"].http.method, rows["spec_pull"].http.path) == (
        "GET",
        "/agent/v1/specs/{spec}",
    )
    assert (rows["spec_push"].http.method, rows["spec_push"].http.path) == (
        "PUT",
        "/agent/v1/specs/{spec}/docs/{path}",
    )
    assert "get" in OPENAPI["paths"]["/agent/v1/specs/{path}"]
    assert "put" in OPENAPI["paths"]["/agent/v1/specs/{path}"]
    for s in rows.values():
        assert s.description.isascii()


async def test_pull_writes_files_and_sidecar(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY, 2)
    board.add("research/topic.md", "# R\n", 5)
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "work/spec"})
    assert not res.is_error, res.text
    root = tmp_path / "work" / "spec"
    assert (root / "plan.md").read_text() == BODY
    assert (root / "research" / "topic.md").read_text() == "# R\n"
    assert json.loads((root / ".spec.json").read_text()) == {
        "key": "S-3",
        "docs": {"plan.md": 2, "research/topic.md": 5},
    }
    out = json.loads(res.text)
    assert out["key"] == "S-3"
    assert sorted(f["path"] for f in out["files"]) == ["plan.md", "research/topic.md"]
    assert {f["version"] for f in out["files"]} == {2, 5}


async def test_default_dir_under_scratchpad_name_spec(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY)
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_spec_pull", {"spec": "S-3"})
    assert not res.is_error, res.text
    assert (tmp_path / ".scratchpad" / "my-spec" / "spec" / "plan.md").read_text() == BODY
    assert json.loads(res.text)["dir"] == str(tmp_path / ".scratchpad" / "my-spec" / "spec")


async def test_pull_one_path_and_version(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY, 2)
    board.add("b.md", "b", 1)
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_pull", {"spec": "S-3", "path": "plan.md", "version": 2}
        )
    assert not res.is_error, res.text
    root = tmp_path / ".scratchpad" / "my-spec" / "spec"
    assert (root / "plan.md").exists() and not (root / "b.md").exists()
    assert any(q == "version=2" for _, _, q, _ in board.requests)


@pytest.mark.parametrize("bad", ["/etc/spec", "../escape", "a/../../escape"])
async def test_dir_outside_workdir_refused(tmp_path: Path, bad: str) -> None:
    board = Board()
    board.add("plan.md", BODY)
    async with harness(board, tmp_path / "w") as h:
        (tmp_path / "w").mkdir(exist_ok=True)
        res = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": bad})
    assert res.is_error
    assert not (tmp_path / "escape").exists()
    assert board.requests == []


async def test_dotdot_refused(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY)
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "a/../b"})
    assert res.is_error
    assert board.requests == []


async def test_symlink_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    os.symlink(outside, work / "link")
    board = Board()
    board.add("plan.md", BODY)
    async with harness(board, work) as h:
        res = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "link"})
        assert res.is_error
        # a symlinked file inside the dir is refused as well
        (work / "d").mkdir()
        os.symlink(outside / "victim.md", work / "d" / "plan.md")
        res2 = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "d"})
    assert res2.is_error
    assert not (outside / "victim.md").exists()
    assert list(outside.iterdir()) == []


async def test_push_uses_sidecar_base(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY, 2)
    async with harness(board, tmp_path) as h:
        await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "d"})
        (tmp_path / "d" / "plan.md").write_text("# Plan v3\n")
        res = await h.call(
            "orchestrator_spec_push",
            {"spec": "S-3", "dir": "d", "files": ["plan.md"], "note": "edit"},
        )
    assert not res.is_error, res.text
    put = board.puts()[0]
    assert put["base_version"] == 2 and put["body"] == "# Plan v3\n" and put["note"] == "edit"
    assert re.fullmatch(r"push-[0-9a-f]{16}", put["client_key"])
    assert json.loads(res.text) == {"key": "S-3", "files": [{"path": "plan.md", "version": 3}]}
    assert json.loads((tmp_path / "d" / ".spec.json").read_text())["docs"] == {"plan.md": 3}


async def test_push_new_path_has_base_zero(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "new.md").write_text("x")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {"spec": "S-3", "dir": "d", "files": ["new.md"], "note": "n", "client_key": "ck"},
        )
    assert not res.is_error, res.text
    put = board.puts()[0]
    assert put["base_version"] == 0 and put["client_key"].startswith("ck")


async def test_push_creates_spec_when_name_new(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "plan.md").write_text("x")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {
                "project": "p",
                "name": "fresh",
                "title": "Fresh",
                "dir": "d",
                "files": ["plan.md"],
                "note": "first",
            },
        )
    assert not res.is_error, res.text
    out = json.loads(res.text)
    assert out["key"] == "S-11"
    assert any(m == "POST" for m, _, _, _ in board.requests)
    created = [json.loads(c) for m, _, _, c in board.requests if m == "POST"][0]
    assert created["name"] == "fresh" and created["title"] == "Fresh" and created["project"] == "p"
    assert json.loads((tmp_path / "d" / ".spec.json").read_text())["key"] == "S-11"


async def test_push_to_existing_name_does_not_create(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "plan.md").write_text("x")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {"name": "my-spec", "title": "T", "dir": "d", "files": ["plan.md"], "note": "n"},
        )
    assert not res.is_error, res.text
    assert json.loads(res.text)["key"] == "S-3"
    assert not any(m == "POST" for m, _, _, _ in board.requests)


async def test_push_412_hint(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY, 4)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "plan.md").write_text("mine")
    (tmp_path / "d" / "ok.md").write_text("fine")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {"spec": "S-3", "dir": "d", "files": ["plan.md", "ok.md"], "note": "n"},
        )
    out = json.loads(res.text)
    assert out["files"][0] == {
        "path": "plan.md",
        "error": "stale_version",
        "current_version": 5,
        "hint": "spec_pull, merge, push again",
    }
    assert out["files"][1] == {"path": "ok.md", "version": 1}
    assert board.specs["S-3"]["docs"]["plan.md"]["body"] == BODY


async def test_push_encoded_size_refused_locally(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    # 192 KiB of quotes: under the body cap, 384 KiB once JSON-escaped.
    (tmp_path / "d" / "big.md").write_text('"' * (192 * 1024))
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {"spec": "S-3", "dir": "d", "files": ["big.md"], "note": "n"},
        )
    assert res.is_error
    assert "payload_too_large: split the document" in res.text
    assert board.requests == []


async def test_push_typical_192kib_markdown_passes(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    text = ("word " * 80 + "\n") * 480
    assert 150 * 1024 < len(text.encode()) <= 192 * 1024
    (tmp_path / "d" / "big.md").write_text(text)
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push", {"spec": "S-3", "dir": "d", "files": ["big.md"], "note": "n"}
        )
    assert not res.is_error, res.text
    assert len(board.requests[-1][3]) < CAP


async def test_push_non_ascii_not_escaped(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "u.md").write_text("zażółć ☃\n")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push", {"spec": "S-3", "dir": "d", "files": ["u.md"], "note": "n"}
        )
    assert not res.is_error, res.text
    raw = board.requests[-1][3]
    assert "zażółć ☃".encode() in raw
    assert b"\\u" not in raw


async def test_push_refuses_a_file_outside_dir(tmp_path: Path) -> None:
    board = Board()
    (tmp_path / "d").mkdir()
    (tmp_path / "secret.md").write_text("s")
    async with harness(board, tmp_path) as h:
        res = await h.call(
            "orchestrator_spec_push",
            {"spec": "S-3", "dir": "d", "files": ["../secret.md"], "note": "n"},
        )
    assert res.is_error
    assert board.requests == []


async def test_no_body_in_any_result(tmp_path: Path) -> None:
    board = Board()
    board.add("plan.md", BODY, 2)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "n.md").write_text("PUSHED-BODY-MARK")
    async with harness(board, tmp_path) as h:
        pulled = await h.call("orchestrator_spec_pull", {"spec": "S-3", "dir": "e"})
        pushed = await h.call(
            "orchestrator_spec_push", {"spec": "S-3", "dir": "d", "files": ["n.md"], "note": "n"}
        )
    for res in (pulled, pushed):
        blob = res.text + json.dumps(res.structured)
        assert "Secret-looking" not in blob and "PUSHED-BODY-MARK" not in blob


async def test_upstream_404_is_surfaced(tmp_path: Path) -> None:
    board = Board()
    async with harness(board, tmp_path) as h:
        res = await h.call("orchestrator_spec_pull", {"spec": "S-99"})
    assert res.is_error
    assert "not_found" in res.text
