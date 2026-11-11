# Orchestrator plugin

Distribution `any-mcp-tatara-orchestrator`, module `any_mcp_tatara_orchestrator`, entry point
`orchestrator` (`any_mcp_tatara_orchestrator:OrchestratorPlugin`). It is the one plugin through which
agents call the agents-orchestrator agent API `/agent/v1`. It does no network I/O at start; every
tool is a thin HTTP call that returns the server JSON, and a non-2xx answer becomes a tool error
carrying the server's `error.code` and message (`not_leader`, `stale_epoch`, `no_peers`, `alone`,
`static_session`, `out_of_range`, `leader_would_leave`, `invalid_path`, `project_forbidden`,
`hop_limit`, `pair_budget`, ...).

Config fields (extra keys are refused): `base_url`, `token`, `session` (session key), `agent` (pod
name), `slug`, `max_replicas` (default 1), `static` (default false), `hive` (default false),
`auto_compact_pct` (default null). Every request carries `Authorization: Bearer <token>` and
`X-Orch-Session: <session>`; the token is never logged or echoed in a result or error.

| Tool | Route |
|------|-------|
| `orchestrator_me` | `GET /agent/v1/me` |
| `orchestrator_activity(agent?, limit?)` | `GET /agent/v1/activity` (query `agent`, a pod name, and `limit` 1..200; what the session's agents did recently, newest last) |
| `orchestrator_leadership_handover(reason, epoch, to?)` | `POST /agent/v1/leadership/handover` (`reason` 1..2000 characters, the state summary) |
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
| `orchestrator_project_list` | `GET /agent/v1/projects` (your own project; a superleader gets every active project) |
| `orchestrator_self_close(reason)` | `POST /agent/v1/close` (close the session once idle, leader only) |

Task tools (`tasks.py`, `views.py`): `decision_withdraw` takes `reason` and exactly one of `superseded_by` (decision id) or `answered_elsewhere` (text); `task_get` takes a `view` (`summary` default, `open_decisions`, `todo`,
`events`, `comments`) and answers only that part; the write tools (`task_create`, `task_update`,
`task_comment`, `todo_set`, `todo_update`, `decision_ask`, `decision_withdraw`) answer a short
confirmation (key, version, url; `todo_set` also the new `[id, text]` items). A task answer over the
tool output budget (40000 chars) is
trimmed (oldest events, then oldest comments, long texts cut) and reports what it left out in
`omitted`; `task_create`, `task_update` and `task_list` take an optional `project` slug (see
`project_list`; omitted on create: the caller's own project, `default` for a superleader); `task_list` answers a summary per task with child counts, `limit` (default 10, at most
20) and `cursor`.

Superleader admin tools (`admin.py`, one declarative row per route of `/agent/v1/admin`; the
orchestrator refuses them unless the caller is a superleader session and renders them into the
allowlist of such sessions only): presets (`presets_list`, `preset_get`, `preset_create`,
`preset_replace`, `preset_patch`, `preset_delete`, `preset_copy`; `preset_patch` and `preset_replace` take an optional `project` slug that
moves the preset to another existing project: 422 `unknown_project`, 422 `vault_label_mismatch`,
422 `project_archived`; running sessions keep their project), sessions (`session_get`, `session_patch`, `session_create`,
`session_discard`, `session_restart`, `agent_restart`, `command_get`), `leadership_withdraw`
(`POST /agent/v1/admin/sessions/{slug}/leadership/withdraw`, body `{request_id?}`: withdraw one or
every pending handover request, never the orchestrator's `rollout` request, which is 409
`rollout_request` by id; the orchestrator also lists it for every hive, whose leader may
call it on its own slug), webhooks (`webhooks_list`,
`webhook_get_meta`, `webhook_create`, `webhook_update`, `webhook_clone`, `webhook_rotate`,
`webhook_delete`, `webhook_filters_set`, `webhook_events`, `webhook_event`,
`webhook_template_preview`), schedules (`schedules_list`, `schedule_get`, `schedule_create`,
`schedule_update`, `schedule_delete`, `schedule_run`, `schedule_runs`, `schedule_runs_all`,
`schedule_run_get`, `schedule_preview`), projects (`project_create`: `POST /agent/v1/admin/projects`, body `{slug, name, description?}`; projects are never deleted, 409 `project_exists` = slug taken), chats (`chat_admin_rooms`, `chat_admin_room`, `chat_users`, `chat_room_allow`, `chat_open`, `chat_open_status`, `chat_open_confirm`, `chat_room_settings`,
`chat_rules`, `chat_rule_add`, `chat_rule_delete`), `usage_get` (read-only: the account's `five_hour` and
`seven_day` usage windows, `GET /agent/v1/admin/usage`) and the history of discarded sessions
(`history_search`, `history_get`, `history_records`; read and search only, there is no delete
tool), and the caller's own change proposals (`proposals_list`, `proposal_get`,
`proposal_withdraw`). `history_search` answers `{next_before_id, sessions}` (default `limit` 20, at most 200):
the relay filters a page after paging, so a page can be short or empty while more sessions exist,
and `next_before_id` (the relay's `X-Next-Before-Id` header, `null` at the end) is the `before_id`
of the next call. The cursor comes first because any-mcp cuts an over-budget tool text (40000
chars) at its tail, and a full page of hits can exceed it. A
contract test pins every row's method and path to `tests/fixtures/orchestrator_openapi.json`.

Secret values live in agents-vault and are written by users in the web UI. `vault_list` (`GET /agent/v1/admin/vault`, query `q`)
lists entries (name, description, labels, env names, readable values, attachments) and presets with env names
to re-attach; protected values are never shown. `vault_attach` (`PUT /agent/v1/admin/presets/{ref}/vault/{entry_id}`, body
`envs`, `replace`) attaches all envs of an entry in one save (or those in `envs`, an object mapping entry env name to
preset env name, e.g. `{"GITHUB_TOKEN": "BOT_TOKEN"}`; same name on both sides attaches a subset unrenamed; `replace`
overwrites an existing preset env of the same name, else 409 `env_exists`) (entry labels must match the preset; privileged presets
included, no proposal, nothing refused) and `vault_detach` (`DELETE` the same path, query `env`) detaches. Attachments
carry `privileged` in `vault_list` and `attachment_privileged` in the `vault_attach` response (0.29.0, orchestrator >=
7.1.0): true means the preset's sessions have Kubernetes API access or docker/dind and can read every Secret in the
namespace, so the entry's values are exposed to them; it is a mark, not a block. There is no `secret_set`
(removed in 0.28.0, orchestrator >= 7.0.0): the preset and session tools no longer scan for protected values.

`session_get` and `session_patch` (`GET` and `PATCH /agent/v1/admin/sessions/{slug}`) are also
rendered for every session that is not a superleader, which may read and patch only its own
session: the orchestrator's gate refuses any other slug and any caller that is not the leader.
`session_patch` takes `slug` and `spec`, an RFC 7396 merge patch of the live session's stored spec
(arrays replace whole, `null` deletes a key); it is applied when the pods are replaced, once the
session is idle, and never writes the source preset. A leader may only lower `usage_quota` and
shorten `inactivity` limits on its own session, human-only fields only move toward safe, and
`replicas`, `max_replicas`, `static`, `workdir`, `pvc_size` and `cache` cannot change on a live
session.

`webhook_create`, `webhook_update`, `webhook_clone` and `webhook_rotate` take an optional `to_file`
(absolute path inside the secrets directory, config `secrets_dir`, default `~/.agent-secrets`, created 0700; real paths are checked so symlinks and `..` cannot escape; an existing file is refused unless `overwrite` is true): the credential the upstream issues once is written there (mode 0600,
atomic replace) from the orchestrator's single local-channel answer (`X-Orch-Tool: local`), and the
result says only `to_file: {written, key, bytes}`. The same token reaches the same channel, so this
guards the transcript, not a hostile model.

`proposal_create(kind, target, patch, reason, client_key?)` and `proposal_revise(id, patch, reason?)` file and revise a
change proposal (`POST /agent/v1/admin/proposals` and `.../{id}/revise`) for a human to review in the web UI: security
changes and human-only fields of presets, schedules and session specs go there. `patch` is an RFC 7396 merge patch of
the target's spec. Proposals carry no secrets; a call with a `secrets` argument is refused by the closed schema before
any request.

The plugin always lists the seventeen agent tools, the admin tools (vault and proposal tools included). Which an agent sees is decided by the `allow` list of the
entry the orchestrator renders (`scale` only on a scalable session, the hive tools only in a hive).
The session tools `sessions_list` and `session_send` are rendered for every session; the
orchestrator decides what each session may list and send: a superleader session's leader lists and
messages every other session, any other session's leader lists and messages the other sessions of
its project and the superleader sessions (a send to another project is refused with
`project_forbidden`, unless that session messaged it in the reply window). A message arrives at the target leader as a new turn framed
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
<slug>; the workdir is shared.". The handover description no longer
advises a context threshold: the orchestrator sends a `handover_due` note when a handover is due.
`orchestrator_leadership_handover` and `orchestrator_session_send` take an optional `handled`
(up to 50 integers): the ids (the `id` attribute of `<session-message>`) of messages from other
sessions the call answers or that need no answer; unmarked ones carry over to the next leader.
`orchestrator_me` tells the leader its answer carries `leader_state`, the live generated state, to
read first after taking over; its and the handover description end with the re-read rule for
decision and approval states.

Peers' work is never streamed to an agent: it pulls it on demand with `orchestrator_activity`
(optional `agent` pod name, `limit` 1..200; answers `{"lines": [...]}`, newest last), and the
`orchestrator_me` description says so. The `orchestrator_leadership_handover` description carries
the voluntary handover protocol: the leader decides when (typically after a `handover_due` note),
passes `reason` as a state summary of at most 2000 characters, optionally `to` (without it the
orchestrator picks the best successor: idle, lowest context, no running background agents), and
reads the epoch from `orchestrator_me`. It applies once the leader's turn has ended and its
background agents have reported (the answer says `pending`). The leader does not brief the
successor: the orchestrator sends it the leader's transcript, headed by `reason`. The requester
learns the outcome as a keyed `leadership_request` note: applied, or rejected with the reason.
Every request has a deadline (the orchestrator's `ORCH_HANDOVER_MAX_WAIT_SECONDS`): past it background agents no longer hold it, and one the leader's turn still holds is
rejected `expired`; a pending one is cancelled with `orchestrator_leadership_withdraw`.
The `orchestrator_me` description tells a member that receives a background-agent notification
meant for the leader to forward one line to the leader.

## Configuration

any-mcp installs the plugin from its source on load (or ahead of time with
`any-mcp plugins install <source>`):

```yaml
plugins:
  - name: orchestrator
    source: github.com/szymonrychu/any-mcp-tatara-plugins//orchestration/orchestrator?ref=v0.33.0
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
