---
title: Local MCP & CJK FTS Tuning Runbook (Episode)
type: note
permalink: episode-20261123-local-mcp-fts-tuning
kind: episode
domain: engineering
sensitivity: normal
status: active
publish_status: raw
source_type: MEASURED
created_at: 2026-11-26
updated_at: 2026-11-26
aliases: [MCP tuning, FTS bigram, bge-m3]
tags: [episode, engineering, mcp, fts]
---

# Local MCP & CJK FTS Tuning Runbook

## 1. Problem
Verify local `basic-memory==1.0.0` paired with local Ollama `bge-m3` (1024-dim) and fix natural-language CJK keyword search recall.

## 2. Root Cause & Investigation
- Upstream `ignore_utils.py` only reads `<config_dir>/.bmignore`, ignoring project-root `.bmignore`.
- Upstream `script_ngrams` treats spaceless CJK natural-language questions as contiguous exact phrases, yielding near-zero pure-FTS recall.

## 3. Verified Commands
- `uv tool install --python 3.14 basic-memory==1.0.0`
- `python scripts/apply-zh-fts-patch.py --apply`

## 4. Verification & Boundaries
- Pure FTS Top-1 recall jumped from 5% to 100% on a 20-query frozen CJK benchmark while keeping 5/5 negative-sample rejection.
- This is a one-off troubleshooting log, not a permanent engineering rule.

## 5. Potential Writing Angles
- Why vector search alone does not excuse broken keyword FTS in local-first agent memory.

Keywords: episode mcp fts bigram ollama bge-m3 排障 实操记录
