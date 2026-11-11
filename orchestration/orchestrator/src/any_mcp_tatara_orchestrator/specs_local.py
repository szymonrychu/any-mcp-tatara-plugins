"""Local spec tools: ``spec_pull`` and ``spec_push`` move document bodies between the task board
and files in the working copy, so a body never enters the model's context.

``spec_pull`` copies the documents of a spec into a directory (default
``.scratchpad/<name>/spec/`` under the workdir) with a ``.spec.json`` sidecar that remembers the
version of each file. ``spec_push`` writes files from that directory back as new document
versions, using the sidecar version as the base so a concurrent change is refused (412), never
overwritten. Every path is confined to the workdir (real paths, no ``..``, no symlinks). Results
carry paths, versions and sizes only, never a body.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from any_mcp.errors import UpstreamError
from any_mcp.http import read_capped
from any_mcp.types import HttpBinding, RawResponse, ToolSpec

from .specs import SPEC_PARAM, _hints, _schema

__all__ = ["SpecLocalError", "confine", "local_spec_specs", "spec_pull", "spec_push"]

_BASE = "/agent/v1"
# The relay caps a request body at 256 KiB (agents-orchestrator api/forward.py MAX_BODY_BYTES).
MAX_REQUEST_BYTES = 256 * 1024
_READ_CAP = 16 * 1024 * 1024
SIDECAR = ".spec.json"
_STALE_HINT = "spec_pull, merge, push again"
_DOC_PATH = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,119}\.md$")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class SpecLocalError(Exception):
    """A refusal with a fixed text: never a body, a file content or a token."""


def confine(path: str, root: str) -> str:
    """The real path of ``path`` (relative to ``root`` unless absolute) when it lies inside
    ``root``. No ``..`` segment, and no component below ``root`` may be a symlink; the target
    itself may not exist yet."""
    if not isinstance(path, str) or not path or "\0" in path:
        raise SpecLocalError("path_invalid")
    if ".." in path.replace("\\", "/").split("/"):
        raise SpecLocalError("path_outside_workdir")
    real_root = os.path.realpath(root)
    target = path if os.path.isabs(path) else os.path.join(real_root, path)
    target = os.path.normpath(target)
    if os.path.commonpath([os.path.realpath(target), real_root]) != real_root:
        raise SpecLocalError("path_outside_workdir")
    # normpath keeps the lexical path under the root only if the root itself is not a symlink
    # alias; compare lexically against the real root and walk it for links.
    if os.path.commonpath([target, real_root]) != real_root:
        raise SpecLocalError("path_outside_workdir")
    current = real_root
    for part in os.path.relpath(target, real_root).split(os.sep):
        if part in ("", "."):
            continue
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise SpecLocalError("symlink_refused")
    return target


def local_spec_specs(base_url: str) -> list[ToolSpec]:
    # The HTTP bindings name the routes the tools use so the OpenAPI conformance test holds;
    # OrchestratorPlugin.call runs spec_pull and spec_push itself and never sends the binding.
    return [
        ToolSpec(
            name="spec_pull",
            description=(
                "Copy the documents of a spec into files in your working copy and answer paths, "
                "versions and sizes only (never the text). dir defaults to "
                ".scratchpad/<spec name>/spec/ under the workdir; any dir must stay inside the "
                "workdir. A .spec.json file in dir remembers each document's version for "
                "spec_push. path pulls one document, version an older version of it. Read and "
                "edit the files with your file tools, then spec_push them. Pull again before "
                "editing when somebody else may have changed the spec."
            ),
            input_schema=_schema(
                {
                    "spec": SPEC_PARAM,
                    "path": {"type": "string", "description": "One document path; omitted: all."},
                    "version": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Version of path to pull; omitted: the newest.",
                    },
                    "dir": {
                        "type": "string",
                        "description": "Directory inside the workdir; default "
                        ".scratchpad/<spec name>/spec/.",
                    },
                },
                ("spec",),
            ),
            annotations=_hints(idempotent=True),
            http=HttpBinding("GET", f"{_BASE}/specs/{{spec}}", base_url=base_url),
        ),
        ToolSpec(
            name="spec_push",
            description=(
                "Write files of your working copy to a spec as new document versions, one "
                "version per file, with a note saying why. Name the spec by spec (key), or by "
                "name and title (and project) to use or create the spec of that name. files are "
                "document paths relative to dir (default .scratchpad/<spec name>/spec/), "
                "markdown ending in .md. The base is the version in dir/.spec.json (0 for a new "
                "document); a document changed by somebody else meanwhile is reported as "
                "stale_version with current_version: spec_pull, merge, push again. A document "
                "whose request would exceed 256 KiB is refused before any request "
                "(payload_too_large: split the document). Answers paths and versions, never the "
                "text. Safe to retry: the default client_key is derived from path and text."
            ),
            input_schema=_schema(
                {
                    "spec": SPEC_PARAM,
                    "project": {"type": "string", "description": "With name: project slug."},
                    "name": {"type": "string", "pattern": _NAME.pattern},
                    "title": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "With name: the title when the spec is created.",
                    },
                    "dir": {"type": "string", "description": "Directory inside the workdir."},
                    "files": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50,
                        "items": {"type": "string"},
                        "description": "Document paths relative to dir.",
                    },
                    "note": {"type": "string", "maxLength": 500, "description": "Why."},
                    "client_key": {
                        "type": "string",
                        "maxLength": 100,
                        "description": "Idempotency key; default derived from path and text.",
                    },
                },
                ("files", "note"),
            ),
            annotations=_hints(idempotent=True),
            http=HttpBinding("PUT", f"{_BASE}/specs/{{spec}}/docs/{{path}}", base_url=base_url),
        ),
    ]


def _headers(cfg: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {cfg.token}", "X-Orch-Session": cfg.session}


async def _request(
    http: httpx.AsyncClient,
    cfg: Any,
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    content: bytes | None = None,
) -> RawResponse:
    headers = _headers(cfg)
    if content is not None:
        headers["Content-Type"] = "application/json"
    request = http.build_request(
        method,
        cfg.base_url.rstrip("/") + _BASE + path,
        params=params,
        content=content,
        headers=headers,
    )
    response = await http.send(request, stream=True)
    try:
        body = await read_capped(response, _READ_CAP)
    finally:
        await response.aclose()
    raw = RawResponse(
        response.status_code,
        dict(response.headers),
        body,
        response.headers.get("content-type", ""),
    )
    if raw.status >= 400:
        raise UpstreamError(raw)
    return raw


def _workdir(cfg: Any) -> str:
    return os.path.realpath(cfg.workdir or os.getcwd())


def _doc_path(path: object) -> str:
    if not isinstance(path, str) or not _DOC_PATH.match(path) or ".." in path.split("/"):
        raise SpecLocalError("document_path_invalid")
    return path


def _read_sidecar(directory: str, key: str) -> dict[str, int]:
    target = os.path.join(directory, SIDECAR)
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise SpecLocalError("sidecar_invalid") from None
    docs = data.get("docs") if isinstance(data, dict) else None
    if not isinstance(docs, dict) or data.get("key") != key:
        return {}
    return {p: v for p, v in docs.items() if isinstance(p, str) and isinstance(v, int)}


def _write_text(path: str, text: str) -> None:
    """Write atomically (temp file in the same directory, then replace)."""
    directory, base = os.path.split(path)
    tmp = os.path.join(directory, f".{base}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise SpecLocalError("write_failed") from None


def _write_sidecar(directory: str, key: str, docs: Mapping[str, int]) -> None:
    _write_text(
        os.path.join(directory, SIDECAR),
        json.dumps({"key": key, "docs": dict(sorted(docs.items()))}, indent=1) + "\n",
    )


def _default_dir(name: str) -> str:
    return os.path.join(".scratchpad", name, "spec")


def _spec_key(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"S-[1-9][0-9]{0,17}", value):
        raise SpecLocalError("spec_invalid")
    return value


async def spec_pull(http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    root = _workdir(cfg)
    key = _spec_key(args.get("spec"))
    explicit = args.get("dir")
    directory = confine(explicit, root) if explicit else None  # before any request
    spec = (await _request(http, cfg, "GET", f"/specs/{key}")).json()
    if directory is None:
        directory = confine(_default_dir(str(spec["name"])), root)
    only = args.get("path")
    paths = [_doc_path(only)] if only else [d["path"] for d in spec.get("documents", [])]
    params = {"version": args["version"]} if args.get("version") else None
    docs = _read_sidecar(directory, key) if os.path.isdir(directory) else {}
    files: list[dict[str, Any]] = []
    for path in paths:
        quoted = quote(_doc_path(path), safe="/")
        doc = (
            await _request(http, cfg, "GET", f"/specs/{key}/docs/{quoted}", params=params)
        ).json()
        target = confine(os.path.join(directory, path), root)
        _write_text(target, doc["body"])
        docs[path] = int(doc["version"])
        files.append(
            {"path": path, "version": int(doc["version"]), "size": len(doc["body"].encode())}
        )
    if files:
        _write_sidecar(directory, key, docs)
    return {"key": key, "dir": directory, "files": files}


async def _resolve_spec(
    http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]
) -> tuple[str, str, dict[str, Any] | None]:
    """``(key, name, create)``: the spec key and name, or the create request body (key empty)
    when a spec of that name does not exist yet. Reads only; nothing is written."""
    if args.get("spec"):
        key = _spec_key(args["spec"])
        if args.get("dir"):
            return key, "", None
        return key, str((await _request(http, cfg, "GET", f"/specs/{key}")).json()["name"]), None
    name, title = args.get("name"), args.get("title")
    if not isinstance(name, str) or not _NAME.match(name):
        raise SpecLocalError("spec_or_name_required")
    project = {"project": args["project"]} if args.get("project") else {}
    params = {"name": name, **project}
    found = (await _request(http, cfg, "GET", "/specs", params=params)).json().get("items", [])
    if found:
        return str(found[0]["key"]), name, None
    if not isinstance(title, str) or not title:
        raise SpecLocalError("title_required_to_create")
    return "", name, {"name": name, "title": title, **project}


def _encode_put(text: str, base: int, note: str, client_key: str) -> bytes:
    body = {"body": text, "base_version": base, "note": note, "client_key": client_key}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_REQUEST_BYTES:
        raise SpecLocalError("payload_too_large: split the document")
    return data


def _client_key(args: Mapping[str, Any], path: str, text: str) -> str:
    if args.get("client_key"):
        return f"{args['client_key']}:{path}"[:200]
    return "push-" + hashlib.sha256((path + text).encode("utf-8")).hexdigest()[:16]


def _read_file(directory: str, root: str, doc: str) -> str:
    try:
        with open(confine(os.path.join(directory, doc), root), encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        raise SpecLocalError("file_unreadable") from None


async def spec_push(http: httpx.AsyncClient, cfg: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    root = _workdir(cfg)
    raw_files = args.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise SpecLocalError("files_required")
    files = list(dict.fromkeys(_doc_path(p) for p in raw_files))
    note = str(args.get("note") or "")
    explicit = args.get("dir")
    directory = confine(explicit, root) if explicit else ""
    key, name, create = await _resolve_spec(http, cfg, args)
    if not directory:
        directory = confine(_default_dir(name), root)
    texts = {doc: _read_file(directory, root, doc) for doc in files}
    # The size check runs on the encoded request, before the spec is created or any PUT is sent.
    for doc in files:
        _encode_put(texts[doc], 0, note, _client_key(args, doc, texts[doc]))
    base_docs = _read_sidecar(directory, key) if key else {}
    if create is not None:
        created = await _request(http, cfg, "POST", "/specs", content=json.dumps(create).encode())
        key = str(created.json()["key"])
    results: list[dict[str, Any]] = []
    docs = dict(base_docs)
    for doc in files:
        data = _encode_put(
            texts[doc], base_docs.get(doc, 0), note, _client_key(args, doc, texts[doc])
        )
        try:
            raw = await _request(
                http, cfg, "PUT", f"/specs/{key}/docs/{quote(doc, safe='/')}", content=data
            )
        except UpstreamError as exc:
            current = _stale(exc)
            if current is None:
                raise
            results.append(
                {
                    "path": doc,
                    "error": "stale_version",
                    "current_version": current,
                    "hint": _STALE_HINT,
                }
            )
            continue
        docs[doc] = int(raw.json()["version"])
        _write_sidecar(directory, key, docs)
        results.append({"path": doc, "version": docs[doc]})
    return {"key": key, "files": results}


def _stale(exc: UpstreamError) -> int | None:
    if exc.response.status != 412:
        return None
    try:
        error = exc.response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return None
    if isinstance(error, dict) and error.get("code") == "stale_version":
        current = error.get("current_version")
        return int(current) if isinstance(current, int) else 0
    return None
