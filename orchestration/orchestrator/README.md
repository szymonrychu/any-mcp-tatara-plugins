# Orchestrator plugin

Distribution `any-mcp-tatara-orchestrator`, module `any_mcp_tatara_orchestrator`, entry point
`orchestrator` (`any_mcp_tatara_orchestrator:OrchestratorPlugin`). It is the one plugin through which
agents call the agents-orchestrator agent API `/agent/v1`. It does no network I/O at start; every
tool is a thin HTTP call that returns the server JSON, and a non-2xx answer becomes a tool error
carrying the server's `error.code` and message (`not_leader`, `stale_epoch`, `no_peers`, `alone`,
`static_session`, `out_of_range`, `leader_would_leave`, `invalid_path`, `target_not_superleader`,
`hop_limit`, `pair_budget`, ...).

Config fields (extra keys are refused): `base_url`, `token`, `session` (session key), `agent` (pod
name), `slug`, `max_replicas` (default 1), `static` (default false), `hive` (default false),
`auto_compact_pct` (default null). Every request carries `Authorization: Bearer <token>` and
`X-Orch-Session: <session>`; the token is never logged or echoed in a result or error.

| Tool | Route |
|------|-------|
| `orchestrator_me` | `GET /agent/v1/me` |
| `orchestrator_activity(agent?, limit?)` | `GET /agent/v1/activity` (query `agent`, a pod name, and `limit` 1..200; what the session's agents did recently, newest last) |
| `orchestrator_leadership_handover(reason, epoch, to?)` | `POST /agent/v1/leadership/handover` |
| `orchestrator_lock_claim(paths)` | `POST /agent/v1/locks/claim` |
| `orchestrator_lock_release(paths)` | `POST /agent/v1/locks/release` |
| `orchestrator_lock_list` | `GET /agent/v1/locks` |
| `orchestrator_lock_reassign(path, to?)` | `POST /agent/v1/locks/reassign` (`to` null discards the lock) |
| `orchestrator_scale(replicas)` | `POST /agent/v1/scale` |
| `orchestrator_init_scripts_get` | `GET /agent/v1/init-scripts` |
| `orchestrator_init_scripts_edit(scripts)` | `PUT /agent/v1/init-scripts` |
| `orchestrator_sessions_list` | `GET /agent/v1/sessions` |
| `orchestrator_session_send(slug, text)` | `POST /agent/v1/sessions/{slug}/messages` (body `{"text"}`; asynchronous, answers a message id) |
| `orchestrator_chat_rooms` | `GET /agent/v1/chat/rooms` |
| `orchestrator_chat_send(text, room?, reply_to?)` | `POST /agent/v1/chat/messages` (asynchronous, answers an id `s<n>`) |
| `orchestrator_chat_react(to, key)` | `POST /agent/v1/chat/reactions` (asynchronous, answers an id `s<n>`) |
| `orchestrator_chat_status(id)` | `GET /agent/v1/chat/messages/{id}` (delivery status of a send `s<n>`) |
| `orchestrator_self_close(reason)` | `POST /agent/v1/close` (close the session once idle, leader only) |

Task tools (`tasks.py`): a task or decision answer over the tool output budget (40000 chars) is
trimmed (oldest events, then oldest comments, long texts cut) and reports what it left out in
`omitted`; `task_list` answers a summary per task with child counts, `limit` (default 10, at most
20) and `cursor`.

Superleader admin tools (`admin.py`, one declarative row per route of `/agent/v1/admin`; the
orchestrator refuses them unless the caller is a superleader session and renders them into the
allowlist of such sessions only): presets (`presets_list`, `preset_get`, `preset_create`,
`preset_replace`, `preset_patch`, `preset_delete`, `preset_copy`), sessions (`session_get`, `session_patch`, `session_create`,
`session_discard`, `session_restart`, `agent_restart`, `command_get`), webhooks (`webhooks_list`,
`webhook_get_meta`, `webhook_create`, `webhook_update`, `webhook_clone`, `webhook_rotate`,
`webhook_delete`, `webhook_filters_set`, `webhook_events`, `webhook_event`,
`webhook_template_preview`), schedules (`schedules_list`, `schedule_get`, `schedule_create`,
`schedule_update`, `schedule_delete`, `schedule_run`, `schedule_runs`, `schedule_runs_all`,
`schedule_run_get`, `schedule_preview`), chats (`chat_admin_rooms`, `chat_admin_room`, `chat_users`, `chat_room_allow`, `chat_open`, `chat_open_status`, `chat_open_confirm`, `chat_room_settings`,
`chat_rules`, `chat_rule_add`, `chat_rule_delete`), `usage_get` (read-only: the account's `five_hour` and
`seven_day` usage windows, `GET /agent/v1/admin/usage`) and the history of discarded sessions
(`history_search`, `history_get`, `history_records`; read and search only, there is no delete
tool). `history_search` answers `{next_before_id, sessions}` (default `limit` 20, at most 200):
the relay filters a page after paging, so a page can be short or empty while more sessions exist,
and `next_before_id` (the relay's `X-Next-Before-Id` header, `null` at the end) is the `before_id`
of the next call. The cursor comes first because any-mcp cuts an over-budget tool text (40000
chars) at its tail, and a full page of hits can exceed it. A
contract test pins every row's method and path to `tests/fixtures/orchestrator_openapi.json`.

Preset and session tools refuse a protected env entry that carries a `value` (use `secret_set`).

`session_get` and `session_patch` (`GET` and `PATCH /agent/v1/admin/sessions/{slug}`) are also
rendered for every session that is not a superleader, which may read and patch only its own
session: the orchestrator's gate refuses any other slug and any caller that is not the leader.
`session_patch` takes `slug` and `spec`, an RFC 7396 merge patch of the live session's stored spec
(arrays replace whole, `null` deletes a key); it is applied when the pods are replaced, once the
session is idle, and never writes the source preset. A leader may only lower `usage_quota` and
shorten `inactivity` limits on its own session, human-only fields only move toward safe, and
`replicas`, `max_replicas`, `static`, `workdir`, `pvc_size` and `cache` cannot change on a live
session.

Local tool `orchestrator_secret_set(preset, name, from_file | from_env | from_command)`: the value
is resolved in the plugin process (file capped at 64 KiB, an env var of the pod, or a command run
without a shell with a 10 s limit) and sent as the body of `PUT /agent/v1/admin/presets/{ref}/secrets/{name}`
(the orchestrator refuses a name that is not a protected variable of the preset, as in the GitLab
CI variables model: write-only, overwrite-only). The result is `{name, set, version, bytes}`;
errors carry a fixed class name, never a value, a path or command output.
`webhook_create`, `webhook_update`, `webhook_clone` and `webhook_rotate` take an optional `to_file`
(absolute path inside the secrets directory, config `secrets_dir`, default `~/.agent-secrets`, created 0700; real paths are checked so symlinks and `..` cannot escape; an existing file is refused unless `overwrite` is true): the credential the upstream issues once is written there (mode 0600,
atomic replace) from the orchestrator's single local-channel answer (`X-Orch-Tool: local`), and the
result says only `to_file: {written, key, bytes}`. The same token reaches the same channel, so this
guards the transcript, not a hostile model.

The plugin always lists the sixteen agent tools, the admin tools and `secret_set`. Which an agent sees is decided by the `allow` list of the
entry the orchestrator renders (`scale` only on a scalable session, the hive tools only in a hive).
The session tools `sessions_list` and `session_send` are rendered for every session; the
orchestrator decides what each session may list and send: a superleader session's leader lists and
messages every other session, any other session's leader lists and messages only the superleader
sessions. A message arrives at the target leader as a new turn framed
`<session-message from="<slug>" ...>`; an answer is a send back to that slug. The chat tools
reach the user's chat through the orchestrator, leader only. The final answer of a turn reaches the
chat only for a chat-started turn; at any other time the leader calls `orchestrator_chat_send`. A
send is asynchronous and answers an id `s<n>`; `reply_to` and `to` take the `m<n>` shown on a chat
message or an `s<n>`; `orchestrator_chat_status` reads the delivery status. A chat room and a
session are bound one to one, so a session talks in one room only, its bound room (one room per
session): it starts a chat only in an unbound room flagged `agents_may_initiate` while it is bound
to none, and otherwise the orchestrator refuses with `room_bound` or `session_bound`. Further chat
refusal codes are `room_not_allowed`, `room_not_authorised`, `bad_reference`, `rate_limited`,
`not_leader` and `chat_unavailable`. With `hive` true the
hive tool descriptions start with "You are agent <agent>, one of up to <max_replicas> agents in hive
<slug>; the workdir is shared."; with `auto_compact_pct` set the handover description advises
handing over once context passes `auto_compact_pct - 15` percent.

Peers' work is never streamed to an agent: it pulls it on demand with `orchestrator_activity`
(optional `agent` pod name, `limit` 1..200; answers `{"lines": [...]}`, newest last), and the
`orchestrator_me` description says so. The `orchestrator_leadership_handover` description carries
the brief-then-handover protocol: when a `handover_due` note names the candidates, the leader briefs
the chosen successor with `mcp__ccw__send_message(notify_when_idle=true)`, waits for its "ready",
then calls the tool with `to`, the current epoch and a reason. The requester learns the outcome as
a keyed `leadership_request` note: applied, or rejected with the reason.

## Configuration

any-mcp installs the plugin from its source on load (or ahead of time with
`any-mcp plugins install <source>`):

```yaml
plugins:
  - name: orchestrator
    source: github.com/szymonrychu/any-mcp-tatara-plugins//orchestration/orchestrator?ref=v0.20.0
    allow: [me, activity, leadership_handover, lock_*, init_scripts_*]
    config:
      base_url: http://orchestrator-agents:8081
      token: ${ORCH_AGENT_TOKEN}
      session: s-1
      agent: agent-0
      slug: my-hive
      max_replicas: 3
      hive: true
```

`${ORCH_AGENT_TOKEN}` is expanded by any-mcp from its environment, so the token is never written to
the config file. The token is scoped to one session.

## Development

From the repo root: `mise run install && mise run test && mise run lint`. The tests are in
`tests/test_orchestrator_plugin.py` and run against the package through any-mcp's `PluginHarness`.
