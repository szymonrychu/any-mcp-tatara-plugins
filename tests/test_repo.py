"""Repo-level tests: tooling, CI, plugin layout and README."""

import re
import sys
import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _root_config() -> dict:
    return _toml(ROOT / "pyproject.toml")


def _matches_any(rel: str, patterns: list[str]) -> bool:
    return any(fnmatch(rel, pattern) for pattern in patterns)


def _is_workspace_member(rel: str) -> bool:
    workspace = _root_config()["tool"]["uv"]["workspace"]
    return _matches_any(rel, workspace["members"]) and not _matches_any(
        rel, workspace.get("exclude", [])
    )


def _is_collected_test_dir(rel: str) -> bool:
    return _matches_any(rel, _root_config()["tool"]["pytest"]["ini_options"]["testpaths"])


def _plugin_dirs() -> list[Path]:
    return sorted(
        path.parent
        for path in ROOT.glob("*/*/pyproject.toml")
        if not path.relative_to(ROOT).parts[0].startswith(".")
    )


def test_mise_tooling():
    mise = _toml(ROOT / ".mise.toml")
    assert mise["tools"] == {"python": "3.12", "uv": "latest"}
    assert mise["env"]["_"]["python"]["venv"] == {"path": ".venv", "create": True}
    tasks = {name: task["run"] for name, task in mise["tasks"].items()}
    assert tasks == {
        "install": "uv sync --all-packages",
        "test": "uv run pytest",
        "lint": "uv run ruff check . && uv run ruff format --check .",
        "fmt": "uv run ruff format . && uv run ruff check --fix .",
    }

    config = _root_config()
    assert "project" not in config, "the root is a virtual workspace, not a package"
    dev = config["dependency-groups"]["dev"]
    assert "pytest>=8.3" in dev
    assert "pytest-asyncio" in dev
    assert "ruff" in dev
    ruff = config["tool"]["ruff"]
    assert ruff["line-length"] == 100
    assert ruff["target-version"] == "py312"
    assert ruff["lint"]["select"] == ["E", "F", "I", "UP", "B", "SIM"]
    pytest_options = config["tool"]["pytest"]["ini_options"]
    assert pytest_options["asyncio_mode"] == "auto"
    # Several plugins may each hold a test module of the same name.
    assert "--import-mode=importlib" in pytest_options["addopts"]
    assert (ROOT / "uv.lock").is_file()


def test_interpreter_is_312():
    assert sys.version_info[:2] == (3, 12)


def test_ci_workflow():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^on:\n  push:\n  pull_request:\n", workflow, re.MULTILINE)
    assert re.search(r"^permissions:\n  contents: read\n", workflow, re.MULTILINE)
    uses = re.findall(r"uses: (\S+)", workflow)
    assert [ref.split("@")[0] for ref in uses] == ["actions/checkout", "jdx/mise-action"]
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
    runs = re.findall(r"- run: (.+)", workflow)
    assert runs == ["mise run install", "mise run lint", "mise run test"]
    assert "secrets" not in workflow


def test_plugin_layout():
    # The member and test globs cover a topic/plugin directory, and nothing else at the root.
    assert _is_workspace_member("orchestration/orchestrator")
    assert _is_collected_test_dir("orchestration/orchestrator/tests")
    assert _is_collected_test_dir("tests")
    for not_a_member in ("tests/__pycache__", ".github/workflows", ".venv/bin"):
        assert not _is_workspace_member(not_a_member), not_a_member

    for plugin in _plugin_dirs():
        rel = plugin.relative_to(ROOT).as_posix()
        assert _is_workspace_member(rel), rel
        project = _toml(plugin / "pyproject.toml")["project"]
        assert project["entry-points"]["any_mcp.plugins"], rel
        assert (plugin / "src").is_dir(), rel
        assert (plugin / "tests").is_dir(), rel
        assert _is_collected_test_dir(f"{rel}/tests"), rel


def test_readme_documents_layout_and_notation():
    readme = (ROOT / "README.md").read_text()
    assert "<topic>/<plugin>/" in readme
    assert "any_mcp.plugins" in readme
    assert re.search(r"^## Adding a plugin$", readme, re.MULTILINE)
    assert re.search(r"^## Source notation$", readme, re.MULTILINE)
    assert (
        "git::https://github.com/szymonrychu/any-mcp-tatara-plugins.git//<topic>/<plugin>?ref=vX.Y.Z"
        in readme
    )
    assert "github.com/szymonrychu/any-mcp-tatara-plugins//<topic>/<plugin>?ref=vX.Y.Z" in readme
    assert re.search(r"any-mcp plugins install \./", readme)
    assert "source:" in readme
    assert "vX.Y.Z" in readme
