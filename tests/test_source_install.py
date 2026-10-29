"""The orchestrator plugin as an installable source: its metadata and both source forms.

The end-to-end test needs the work committed: the git form pins ``?ref=`` to the HEAD sha.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "orchestration" / "orchestrator"
DIST = "any-mcp-tatara-orchestrator"
ENTRY_POINT = "any_mcp_tatara_orchestrator:OrchestratorPlugin"
ANY_MCP = "any-mcp @ git+https://github.com/szymonrychu/any-mcp@v0.5.0"
UV = shutil.which("uv")
assert UV, "uv is a pinned mise tool and must be on PATH"
ALL_TOOLS = sorted(
    [
        "orchestrator_me",
        "orchestrator_leadership_handover",
        "orchestrator_lock_claim",
        "orchestrator_lock_release",
        "orchestrator_lock_list",
        "orchestrator_lock_reassign",
        "orchestrator_scale",
        "orchestrator_init_scripts_get",
        "orchestrator_init_scripts_edit",
        "orchestrator_sessions_list",
        "orchestrator_session_send",
        "orchestrator_chat_rooms",
        "orchestrator_chat_send",
        "orchestrator_chat_react",
        "orchestrator_chat_status",
        "orchestrator_activity",
    ]
)
# Literal values only: the plugin does no network I/O at load.
CONFIG = {
    "base_url": "http://orch.test:8081",
    "token": "t",
    "session": "s-1",
    "agent": "agent-0",
    "slug": "my-hive",
    "max_replicas": 3,
    "hive": True,
}


def _run(cmd: list[str | Path], **kwargs) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True, timeout=600, **kwargs
    )
    assert proc.returncode == 0, f"{cmd} failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def _list_tools(any_mcp: Path, config: Path, env: dict[str, str]) -> list[str]:
    listed = _run([any_mcp, "--config", config, "--list-tools"], env=env, cwd=config.parent)
    return sorted(tool["name"] for tool in json.loads(listed.stdout))


def _config(path: Path, source: str) -> Path:
    entry = {"name": "orchestrator", "source": source, "config": CONFIG}
    path.write_text(json.dumps({"plugins": [entry]}))
    return path


def test_orchestrator_distribution_metadata():
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text())
    meta = project["project"]
    assert meta["name"] == DIST
    assert meta["version"] == "0.2.0"
    assert meta["requires-python"] == ">=3.12"
    assert meta["dependencies"] == ["any-mcp"]
    assert meta["entry-points"] == {"any_mcp.plugins": {"orchestrator": ENTRY_POINT}}
    assert project["build-system"]["build-backend"] == "hatchling.build"

    installed = distribution(DIST)
    assert installed.version == "0.2.0"
    assert installed.requires == ["any-mcp"]
    points = installed.entry_points.select(group="any_mcp.plugins")
    assert [(ep.name, ep.value) for ep in points] == [("orchestrator", ENTRY_POINT)]


def test_dev_group_pins_any_mcp_release():
    root = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "any-mcp" in root["dependency-groups"]["dev"]
    assert root["tool"]["uv"]["sources"]["any-mcp"] == {
        "git": "https://github.com/szymonrychu/any-mcp",
        "tag": "v0.5.0",
    }
    assert distribution("any-mcp").version == "0.5.0"


def test_orchestrator_plugin_installs_from_sources(tmp_path):
    venv = tmp_path / "venv"
    _run([UV, "venv", "--seed", "--python", sys.executable, venv])
    _run([UV, "pip", "install", "--python", venv / "bin" / "python", ANY_MCP])
    any_mcp = venv / "bin" / "any-mcp"
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PIP_", "ANY_MCP_", "VIRTUAL_ENV", "PYTHON", "UV_"))
    }
    env["ANY_MCP_CACHE_DIR"] = str(tmp_path / "cache")

    # Local directory form, relative to the working directory.
    local = "./orchestration/orchestrator"
    installed = _run([any_mcp, "plugins", "install", local], env=env, cwd=ROOT)
    assert installed.stdout.splitlines() == [f"{local} {DIST} local"]
    # The same directory named absolutely is the same manifest entry: no reinstall at load.
    config = _config(tmp_path / "local.json", str(PLUGIN))
    assert _list_tools(any_mcp, config, env) == ALL_TOOLS

    # git::file:// form of this repository, with //subdir and ?ref= pinned to the HEAD sha.
    sha = _run(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    source = f"git::file://{ROOT}//orchestration/orchestrator?ref={sha}"
    installed = _run([any_mcp, "plugins", "install", source], env=env, cwd=tmp_path)
    assert installed.stdout.splitlines() == [f"{source} {DIST} {sha}"]
    config = _config(tmp_path / "git.json", source)
    assert _list_tools(any_mcp, config, env) == ALL_TOOLS
