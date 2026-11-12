"""Declarative rows for the superleader admin routes ``/agent/v1/admin`` of agents-orchestrator.

One row per route; ``__init__`` turns each into a ``ToolSpec``. The orchestrator refuses every
route unless the caller is a superleader session, and the rendered allowlist offers these tools
to such sessions only. Secret values never pass through a row: a preset env variable refers to a
value that a user wrote into agents-vault (vault_list, vault_attach, vault_detach), and webhook
credentials are issued by the upstream and stored by the orchestrator (see ``secrets_local``).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .fit import measure

__all__ = [
    "ADMIN_TOOLS",
    "AdminRow",
    "SESSION_EVENTS_LIMIT",
    "fit_events",
    "fit_sessions",
    "schema",
    "summarize_presets",
    "summarize_sessions",
]

_A = "/admin"


# Spec keys kept in the presets_list summary; kube_access, session_messaging and docker are
# privilege flags (absent means false), inactivity is shown only when set.
_FLAGS = ("kube_access", "session_messaging", "docker")


def summarize_presets(presets: list[Any]) -> list[Any]:
    """Compact presets_list answer: the full specs of all presets exceed the 40000 char tool
    output budget, so each preset keeps its name, labels, superleader_manageable (when the
    upstream sends it) and the privilege flags of its spec; preset_get shows one in full."""
    out: list[Any] = []
    for preset in presets:
        if not isinstance(preset, dict):
            out.append(preset)
            continue
        spec = preset.get("spec")
        spec = spec if isinstance(spec, dict) else {}
        row: dict[str, Any] = {"name": preset.get("name"), "labels": preset.get("labels", [])}
        if "superleader_manageable" in preset:
            row["superleader_manageable"] = preset["superleader_manageable"]
        for flag in _FLAGS:
            row[flag] = spec.get(flag) is True
        if spec.get("inactivity") is not None:
            row["inactivity"] = spec["inactivity"]
        out.append(row)
    return out


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


def fit_events(page: Any, budget: int) -> Any:
    """``page`` (an events page ``{events, has_more_before, next_after_id}``, ascending ids) when
    its text fits ``budget``, else the same page with the NEWEST events that fit and
    ``omitted``, the count of older events dropped. any-mcp keeps the head of an over-budget
    text, which would lose the newest events; the dropped ones are still reachable with
    before_id set to the lowest id kept."""
    if not isinstance(page, dict) or not isinstance(page.get("events"), list):
        return page
    events = page["events"]

    def shaped(keep: int) -> dict[str, Any]:
        rest = {k: v for k, v in page.items() if k != "events"}
        return {"omitted": len(events) - keep, **rest, "events": events[len(events) - keep :]}

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


_OBJ = {"type": "object"}
_REF = _str("Preset id or name.")
_PROJECT = _str("Project slug of an existing project (see project_list).")
_GRANT_TEXT = (
    "Every grant is a change proposal the user approves on the Proposals page; nothing is "
    "granted before that (answer 202 with the proposal, follow it with proposal_get; 200 "
    "with the first proposal on a client_key replay). A grant is never revised: withdraw it "
    "with proposal_withdraw and grant again. At most 9 extra projects. Errors: own_project "
    "(already the home project), already_granted, unknown_project, project_archived, "
    "too_many_projects; not_found for an unknown target."
)
_GRANT_SCHEMA = {
    "project": _PROJECT,
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
        "preset_replace", "preset_patch", "preset_delete", "preset_copy",
        "preset_projects_grant", "preset_projects_revoke", "vault_attach", "vault_detach",
        "session_patch", "session_discard", "session_restart", "agent_restart",
        "session_projects_grant", "session_projects_revoke", "leadership_withdraw",
        "webhook_create", "webhook_update", "webhook_clone", "webhook_rotate",
        "webhook_delete", "webhook_filters_set", "schedule_create", "schedule_update",
        "schedule_delete", "schedule_run",
    }
)  # fmt: skip
_SLUG = _str("Session slug.")
_POD = _str("Agent pod name.")
_EP = _str("Webhook endpoint id.")
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
    "attach a vault entry with vault_attach. Human-only fields (kube_access, "
    "session_messaging, docker, discard_hook_url, wrapper.permission_mode, "
    "wrapper.permissions) may only move toward safe.",
}
_PATCH_SPEC_SESSION = {
    **_PATCH_SPEC,
    "description": "JSON merge patch (RFC 7396) of the session's stored spec: objects merge, "
    "null deletes a key, arrays replace. Keys you leave out are kept. Secret values do not "
    "belong here. A leader may only lower usage_quota and shorten inactivity limits on its own "
    "session; human-only fields "
    "(kube_access, session_messaging, docker, discard_hook_url, wrapper.permission_mode, "
    "wrapper.permissions) may only move toward safe.",
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
        "and replies to it always count."
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
_SCHED_FIELDS = {
    "name": {"type": "string"},
    "cron": {"type": "string", "description": "Five-field cron expression."},
    "preset_id": {"type": "string"},
    "prompt": {"type": "string"},
    "enabled": {"type": "boolean"},
    "timezone": _NULL_STR,
}
_ROOM_ID = _str("Chat room id.")
_HISTORY_SESSION_ID = _str("Session id (uuid) of the discarded session.")
_HISTORY_SCOPE = " History is read-only: a stored session is deleted by the user in the web UI."
_HISTORY_MATCH = (
    "q (3 to 500 characters) is a case-insensitive substring match, or with regex true a "
    "Postgres regex."
)
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
        "presets_list",
        "GET",
        f"{_A}/presets",
        "List the presets: name, labels and privilege flags; use preset_get for the full spec.",
    ),
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
        "preset_replace",
        "PUT",
        f"{_A}/presets/{{ref}}",
        "Replace a preset (name and spec). Secret values come from the vault: change them "
        "with vault_attach and vault_detach, not here. An omitted project keeps the stored one.",
        schema(
            {"ref": _REF, "name": {"type": "string"}, "spec": _SPEC, "project": _PROJECT},
            ("ref", "name"),
        ),
        "*",
    ),
    AdminRow(
        "preset_patch",
        "PATCH",
        f"{_A}/presets/{{ref}}",
        "Change part of a preset (name, labels, project, spec) by id or name. Prefer this over "
        "preset_replace: read-modify-write is not needed. Set project to move the preset to "
        "another existing project (422 unknown_project; 422 vault_label_mismatch when its vault "
        "refs no longer match the project label; 422 project_archived when the target project "
        "is archived); running sessions keep their project. Example: "
        '{"ref": "webhook", "spec": {"usage_quota": {"five_hour_pct": 80}}}.',
        schema(
            {
                "ref": _REF,
                "name": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "project": _PROJECT,
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
    # extra projects of presets (copied to every session created from the preset)
    AdminRow(
        "preset_projects_grant",
        "POST",
        f"{_A}/presets/{{ref}}/projects",
        "Ask the user to grant a preset an extra project: once approved, every session created "
        "from the preset also fully belongs to that project; running sessions keep theirs. "
        + _GRANT_TEXT,
        schema({"ref": _REF, **_GRANT_SCHEMA}, ("ref", "project", "reason")),
        "*",
    ),
    AdminRow(
        "preset_projects_revoke",
        "DELETE",
        f"{_A}/presets/{{ref}}/projects/{{project}}",
        "Remove an extra project from a preset at once (no approval needed); running sessions "
        "keep theirs (revoke those with session_projects_revoke). Answers the preset view. "
        "not_found when the project is not one of its extra projects.",
        schema({"ref": _REF, "project": _PROJECT}, ("ref", "project")),
    ),
    # projects
    AdminRow(
        "project_create",
        "POST",
        f"{_A}/projects",
        "Create a project (superleader only): a named bucket that presets, sessions, tasks "
        "and specs belong to. Projects are never deleted, only archived by the user in the "
        "web UI, so pick the slug with care. 409 project_exists means the slug is taken. "
        "Answers 201 with slug, name, description, archived, created_at, updated_at, "
        "presets and sessions.",
        schema(
            {
                "slug": {
                    "type": "string",
                    "pattern": "^[a-z0-9][a-z0-9-]{0,39}$",
                    "description": "Project slug: lowercase letters, digits and hyphens, "
                    "1 to 40 characters, starting with a letter or digit.",
                },
                "name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "description": "Display name, 1 to 80 characters.",
                },
                "description": {
                    "type": "string",
                    "maxLength": 4000,
                    "description": "Optional description, up to 4000 characters.",
                },
            },
            ("slug", "name"),
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
    AdminRow(
        "vault_attach",
        "PUT",
        f"{_A}/presets/{{ref}}/vault/{{entry_id}}",
        "Attach a vault entry to a preset: all envs of the entry are attached in one save "
        "under their own names, or only the envs you map (envs: entry env name to preset env "
        "name, which also renames). The entry labels must match the preset (labels plus "
        "project), else 422 vault_label_mismatch. Privileged presets included: no proposal is "
        "needed and nothing is refused. The response has attachment_privileged (bool): true "
        "means the preset's sessions have cluster access or docker/dind and can read every "
        "Secret in the namespace, so attaching exposes the entry's values to them; weigh "
        "that before attaching sensitive entries. A preset env of the same name that already "
        "exists and is not a reference to the same entry env gives 409 env_exists unless "
        "replace is true, which overwrites it. Read entry ids and env names from vault_list.",
        schema(
            {
                "ref": _REF,
                "entry_id": _str("Vault entry id (uuid), from vault_list."),
                "envs": {
                    "type": "object",
                    "minProperties": 1,
                    "propertyNames": {"pattern": "^[A-Z_][A-Z0-9_]{0,127}$"},
                    "additionalProperties": {
                        "type": "string",
                        "pattern": "^[A-Z_][A-Z0-9_]{0,127}$",
                    },
                    "description": "Maps entry env name to preset env name, e.g. "
                    '{"GITHUB_TOKEN": "BOT_TOKEN"}. Same name on both sides attaches a '
                    "subset unrenamed. Omitted: all envs of the entry under their own names.",
                },
                "replace": {
                    "type": "boolean",
                    "description": "Overwrite an existing preset env of the same name "
                    "(else 409 env_exists unless it already references the same entry env).",
                },
            },
            ("ref", "entry_id"),
        ),
        "*",
    ),
    AdminRow(
        "vault_detach",
        "DELETE",
        f"{_A}/presets/{{ref}}/vault/{{entry_id}}",
        "Detach a vault entry from a preset: one env of the entry when env is given, else "
        "every env of the entry. Privileged presets included.",
        schema(
            {
                "ref": _REF,
                "entry_id": _str("Vault entry id (uuid), from vault_list."),
                "env": {
                    "type": "string",
                    "pattern": "^[A-Z_][A-Z0-9_]{0,127}$",
                    "description": "Entry env name to detach; omitted: all envs of the entry.",
                },
            },
            ("ref", "entry_id"),
        ),
        None,
        ("env",),
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
        "start: read them and act without the user." + _READ_ANY,
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
                    "project_list); omitted: the preset's project.",
                },
            }
        ),
        "*",
    ),
    AdminRow(
        "session_projects_grant",
        "POST",
        f"{_A}/sessions/{{slug}}/projects",
        "Ask the user to grant a session an extra project: once approved the session has the "
        "access of a full member of that project (its tasks, specs, project room and sessions) "
        "next to its home project, at once, with no restart. " + _GRANT_TEXT,
        schema({"slug": _SLUG, **_GRANT_SCHEMA}, ("slug", "project", "reason")),
        "*",
    ),
    AdminRow(
        "session_projects_revoke",
        "DELETE",
        f"{_A}/sessions/{{slug}}/projects/{{project}}",
        "Remove an extra project from a session at once (no approval needed, no restart); the "
        "session is told. Answers the session view. not_found when the project is not one of "
        "its extra projects.",
        schema({"slug": _SLUG, "project": _PROJECT}, ("slug", "project")),
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
        "session_restart",
        "POST",
        f"{_A}/sessions/{{slug}}/restart",
        "Restart every agent of a session. Answers command ids; read them with command_get. "
        "Restarting your own session is a self-restart: it has a cooldown (429 "
        "restart_cooldown, with retry_after) and you get a note when you are back. Restarting "
        "an agent that never bootstrapped reruns the same install and reports a warning; a "
        "command may end failed with interrupted_by_restart.",
        schema({"slug": _SLUG}, ("slug",)),
    ),
    AdminRow(
        "agent_restart",
        "POST",
        f"{_A}/sessions/{{slug}}/agents/{{pod}}/restart",
        "Restart one agent pod of a session. Same self-restart rules as session_restart.",
        schema({"slug": _SLUG, "pod": _POD}, ("slug", "pod")),
    ),
    AdminRow(
        "leadership_withdraw",
        "POST",
        f"{_A}/sessions/{{slug}}/leadership/withdraw",
        "Withdraw a pending leadership handover request so it is never applied: request_id "
        "(from orchestrator_leadership_handover) withdraws that one, without it every pending "
        "handover of the session is withdrawn. The orchestrator's own rollout handover request "
        "is never withdrawn: it stays queued, and by request_id it is 409 rollout_request. "
        "A session's leader may withdraw on its own slug; "
        "a superleader on its own or any manageable session. Answers {withdrawn: [ids]}; the "
        "requester gets the usual leadership-request note (rejected: withdrawn). 409 not_pending "
        "when the request is already decided.",
        schema(
            {"slug": _SLUG, "request_id": {"type": ["integer", "null"], "minimum": 1}},
            ("slug",),
        ),
        "*",
    ),
    AdminRow(
        "command_get",
        "GET",
        f"{_A}/commands/{{command_id}}",
        "Show the status of any command (a superleader reads every command, see session_commands).",
        schema({"command_id": _str("Command id.")}, ("command_id",)),
    ),
    # usage
    AdminRow(
        "usage_get",
        "GET",
        f"{_A}/usage",
        "Read-only, superleader-only (any other session is refused with not_superleader). The "
        "account's current Claude usage, from the same source the orchestrator's usage_quota "
        "gate uses. The answer is {configured, available, source, updated_at, five_hour, "
        "seven_day}; five_hour and seven_day are {utilization_pct, resets_at, stale, "
        "observed_at, source} or null when the window is unknown. available false means the "
        "usage service is not configured or not reachable (the gate allows starts then). "
        "stale true means the reading is old; usage only grows until resets_at, so it is a "
        "lower bound.",
    ),
    # change proposals
    AdminRow(
        "proposals_list",
        "GET",
        f"{_A}/proposals",
        "List change proposals, newest first, current revision only: your own, or every proposal "
        "when you are a superleader. status: active "
        "(default: open, rework, stale), open, closed or all. kind session_projects or "
        "preset_projects is a project grant (session_projects_grant, preset_projects_grant). "
        "Each shows its status, revision, "
        "stale flag and per-item verdicts; read one with proposal_get.",
        schema({"status": _str("active, open, closed or all."), "limit": _LIMIT}),
        None,
        ("status", "limit"),
    ),
    AdminRow(
        "proposal_create",
        "POST",
        f"{_A}/proposals",
        "File a change proposal for a human to review. " + _PROPOSAL_TEXT + "kind is preset, "
        "schedule or session; target is the preset id or name, schedule id or session slug. "
        "client_key makes a retry return the first proposal instead of a second one. The "
        "answer is the proposal as the orchestrator shows it.",
        schema(
            {
                "kind": {"type": "string", "enum": ["preset", "schedule", "session"]},
                "target": _str("Preset id or name, schedule id or slug."),
                "patch": _PROPOSAL_PATCH,
                "reason": _PROPOSAL_REASON,
                "client_key": {"type": "string", "maxLength": 200},
            },
            ("kind", "target", "patch", "reason"),
        ),
        "*",
    ),
    AdminRow(
        "proposal_revise",
        "POST",
        f"{_A}/proposals/{{id}}/revise",
        "Send a new revision of one of your proposals after a rework verdict or a stale "
        "notice: a full proposal against the target as it is now, not a delta (the patch "
        "replaces the previous revision's). " + _PROPOSAL_TEXT + "The answer is the proposal "
        "as the orchestrator shows it.",
        schema(
            {
                "id": {"type": "integer", "description": "Proposal id."},
                "patch": _PROPOSAL_PATCH,
                "reason": _PROPOSAL_REASON,
            },
            ("id", "patch"),
        ),
        "*",
    ),
    AdminRow(
        "proposal_get",
        "GET",
        f"{_A}/proposals/{{proposal_id}}",
        "Read a change proposal (a superleader sees every proposal) with every revision: per "
        "item the before and after values, the human's verdict (pending, approved, rework, "
        "denied) and note. "
        "After a rework verdict or a stale notice, send a new revision with proposal_revise.",
        schema({"proposal_id": {"type": "integer"}}, ("proposal_id",)),
    ),
    AdminRow(
        "proposal_withdraw",
        "POST",
        f"{_A}/proposals/{{proposal_id}}/withdraw",
        "Withdraw one of your open change proposals.",
        schema({"proposal_id": {"type": "integer"}}, ("proposal_id",)),
    ),
    # webhooks
    AdminRow("webhooks_list", "GET", f"{_A}/webhooks/endpoints", "List the webhook endpoints."),
    AdminRow(
        "webhook_get_meta",
        "GET",
        f"{_A}/webhooks/meta",
        "Show what webhook endpoints support (types, auth modes, template fields).",
    ),
    AdminRow(
        "webhook_create",
        "POST",
        f"{_A}/webhooks/endpoints",
        "Create a webhook endpoint for a preset." + _CRED,
        schema(
            {
                "type": {"type": "string"},
                "preset_id": {"type": "string"},
                "auth_mode": {"type": "string", "enum": ["basic", "header", "bearer", "hmac"]},
                "filters": _FILTERS,
                "prompt_template": _NULL_STR,
                "suffix": _NULL_STR,
                **_AUTH,
            },
            ("type", "preset_id"),
        ),
        "*",
    ),
    AdminRow(
        "webhook_update",
        "PATCH",
        f"{_A}/webhooks/endpoints/{{id}}",
        "Edit a webhook endpoint; only the fields given change. Changing the auth mode "
        "issues a new credential." + _CRED,
        schema(
            {
                "id": _EP,
                "auth_mode": {"type": "string", "enum": ["basic", "header", "bearer", "hmac"]},
                "preset_id": _NULL_STR,
                "filters": _FILTERS,
                "prompt_template": _NULL_STR,
                "suffix": _NULL_STR,
                **_AUTH,
            },
            ("id",),
        ),
        "*",
    ),
    AdminRow(
        "webhook_clone",
        "POST",
        f"{_A}/webhooks/endpoints/{{id}}/clone",
        "Clone a webhook endpoint, optionally to another preset or suffix." + _CRED,
        schema({"id": _EP, "preset_id": _NULL_STR, "suffix": _NULL_STR}, ("id",)),
        "*",
    ),
    AdminRow(
        "webhook_rotate",
        "POST",
        f"{_A}/webhooks/endpoints/{{id}}/rotate",
        "Rotate the credential of a webhook endpoint." + _CRED,
        schema({"id": _EP, "username": _NULL_STR}, ("id",)),
        "*",
    ),
    AdminRow(
        "webhook_delete",
        "DELETE",
        f"{_A}/webhooks/endpoints/{{id}}",
        "Delete a webhook endpoint and its stored credential.",
        schema({"id": _EP}, ("id",)),
    ),
    AdminRow(
        "webhook_filters_set",
        "PUT",
        f"{_A}/webhooks/endpoints/{{id}}/filters",
        "Replace the allow and deny filters of a webhook endpoint.",
        schema(
            {"id": _EP, "allow": {"type": "array"}, "deny": {"type": "array"}},
            ("id",),
        ),
        "*",
    ),
    AdminRow(
        "webhook_events",
        "GET",
        f"{_A}/webhooks/events",
        "List received webhook events, newest first; before_id pages back.",
        schema(
            {
                "status": {"type": "string"},
                "endpoint_id": {"type": "string"},
                "limit": _LIMIT,
                "before_id": {"type": "string"},
            }
        ),
        None,
        ("status", "endpoint_id", "limit", "before_id"),
    ),
    AdminRow(
        "webhook_event",
        "GET",
        f"{_A}/webhooks/events/{{id}}",
        "Show one received webhook event.",
        schema({"id": _str("Event id.")}, ("id",)),
    ),
    AdminRow(
        "webhook_template_preview",
        "POST",
        f"{_A}/webhooks/templates/preview",
        "Render a prompt template against an example payload.",
        schema(
            {
                "type": {"type": "string"},
                "example": {},
                "template": _NULL_STR,
                "filters": _FILTERS,
            },
            ("type", "example"),
        ),
        "*",
    ),
    # schedules
    AdminRow("schedules_list", "GET", f"{_A}/schedules/schedules", "List the schedules."),
    AdminRow(
        "schedule_get",
        "GET",
        f"{_A}/schedules/schedules/{{schedule_id}}",
        "Show a schedule.",
        schema({"schedule_id": _SCHED}, ("schedule_id",)),
    ),
    AdminRow(
        "schedule_create",
        "POST",
        f"{_A}/schedules/schedules",
        "Create a schedule: a cron expression that starts a session from a preset with a prompt.",
        schema(_SCHED_FIELDS, ("name", "cron", "preset_id", "prompt")),
        "*",
    ),
    AdminRow(
        "schedule_update",
        "PUT",
        f"{_A}/schedules/schedules/{{schedule_id}}",
        "Replace a schedule (all required fields again).",
        schema(
            {"schedule_id": _SCHED, **_SCHED_FIELDS},
            ("schedule_id", "name", "cron", "preset_id", "prompt"),
        ),
        "*",
    ),
    AdminRow(
        "schedule_delete",
        "DELETE",
        f"{_A}/schedules/schedules/{{schedule_id}}",
        "Delete a schedule.",
        schema({"schedule_id": _SCHED}, ("schedule_id",)),
    ),
    AdminRow(
        "schedule_run",
        "POST",
        f"{_A}/schedules/schedules/{{schedule_id}}/run",
        "Fire a schedule now.",
        schema({"schedule_id": _SCHED}, ("schedule_id",)),
    ),
    AdminRow(
        "schedule_runs",
        "GET",
        f"{_A}/schedules/schedules/{{schedule_id}}/runs",
        "List the runs of one schedule, newest first.",
        schema({"schedule_id": _SCHED, "limit": _LIMIT}, ("schedule_id",)),
        None,
        ("limit",),
    ),
    AdminRow(
        "schedule_runs_all",
        "GET",
        f"{_A}/schedules/runs",
        "List the runs of all schedules, newest first.",
        schema({"status": {"type": "string"}, "limit": _LIMIT}),
        None,
        ("status", "limit"),
    ),
    AdminRow(
        "schedule_run_get",
        "GET",
        f"{_A}/schedules/runs/{{run_id}}",
        "Show one schedule run.",
        schema({"run_id": _str("Run id.")}, ("run_id",)),
    ),
    AdminRow(
        "schedule_preview",
        "POST",
        f"{_A}/schedules/schedules/preview",
        "Preview the next fire times of a cron expression and the rendered prompt.",
        schema(
            {
                "cron": {"type": "string"},
                "preset_id": _NULL_STR,
                "prompt": _NULL_STR,
                "schedule_name": _NULL_STR,
                "timezone": _NULL_STR,
            },
            ("cron",),
        ),
        "*",
    ),
    # chats
    AdminRow(
        "chat_admin_rooms",
        "GET",
        f"{_A}/chats/rooms",
        "List the chat rooms the connector knows, with their settings. The answer also carries a "
        "pending block: rooms and message requests waiting to be allowed.",
        schema({"kind": {"type": "string"}, "access": {"type": "string"}}),
        None,
        ("kind", "access"),
    ),
    AdminRow(
        "chat_room_settings",
        "PUT",
        f"{_A}/chats/rooms/{{room_id}}/settings",
        "Set a chat room's settings: trigger (all or mention), the preset that serves it and "
        "whether agents may start a chat there. agent_aliases: names that wake the agent in a "
        "mention room besides a tag of the bot or a reply to it.",
        schema(
            {
                "room_id": _ROOM_ID,
                "trigger": {"type": ["string", "null"], "enum": ["all", "mention", None]},
                "preset_id": _NULL_STR,
                "agents_may_initiate": {"type": ["boolean", "null"]},
                "agent_aliases": _ALIASES,
            },
            ("room_id",),
        ),
        "*",
    ),
    AdminRow(
        "chat_admin_room",
        "GET",
        f"{_A}/chats/rooms/{{room_id}}",
        "Show one chat room: its settings, members and the access of each member.",
        schema({"room_id": _ROOM_ID}, ("room_id",)),
    ),
    AdminRow(
        "chat_users",
        "GET",
        f"{_A}/chats/users",
        "Look up people the connector knows by name or Facebook id (q); use the Facebook id "
        "as the target of chat_open.",
        schema({"q": _str("Name part or Facebook id."), "limit": _LIMIT}),
        None,
        ("q", "limit"),
    ),
    AdminRow(
        "chat_room_allow",
        "POST",
        f"{_A}/chats/rooms/{{room_id}}/allow",
        "Allow a chat room and configure it (preset_id, trigger, agents_may_initiate). Also "
        "accepts a Messenger message request; an invited room is joined on the next sync.",
        schema(
            {
                "room_id": _ROOM_ID,
                "preset_id": _NULL_STR,
                "trigger": {"type": ["string", "null"], "enum": ["all", "mention", None]},
                "agents_may_initiate": {"type": ["boolean", "null"]},
                "agent_aliases": _ALIASES,
            },
            ("room_id",),
        ),
        "*",
    ),
    AdminRow(
        "chat_open",
        "POST",
        f"{_A}/chats/room-requests",
        "Ask to open a Messenger chat with a person (action open) or invite them to a room "
        "(action invite, with room_id). It asks the user on the taskboard under task_key; "
        "when you get 'Task T-n was updated', call chat_open_confirm; then poll "
        "chat_open_status until done; chat_room_allow the new room; session_create + "
        "session_send to start a session that posts there with chat_send. target is a "
        "Facebook id, a profile.php?id= link or a name. When several people match, the "
        "answer has candidates: call again with the chosen Facebook id. A vanity link "
        "(facebook.com/some.name) cannot be resolved: pass the person's name instead.",
        schema(
            {
                "target": _str("Facebook id, profile link or name of the person."),
                "task_key": _str("Taskboard task key (T-n) the question is asked under."),
                "action": {"type": "string", "enum": ["open", "invite"]},
                "room_id": _str("Room to invite the person to (action invite)."),
            },
            ("target", "task_key"),
        ),
        "*",
    ),
    AdminRow(
        "chat_open_status",
        "GET",
        f"{_A}/chats/room-requests/{{request_id}}",
        "Show the status of a chat_open request (poll it until done).",
        schema({"request_id": _str("Room request id.")}, ("request_id",)),
    ),
    AdminRow(
        "chat_open_confirm",
        "POST",
        f"{_A}/chats/room-requests/{{request_id}}/confirm",
        "Confirm a chat_open request after the taskboard decision was answered ('Task T-n was "
        "updated'). Only the user's answer to the decision the orchestrator attached counts.",
        schema({"request_id": _str("Room request id.")}, ("request_id",)),
        "*",
    ),
    AdminRow("chat_rules", "GET", f"{_A}/chats/rules", "List the chat allow and deny rules."),
    AdminRow(
        "chat_rule_add",
        "POST",
        f"{_A}/chats/rules",
        "Add a chat rule: list (allow or deny), kind and value select the chats it covers.",
        schema(
            {
                "list": {"type": "string"},
                "kind": {"type": "string"},
                "value": {"type": "string"},
                "note": _NULL_STR,
                "preamble": _NULL_STR,
            },
            ("list", "kind", "value"),
        ),
        "*",
    ),
    AdminRow(
        "chat_rule_delete",
        "DELETE",
        f"{_A}/chats/rules/{{rule_id}}",
        "Delete a chat rule.",
        schema({"rule_id": _str("Rule id.")}, ("rule_id",)),
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
    AdminRow(
        "history_get",
        "GET",
        f"{_A}/history/sessions/{{session_id}}",
        "Show one stored session of the history: provenance, preset, timestamps and record "
        "counts." + _HISTORY_SCOPE,
        schema({"session_id": _HISTORY_SESSION_ID}, ("session_id",)),
    ),
    AdminRow(
        "history_records",
        "GET",
        f"{_A}/history/sessions/{{session_id}}/records",
        "Page the stored records (chat events, commands and so on) of one session in id order. "
        "Filter by kind and by text: "
        + _HISTORY_MATCH
        + " Page forward with after_id (the last id seen) or backward with before_id."
        + _HISTORY_SCOPE,
        schema(
            {
                "session_id": _HISTORY_SESSION_ID,
                "kind": _str("Only records of this kind."),
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
            ("session_id",),
        ),
        None,
        ("kind", "q", "regex", "before_id", "after_id", "limit"),
    ),
    # superleader reads of any session's live work, the deploy and the cache archives
    AdminRow(
        "sessions_admin_list",
        "GET",
        f"{_A}/sessions",
        "Superleader only: list every session of every project, manageable or not, as a "
        "summary (slug, preset_name, project, extra_projects, status, desired, labels, hive, "
        "superleader, created_at); session_get shows one in full. Filter by label, project, "
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
        "session_activity",
        "GET",
        f"{_A}/sessions/{{slug}}/activity",
        "Superleader only: the recent activity of any session (what its agents did), "
        "manageable or not; redacted like the web UI. Filter by agent (pod name).",
        schema(
            {"slug": _SLUG, "agent": _str("Pod name; omitted: all."), "limit": _LIMIT}, ("slug",)
        ),
        None,
        ("agent", "limit"),
    ),
    AdminRow(
        "session_events",
        "GET",
        f"{_A}/sessions/{{slug}}/events",
        "Superleader only: a page of the stored events of any session (chat, tool calls, "
        "hooks), manageable or not; redacted like the web UI. Not a stream. A page is in "
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
        "session_messages",
        "GET",
        f"{_A}/sessions/{{slug}}/messages",
        "Superleader only: the message queue of any session (queued, delivered, failed), "
        "manageable or not. Filter by status.",
        schema({"slug": _SLUG, "status": _str("Message status."), "limit": _LIMIT}, ("slug",)),
        None,
        ("status", "limit"),
    ),
    AdminRow(
        "session_locks",
        "GET",
        f"{_A}/sessions/{{slug}}/locks",
        "Superleader only: the path locks held in any session, manageable or not.",
        schema({"slug": _SLUG}, ("slug",)),
    ),
    AdminRow(
        "session_commands",
        "GET",
        f"{_A}/sessions/{{slug}}/commands",
        "Superleader only: the recent commands (restarts and so on) of any session, manageable "
        "or not; read one with command_get.",
        schema({"slug": _SLUG, "limit": _LIMIT}, ("slug",)),
        None,
        ("limit",),
    ),
    AdminRow(
        "meta_get",
        "GET",
        f"{_A}/meta",
        "Superleader only: deploy metadata of the orchestrator that is not secret: defaults "
        "(model, effort, resources, ...), agent images, models, efforts, caps, cache, "
        "kube/docker availability, hook events, permission modes, usage and inactivity "
        "settings.",
    ),
    AdminRow(
        "archives_list",
        "GET",
        f"{_A}/archives",
        "Superleader only: list the cache archives (object keys, sizes, times), one page at a "
        "time. Filter by prefix; pass the cursor of the previous page to continue. There is no "
        "download: an archive link carries a credential. 400 bad_request for a bad prefix or "
        "cursor, 503 unavailable when archives are not configured.",
        schema(
            {
                "prefix": _str("Only keys under this prefix."),
                "cursor": _str("Cursor of the previous page."),
                "limit": _LIMIT,
            }
        ),
        None,
        ("prefix", "cursor", "limit"),
    ),
    AdminRow(
        "archives_settings_get",
        "GET",
        f"{_A}/archives/settings",
        "Superleader only: the cache archive cleanup settings (retention and schedule). "
        "503 unavailable when archives are not configured.",
    ),
    AdminRow(
        "archives_cleanup_preview",
        "GET",
        f"{_A}/archives/cleanup/preview",
        "Superleader only: what the next cache archive cleanup would delete, without deleting "
        "anything. 503 unavailable when archives are not configured.",
    ),
    # usage history and the session start and end marks (relayed from agents-usage)
    AdminRow(
        "usage_history",
        "GET",
        f"{_A}/usage/history",
        "Superleader only: the account's Claude usage over time (five_hour and seven_day "
        "utilization points) between from and to (ISO 8601). metric picks the window; "
        "max_points downsamples the series. usage_get shows the current reading.",
        schema(
            {
                "from": _str("Start, ISO 8601."),
                "to": _str("End, ISO 8601."),
                "metric": _str("Usage window: five_hour or seven_day."),
                "max_points": {
                    "type": "integer",
                    "minimum": 10,
                    "description": "Most points (at least 10).",
                },
            }
        ),
        None,
        ("from", "to", "metric", "max_points"),
    ),
    AdminRow(
        "usage_sessions",
        "GET",
        f"{_A}/usage/sessions",
        "Superleader only: the session start and end marks recorded for usage, between from "
        "and to (ISO 8601), to relate usage to sessions.",
        schema({"from": _str("Start, ISO 8601."), "to": _str("End, ISO 8601."), "limit": _LIMIT}),
        None,
        ("from", "to", "limit"),
    ),
)

ADMIN_TOOLS: tuple[AdminRow, ...] = tuple(
    replace(r, description=r.description + _NOT_MANAGEABLE) if r.name in _WRITE_NAMES else r
    for r in _ROWS
)
