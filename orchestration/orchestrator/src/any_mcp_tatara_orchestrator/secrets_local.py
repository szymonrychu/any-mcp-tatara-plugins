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
from typing import Any
from urllib.parse import quote

import httpx

from any_mcp.context import PluginContext
from any_mcp.types import ToolCall, ToolResult, ToolSpec

__all__ = ["SecretResolveError", "local_secret_tool", "resolve_secret", "secret_error_result"]

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
        raise SecretResolveError("value_not_utf8") from None
    if not value:
        raise SecretResolveError("value_empty")
    return value


def _read_file(path: str) -> bytes:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError:
        raise SecretResolveError("file_missing") from None
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
        raw = await asyncio.wait_for(_read_capped(proc.stdout), COMMAND_TIMEOUT)
        if len(raw) > MAX_BYTES:
            raise SecretResolveError("value_too_large")
        code = await asyncio.wait_for(proc.wait(), COMMAND_TIMEOUT)
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


def secret_error_result(exc: Exception) -> ToolResult:
    """The tool result of a ``SecretResolveError``: its fixed class text, nothing else."""
    text = f"secret_set failed: {exc}"
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
