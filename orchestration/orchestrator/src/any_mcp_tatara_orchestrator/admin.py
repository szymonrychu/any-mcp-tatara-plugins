"""Declarative rows for the superleader admin routes ``/agent/v1/admin`` of agents-orchestrator.

One row per route; ``__init__`` turns each into a ``ToolSpec``. The orchestrator refuses every
route unless the caller is a superleader session, and the rendered allowlist offers these tools
to such sessions only. Secret values never pass through a row: a preset env variable refers to a
value that a user wrote into agents-vault (vault_list, vault_attach), and webhook
credentials are issued by the upstream and stored by the orchestrator (see ``secrets_local``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .fit import measure
from .merged import Merged, Route
from .secrets_local import OVERWRITE_SCHEMA, TO_FILE_SCHEMA

__all__ = [
    "ADMIN_MERGED",
    "ADMIN_TOOLS",
    "AdminRow",
    "CHAT_FROM",
    "CHAT_HISTORY_LIMIT",
    "CHAT_LIMIT",
    "CHAT_MSO_ID",
    "SESSION_EVENTS_LIMIT",
    "fit_events",
    "fit_sessions",
    "schema",
    "summarize_sessions",
]

_A = "/admin"


# Spec keys kept in the sessions_admin_list summary; "superleader" is spec.session_messaging.
_SESSION_KEYS = (
    "slug",
    "preset_name",
    "project",
    "extra_projects",
    "status",
    "desired",
    "labels",
    "hive",
)


def summarize_sessions(sessions: list[Any]) -> list[Any]:
    """Compact sessions_admin_list answer: a full session view (spec, vault, summary) is large
    and the list covers every project, so each session keeps the identifying fields, its status,
    labels, hive, whether it is a superleader (spec.session_messaging) and created_at;
    session_get shows one in full."""
    out: list[Any] = []
    for session in sessions:
        if not isinstance(session, dict):
            out.append(session)
            continue
        spec = session.get("spec")
        row = {k: session.get(k) for k in _SESSION_KEYS}
        row["superleader"] = isinstance(spec, dict) and spec.get("session_messaging") is True
        row["created_at"] = session.get("created_at")
        out.append(row)
    return out


def fit_sessions(sessions: list[Any], budget: int) -> Any:
    """``sessions`` when its text fits ``budget``, else ``{"omitted": n, "sessions": [...]}``
    with the most leading sessions that fit: the list is not valid JSON once any-mcp cuts it, so
    the plugin drops the tail itself and says how many it dropped."""

    def shaped(keep: int) -> dict[str, Any]:
        return {"omitted": len(sessions) - keep, "sessions": sessions[:keep]}

    if measure(sessions) <= budget:
        return sessions
    lo, hi = 0, len(sessions)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(shaped(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return shaped(lo)


def fit_events(page: Any, budget: int, key: str = "events") -> Any:
    """``page`` (a page ``{<key>, has_more_before, ...}``, ascending ids; key ``events`` for
    session events, ``messages`` for chat history) when its text fits ``budget``, else the same
    page with the NEWEST items that fit and ``omitted``, the count of older ones dropped.
    any-mcp keeps the head of an over-budget text, which would lose the newest items; the
    dropped ones are still reachable with before_id (before for chat history) set to the lowest
    id kept."""
    if not isinstance(page, dict) or not isinstance(page.get(key), list):
        return page
    events = page[key]

    def shaped(keep: int) -> dict[str, Any]:
        rest = {k: v for k, v in page.items() if k != key}
        dropped = len(events) - keep
        if dropped:
            rest["has_more_before"] = True  # dropped older items are reachable with before
        return {"omitted": dropped, **rest, key: events[len(events) - keep :]}

    if measure(page) <= budget:
        return page
    lo, hi = 0, len(events)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(shaped(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return shaped(lo)


def schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        out["required"] = list(required)
    return out


@dataclass(frozen=True)
class AdminRow:
    name: str
    method: str
    path: str  # below /agent/v1
    description: str
    schema: dict[str, Any] = field(default_factory=lambda: schema({}))
    body: str | None = None
    query: tuple[str, ...] = ()


def _str(text: str) -> dict[str, Any]:
    return {"type": "string", "description": text}


def _bounded_int(low: int, high: int, text: str) -> dict[str, Any]:
    return {"type": "integer", "minimum": low, "maximum": high, "description": text}


_OBJ = {"type": "object"}
_REF = _str("Preset id or name.")
_PROJECT = _str("Project slug of an existing project (see project).")
_GRANT_SCHEMA = {
    "reason": {
        "type": "string",
        "minLength": 1,
        "maxLength": 4000,
        "description": "Why the grant is needed, shown to the user who approves it.",
    },
    "client_key": {"type": "string", "maxLength": 200},
}
_NOT_MANAGEABLE = (
    " A target that is not manageable (superleader_manageable false) answers 403 "
    "not_manageable: the change needs the user's approval."
)
_READ_ANY = (
    " Writes on a target that is not manageable (superleader_manageable false) answer 403 "
    "not_manageable (needs the user's approval)."
)
# Rows that write to a preset, session, webhook endpoint or schedule: they get _NOT_MANAGEABLE.
_WRITE_NAMES = frozenset(
    {
        "preset_patch", "preset_delete", "preset_copy", "session_patch", "session_discard",
        "leadership_withdraw", "session_message_cancel",
    }
)  # fmt: skip
_SLUG = _str("Session slug.")
_POD = _str("Agent pod name.")
_SCHED = _str("Schedule id.")
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 200}
_SPEC = {
    "type": "object",
    "description": "Session spec. Secret values do not belong here: attach a vault entry to the "
    "preset with vault_attach.",
}
_PATCH_SPEC = {
    "type": "object",
    "description": "JSON merge patch (RFC 7396) of the stored spec: objects merge, null deletes "
    "a key, arrays replace. Keys you leave out are kept. Secret values do not belong here: "
    "attach a vault entry with vault_attach. Human-only fields (kube_cluster_access, "
    "kube_namespace_access, docker, discard_hook_url, wrapper.permission_mode, "
    "wrapper.permissions) may only move toward safe.",
}
_PATCH_SPEC_SESSION = {
    **_PATCH_SPEC,
    "description": "JSON merge patch (RFC 7396) of the session's stored spec: objects merge, "
    "null deletes a key, arrays replace. Keys you leave out are kept. Secret values do not "
    "belong here. A leader may only lower usage_quota and shorten inactivity limits on its own "
    "session; human-only fields "
    "(kube_cluster_access, kube_namespace_access, docker, discard_hook_url, "
    "wrapper.permission_mode, wrapper.permissions) may only move toward safe; a regular "
    "leader may not turn session_messaging on for its own session.",
}
_PROPOSAL_PATCH = {
    "type": "object",
    "description": "JSON merge patch (RFC 7396) of the target's spec: objects merge, null "
    "deletes a key, arrays replace. Never put a secret value here.",
}
_PROPOSAL_REASON = {"type": "string", "minLength": 1, "maxLength": 4000}
_PROPOSAL_TEXT = (
    "Security changes and human-only fields of presets, schedules and session specs go "
    "here, not through preset_patch or session_patch (which refuse them). Secret values are "
    "never part of a proposal: they live in the vault (vault_attach). The human reviews "
    "every item in the web UI and approves, denies or asks for a rework; proposal_get shows "
    "the verdicts and notes. "
)
_FILTERS = {
    "type": "object",
    "properties": {"allow": {"type": "array"}, "deny": {"type": "array"}},
    "additionalProperties": False,
}
_NULL_STR = {"type": ["string", "null"]}
_ALIASES = {
    "type": ["array", "null"],
    "items": {"type": "string", "minLength": 1, "maxLength": 64},
    "maxItems": 10,
    "description": (
        "Names people use to call the agent in this room, matched as whole words, "
        "case-insensitive, optional leading @. Omit to keep, [] to clear. The bot's own tag "
        "and replies to it always count. Unlike agent_aliases, preset_id and trigger are "
        "replaced on every call (omitted or null clears them): to change only aliases, read "
        "the room with chat_rooms scope projects first and resend its preset_id and trigger."
    ),
}
_AUTH = {
    "auth_header": _NULL_STR,
    "hmac_algorithm": {"type": ["string", "null"], "enum": ["sha256", None]},
    "hmac_prefix": _NULL_STR,
    "hmac_timestamp_header": _NULL_STR,
    "username": _NULL_STR,
}
_CRED = (
    " The upstream issues the credential once; it is stored in a Secret by the orchestrator "
    "and never shown: the answer names the Secret (password_secret, key)."
)
_CHAIN = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 10,
    "description": (
        "Schedule ids started (one run each) when a session started by this trigger finishes "
        "after a handled run; [] clears."
    ),
}
_CHAIN_PATCH = {
    **_CHAIN,
    "description": _CHAIN["description"] + " Omitted or null leaves the links unchanged.",
}
_SCHED_FIELDS = {
    "name": {"type": "string"},
    "cron": {"type": "string", "description": "Five-field cron expression."},
    "preset_id": {"type": "string"},
    "prompt": {"type": "string"},
    "enabled": {"type": "boolean"},
    "timezone": _NULL_STR,
    "then_schedule_ids": {
        **_CHAIN,
        "description": _CHAIN["description"]
        + " On action update, omitting it keeps the current links (resent by the plugin).",
    },
}
_ROOM_ID = _str("Chat room id.")
CHAT_MSO_ID = {"type": "string", "pattern": "^[mso][1-9][0-9]*$"}
CHAT_FROM = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
    "description": "Display name, or the pseudonym in an anonymized room.",
}
_HISTORY_SESSION_ID = _str("Session id (uuid) of the discarded session.")
_HISTORY_SCOPE = " History is read-only: a stored session is deleted by the user in the web UI."
_HISTORY_MATCH = (
    "q (3 to 500 characters) is a case-insensitive substring match, or with regex true a "
    "Postgres regex."
)
CHAT_LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
CHAT_HISTORY_LIMIT = 20  # default page of chat_history
SESSION_EVENTS_LIMIT = 20  # default page of session_events (events carry raw tool data)
HISTORY_SEARCH_LIMIT = 20  # default page: 20 hits stay well inside the 40000 char tool budget
_HISTORY_Q = {"type": "string", "minLength": 3, "maxLength": 500}
_HISTORY_REGEX = {"type": "boolean", "description": "Treat q as a Postgres regex."}
_HISTORY_BEFORE = {
    "type": "integer",
    "minimum": 1,
    "description": "Return rows with an id below this one (the next_before_id of a page).",
}

_ROWS: tuple[AdminRow, ...] = (
    # presets
    AdminRow(
        "preset_get",
        "GET",
        f"{_A}/presets/{{ref}}",
        "Show a preset by id or name, manageable or not. A secret value is never shown."
        + _READ_ANY,
        schema({"ref": _REF}, ("ref",)),
    ),
    AdminRow(
        "preset_create",
        "POST",
        f"{_A}/presets",
        "Create a preset. Secret values come from the vault: after creating, attach entries "
        "with vault_attach.",
        schema({"name": {"type": "string"}, "spec": _SPEC}, ("name",)),
        "*",
    ),
    AdminRow(
        "preset_patch",
        "PATCH",
        f"{_A}/presets/{{ref}}",
        "Change part of a preset (name, labels, project, spec) by id or name. "
        "Set project to move the preset to another existing project (422 unknown_project; "
        "422 vault_label_mismatch when its vault "
        "refs no longer match the project label; 422 project_archived when the target project "
        "is archived); running sessions keep their project. Example: "
        '{"ref": "webhook", "spec": {"usage_quota": {"five_hour_pct": 80}}}.',
        schema(
            {
                "ref": _REF,
                "name": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "project": _PROJECT,
                "superleader_manageable": {
                    "type": "boolean",
                    "enum": [False],
                    "description": "false only: the preset stops being manageable by "
                    "superleaders (writes then need the user's approval); true: ask with "
                    "preset_manageable_request. 409 open_proposals while the preset has active "
                    "change proposals.",
                },
                "spec": _PATCH_SPEC,
            },
            ("ref",),
        ),
        "*",
    ),
    AdminRow(
        "preset_delete",
        "DELETE",
        f"{_A}/presets/{{ref}}",
        "Delete a preset.",
        schema({"ref": _REF}, ("ref",)),
    ),
    AdminRow(
        "preset_copy",
        "POST",
        f"{_A}/presets/{{ref}}/copy",
        "Copy a preset under a new name (default: derived). Check the copy's vault "
        "attachments with vault_list.",
        schema({"ref": _REF, "name": _NULL_STR}, ("ref",)),
        "*",
    ),
    AdminRow(
        "preset_manageable_request",
        "POST",
        f"{_A}/presets/{{ref}}/manageable",
        "Ask the user to make a preset manageable by superleaders (superleader_manageable "
        "true): files a change proposal of kind preset_manageable that the user approves or "
        "denies in the UI; answers the proposal. 409 already_manageable when it already is. "
        "Setting it with preset_patch is refused.",
        schema(
            {
                "ref": _REF,
                "reason": _GRANT_SCHEMA["reason"],
                "client_key": _GRANT_SCHEMA["client_key"],
            },
            ("ref", "reason"),
        ),
        "*",
    ),
    # vault: users write the secret values in the web UI; superleaders only attach them
    AdminRow(
        "vault_list",
        "GET",
        f"{_A}/vault",
        "List vault entries (name, description, labels, env names, readable values, "
        "attachments) and presets with env names to re-attach. Entries of other projects "
        "show without values (names and ids only). Choose an entry by name and "
        "description; protected values are never shown. Each attachment has manageable "
        "(bool: whether its preset is superleader_manageable) and privileged "
        "(bool): true means the preset's sessions have cluster access or docker/dind and "
        "can read every Secret in the namespace, so the entry's values are exposed to them.",
        schema({"q": _str("Filter entries by name or description.")}),
        None,
        ("q",),
    ),
    # sessions
    AdminRow(
        "session_get",
        "GET",
        f"{_A}/sessions/{{slug}}",
        "Show a session by slug, with its agents. A superleader reads any session, manageable "
        "or not, any other leader its own. project is its home project, extra_projects the "
        "other projects it fully belongs to. start_state (booting, ready, bootstrap_failed) and "
        "bootstrap_failures (pod, step, message, redacted log_tail) say why an agent failed to "
        "start: read them and act without the user. Superleader writes on another session: "
        "session_labels op update, scale, lock and session_message_cancel with its slug."
        + _READ_ANY,
        schema({"slug": _SLUG}, ("slug",)),
    ),
    AdminRow(
        "session_patch",
        "PATCH",
        f"{_A}/sessions/{{slug}}",
        "Change part of a live session's spec (JSON merge patch, RFC 7396). A superleader may "
        "patch its own or any manageable session, any other leader only its own. The change "
        "is applied when the pods are replaced, once the session is idle; the source preset "
        "is never written. Arrays replace whole. replicas, max_replicas, static, workdir, "
        "pvc_size and cache cannot change on a live session. Example: "
        '{"slug": "my-hive", "spec": {"usage_quota": {"five_hour_pct": 50}}}.',
        schema({"slug": _SLUG, "spec": _PATCH_SPEC_SESSION}, ("slug", "spec")),
        "*",
    ),
    AdminRow(
        "session_message_cancel",
        "DELETE",
        f"{_A}/sessions/{{slug}}/messages/{{message_id}}",
        "Superleader only: cancels a QUEUED message of that session. "
        "404 not_found for an id of another session; 409 not_queued once it was delivered.",
        schema({"slug": _SLUG, "message_id": {"type": "integer"}}, ("slug", "message_id")),
    ),
    AdminRow(
        "session_create",
        "POST",
        f"{_A}/sessions",
        "Create a session from a preset (preset_id), a spec, or a preset with spec overrides. "
        "The new session is not a trigger session and has its own leader. project places it "
        "(default: the preset's project); it also gets the preset's extra projects.",
        schema(
            {
                "preset_id": _NULL_STR,
                "slug": _NULL_STR,
                "spec": {"type": ["object", "null"]},
                "project": {
                    "type": ["string", "null"],
                    "description": "Project slug of an existing, active project (see "
                    "project); omitted: the preset's project.",
                },
            }
        ),
        "*",
    ),
    AdminRow(
        "session_discard",
        "DELETE",
        f"{_A}/sessions/{{slug}}",
        "Discard a session by slug. Your own session is refused (own_session). Set force true "
        "only when the session has been stuck in phase deleting because a pre-discard hook "
        "keeps failing: the owed hooks are abandoned (reason forced) and the discard finishes "
        "at once, so the history export may be lost; the workspace PVC is kept. Without force "
        "the discard is unchanged.",
        schema(
            {
                "slug": _SLUG,
                "force": {
                    "type": "boolean",
                    "description": "Abandon owed pre-discard hooks and finish the discard "
                    "at once. Default false; sent only when true.",
                },
            },
            ("slug",),
        ),
        query=("force",),
    ),
    AdminRow(
        "leadership_withdraw",
        "POST",
        f"{_A}/sessions/{{slug}}/leadership/withdraw",
        "Withdraw a pending leadership handover request so it is never applied: request_id "
        "(from orchestrator_handoff_submit with handover) withdraws that one, without it every "
        "pending handover of the session is withdrawn. The orchestrator's own rollout "
        "handover request is never withdrawn: it stays queued, and by request_id it is 409 "
        "rollout_request. A session's leader may withdraw on its own slug; "
        "a superleader on its own or any manageable session. Answers {withdrawn: [ids]}; the "
        "requester gets the usual leadership-request note (rejected: withdrawn). 409 not_pending "
        "when the request is already decided.",
        schema(
            {"slug": _SLUG, "request_id": {"type": ["integer", "null"], "minimum": 1}},
            ("slug",),
        ),
        "*",
    ),
    # history
    AdminRow(
        "history_search",
        "GET",
        f"{_A}/history/sessions",
        "List or search the stored history of discarded sessions, newest first. "
        + _HISTORY_MATCH
        + " Filters: from and to (ISO 8601 timestamps), preset, session (id or slug part). "
        "Each entry shows the session and, for a q search, up to 3 snippets. A page holds up to "
        "limit sessions (default 20, at most 200). The answer is "
        "{next_before_id, sessions: [...]}: a page can be short or even empty while more "
        "sessions exist, so while next_before_id is not null call again with it as before_id; "
        "null means the end." + _HISTORY_SCOPE,
        schema(
            {
                "q": _HISTORY_Q,
                "regex": _HISTORY_REGEX,
                "from": _str("Only sessions from this ISO 8601 timestamp on."),
                "to": _str("Only sessions up to this ISO 8601 timestamp."),
                "preset": _str("Preset id or name."),
                "session": _str("Session id (uuid) or part of the slug."),
                "limit": {**_LIMIT, "default": HISTORY_SEARCH_LIMIT},
                "before_id": _HISTORY_BEFORE,
            }
        ),
        None,
        ("q", "regex", "from", "to", "preset", "session", "limit", "before_id"),
    ),
    # superleader reads of any session's live work, the deploy and the backups
    AdminRow(
        "sessions_admin_list",
        "GET",
        f"{_A}/sessions",
        "List the sessions of your own and granted projects (a superleader: every session of "
        "every project, manageable or not) as a "
        "summary (slug, preset_name, project, extra_projects, status, desired, labels, hive, "
        "superleader, created_at); session_get shows one in full. On the listed sessions a "
        "superleader can write with session_labels op update, scale, lock and "
        "session_message_cancel with their slug. Filter by label, project, "
        "session_slug or superleaders (include, with project: also list its superleader sessions; "
        "without project every session is listed). When the list "
        "outgrows the output budget the answer is {omitted, sessions} with the first sessions "
        "that fit and the count left out: narrow it with a filter.",
        schema(
            {
                "label": _str("Only sessions with this label."),
                "project": _str("Only sessions whose home project is this one."),
                "superleaders": {
                    "type": "string",
                    "enum": ["include"],
                    "description": "include: also list the superleader sessions of the project "
                    "(only with project; without it every session is listed).",
                },
                "session_slug": _str("Only the session with this slug."),
            }
        ),
        None,
        ("label", "project", "superleaders", "session_slug"),
    ),
    AdminRow(
        "session_events",
        "GET",
        f"{_A}/sessions/{{slug}}/events",
        "A page of the stored events of a session of your own or granted projects (a "
        "superleader: any session) (chat, tool calls, "
        "hooks); redacted like the web UI. Not a stream. A page is in "
        "ascending id order and, without a cursor, is the newest events. Default limit 20. "
        "When a page outgrows the output budget the oldest events are dropped and omitted "
        "says how many. Page back with before_id (the lowest id seen) or forward with "
        "after_id; agent_id and types (comma-separated kinds) filter.",
        schema(
            {
                "slug": _SLUG,
                "before_id": _HISTORY_BEFORE,
                "after_id": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Return rows with an id above this one.",
                },
                "limit": {**_LIMIT, "default": SESSION_EVENTS_LIMIT},
                "agent_id": _str("Only events of this agent id."),
                "types": _str("Comma-separated event types."),
            },
            ("slug",),
        ),
        None,
        ("before_id", "after_id", "limit", "agent_id", "types"),
    ),
    AdminRow(
        "volume_save",
        "POST",
        "/volumes/{name}/save",
        "Save a mounted volume as a new version of its backup now (answers 202, the save runs "
        "in the background; volumes shows the result). Only for a volume attached with "
        "backup_restore true, else 403 backup_not_enabled. 404 for a name that is not mounted "
        "in this session, 409 volume_busy while the volume is not ready (saving, restoring or "
        "resizing).",
        schema({"name": _str("Volume name (from volumes).")}, ("name",)),
    ),
    AdminRow(
        "volume_create",
        "POST",
        f"{_A}/volumes",
        "Ask for a new volume: this never creates it, it files a volume_create proposal "
        "(answer 202) the user approves on the Proposals page; follow it with proposal_get. "
        "name is lowercase letters, digits and hyphens, at most 40 characters. size is a "
        "Kubernetes quantity such as 10Gi (default from the deploy, capped).",
        schema(
            {
                "name": _str("Volume name, ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$."),
                "projects": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                    "description": "Project slugs the volume belongs to (labels, not isolation).",
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4000,
                    "description": "Why the volume is needed, shown to the user who approves.",
                },
                "size": _str("Quantity such as 10Gi."),
                "description": {"type": "string", "maxLength": 2000},
                "labels": {"type": "array", "items": {"type": "string"}},
                "exclude": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Path patterns left out of the volume backup.",
                },
                "client_key": {"type": "string", "maxLength": 200},
            },
            ("name", "projects", "reason"),
        ),
        "*",
    ),
    AdminRow(
        "backups_copy",
        "POST",
        f"{_A}/backups/copy",
        "Superleader only: copy a backup version to a target backup name. The copy becomes the "
        "target's current version; the target's earlier versions stay. source_version omitted "
        "copies the current version of the source. There is no merge. 404 backup_not_found.",
        schema(
            {
                "source": _str("Source backup name."),
                "source_version": _str("Source version id; omit for the current one."),
                "target": _str("Target backup name."),
            },
            ("source", "target"),
        ),
        "*",
    ),
    # superleader spec admin on the task board (accept stays the user's)
    AdminRow(
        "spec_delete",
        "DELETE",
        f"{_A}/specs/{{key}}",
        "Superleader only: deletes a spec on the task board (204). To accept a spec stays the "
        "user's decision in the web UI.",
        schema({"key": {"type": "string", "pattern": "^S-[0-9]+$"}}, ("key",)),
    ),
)

ADMIN_TOOLS: tuple[AdminRow, ...] = tuple(
    replace(r, description=r.description + _NOT_MANAGEABLE) if r.name in _WRITE_NAMES else r
    for r in _ROWS
)

# Merged admin tools (S-83 905-916): an argument picks the route (``merged.py``).
_W = f"{_A}/webhooks"
_SC = f"{_A}/schedules"
_CH = f"{_A}/chats"
_VAULT = f"{_A}/presets/{{ref}}/vault/{{entry_id}}"
_VOLUME = f"{_A}/presets/{{ref}}/volumes/{{volume_id}}"
_ISSUED = ("to_file", "overwrite")
_HOOK_EDIT = ("filters", "prompt_template", "suffix", "then_schedule_ids", *_AUTH, *_ISSUED)
_ROOM_SET = ("trigger", "preset_id", "agents_may_initiate", "agent_aliases")
_SCHED_SET = ("enabled", "timezone", "then_schedule_ids")
_SCHED_REQ = ("name", "cron", "preset_id", "prompt")
_RECORDS = ("kind", "q", "regex", "before_id", "after_id", "limit")
_ROOM_REPLACED = (
    " preset_id and trigger are replaced on every call (omitted or null clears them): to change "
    "only aliases, read the room with chat_rooms scope projects first and resend its preset_id "
    "and trigger."
)
_VOLUME_ID = _str("Volume id (uuid), from volumes scope all.")
_ENTRY_ID = _str("Vault entry id (uuid), from vault_list.")
_ENV_NAME = {"type": "string", "pattern": "^[A-Z_][A-Z0-9_]{0,127}$"}

ADMIN_MERGED: tuple[Merged, ...] = (
    Merged(
        "volumes",
        "Shared volumes. scope own (default): the volumes mounted in this session (name, mount "
        "path, mode ro or rw, backup_restore, state, last save and restore result). scope all "
        "(superleader only): the volumes that share a project with you (id, name, projects, size, "
        "state, attached presets and mounted sessions); with volume_id one volume with the "
        "presets that attach it (preset, mode, mount path, backup_restore); 404 volume_not_found. "
        "Names, ids, sizes and states only, never file listings or manifests.",
        {
            "scope": {"type": "string", "enum": ["own", "all"]},
            "volume_id": _VOLUME_ID,
        },
        {
            "own": Route("GET", "/volumes"),
            "all": Route("GET", f"{_A}/volumes"),
            "all+volume_id": Route("GET", f"{_A}/volumes/{{volume_id}}", ("volume_id",)),
            "own+volume_id": Route("GET", f"{_A}/volumes/{{volume_id}}", ("volume_id",)),
        },
        selectors=("scope",),
        scopes=("volume_id",),
        defaults={"scope": "own"},
    ),
    Merged(
        "vault_attach",
        "Attach a vault entry to a preset, or with detach true detach it. Attach: all envs of "
        "the entry under their own names, or only the envs you map (envs: entry env name to "
        "preset env name, which also renames). The entry labels must match the preset (labels "
        "plus project), else 422 vault_label_mismatch. Privileged presets included: no proposal "
        "is needed and nothing is refused. The response has attachment_privileged (bool): true "
        "means the preset's sessions have cluster access or docker/dind and can read every "
        "Secret in the namespace, so attaching exposes the entry's values to them; weigh that "
        "before attaching sensitive entries. A preset env of the same name that is not a "
        "reference to the same entry env gives 409 env_exists unless replace is true. Detach: "
        "one env of the entry when env is given, else every env of the entry. Read entry ids "
        "and env names from vault_list." + _NOT_MANAGEABLE,
        {
            "ref": _REF,
            "entry_id": _ENTRY_ID,
            "detach": {"type": "boolean", "description": "Detach instead of attach."},
            "envs": {
                "type": "object",
                "minProperties": 1,
                "propertyNames": {"pattern": _ENV_NAME["pattern"]},
                "additionalProperties": _ENV_NAME,
                "description": "Maps entry env name to preset env name, e.g. "
                '{"GITHUB_TOKEN": "BOT_TOKEN"}. Same name on both sides attaches a subset '
                "unrenamed. Omitted: all envs of the entry under their own names.",
            },
            "replace": {
                "type": "boolean",
                "description": "Overwrite an existing preset env of the same name.",
            },
            "env": {**_ENV_NAME, "description": "detach: entry env name; omitted: all envs."},
        },
        {
            "": Route("PUT", _VAULT, ("ref", "entry_id"), ("envs", "replace"), body=True),
            "detach": Route("DELETE", _VAULT, ("ref", "entry_id"), ("env",), query=("env",)),
        },
        scopes=("detach",),
        required=("ref", "entry_id"),
    ),
    Merged(
        "volume_attach",
        "Attach a volume to a manageable preset (mounted in its new sessions), or with detach "
        "true detach it (running sessions keep the mount; the volume and its content stay). "
        "mode ro or rw, mount_path absolute (default /volumes/<name>), backup_restore true opts "
        "into the automatic restore and save-on-last-release. An attach that would share the "
        "volume between a privileged preset (cluster access, namespace access, docker) and any "
        "other preset, or that is RW on a privileged preset, is a cross-trust attach: nothing "
        "is applied and the answer is 202 with a proposal the user approves (follow it with "
        "proposal_get); any other attach answers 200 with the preset. Errors: 403 "
        "volume_cross_trust, 409 when the volume is busy or the attach conflicts, 422 for an "
        "invalid mount path or attachment.",
        {
            "ref": _REF,
            "volume_id": _VOLUME_ID,
            "detach": {"type": "boolean", "description": "Detach instead of attach."},
            "mode": {"type": "string", "enum": ["ro", "rw"]},
            "mount_path": _str("Absolute mount path, at most 200 characters."),
            "backup_restore": {"type": "boolean"},
        },
        {
            "": Route(
                "POST",
                _VOLUME,
                ("ref", "volume_id", "mode"),
                ("mount_path", "backup_restore"),
                body=True,
            ),
            "detach": Route("DELETE", _VOLUME, ("ref", "volume_id")),
        },
        scopes=("detach",),
        required=("ref", "volume_id"),
    ),
    Merged(
        "proposal_put",
        "File, revise or withdraw a change proposal for a human to review. "
        + _PROPOSAL_TEXT
        + "op create: kind preset, schedule or session; target the preset id or name, schedule "
        "id or session slug; patch, reason; client_key makes a retry return the first proposal. "
        "op revise (id, patch, reason): a new revision after a rework verdict or a stale notice, "
        "a full proposal against the target as it is now, not a delta. op withdraw (id): "
        "withdraw one of your open proposals. Answers the proposal.",
        {
            "op": {"type": "string", "enum": ["create", "revise", "withdraw"]},
            "id": {"type": "integer", "description": "Proposal id."},
            "kind": {"type": "string", "enum": ["preset", "schedule", "session"]},
            "target": _str("Preset id or name, schedule id or slug."),
            "patch": _PROPOSAL_PATCH,
            "reason": _PROPOSAL_REASON,
            "client_key": {"type": "string", "maxLength": 200},
        },
        {
            "create": Route(
                "POST",
                f"{_A}/proposals",
                ("kind", "target", "patch", "reason"),
                ("client_key",),
                body=True,
            ),
            "revise": Route(
                "POST", f"{_A}/proposals/{{id}}/revise", ("id", "patch"), ("reason",), body=True
            ),
            "withdraw": Route("POST", f"{_A}/proposals/{{id}}/withdraw", ("id",)),
        },
        selectors=("op",),
        required=("op",),
    ),
    Merged(
        "proposal_get",
        "Change proposals (your own; a superleader sees every proposal). Without id: list them, "
        "newest first, current revision only, filtered by status active (default: open, rework, "
        "stale), open, closed or all; kind session_projects or preset_projects is a project "
        "grant, preset_manageable asks to make a preset manageable. With id: one proposal with "
        "every revision: per item the before and after values, the human's verdict (pending, "
        "approved, rework, denied) and note; after a rework verdict or a stale notice send a "
        "new revision with proposal_put op revise.",
        {
            "id": {"type": "integer", "description": "Proposal id; omitted: the list."},
            "status": _str("active, open, closed or all."),
            "limit": _LIMIT,
        },
        {
            "": Route("GET", f"{_A}/proposals", (), ("status", "limit"), query=("status", "limit")),
            "id": Route("GET", f"{_A}/proposals/{{id}}", ("id",)),
        },
        scopes=("id",),
    ),
    Merged(
        "session_restart",
        "Restart every agent of a session (scope session, default) or one agent pod (scope "
        "agent, pod). Answers command ids; read them with session_commands command_id. "
        "Restarting your own session is a self-restart: it has a cooldown (429 "
        "restart_cooldown, with retry_after) and you get a note when you are back. Restarting "
        "an agent that never bootstrapped reruns the same install and reports a warning; a "
        "command may end failed with interrupted_by_restart." + _NOT_MANAGEABLE,
        {
            "scope": {"type": "string", "enum": ["session", "agent"]},
            "slug": _SLUG,
            "pod": _POD,
        },
        {
            "session": Route("POST", f"{_A}/sessions/{{slug}}/restart", ("slug",)),
            "agent": Route(
                "POST", f"{_A}/sessions/{{slug}}/agents/{{pod}}/restart", ("slug", "pod")
            ),
        },
        selectors=("scope",),
        defaults={"scope": "session"},
    ),
    Merged(
        "session_commands",
        "Superleader only: the recent commands (restarts and so on) of any session (slug), "
        "manageable or not; with command_id the status of any command.",
        {"slug": _SLUG, "limit": _LIMIT, "command_id": _str("Command id.")},
        {
            "": Route(
                "GET", f"{_A}/sessions/{{slug}}/commands", ("slug",), ("limit",), query=("limit",)
            ),
            "command_id": Route("GET", f"{_A}/commands/{{command_id}}", ("command_id",)),
        },
        scopes=("command_id",),
    ),
    Merged(
        "usage",
        "The account's Claude usage, read-only. what current (default): the current reading, "
        "from the same source the usage_quota gate uses: {configured, available, source, "
        "updated_at, five_hour, seven_day}; each window is {utilization_pct, resets_at, stale, "
        "observed_at, source} or null when unknown. available false means the usage service is "
        "not configured or not reachable (the gate allows starts then); stale true means an old "
        "reading, a lower bound. Superleader only: what history (five_hour and seven_day points "
        "between from and to, ISO 8601; metric picks the window, max_points downsamples) and "
        "what sessions (the session start and end marks between from and to).",
        {
            "what": {"type": "string", "enum": ["current", "history", "sessions"]},
            "from": _str("Start, ISO 8601."),
            "to": _str("End, ISO 8601."),
            "metric": _str("Usage window: five_hour or seven_day."),
            "max_points": {"type": "integer", "minimum": 10, "description": "At least 10."},
            "limit": _LIMIT,
        },
        {
            "current": Route("GET", f"{_A}/usage"),
            "history": Route(
                "GET",
                f"{_A}/usage/history",
                (),
                ("from", "to", "metric", "max_points"),
                query=("from", "to", "metric", "max_points"),
            ),
            "sessions": Route(
                "GET",
                f"{_A}/usage/sessions",
                (),
                ("from", "to", "limit"),
                query=("from", "to", "limit"),
            ),
        },
        selectors=("what",),
        defaults={"what": "current"},
    ),
    Merged(
        "webhook",
        "Webhook endpoints (superleader only). action list; meta (supported types, auth modes, "
        "template fields); create (type, preset_id); update (id; only the fields given change, "
        "a new auth mode issues a new credential); clone (id, optionally to another preset_id "
        "or suffix); rotate (id) the credential; delete (id) with its stored credential; filters "
        "(id, allow, deny) replaces the filters; events (received events, newest first, "
        "before_id pages back); event (id) one event; preview (type, example, template) "
        "renders a prompt template."
        + _CRED
        + " to_file writes it to a file instead."
        + _NOT_MANAGEABLE,
        {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "meta",
                    "create",
                    "update",
                    "clone",
                    "rotate",
                    "delete",
                    "filters",
                    "events",
                    "event",
                    "preview",
                ],
            },  # fmt: skip
            "id": _str("Webhook endpoint id (action event: the event id)."),
            "type": {"type": "string"},
            "preset_id": _NULL_STR,
            "auth_mode": {"type": "string", "enum": ["basic", "header", "bearer", "hmac"]},
            "filters": _FILTERS,
            "prompt_template": _NULL_STR,
            "suffix": _NULL_STR,
            "then_schedule_ids": {**_CHAIN_PATCH, "type": ["array", "null"]},
            **_AUTH,
            "allow": {"type": "array"},
            "deny": {"type": "array"},
            "status": {"type": "string"},
            "endpoint_id": {"type": "string"},
            "limit": _LIMIT,
            "before_id": {"type": "string"},
            "example": {},
            "template": _NULL_STR,
            "to_file": TO_FILE_SCHEMA,
            "overwrite": OVERWRITE_SCHEMA,
        },
        {
            "list": Route("GET", f"{_W}/endpoints"),
            "meta": Route("GET", f"{_W}/meta"),
            "create": Route(
                "POST",
                f"{_W}/endpoints",
                ("type", "preset_id"),
                ("auth_mode", *_HOOK_EDIT),
                body=True,
            ),
            "update": Route(
                "PATCH",
                f"{_W}/endpoints/{{id}}",
                ("id",),
                ("auth_mode", "preset_id", *_HOOK_EDIT),
                body=True,
            ),
            "clone": Route(
                "POST",
                f"{_W}/endpoints/{{id}}/clone",
                ("id",),
                ("preset_id", "suffix", *_ISSUED),
                body=True,
            ),
            "rotate": Route(
                "POST", f"{_W}/endpoints/{{id}}/rotate", ("id",), ("username", *_ISSUED), body=True
            ),
            "delete": Route("DELETE", f"{_W}/endpoints/{{id}}", ("id",)),
            "filters": Route(
                "PUT", f"{_W}/endpoints/{{id}}/filters", ("id",), ("allow", "deny"), body=True
            ),
            "events": Route(
                "GET",
                f"{_W}/events",
                (),
                ("status", "endpoint_id", "limit", "before_id"),
                query=("status", "endpoint_id", "limit", "before_id"),
            ),
            "event": Route("GET", f"{_W}/events/{{id}}", ("id",)),
            "preview": Route(
                "POST",
                f"{_W}/templates/preview",
                ("type", "example"),
                ("template", "filters"),
                body=True,
            ),
        },
        selectors=("action",),
        required=("action",),
    ),
    Merged(
        "schedule",
        "Schedules (superleader only): a cron expression that starts a session from a preset "
        "with a prompt. action list; get (schedule_id); create (name, cron, preset_id, prompt); "
        "update (schedule_id and all required fields again; then_schedule_ids omitted keeps the "
        "current links); delete; run (fire it now); runs (the runs of one schedule, newest "
        "first); runs_all (the runs of all schedules); run_get (run_id); preview (cron: the "
        "next fire times and the rendered prompt)." + _NOT_MANAGEABLE,
        {
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "get",
                    "create",
                    "update",
                    "delete",
                    "run",
                    "runs",
                    "runs_all",
                    "run_get",
                    "preview",
                ],
            },  # fmt: skip
            "schedule_id": _SCHED,
            "run_id": _str("Run id."),
            **_SCHED_FIELDS,
            "preset_id": _NULL_STR,
            "prompt": _NULL_STR,
            "schedule_name": _NULL_STR,
            "status": {"type": "string"},
            "limit": _LIMIT,
        },
        {
            "list": Route("GET", f"{_SC}/schedules"),
            "get": Route("GET", f"{_SC}/schedules/{{schedule_id}}", ("schedule_id",)),
            "create": Route("POST", f"{_SC}/schedules", _SCHED_REQ, _SCHED_SET, body=True),
            "update": Route(
                "PUT",
                f"{_SC}/schedules/{{schedule_id}}",
                ("schedule_id", *_SCHED_REQ),
                _SCHED_SET,
                body=True,
            ),
            "delete": Route("DELETE", f"{_SC}/schedules/{{schedule_id}}", ("schedule_id",)),
            "run": Route("POST", f"{_SC}/schedules/{{schedule_id}}/run", ("schedule_id",)),
            "runs": Route(
                "GET",
                f"{_SC}/schedules/{{schedule_id}}/runs",
                ("schedule_id",),
                ("limit",),
                query=("limit",),
            ),
            "runs_all": Route(
                "GET", f"{_SC}/runs", (), ("status", "limit"), query=("status", "limit")
            ),
            "run_get": Route("GET", f"{_SC}/runs/{{run_id}}", ("run_id",)),
            "preview": Route(
                "POST",
                f"{_SC}/schedules/preview",
                ("cron",),
                ("preset_id", "prompt", "schedule_name", "timezone"),
                body=True,
            ),
        },
        selectors=("action",),
        required=("action",),
    ),
    Merged(
        "chat_connector",
        "The chat connector (superleader only). action settings (room_id): a room's trigger "
        "(all or mention), the preset that serves it, agents_may_initiate and agent_aliases; "
        "allow (room_id): allow and configure a room, also accepts a Messenger message request "
        "(an invited room is joined on the next sync)." + _ROOM_REPLACED + " action users (q): "
        "people the connector knows by name or Facebook id. action open (target, task_key) "
        "asks to open a Messenger chat with a person, invite (target, task_key, room_id) to "
        "invite them to a room: it asks the user on the taskboard under task_key; when you get "
        "'Task T-n was updated', call open_confirm (request_id), then poll open_status "
        "(request_id) until done; allow the new room; session_create, then message it with a "
        "<session-send> envelope; it posts there with a <chat-send> envelope. target is a "
        "Facebook id, a profile.php?id= link or a name; when several people match, the answer "
        "has candidates: call again with the chosen Facebook id. A vanity link "
        "(facebook.com/some.name) cannot be resolved: pass the name instead. action rules "
        "lists the allow and deny rules, rule_add (list allow or deny, kind, value) adds one, "
        "rule_delete (rule_id) deletes one.",
        {
            "action": {
                "type": "string",
                "enum": [
                    "settings",
                    "allow",
                    "users",
                    "open",
                    "invite",
                    "open_status",
                    "open_confirm",
                    "rules",
                    "rule_add",
                    "rule_delete",
                ],
            },  # fmt: skip
            "room_id": _ROOM_ID,
            "trigger": {"type": ["string", "null"], "enum": ["all", "mention", None]},
            "preset_id": _NULL_STR,
            "agents_may_initiate": {"type": ["boolean", "null"]},
            "agent_aliases": _ALIASES,
            "q": _str("Name part or Facebook id."),
            "limit": _LIMIT,
            "target": _str("Facebook id, profile link or name of the person."),
            "task_key": _str("Taskboard task key (T-n) the question is asked under."),
            "request_id": _str("Room request id."),
            "list": {"type": "string", "enum": ["allow", "deny"]},
            "kind": {"type": "string"},
            "value": {"type": "string"},
            "note": _NULL_STR,
            "preamble": _NULL_STR,
            "rule_id": _str("Rule id."),
        },
        {
            "settings": Route(
                "PUT", f"{_CH}/rooms/{{room_id}}/settings", ("room_id",), _ROOM_SET, body=True
            ),
            "allow": Route(
                "POST", f"{_CH}/rooms/{{room_id}}/allow", ("room_id",), _ROOM_SET, body=True
            ),
            "users": Route("GET", f"{_CH}/users", (), ("q", "limit"), query=("q", "limit")),
            "open": Route(
                "POST", f"{_CH}/room-requests", ("target", "task_key"), ("action",), body=True
            ),
            "invite": Route(
                "POST",
                f"{_CH}/room-requests",
                ("target", "task_key", "room_id"),
                ("action",),
                body=True,
            ),
            "open_status": Route("GET", f"{_CH}/room-requests/{{request_id}}", ("request_id",)),
            "open_confirm": Route(
                "POST", f"{_CH}/room-requests/{{request_id}}/confirm", ("request_id",), body=True
            ),
            "rules": Route("GET", f"{_CH}/rules"),
            "rule_add": Route(
                "POST",
                f"{_CH}/rules",
                ("list", "kind", "value"),
                ("note", "preamble"),
                body=True,
            ),
            "rule_delete": Route("DELETE", f"{_CH}/rules/{{rule_id}}", ("rule_id",)),
        },
        selectors=("action",),
        required=("action",),
    ),
    Merged(
        "history_get",
        "One stored session of the history of discarded sessions: provenance, preset, "
        "timestamps and record counts. With records true: page its stored records (chat "
        "events, commands and so on) in id order, filtered by kind and by text: "
        + _HISTORY_MATCH
        + " Page forward with after_id (the last id seen) or backward with before_id."
        + _HISTORY_SCOPE,
        {
            "session_id": _HISTORY_SESSION_ID,
            "records": {"type": "boolean", "description": "Page the session's records."},
            "kind": _str("records: only records of this kind."),
            "q": _HISTORY_Q,
            "regex": _HISTORY_REGEX,
            "before_id": _HISTORY_BEFORE,
            "after_id": {
                "type": "integer",
                "minimum": 0,
                "description": "Return rows with an id above this one.",
            },
            "limit": _LIMIT,
        },
        {
            "": Route("GET", f"{_A}/history/sessions/{{session_id}}", ("session_id",)),
            "records": Route(
                "GET",
                f"{_A}/history/sessions/{{session_id}}/records",
                ("session_id",),
                _RECORDS,
                query=_RECORDS,
            ),
        },
        scopes=("records",),
        required=("session_id",),
    ),
    Merged(
        "handoffs",
        "The handoff history of the sessions of your own and granted projects (a superleader: "
        "every session). Without id: newest first (id, session, created_at, a short head), "
        "paged by before_id, filtered by session (id or slug). With id: one handoff in full; "
        "404 handoff_not_found.",
        {
            "id": {"type": "integer", "minimum": 1, "description": "Handoff id."},
            "session": _str("Only handoffs of this session (id or slug)."),
            "limit": _LIMIT,
            "before_id": {
                "type": "integer",
                "minimum": 1,
                "description": "Only handoffs older than this id.",
            },
        },
        {
            "": Route(
                "GET",
                f"{_A}/handoffs",
                (),
                ("session", "limit", "before_id"),
                query=("session", "limit", "before_id"),
            ),
            "id": Route("GET", f"{_A}/handoffs/{{id}}", ("id",)),
        },
        scopes=("id",),
    ),
    Merged(
        "backups",
        "Superleader only: the named backups. Without name: the backups (name, current version, "
        "sizes, times), filtered by q (substring of the name). With name: its versions, newest "
        "first (version id, actor, size, time, which one is current). With name and version: "
        "the file list (paths, sizes) of that version, without any file content. Lists page "
        "with the cursor of the previous page. There is no download. 400 bad_request for a bad "
        "cursor, 404 backup_not_found or version_not_found, 503 unavailable when backups are "
        "not configured.",
        {
            "name": _str("Backup name."),
            "version": _str("Version id."),
            "q": _str("Only names containing this text."),
            "limit": _LIMIT,
            "cursor": _str("Cursor of the previous page."),
        },
        {
            "": Route(
                "GET", f"{_A}/backups", (), ("q", "limit", "cursor"), query=("q", "limit", "cursor")
            ),
            "name": Route(
                "GET",
                f"{_A}/backups/{{name}}/versions",
                ("name",),
                ("limit", "cursor"),
                query=("limit", "cursor"),
            ),
            "name+version": Route(
                "GET", f"{_A}/backups/{{name}}/versions/{{version}}/manifest", ("name", "version")
            ),
        },
        scopes=("name", "version"),
    ),
)
