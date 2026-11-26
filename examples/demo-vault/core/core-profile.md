<!-- core-lite:begin -->
> **Conflict Resolution**: Security, privacy, and auth redlines here win; tone/persona follow the host client; if facts conflict with local `USER.md`/`MEMORY.md`, this block wins and you must notify the user.

# Alex Chen — Core Profile & Redlines (Demo)

- **Identity**: `Alex`; backend engineer (Go/Java); `Asia/Shanghai`; Windows 11.
- **Communication**: Direct, engineering-first, no fluff. Distinguish **Fact / Verified / Inferred / Hypothesis / Unknown**; verify volatile external facts unless told offline-only.
- **Authorization**: Read-only first; **code edits != `git push`/deploy**; public posts & external writes require per-action confirmation. `order-gateway` is live in prod (read-only default).
- **Privacy**: **Never cite `personal-*` or `private`/`restricted` memories in public posts or external deliverables.**
- **Git**: Small descriptive commits; stage only task files. Writes to `pending/`/`episodes/` never auto-commit; never stage `pending/` in other commits.

## Memory Routing & Discipline

<!-- target: default,agent-mcp -->
- **Retrieval**: Use `bm_search(query, project="agent-memory")`, `bm_read`, `bm_context`; **never call MCP write tools**.
<!-- /target -->
<!-- target: agent-nomcp -->
- **Retrieval**: No MCP. Read `domains/writing/_index.md` & cards directly; never read `personal/` or `demo-restricted/`.
<!-- /target -->
- **Prefixes**: `<domain>-*` = durable rule; `episode-YYYYMMDD-*` = one-off log (**never a permanent rule**). Call `bm_read` if status/TTL is unclear; ignore cross-domain false hits.
<!-- target: default,agent-mcp -->
- **Fallback**: If `hybrid` errors, retry `mode="text"`; if MCP is down/empty, read `domains/<domain>/_index.md`; if absent, say "No relevant memory".
<!-- /target -->
<!-- target: agent-nomcp -->
- **Fallback**: If no record exists, explicitly say "No relevant memory" — never fabricate.
<!-- /target -->
- **Staging**: Never edit `domains/`. Append YAML patches to `pending/<agent>/YYYY-MM-DD.md` only on the 4 gates (correction / durable preference / L2 change / >=30m reusable log). Max 1/session, 3/day; banned in cron.
<!-- core-lite:end -->

<!-- target: default,agent-mcp -->
## Local Workspace Map (Demo)

- `C:\workspace\demo-vault`: Canonical Markdown + Git memory root (`../demo-restricted` is physically outside MCP indexing).
- `C:\workspace\order-gateway`: Production Go/Java gateway (read-only diagnostics by default).
- `C:\workspace\blog-drafts`: Technical writing workspace.
<!-- /target -->
