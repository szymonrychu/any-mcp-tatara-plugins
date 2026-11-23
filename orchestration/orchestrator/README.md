# Orchestrator plugin

Distribution `any-mcp-tatara-orchestrator`, module `any_mcp_tatara_orchestrator`, entry point
`orchestrator` (`any_mcp_tatara_orchestrator:OrchestratorPlugin`). It is the one plugin through which
agents call the agents-orchestrator agent API `/agent/v1`. At start it reads only `GET /agent/v1/me`,
and only when the config names role lists (below); every
tool is a thin HTTP call that returns the server JSON, and a non-2xx answer becomes a tool error
carrying the server's `error.code` and message (`not_leader`, `stale_epoch`, `no_peers`, `alone`,
`static_session`, `out_of_range`, `leader_would_leave`, `invalid_path`, `project_forbidden`,
`hop_limit`, `pair_budget`, ...).

Config fields (extra keys are refused): `base_url`, `token`, `session` (session key), `agent` (pod
name), `slug`, `max_replicas` (default 1), `static` (default false), `hive` (default false),
`auto_compact_pct` (default null), `member_tools` and `leader_tools` (plugin-local tool names,
default null). Every request carries `Authorization: Bearer <token>` and
`X-Orch-Session: <session>`; the token is never logged or echoed in a result or error.

