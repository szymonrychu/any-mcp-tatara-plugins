"""Declarative rows for the superleader admin routes ``/agent/v1/admin`` of agents-orchestrator.

One row per route; ``__init__`` turns each into a ``ToolSpec``. The orchestrator refuses every
route unless the caller is a superleader session, and the rendered allowlist offers these tools
to such sessions only. Secret values never pass through a row: a protected variable is set with
the local ``secret_set`` tool, and webhook credentials are issued by the upstream and stored by
the orchestrator (see ``secrets_local``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["ADMIN_TOOLS", "AdminRow", "schema", "summarize_presets"]

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
_SLUG = _str("Session slug.")
_POD = _str("Agent pod name.")
_EP = _str("Webhook endpoint id.")
_SCHED = _str("Schedule id.")
_LIMIT = {"type": "integer", "minimum": 1, "maximum": 200}
_SPEC = {
    "type": "object",
    "description": "Session spec. A protected env entry never carries a value here: set it "
    "with secret_set.",
}
_PATCH_SPEC = {
    "type": "object",
    "description": "JSON merge patch (RFC 7396) of the stored spec: objects merge, null deletes "
    "a key, arrays replace. Keys you leave out are kept, protected variables included. Never "
    "put a protected value here: set it with secret_set. Human-only fields (kube_access, "
    "session_messaging, docker, discard_hook_url, wrapper.permission_mode, "
    "wrapper.permissions) may only move toward safe.",
}
_PATCH_SPEC_SESSION = {
    **_PATCH_SPEC,
    "description": "JSON merge patch (RFC 7396) of the session's stored spec: objects merge, "
    "null deletes a key, arrays replace. Keys you leave out are kept, protected variables "
    "included. Never put a protected value here: set it with secret_set. A leader may only "
    "lower usage_quota and shorten inactivity limits on its own session; human-only fields "
    "(kube_access, session_messaging, docker, discard_hook_url, wrapper.permission_mode, "
    "wrapper.permissions) may only move toward safe.",
}
_FILTERS = {
    "type": "object",
    "properties": {"allow": {"type": "array"}, "deny": {"type": "array"}},
    "additionalProperties": False,
}
_NULL_STR = {"type": ["string", "null"]}
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
_HISTORY_SCOPE = (
    " Results only cover sessions on presets you may manage; history is read-only, a stored "
    "session is deleted by the user in the web UI."
)
_HISTORY_MATCH = (
    "q (3 to 500 characters) is a case-insensitive substring match, or with regex true a "
    "Postgres regex."
)
HISTORY_SEARCH_LIMIT = 20  # default page: 20 hits stay well inside the 40000 char tool budget
_HISTORY_Q = {"type": "string", "minLength": 3, "maxLength": 500}
_HISTORY_REGEX = {"type": "boolean", "description": "Treat q as a Postgres regex."}
_HISTORY_BEFORE = {
    "type": "integer",
    "minimum": 1,
    "description": "Return rows with an id below this one (the next_before_id of a page).",
}

ADMIN_TOOLS: tuple[AdminRow, ...] = (
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
        "Show a preset by id or name. Protected env variables show metadata only "
        "(set, version, updated_at), never a value.",
        schema({"ref": _REF}, ("ref",)),
    ),
    AdminRow(
        "preset_create",
        "POST",
        f"{_A}/presets",
        "Create a preset. Declare a secret as an env entry with protected true and no value, "
        "then set its value with secret_set.",
        schema({"name": {"type": "string"}, "spec": _SPEC}, ("name",)),
        "*",
    ),
    AdminRow(
        "preset_replace",
        "PUT",
        f"{_A}/presets/{{ref}}",
        "Replace a preset (name and spec). A protected entry sent without a value keeps its "
        "stored value; one left out is removed.",
        schema({"ref": _REF, "name": {"type": "string"}, "spec": _SPEC}, ("ref", "name")),
        "*",
    ),
    AdminRow(
        "preset_patch",
        "PATCH",
        f"{_A}/presets/{{ref}}",
        "Change part of a preset (name, labels, spec) by id or name. Prefer this over "
        "preset_replace: read-modify-write is not needed. Example: "
        '{"ref": "webhook", "spec": {"usage_quota": {"five_hour_pct": 80}}}.',
        schema(
            {
                "ref": _REF,
                "name": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
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
        "Delete a preset and its stored secrets.",
        schema({"ref": _REF}, ("ref",)),
    ),
    AdminRow(
        "preset_copy",
        "POST",
        f"{_A}/presets/{{ref}}/copy",
        "Copy a preset under a new name (default: derived). Protected variables are copied "
        "as unset: set them again with secret_set.",
        schema({"ref": _REF, "name": _NULL_STR}, ("ref",)),
        "*",
    ),
    # sessions
    AdminRow(
        "session_get",
        "GET",
        f"{_A}/sessions/{{slug}}",
        "Show a session by slug, with its agents. A superleader reads its own or a manageable "
        "session, any other leader its own.",
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
        "The new session is not a trigger session and has its own leader.",
        schema({"preset_id": _NULL_STR, "slug": _NULL_STR, "spec": {"type": ["object", "null"]}}),
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
        "session_restart",
        "POST",
        f"{_A}/sessions/{{slug}}/restart",
        "Restart every agent of a session. Answers command ids; read them with command_get. "
        "Restarting your own session is a self-restart: it has a cooldown (429 "
        "restart_cooldown, with retry_after) and you get a note when you are back.",
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
        "command_get",
        "GET",
        f"{_A}/commands/{{command_id}}",
        "Show the status of a restart command you issued.",
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
        "List the chat rooms the connector knows, with their settings.",
        schema({"kind": {"type": "string"}, "access": {"type": "string"}}),
        None,
        ("kind", "access"),
    ),
    AdminRow(
        "chat_room_settings",
        "PUT",
        f"{_A}/chats/rooms/{{room_id}}/settings",
        "Set a chat room's settings: trigger (all or mention), the preset that serves it and "
        "whether agents may start a chat there.",
        schema(
            {
                "room_id": _ROOM_ID,
                "trigger": {"type": ["string", "null"], "enum": ["all", "mention", None]},
                "preset_id": _NULL_STR,
                "agents_may_initiate": {"type": ["boolean", "null"]},
            },
            ("room_id",),
        ),
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
)
