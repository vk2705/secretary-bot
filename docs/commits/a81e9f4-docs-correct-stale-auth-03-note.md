# a81e9f4 — docs: correct stale AUTH-03 note

**Date:** 2026-08-27
**Files:** `CLAUDE.md`

## What changed

`CLAUDE.md`'s `mcp_server.py` section had a **"Not yet done"** note claiming
tool calls still took a caller-supplied `chat_id` on the remote MCP
transport, with nothing stopping a request from naming a different user's
`chat_id`. That note was written mid-session, before AUTH-03 was actually
implemented later the same session.

Left as-is, it read as an open cross-user data leak that no longer exists —
the exact bug it describes was already fixed (transport-scoped tool
registration, `chat_id` resolved from the Bearer token via
`_authed_chat_id()`, never from a model-supplied argument).

## Why

Verified against current code before editing (`_IS_REMOTE`,
`_authed_chat_id()`, and the 17 remote tool wrappers in `mcp_server.py` all
confirmed present) rather than trusting the note's own claim — per the
project's "verify completeness claims" standard, a stale doc claim isn't
corrected by memory of having fixed it, only by re-checking the code.

## Result

The note now describes AUTH-03 as implemented: `chat_id` dropped from the
remote schema entirely, resolved server-side from the auth token's
`.subject`, `list_users()` and `bot://users*` resources stdio-only.
