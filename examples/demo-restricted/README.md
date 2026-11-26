# Demo Restricted Directory (Outside MCP Index Root)

This directory sits **physically outside** `examples/demo-vault/` (the MCP index root).
- It is never indexed into SQLite FTS5 or `sqlite-vec`.
- Keep highly sensitive records here only when strictly necessary, accessible exclusively via explicit file path reads by authorized profiles.
