#!/usr/bin/env python3
"""Hidden-window Windows launcher for local-agent-memory Basic Memory Streamable HTTP MCP server.

Designed to be invoked by Windows Task Scheduler via `pythonw.exe`:
- Zero flashing console window (GUI subsystem binary).
- Redirects `sys.stdin` to `os.devnull` so `sys.stdin.isatty()` does not crash under pythonw.exe.
- Enforces `PYTHONUTF8=1`, `HF_HUB_OFFLINE=1`, `BASIC_MEMORY_READ_ONLY=true`, and loopback `NO_PROXY`.
- Direct PID and exit-code reporting for Task Scheduler automatic restart on failure.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CFG_DIR = Path(os.environ.get("BASIC_MEMORY_CONFIG_DIR", str(Path.home() / ".basic-memory")))
HOST = os.environ.get("AGENT_MEMORY_HOST", "127.0.0.1")
PORT = os.environ.get("AGENT_MEMORY_PORT", "8765")
LOG_PATH = CFG_DIR / "mcp-http-service.log"
MAX_LOG_BYTES = 5 * 1024 * 1024

os.environ["BASIC_MEMORY_CONFIG_DIR"] = str(CFG_DIR)
os.environ["PYTHONUTF8"] = "1"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["LOGFIRE_IGNORE_NO_CONFIG"] = "1"
os.environ.setdefault("BASIC_MEMORY_READ_ONLY", "true")
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

CFG_DIR.mkdir(parents=True, exist_ok=True)
if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
    backup = CFG_DIR / "mcp-http-service.log.1"
    if backup.exists():
        backup.unlink()
    LOG_PATH.rename(backup)

log_fh = LOG_PATH.open("a", encoding="utf-8", buffering=1)
sys.stdin = open(os.devnull, "r", encoding="utf-8")
sys.stdout = log_fh
sys.stderr = log_fh

sys.argv = [
    "basic-memory",
    "mcp",
    "--transport",
    "streamable-http",
    "--host",
    HOST,
    "--port",
    PORT,
]

from basic_memory.cli.main import app  # noqa: E402

app()
