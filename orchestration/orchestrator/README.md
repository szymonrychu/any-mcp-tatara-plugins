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

The plugin always lists all sixteen tools. Which an agent sees is decided by the `allow` list of the
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
    source: github.com/szymonrychu/any-mcp-tatara-plugins//orchestration/orchestrator?ref=v0.2.0
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
