"""Local secret resolver: a tool that reads a secret in-process and PUTs it to the orchestrator.

The model passes a reference (``from_file``, ``from_env`` or ``from_command``), never the value.
The value is resolved here, sent once as the PUT body, and never appears in a tool result, an
error message, a log line or an exception: errors carry a fixed class name only.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import stat
from typing import Any
from urllib.parse import quote

import httpx

from any_mcp.context import PluginContext
from any_mcp.types import ToolCall, ToolResult, ToolSpec

__all__ = [
    "CREDENTIAL_TOOLS",
    "OVERWRITE_SCHEMA",
    "SPEC_TOOLS",
    "SecretResolveError",
    "local_secret_tool",
    "refuse_protected_values",
    "resolve_secret",
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

# Tools whose ``spec`` input may declare env variables; a protected one never carries a value.
SPEC_TOOLS = frozenset({"preset_create", "preset_replace", "session_create"})


def refuse_protected_values(spec: object) -> None:
    """Raise if a protected env entry of ``spec`` carries a value (it would enter the
    transcript); the error names the variable only."""
    env = spec.get("env") if isinstance(spec, dict) else None
    if not isinstance(env, dict):
        return
    for name, entry in env.items():
        if isinstance(entry, dict) and entry.get("protected") and entry.get("value") is not None:
            raise SecretResolveError(
                f"protected_value_refused env={name}: never put a protected value in a tool "
                "call; declare it protected without a value and use secret_set"
            )


MAX_BYTES = 64 * 1024
COMMAND_TIMEOUT = 10.0
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PRESET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCES = ("from_file", "from_env", "from_command")

_DESCRIPTION = (
    "Set a write-only secret of a preset from a reference, never from a value you type. "
    "Give exactly one of from_file (a path in this pod), from_env (an environment variable "
    "name of this pod) or from_command (a command line, run without a shell, 10 s limit). The "
    "value is read in this process and sent to the orchestrator; you only get back "
    "{name, set, version, bytes}. A trailing newline is stripped once."
)


class SecretResolveError(Exception):
    """A failure class name only; the text never holds a value, a path or command output."""


def _strip_newline(raw: bytes) -> str:
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        value = None
    if value is None:
        raise SecretResolveError("value_not_utf8")
    if not value:
        raise SecretResolveError("value_empty")
    return value


def _read_file(path: str) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        raise SecretResolveError("file_missing") from None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise SecretResolveError("file_not_regular")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        raise SecretResolveError("file_missing") from None
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise SecretResolveError("value_too_large")
    return raw


async def _read_capped(stream: asyncio.StreamReader) -> bytes:
    """Read until EOF or one byte past the cap."""
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_BYTES:
        chunk = await stream.read(MAX_BYTES + 1 - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


async def _run(command: str) -> bytes:
    try:
        argv = shlex.split(command)
    except ValueError:
        raise SecretResolveError("command_invalid") from None
    if not argv:
        raise SecretResolveError("command_invalid")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise SecretResolveError("command_not_found") from None
    assert proc.stdout is not None
    try:
        async with asyncio.timeout(COMMAND_TIMEOUT):
            raw = await _read_capped(proc.stdout)
            if len(raw) > MAX_BYTES:
                raise SecretResolveError("value_too_large")
            code = await proc.wait()
    except TimeoutError:
        raise SecretResolveError("command_timeout") from None
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
    if code != 0:
        raise SecretResolveError(f"command_failed exit_code={code}")
    return raw


async def resolve_secret(
    *, from_file: str | None = None, from_env: str | None = None, from_command: str | None = None
) -> str:
    """Resolve exactly one source to the secret value (one trailing newline stripped)."""
    given = [s for s in (from_file, from_env, from_command) if s is not None]
    if not all(isinstance(s, str) for s in given):
        raise SecretResolveError("invalid_source")
    if len(given) != 1:
        raise SecretResolveError("exactly_one_source")
    if from_file is not None:
        return _strip_newline(_read_file(from_file))
    if from_env is not None:
        value = os.environ.get(from_env)
        if value is None:
            raise SecretResolveError("env_missing")
        raw = value.encode("utf-8")
        if len(raw) > MAX_BYTES:
            raise SecretResolveError("value_too_large")
        return _strip_newline(raw)
    assert from_command is not None
    return _strip_newline(await _run(from_command))


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


def secret_error_result(exc: Exception, tool: str = "secret_set") -> ToolResult:
    """The tool result of a ``SecretResolveError``: its fixed class text, nothing else."""
    text = f"{tool} failed: {exc}"
    return ToolResult(
        text=text, structured={"error": {"kind": "secret", "message": text}}, is_error=True
    )


def _schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "preset": {"type": "string", "description": "Preset name."},
            "name": {"type": "string", "description": "Declared secret name (env-var style)."},
            "from_file": {"type": "string", "description": "Path of a file in this pod."},
            "from_env": {"type": "string", "description": "Env var name in this pod."},
            "from_command": {"type": "string", "description": "Command line, no shell."},
        },
        "required": ["preset", "name"],
        "additionalProperties": False,
    }


def local_secret_tool(base_url: str, token: str, session: str, ctx: PluginContext) -> ToolSpec:
    """The ``secret_set`` tool: a handler tool, not an HTTP binding, so the value stays local."""

    async def handler(call: ToolCall) -> dict[str, Any]:
        args = call.arguments
        preset, name = str(args["preset"]), str(args["name"])
        if not _PRESET.match(preset) or not _NAME.match(name):
            raise SecretResolveError("invalid_name")
        value = await resolve_secret(**{k: args[k] for k in _SOURCES if k in args})
        url = (
            f"{base_url.rstrip('/')}/agent/v1/admin/presets/"
            f"{quote(preset, safe='')}/secrets/{quote(name, safe='')}"
        )
        try:
            response = await ctx.http.put(
                url,
                json={"value": value},
                headers={"Authorization": f"Bearer {token}", "X-Orch-Session": session},
            )
        except httpx.HTTPError:
            raise SecretResolveError("upload_failed") from None
        if response.status_code >= 400:
            raise SecretResolveError(f"upload_failed http={response.status_code}")
        try:
            meta = response.json()
            version = int(meta["version"])
        except (ValueError, KeyError, TypeError):
            raise SecretResolveError("upload_bad_response") from None
        return {"name": name, "set": True, "version": version, "bytes": len(value.encode())}

    return ToolSpec(
        name="secret_set",
        description=_DESCRIPTION,
        input_schema=_schema(),
        handler=handler,
    )
