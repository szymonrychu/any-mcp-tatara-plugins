"""Credential file writer for the webhook tools (the secret_set resolver is gone, T-72 S2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from any_mcp_tatara_orchestrator.secrets_local import (
    SecretResolveError,
    validate_to_file,
    write_credential,
)


def test_no_overwrite_create_is_atomic_against_a_racing_file(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    validated = validate_to_file(str(target), str(tmp_path), overwrite=False)
    target.write_text("racer")  # appears between the check and the create
    with pytest.raises(SecretResolveError, match="to_file_exists"):
        write_credential(validated, "new-value", overwrite=False)
    assert target.read_text() == "racer"


def test_no_overwrite_create_refuses_a_racing_symlink(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    victim.write_text("keep")
    target = tmp_path / "cred"
    validated = validate_to_file(str(target), str(tmp_path), overwrite=False)
    target.symlink_to(victim)
    with pytest.raises(SecretResolveError):
        write_credential(validated, "new-value", overwrite=False)
    assert victim.read_text() == "keep"


def test_no_overwrite_create_writes_mode_0600(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    write_credential(str(target), "v", overwrite=False)
    assert target.read_text() == "v"
    assert target.stat().st_mode & 0o777 == 0o600


def test_overwrite_replaces_existing(tmp_path: Path) -> None:
    target = tmp_path / "cred"
    target.write_text("old")
    write_credential(str(target), "new", overwrite=True)
    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o600
