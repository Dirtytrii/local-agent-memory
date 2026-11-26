# Multi-Client Setup Guide

All clients share **one** local read-only Streamable HTTP MCP endpoint (`http://127.0.0.1:8765/mcp`) and receive one-way synced static core blocks from `core/core-profile.md`.

---

## 1. Local Infrastructure (Ollama + `basic-memory`)

### 1.1 Pull Local Embedding Model
```bash
ollama pull bge-m3
```
Verify `bge-m3` is served locally on `http://127.0.0.1:11434` (1024-dim vectors).

### 1.2 Install Pinned `basic-memory==1.0.0` & Apply Idempotent Patch
```bash
uv tool install --python 3.14 basic-memory==1.0.0
python scripts/apply-zh-fts-patch.py --apply
python scripts/apply-zh-fts-patch.py --check
```
> All 4 checks must print `[OK]` (`ignore_utils.py`, `sqlite_search_query.py`, `mcp/server.py`, `mcp/tools/projects.py`).

### 1.3 Configure `~/.basic-memory` & Build Initial Index
1. Copy `templates/basic-memory.config.example.json` to `~/.basic-memory/config.json` and set `"path"` to your vault root.
2. Copy your vault's `.bmignore` to `~/.basic-memory/.bmignore` as a belt-and-suspenders mirror.
3. Run initial index + vector embedding build:
```bash
bm reindex --project agent-memory
bm reindex --embeddings --project agent-memory
```
4. Verify **G2**: `git status --porcelain` inside your vault must be completely clean.

### 1.4 Keep-Alive Background Service

- **Windows 11 (Verified)**:
  1. Copy `scripts/windows/run_mcp_server.py` to `%USERPROFILE%\.basic-memory\run_mcp_server.py`.
  2. Register and start via Windows Task Scheduler (current interactive user, hidden `pythonw.exe`, 1-min restart on failure, single-instance lock):
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/register-task-scheduler.ps1
  ```
- **macOS / Linux**:
  Run `BASIC_MEMORY_READ_ONLY=true HF_HUB_OFFLINE=1 PYTHONUTF8=1 NO_PROXY=127.0.0.1,localhost bm mcp --transport streamable-http --host 127.0.0.1 --port 8765` under a user-level `launchd` LaunchAgent or `systemd --user` unit.

---

## 2. Static Core Sync (`sync-static-core.ps1`)

1. Copy `scripts/sync-targets.example.json` to `scripts/sync-targets.local.json` (gitignored) and adjust target paths.
2. Always preview first (**dry-run is the default**):
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-static-core.ps1 -CorePath path\to\vault\core\core-profile.md -Target all
```
3. Apply once verified:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-static-core.ps1 -CorePath path\to\vault\core\core-profile.md -Target all -Apply
```

Safety features built into `sync-static-core.ps1`:
- Creates a timestamped `.bak` backup on first injection into an existing file;
- Preserves Antigravity YAML lines 1–3 (`---\ntrigger: always_on\n---`);
- Preserves existing user rules outside the `<!-- AGENTMEMORY:CORE:BEGIN ... END -->` block;
- Verifies SHA-256 of existing managed blocks and aborts with a line diff on local drift (requires `-Force` to overwrite);
- Hard-blocks writes to `USER.md` and `MEMORY.md`;
- Always writes UTF-8 (no BOM) with LF line endings.

---

## 3. Client-Specific MCP Wiring

### Antigravity
Add `templates/antigravity-mcp_config.example.json` as your plugin's `mcp_config.json` (e.g., `~/.gemini/config/plugins/personal-memory/mcp_config.json`) or global `~/.gemini/config/mcp_config.json`:
```json
{
  "mcpServers": {
    "agent-memory": {
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```
Sync `core-profile.md` with strategy `antigravity_frontmatter_replace`.

### Codex
Append `templates/codex-config.example.toml` to `~/.codex/config.toml`:
```toml
[mcp_servers.agent-memory]
url = "http://127.0.0.1:8765/mcp"
```
Verify with `codex mcp list`. Sync `~/.codex/AGENTS.md` with strategy `preserve_outside_append` so existing Git/project instructions stay intact. Leave Codex's native memory enabled as a local scratchpad.

### Hermes (Multi-Profile Staged Rollout)
1. **MCP-enabled profiles** (e.g., general assistant, coding, research): append `templates/hermes-config.example.yaml` to that profile's `config.yaml` and sync `core-lite` (`<= 2 KB`, `renderTarget: "agent-mcp"`) into `SOUL.md`. Never modify `USER.md` or `MEMORY.md`.
2. **Non-MCP profiles** (e.g., high-noise public social/media profiles or local small-context models): do **not** add `mcp_servers`; sync `core-lite` with `renderTarget: "agent-nomcp"` so the profile reads only its allowed domain `_index.md` directly.
3. Roll out one profile at a time, running G4 red-team checks in each profile before onboarding the next.
