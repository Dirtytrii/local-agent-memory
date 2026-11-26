# Demo AgentMemory Vault (Fictional Example)

This is a **100% fictional** demo vault for `local-agent-memory` so you can immediately run
`audit-freshness.py`, `sync-static-core.py`, and `sync-static-core.ps1` without exposing private notes.

- **Derived cache rule**: SQLite FTS5 + `sqlite-vec` (`~/.basic-memory/memory.db`) is a disposable derived cache. Markdown + Git in this directory is the single source of truth.
- **Physical isolation**: `../demo-restricted/` lives outside this MCP index root and is never indexed.
- **Git write exception**: Daily appends to `pending/<agent>/YYYY-MM-DD.md` and `episodes/` are never auto-committed; never mix `pending/` into unrelated commits.
