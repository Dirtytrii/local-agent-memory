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
  3. After rebooting (or anytime you want a 10-second read-only health check), run the G1/G8 & security verifier (bypasses system HTTP proxy, checks Task Scheduler, loopback `:8765`/`:11434`, `bge-m3` 1024-dim vector & GGUF digest, `Host 421`/`Origin 403`, read-only tool surface, and `text`/`hybrid` latency):
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/verify-reboot-g1-g8.ps1
  # Optional strict G8 digest pin:
  # powershell -NoProfile -ExecutionPolicy Bypass -File scripts/windows/verify-reboot-g1-g8.ps1 -ExpectedGgufDigest "sha256:<your-bge-m3-gguf-digest>"
  ```
  > Tip: Set `OLLAMA_KEEP_ALIVE=30m` (and `OLLAMA_MODELS` if using a custom model directory) in User environment variables to avoid repeated 5-minute cold reloads on laptops without permanently locking RAM/VRAM.
- **macOS / Linux**:
  Use the included keep-alive templates in [`templates/posix/`](../templates/posix/) (`agent-memory-mcp.service` for `systemd --user` on Linux, or `com.local-agent-memory.mcp.plist` for `launchd` on macOS) to run `bm mcp --transport streamable-http --host 127.0.0.1 --port 8765` with `BASIC_MEMORY_READ_ONLY=true`, `HF_HUB_OFFLINE=1`, `PYTHONUTF8=1`, and `NO_PROXY=127.0.0.1,localhost`.

---

## 2. Static Core Sync (`sync-static-core.py` / `sync-static-core.ps1`)

Both synchronizers share **identical** rendering rules, exit codes, and 16-char SHA-256 managed-block hashes:

1. Copy `scripts/sync-targets.posix.example.json` (macOS/Linux) or `scripts/sync-targets.example.json` (Windows) to `scripts/sync-targets.local.json` (gitignored) and adjust target paths.
2. Always preview first (**dry-run is the default**):
```bash
# Cross-platform Python 3 (macOS / Linux / Windows)
python scripts/sync-static-core.py --core-path path/to/vault/core/core-profile.md --target all
```
```powershell
# PowerShell alternative on Windows
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-static-core.ps1 -CorePath path\to\vault\core\core-profile.md -Target all
```
3. Apply once verified:
```bash
python scripts/sync-static-core.py --core-path path/to/vault/core/core-profile.md --target all --apply
```
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/sync-static-core.ps1 -CorePath path\to\vault\core\core-profile.md -Target all -Apply
```

Safety features built into both `sync-static-core.py` and `sync-static-core.ps1`:
- Creates a timestamped `.bak` backup on first injection into an existing file;
- Preserves Antigravity YAML lines 1–3 (`---\ntrigger: always_on\n---`);
- Preserves existing user rules outside the `<!-- AGENTMEMORY:CORE:BEGIN ... END -->` block;
- Verifies SHA-256 of existing managed blocks and aborts with a line diff on local drift (requires `--force` / `-Force` to overwrite);
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
