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

__all__ = ["ADMIN_TOOLS", "AdminRow", "schema"]

_A = "/admin"


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
_HISTORY_Q = {"type": "string", "minLength": 3, "maxLength": 500}
_HISTORY_REGEX = {"type": "boolean", "description": "Treat q as a Postgres regex."}
_HISTORY_BEFORE = {
    "type": "integer",
    "minimum": 1,
    "description": "Return rows with an id below this one (the next_before_id of a page).",
}

ADMIN_TOOLS: tuple[AdminRow, ...] = (
    # presets
    AdminRow("presets_list", "GET", f"{_A}/presets", "List the presets."),
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
        "Show a session by slug, with its agents.",
        schema({"slug": _SLUG}, ("slug",)),
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
        "Discard a session by slug. Your own session is refused (own_session).",
        schema({"slug": _SLUG}, ("slug",)),
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
        "limit sessions (default 50, at most 200). The answer is "
        "{sessions: [...], next_before_id}: a page can be short or even empty while more "
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
                "limit": _LIMIT,
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