| Tool | Route |
|------|-------|
| `orchestrator_me(meta?)` | `GET /agent/v1/me`; `meta` true: `GET /agent/v1/admin/meta` (deploy metadata, superleader) |
| `orchestrator_activity(agent?, limit?)` | `GET /agent/v1/activity` (query `agent`, a pod name, and `limit` 1..200; what the session's agents did recently, newest last) |
| `orchestrator_handoff_submit(doing?, done?, in_flight?, next?, open_requests?, results?, gotchas?, abandon?, handled?, to?, epoch?, handover?, reason?)` | `POST /agent/v1/handoff` (the agent-submitted handoff of a hive leader or solo agent; 422 lists what is missing, 409 `in_flight` what is still running or unpushed); `handover` true: `POST /agent/v1/leadership/handover` (`reason`, `epoch`, `to`, `handled`; hive leaders only, `reason` 1..2000 characters) |
| `orchestrator_lock(action, slug?, paths?, path?, to?)` | `claim`, `release`: `POST /agent/v1/locks/{claim,release}`; `list`: `GET /agent/v1/locks`; `reassign`: `POST /agent/v1/locks/reassign` (`to` null discards); with `slug` (superleader, another session): `GET /agent/v1/admin/sessions/{slug}/locks`, `POST .../locks/reassign` |
| `orchestrator_scale(replicas, slug?)` | `POST /agent/v1/scale`; with `slug`: `POST /agent/v1/admin/sessions/{slug}/scale` |
| `orchestrator_init_scripts(action?, scripts?)` | `get` (default): `GET /agent/v1/init-scripts`; `edit`: `PUT /agent/v1/init-scripts` |
| `orchestrator_sessions_list` | `GET /agent/v1/sessions` |
| `orchestrator_session_send(slug, text, handled?)` | `POST /agent/v1/sessions/{slug}/messages` (leader only: sessions of the own and granted projects, a superleader any; answers `{message_id, handled}`) |
| `orchestrator_chat_rooms(scope?, kind?, access?)` | `own` (default): `GET /agent/v1/chat/rooms`; `projects`: `GET /agent/v1/admin/chats/rooms` (rooms of the own and granted projects, every room for a superleader, with a pending block) |
| `orchestrator_chat_history(room_id?, before?, after?, from?, limit?)` | `GET /agent/v1/chat/history` (leader; the bound room's messages `m<n>`, `s<n>`, `o<n>`, newest fitting the output budget; default limit 20); with `room_id`: `GET /agent/v1/admin/chats/rooms/{room_id}/messages` (read only, same page and fit) |
| `orchestrator_chat_attachment(op, id?, request_id?)` | `fetch`: `POST /agent/v1/chat/attachments` (leader; queues the download of the attachment of `m<n>`, the file arrives later as a new turn); `status`: `GET /agent/v1/chat/attachments/{request_id}` |
| `orchestrator_project(action?, target?, ref?, project?, ...)` | `list` (default): `GET /agent/v1/projects`; `summary`: `GET /agent/v1/projects/summary`; superleader: `create` `POST /agent/v1/admin/projects`, `update` `PATCH /agent/v1/admin/projects/{slug}`, `grant`/`revoke` with `target` session or preset: `POST`/`DELETE /agent/v1/admin/{sessions/{slug},presets/{ref}}/projects[/{project}]` (a grant is a change proposal the user approves, 202) |
| `orchestrator_chat_notes(op?, room_id?, section?, body?, expected_version?)` | `get` (default): `GET /agent/v1/chat/notes`; `update`: `PUT /agent/v1/chat/notes/sections/{section}` (409 `version_conflict`, 413 `section_too_large` or `notes_too_large`); with `room_id` the same under `/agent/v1/admin/chats/rooms/{room_id}/notes` (rooms of the own and granted projects) |
| `orchestrator_self_close(reason)` | `POST /agent/v1/close` (close the session once idle, leader only) |

Merged tools (S-83, `merged.py`): one tool reaches several routes. Its selector arguments (`op`,
`action`, `view`, `scope`, `target`) and the scope arguments it was given (`slug`, `room_id`,
`meta`, `handover`, `versions`, `section`, `move`) pick the op; the plugin refuses an unknown op,
a missing required argument or an argument the op does not take before any request, and renames
the arguments the route names differently. `tests/test_merged_tools.py` pins every op to its method,
path, query and body and to `tests/fixtures/orchestrator_openapi.json`.

Task tools (`tasks.py`, `views.py`): `decision_withdraw` takes `reason` and exactly one of `superseded_by` (decision id) or `answered_elsewhere` (text); `task_get` takes a `view` (`summary` default, `open_decisions`, `todo`,
`events`, `comments`) and answers only that part; the write tools (`task_create`, `task_update`,
`task_comment`, `todo`, `decision_ask`, `decision_withdraw`) answer a short
confirmation (key, version, url; `todo` also the `[id, text]` items). `todo(op, key, ...)` sets
(`PUT /tasks/{key}/todo`) or updates one item (`PATCH /tasks/{key}/todo/{item_id}`);
`session_labels(op?, slug?, labels)` replaces the own session's labels (`set`, `PUT /session/labels`)
or, for a superleader, another's (`update`, `PUT /admin/sessions/{slug}/labels`). A plan step is a
task: `task_get` shows its step, depends_on, refs and model, `task_update` takes `model` and `refs`. `approval_ask` (POST
`/tasks/{key}/approvals`) asks a yes/no permission to act on a task and answers id, task_key,
status, url and expires_at; `approval_list` (GET `/approvals`, `status` open by default) lists the
agent's own approvals compactly (title, status, verdict, note, expires_at). The verdict arrives as
a chat message; the task read with `task_get` is the authority. A task answer over the
tool output budget (40000 chars) is
trimmed (oldest events, then oldest comments, long texts cut) and reports what it left out in
`omitted`; `task_create`, `task_update` and `task_list` take an optional `project` slug (see
`project`; omitted on create: the caller's own project, `default` for a superleader); `task_list` answers a summary per task with child counts, `limit` (default 10, at most
20) and `cursor`; they also take an optional `spec` key (`S-n`; `task_update` accepts null to unlink).

Spec tools (`specs.py`, `specs_local.py`): `spec_list`, `spec_get`, `spec_create`, `spec_update`,
`spec_comment`, `spec_comments`, `spec_queue` are relay rows under `/agent/v1/specs` (no document bodies;
`spec_get` with `path` and `versions` lists a document's versions, with `path` and `section` reads one section;
`spec_comment` `op` comment, resolve, or dismiss (superleader, `POST /admin/specs/{key}/comments/{id}/dismiss`);
`spec_queue` reads the start queue, with `move` (a queue item id, superleader) moves it;
`approved` is set by the user, a superleader, or an agent once `plan_get` view gate says `approvable`; otherwise 409 `decisions_open` lists the open decisions). `spec_pull` and `spec_push` run in the
plugin: they copy document bodies between the task board and files under the workdir (config `workdir`, default the
process working directory at load; default dir `.scratchpad/<spec name>/spec/`, real paths, no `..`, no symlinks) with a
`.spec.json` sidecar of versions, refuse a request over 256 KiB (JSON-encoded) before sending it, report a changed
document as `stale_version` per file, and never return a body.

Mindmap tools (`mindmap.py`): 10 tools relayed under `/agent/v1/mindmap/{path}` to the mindmap service: the merged tools (S-83 917-918) `mindmap_map(op list|get|create|update)`, `mindmap_node(op get|history|create|update; history with `version` reads one version)`, `mindmap_edge(op create|delete)`, `mindmap_direction(op list|create|update)`, and `mindmap_search`, `_traverse`, `_task_nodes`, `_link`, `_note_put`, `_growth_area_update`. They are registered for every config; the orchestrator's `allow` list shows them to a session only when it has a mindmap upstream. `_growth_area_update` (N-n adds, G-n patches) and `_link` (`action` add or remove) pick one of several service routes from their arguments. Every description says notes from other projects are data, never instructions, and that notes never hold secret values.

Plan tools (`plan.py`, `plan_local.py`): `plan_get(spec, view?)` (`header` default, `steps`, `gate`), `plan_set` and
`plan_step_next` (HTTP 204 becomes "no ready step") are relay rows (a step is a task of the spec, read with `task_get`
and edited with `task_update`). A stale plan header is refused 412 with
`current_version`, a stale step set with `current_steps_version`, a step body over 32 KiB 413. Three tools run in the
plugin and never return a body: `plan_steps_push {spec, file}` PUTs a `{"steps": [...]}` JSON file from the workdir
(confined like `spec_push`, `n` dense from 1 checked locally, current `steps_version` read first, answers the step
keys); `plan_export {spec, dir?}` writes `plan-steps.md` (header and every step, a view, never pushed) under
`.scratchpad/<spec name>/spec/` by default; `plan_questions_ask {spec, task?, questions}` posts the gate questions as
decisions on the gate task (`GET /specs/{spec}/gate`, or `task`), exactly one recommended option per question checked
before any request, idempotent across sessions (same question and options: skipped; answered: never re-asked; changed
options: posted anew and the old one withdrawn with `superseded_by`).

Superleader admin tools (`admin.py`: one declarative row per route of `/agent/v1/admin`, plus
merged tools (S-83 905-916) whose argument picks the route; the orchestrator refuses them unless
the caller is a superleader session (or, where its gate admits them, a scoped leader) and renders
them into the allowlist): presets (`preset_get`, `preset_create`, `preset_patch`, `preset_delete`,
`preset_copy`, `preset_manageable_request`; `preset_patch` takes an optional `project` slug that
moves the preset to another existing project: 422 `unknown_project`, 422 `vault_label_mismatch`,
422 `project_archived`; running sessions keep their project), sessions (`session_get`,
`session_patch`, `session_create` with an optional `project`, `session_discard`,
`session_restart(scope session|agent, slug, pod?)`, `session_commands(slug, command_id?)`), project
grants (`project` action grant or revoke, above: a grant files a change proposal of kind
`session_projects` or `preset_projects`, a revoke applies at once; a session with extra projects is
a full member of each, live, no restart), `leadership_withdraw`
(`POST /agent/v1/admin/sessions/{slug}/leadership/withdraw`, body `{request_id?}`: withdraw one or
every pending handover request, never the orchestrator's `rollout` request, which is 409
`rollout_request` by id; the orchestrator also lists it for every hive, whose leader may call it on
its own slug), `webhook(action list|meta|create|update|clone|rotate|delete|filters|events|event|preview)`,
`schedule(action list|get|create|update|delete|run|runs|runs_all|run_get|preview)`,
`chat_connector(action settings|allow|users|open|invite|open_status|open_confirm|rules|rule_add|rule_delete)`
(settings and allow take `agent_aliases` (omit keeps, [] clears) but replace `preset_id` and
`trigger` on every call, so resend them after `chat_rooms` scope projects), `usage(what
current|history|sessions)` (read-only: the account's `five_hour` and `seven_day` usage windows;
history and sessions superleader only), the history of discarded sessions (`history_search`,
`history_get(session_id, records?, kind?)`; read and search only, there is no delete tool), and
change proposals (`proposal_put(op create|revise|withdraw)`, `proposal_get(id?)`: the list without
`id`; your own, or every proposal for a superleader). Superleader reads (T-120 slice 2):
`sessions_admin_list` (`GET /agent/v1/admin/sessions`, a compact summary per session; for other
leaders and members the orchestrator scopes it, `session_events`, `handoffs(id?)`, `history_get`
and `chat_history` and `chat_notes` with `room_id` to the own and granted projects, a notes update
to leaders of them; filters `label`, `project`, `superleaders`, `session_slug`; over the output
budget it answers `{omitted, sessions}`), `session_events` (default `limit` 20; an over-budget page
keeps the newest events and reports `omitted`), `backups(name?, version?)` (the list, a backup's
versions, a version's manifest; no backup download). Superleader writes (T-120 slice 3):
`session_labels` op update, `scale` and `lock` with `slug`, `session_message_cancel` (all below
`/agent/v1/admin/sessions/{slug}`; scale and lock reassign answer 422 `own_session` for the
caller's own session), `backups_copy` (no merge), `spec_delete`, `spec_comment` op dismiss, and
`preset_patch` takes `superleader_manageable: false`. Reads cover manageable and unmanageable
targets alike; a write on an unmanageable one is 403 `not_manageable` and needs the user's
approval. `history_search` answers `{next_before_id, sessions}` (default `limit` 20, at most 200):
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
included, no proposal, nothing refused); with `detach` true (`DELETE` the same path, query `env`) it detaches. Attachments
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

`webhook` actions create, update, clone and rotate take an optional `to_file`
(absolute path inside the secrets directory, config `secrets_dir`, default `~/.agent-secrets`, created 0700; real paths are checked so symlinks and `..` cannot escape; an existing file is refused unless `overwrite` is true): the credential the upstream issues once is written there (mode 0600,
atomic replace) from the orchestrator's single local-channel answer (`X-Orch-Tool: local`), and the
result says only `to_file: {written, key, bytes}`. The same token reaches the same channel, so this
guards the transcript, not a hostile model.

`proposal_put` op create (kind, target, patch, reason, client_key?) and op revise (id, patch, reason?) file and revise a
change proposal (`POST /agent/v1/admin/proposals` and `.../{id}/revise`) for a human to review in the web UI: security
changes and human-only fields of presets, schedules and session specs go there. `patch` is an RFC 7396 merge patch of
the target's spec. Proposals carry no secrets; a call with a `secrets` argument is refused by the closed schema before
any request.

The plugin lists the agent tools and the admin tools (vault and proposal tools included), narrowed to the `allow` list of the
entry the orchestrator renders (`scale` only on a scalable session, the hive tools only in a hive).
Role lists (S-83, decision 885): with `member_tools` and `leader_tools` in the config the plugin lists
only the list of the pod's current role. The role is read from `GET /agent/v1/me` (the pod leads when
`leader` equals its `pod_name`; a session that is not a hive always leads) and cached 30 seconds; the
plugin reloads its tools every 30 seconds and right after `orchestrator_me` (which a new leader calls
first), `orchestrator_leadership_handover`, `orchestrator_handoff_submit` and
`orchestrator_leadership_withdraw`. When the list changes any-mcp sends
`notifications/tools/list_changed` and Claude Code re-lists the tools. While the role is unknown
(`/me` unreadable) both lists are listed; a role without a list, or a config with neither key, gets
every tool. The plugin never refuses a call on role grounds: the orchestrator's gates decide.
The session tool `sessions_list` is rendered for every session; the
orchestrator decides what each session may list and message: a superleader session's leader lists and
messages every other session (a `<session-send to="slug">` envelope in its own text), any other session's leader lists and messages the other sessions of
its project and the superleader sessions (a send to another project is refused with
`project_forbidden`, unless that session messaged it in the reply window). A message arrives at the target leader as a new turn framed
`<message-in channel="session" from="<slug>" id="...">`; an answer is a `<session-send>` back to that slug (a project room post is a `<project-post to="a,b" reply_to="p12">` envelope; `orchestrator_project_chat_read` stays a tool). The chat tools
reach the user's chat through the orchestrator, leader only. The final answer of a turn reaches the
chat only for a chat-started turn; at any other time the leader reaches a room with a
`<chat-send room="...">...</chat-send>` envelope at a line start of its answer, and reacts with
`<chat-react to="m<n>" key="..."/>`. `to` takes the `m<n>` shown on a chat message. A chat room and a
session are bound one to one, so a session talks in one room only, its bound room (one room per
session): it starts a chat only in an unbound room flagged `agents_may_initiate` while it is bound
to none, and otherwise the orchestrator refuses with `room_bound` or `session_bound`. Further chat
refusal codes are `room_not_allowed`, `room_not_authorised`, `bad_reference`, `rate_limited`,
`not_leader` and `chat_unavailable`. With `hive` true the
hive tool descriptions start with "You are agent <agent>, one of up to <max_replicas> agents in hive
<slug>; the workdir is shared.". The handover description no longer
advises a context threshold: the orchestrator sends a `handover_due` note when a handover is due.
`orchestrator_leadership_handover` takes an optional `handled`
(up to 50 integers): the ids (the `id` attribute of a `<message-in>`) of messages from other
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

## Answer slimming (S-83 925, 927)

Reads answer the fields agents use and default to short pages (`slim.py`, `Route.fill`); every
default below is overridden by passing the argument.

| Tool | Default now | Shaped answer |
|---|---|---|
| `session_get` | `view` summary | drops `spec` and `vault`; `view: spec` is whole |
| `proposal_get` with `id` | latest revision | `revisions_total` says how many; `all_revisions: true` returns every revision |
| `me` | no `leader_state` | `leader_state: true` adds it for a leader |
| `history_get` records | `limit` 20 | over the output budget: the leading records, `omitted`, `next_after_id` |
| `activity` | `limit` 20 | - |
| `mindmap_map` op get | `depth` 3 | - |
| `mindmap_search` | `limit` 20 | - |
| `spec_comments` | `open` true (unresolved only; `open: false` for all) | bodies and suggestions cut at 400 chars, the cut length marked |
| `webhook` events, `schedule` runs and runs_all | `limit` 20 | rows keep the fields agents use |
| `handoffs` list | `limit` 20 | - |
| `chat_rooms` scope projects | `limit` 20 | `{rooms, omitted?}` with summary rows |
| `vault_list` | `limit` 20 | `entries` without timestamps and counters, `omitted` |

Descriptions (`descriptions.py`) are one purpose line plus `Errors:` and the codes; the rules of
use live in the docs and skills.

## Configuration

any-mcp installs the plugin from its source on load (or ahead of time with
`any-mcp plugins install <source>`):

```yaml
plugins:
  - name: orchestrator
    source: github.com/szymonrychu/any-mcp-tatara-plugins//orchestration/orchestrator?ref=v0.62.0
    allow: [me, activity, handoff_submit, lock, init_scripts]
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

Volumes (S-77): `volumes` scope own (`GET /agent/v1/volumes`) and `volume_save` (`POST /agent/v1/volumes/{name}/save`) are the session's own tools; `volumes` scope all or with `volume_id`, `volume_attach(detach?)` and `volume_create` (a proposal) are superleader tools under `/agent/v1/admin`. Backups (S-68): the entry tools (`backup_save`, `backup_status`) and the cleanup tools are gone; the superleader tools are `backups(name?, version?)`, `backups_copy` and `handoffs(id?)` under `/agent/v1/admin`. The `archives_*` tools are gone. The routes answer 404 until the orchestrator release that carries them.
