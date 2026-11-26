# Architecture & Governance Contracts

`local-agent-memory` separates **truth**, **index**, **distribution**, and **mutation** so multiple AI clients can share long-term personal memory without silent corruption, cross-domain leakage, or vendor lock-in.

---

## 1. The Four Memory Layers

| Layer | Location | Indexed by MCP? | Mutation Policy | Purpose |
| --- | --- | --- | --- | --- |
| **1. Static Core** | `core/core-profile.md` | **No** (`.bmignore`) | Human-edited; synced one-way into client configs via managed blocks | Always-on identity, security/privacy/auth redlines, conflict resolution header, memory routing rules |
| **2. Dynamic Atomic Cards** | `domains/<domain>/*.md` | **Yes** (FTS5 + `sqlite-vec`) | Human-approved merges only; agents **never** write here directly | Compact (1–3 KB) durable domain rules with explicit TTL (`L0`/`L1`/`L2`) |
| **3. Episodic Flow** | `episodes/YYYY-MM/*.md` | **Yes** | Append-only troubleshooting/experiment logs (`BUILT`/`MEASURED`/`LIVED`) | Searchable troubleshooting memory & writing source material; **never treated as permanent rules** |
| **4. Staging & Archive** | `pending/`, `archive/`, `../Restricted/` | **No** (`.bmignore` or physically outside root) | Agents append YAML patches to `pending/<agent>/YYYY-MM-DD.md` via file tools | Human review gate (`pending/`), vector-purged retirement (`archive/`), zero-index isolation (`Restricted/`) |

---

## 2. Frontmatter & Mandatory `permalink` Prefix Encoding

Many MCP search summaries omit custom YAML frontmatter fields. To prevent an agent from mistaking a one-off log for a permanent rule—or pulling a private card into an unrelated task—every indexed document encodes its domain and kind directly into `permalink` and its file path:

- **Durable rule cards (`kind: card`)**: `permalink` MUST start with `<domain>-` (e.g., `engineering-prod-service-boundary`, `writing-style-and-sources`).
- **One-off episodic logs (`kind: episode`)**: `permalink` MUST start with `episode-YYYYMMDD-` (e.g., `episode-20261123-local-mcp-fts-tuning`).

```yaml
---
title: Production Service Boundary (生产服务边界)
type: note
permalink: engineering-prod-service-boundary
kind: card                 # card | episode | index
domain: engineering
sensitivity: normal        # normal | private | restricted
status: active             # active | stale | archived | superseded
ttl_tier: L1               # L0 | L1 | L2 (cards only; episodes omit ttl_tier)
updated_at: 2026-11-26
verified_at: 2026-11-26
aliases: [prod boundary, read-only]
tags: [engineering, production]
---
```

---

## 3. Three-Tier TTL & Physical Archival

| Tier | Applies To | Review Window | Expiry Action |
| --- | --- | --- | --- |
| **L0** | Core identity, hard security/privacy redlines | `never` | Explicit manual edit only |
| **L1** | Methodologies, engineering/writing/risk rules | `180 days` | Semi-annual freshness check |
| **L2** | Mutable environment/project/account facts | `60–90 days` | Mark `stale` past 90d; verify before citing |

- **Physical Archival Rule**: When a card becomes `archived` or `superseded`, you MUST physically move the file into `archive/` and run `bm reindex` so both FTS5 and `sqlite-vec` entries are purged. Leaving `status: archived` inside `domains/` causes vector false-positives.
- **Episode Lifecycle Decoupling**: `publish_status: raw | drafted | published | skip` tracks editorial conversion only. Even after `published` or `skip`, keep an episode `status: active` while it still holds troubleshooting value; archive it only when obsolete or past 30 days with no retention value.

---

## 4. Retrieval Discipline & Two-Tier Fallback

1. **Read-Only MCP Surface**: `scripts/apply-zh-fts-patch.py` hides `bm_write`, `bm_edit`, `bm_move`, `bm_delete`, and `bm_project_delete` via FastMCP `Visibility(False)` and refuses `bm_project(command="create")` by default (`BASIC_MEMORY_READ_ONLY=true`).
2. **Two-Tier Fallback**:
   - **Tier 1 (Ollama offline / slow)**: If `bm_search(mode="hybrid")` errors, immediately retry `bm_search(mode="text")`. With the CJK Bigram + BM25 relaxation patch, pure keyword search maintains high recall even without vector embeddings.
   - **Tier 2 (MCP server down or empty result)**: Read `domains/<domain>/_index.md` directly via local file tools (`_index.md` is excluded by `.bmignore` so it never crowds Top-3 results).
   - **Anti-Hallucination**: If neither search nor `_index.md` contains an answer, explicitly reply "No relevant memory".
3. **Cross-Domain & Public-Publishing Redline**:
   - Ignore cross-domain false hits (e.g., private routine/health cards appearing during engineering or writing tasks).
   - Any public writing, social post, or external deliverable MUST NEVER cite `sensitivity: private` or `restricted` cards.

---

## 5. B2 Staging (`pending/`) & Noise Control

Agents never mutate `domains/`. Instead, they append YAML patch proposals to `pending/<agent>/YYYY-MM-DD.md` using local file tools (one file per agent per day avoids concurrent write locks):

- **4 Write Gates (at least one required)**:
  1. User corrects reusable agent behavior;
  2. User explicitly states a durable preference;
  3. A fact change affects an existing `L2` card;
  4. `>= 30 min` hands-on troubleshooting produced reusable commands/config (`episode` candidate).
- **Hard Bans**: Unattended/cron sessions, `restricted` data, and one-off trivia are strictly forbidden from writing `pending/`.
- **Quotas & Deduplication**: Check existing `domains/` and `pending/` first; increment `hits` on matching `dedupe_key` (`<target_permalink>#<topic_slug>`). Max 1 entry per session, 3 per agent per day.
- **Weekly Review**: Run `python scripts/audit-freshness.py --write-digest` to generate `pending/_digest.md`. Unreviewed drafts warn at 14 days and move to `pending/_expired/` at 30 days (`--expire-pending`). If approval rate stays `< 30%` for 2 weeks or weekly volume exceeds 15 entries, tighten gates.
- **Git Exception**: Routine writes to `pending/` and `episodes/` never auto-commit, and unrelated commits MUST NEVER stage `pending/`.
