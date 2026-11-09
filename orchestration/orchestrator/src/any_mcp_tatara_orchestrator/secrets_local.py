"""Local helpers for the webhook tools that receive an issued credential.

``to_file`` receives the credential the upstream issues once: it is confined to the secrets
directory and written 0600; it never appears in a tool result. Errors carry a fixed class name
only. Secret values of presets live in agents-vault (vault_list, vault_attach, vault_detach).
"""

from __future__ import annotations

import contextlib
import os

from any_mcp.types import ToolResult

__all__ = [
    "CREDENTIAL_TOOLS",
    "OVERWRITE_SCHEMA",
    "SecretResolveError",
    "secrets_root",
    "secret_error_result",
    "validate_to_file",
    "write_credential",
]

# Webhook tools whose answer may carry a credential the upstream issues once; each takes an
# optional ``to_file`` that receives it (see ``write_credential``).
CREDENTIAL_TOOLS = frozenset(
    {"webhook_create", "webhook_update", "webhook_clone", "webhook_rotate"}
)
TO_FILE_SCHEMA = {
    "type": "string",
    "description": (
        "Absolute path in this pod to write the issued credential to (mode 0600, replaced "
        "atomically) and inside the secrets directory (default ~/.agent-secrets). An existing "
        "file is refused unless overwrite is true. The credential is never returned to you; "
        "the answer says whether it was written."
    ),
}
OVERWRITE_SCHEMA = {"type": "boolean", "description": "Replace an existing to_file target."}


class SecretResolveError(Exception):
    """A failure class name only; the text never holds a value, a path or command output."""


def secrets_root(configured: str | None) -> str:
    """The real path of the allowed root for ``to_file``, created 0700 if missing."""
    root = os.path.expanduser(configured or "~/.agent-secrets")
    try:
        os.makedirs(root, mode=0o700, exist_ok=True)
        os.chmod(root, 0o700)
        return os.path.realpath(root)
    except OSError:
        raise SecretResolveError("to_file_failed") from None


def validate_to_file(path: object, root: str, overwrite: bool = False) -> str:
    """The real target path if it lies directly inside or below ``root`` (symlinks and ``..``
    resolved), is not a symlink or directory, and either is new or ``overwrite`` is set."""
    if not isinstance(path, str) or not path or "\0" in path or not os.path.isabs(path):
        raise SecretResolveError("to_file_invalid")
    real_root = os.path.realpath(root)
    parent = os.path.realpath(os.path.dirname(path))
    if not os.path.isdir(parent) or os.path.commonpath([parent, real_root]) != real_root:
        raise SecretResolveError("to_file_invalid")
    target = os.path.join(parent, os.path.basename(path))
    if not os.path.basename(path) or target == real_root or os.path.islink(target):
        raise SecretResolveError("to_file_invalid")
    if os.path.isdir(target):
        raise SecretResolveError("to_file_invalid")
    if os.path.exists(target) and not overwrite:
        raise SecretResolveError("to_file_exists")
    return target


def write_credential(path: str, value: str, overwrite: bool = False) -> None:
    """Write ``value`` to ``path`` with mode 0600.

    Without ``overwrite`` the file is created with O_CREAT|O_EXCL|O_NOFOLLOW, so an existing
    file or symlink (even one that appeared after validation) is refused atomically. With
    ``overwrite`` a temp file in the same directory is atomically replaced over the target, so a
    reader never sees a partial file."""
    data = value.encode("utf-8")
    if not overwrite:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            raise SecretResolveError("to_file_exists") from None
        except OSError:
            raise SecretResolveError("to_file_failed") from None
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(path)
            raise SecretResolveError("to_file_failed") from None
        return
    directory, base = os.path.split(path)
    tmp = os.path.join(directory, f".{base}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            os.unlink(tmp)
            raise
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise SecretResolveError("to_file_failed") from None


def secret_error_result(exc: Exception, tool: str) -> ToolResult:
    """The tool result of a ``SecretResolveError``: its fixed class text, nothing else."""
    text = f"{tool} failed: {exc}"
    return ToolResult(
        text=text, structured={"error": {"kind": "secret", "message": text}}, is_error=True
    )
