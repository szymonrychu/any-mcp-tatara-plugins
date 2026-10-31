# any-mcp-tatara-plugins memory

Lessons that outlive a session. Newest first.

- 2026-10-31: non-declarative (local) tools in any-mcp: a `ToolSpec` with `handler=async (ToolCall) -> value` instead of `http=`; the `Plugin.call` hook runs the handler, so the arguments stay in-process. `orchestration/orchestrator/.../secrets_local.py` builds `secret_set` this way (`local_secret_tool`); `Plugin.on_error` hides exception text, so the plugin's `on_error` must route `SecretResolveError` to `secret_error_result` (PL2 wires both into `load_tools`). Errors carry fixed class names only, never value, path or command output.
