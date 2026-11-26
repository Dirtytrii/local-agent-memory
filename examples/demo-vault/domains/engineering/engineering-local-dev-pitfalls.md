---
title: Local Windows Dev & PowerShell Pitfalls (本地开发与脚本避坑)
type: note
permalink: engineering-local-dev-pitfalls
kind: card
domain: engineering
sensitivity: normal
status: active
ttl_tier: L2
updated_at: 2026-11-26
verified_at: 2026-11-26
aliases: [PowerShell, UTF-8, CRLF, NO_PROXY, 编码坑]
tags: [engineering, windows, powershell, networking]
---

# Local Windows Dev & PowerShell Pitfalls

- **UTF-8 & LF**: Avoid PowerShell 5.1 `Out-File` / `Set-Content` (adds UTF-8 BOM or CRLF). Use `[System.IO.File]::WriteAllText($path, $text, [System.Text.UTF8Encoding]::new($false))` after normalizing `\r\n` to `\n`.
- **Loopback Proxy Bypass**: Always set `NO_PROXY=127.0.0.1,localhost` when calling local Ollama (`11434`) or local MCP HTTP (`8765`) endpoints.
- **Variable Interpolation**: Use `${file}:` instead of `$file:` in PowerShell double-quoted strings.

Keywords: PowerShell UTF-8 BOM CRLF LF NO_PROXY localhost 编码 避坑 脚本
