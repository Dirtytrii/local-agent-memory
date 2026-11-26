#!/usr/bin/env python3
"""Cross-platform managed-block renderer & synchronizer for local-agent-memory core-profile.md.

Behavioral & SHA-256 parity with `scripts/sync-static-core.ps1`:
- Default mode is DRY-RUN (pass `--apply` to write files).
- Enforces UTF-8 without BOM and LF line endings on all written files.
- Protects YAML frontmatter lines 1-3 (`trigger: always_on`) for Antigravity-style rules.
- Creates a timestamped `.bak` backup on first injection into an existing file.
- Verifies SHA-256 of existing managed blocks; aborts with line diff on drift unless `--force` is passed.
- Supports per-target conditional blocks (`<!-- target: ... -->`) and `core-lite` extraction.
- Refuses to modify `USER.md` or `MEMORY.md` (preserves native agent scratchpads).

Exit codes:
  0 - Success (dry-run preview complete or all targets applied/unchanged)
  1 - Validation or blocked-target error
  2 - Managed-block SHA-256 drift detected without `--force`
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

BLOCK_RE = re.compile(
    r"<!-- AGENTMEMORY:CORE:BEGIN\s+src=([^\s]+)\s+target=([^\s]+)\s+sha256=([0-9a-fA-F]+)\s*-->\n(.*?)\n<!-- AGENTMEMORY:CORE:END -->",
    re.DOTALL,
)
LITE_RE = re.compile(r"<!-- core-lite:begin -->\n(.*?)<!-- core-lite:end -->", re.DOTALL)
LITE_MARKER_RE = re.compile(r"<!-- core-lite:(?:begin|end) -->\n?")
TARGET_BLOCK_RE = re.compile(r"<!-- target: ([^>]*?) -->\n(.*?)<!-- /target -->\n?", re.DOTALL)
FRONTMATTER_RE = re.compile(r"^---\n[\s\S]*?\n---")
WIN_ENV_RE = re.compile(r"%([A-Za-z_][A-Za-z0-9_]*)%")


def normalize_lf(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_source_git_sha(file_path: Path) -> str:
    parent = str(file_path.parent)
    try:
        proc = subprocess.run(
            ["git", "-C", parent, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        sha = proc.stdout.strip()
        if proc.returncode != 0 or not sha:
            return "uncommitted"
        dirty = subprocess.run(
            ["git", "-C", parent, "status", "--porcelain", "--", str(file_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if dirty.stdout.strip():
            return f"{sha}-dirty"
        return sha
    except OSError:
        return "uncommitted"


def expand_target_path(raw_path: str) -> Path:
    """Expand ~, $VAR/${VAR}, and %VAR% across Windows, macOS, and Linux."""
    home = Path.home()

    def _win_env_repl(match: re.Match[str]) -> str:
        var = match.group(1)
        val = os.environ.get(var)
        if val:
            return val
        upper = var.upper()
        if upper in ("USERPROFILE", "HOME"):
            return str(home)
        if upper == "LOCALAPPDATA":
            if sys.platform == "darwin":
                return str(home / "Library" / "Application Support")
            return os.environ.get("XDG_DATA_HOME", str(home / ".local" / "share"))
        if upper == "APPDATA":
            if sys.platform == "darwin":
                return str(home / "Library" / "Application Support")
            return os.environ.get("XDG_CONFIG_HOME", str(home / ".config"))
        return match.group(0)

    expanded = WIN_ENV_RE.sub(_win_env_repl, raw_path)
    expanded = os.path.expandvars(expanded)
    if os.name != "nt":
        expanded = expanded.replace("\\", "/")
    expanded = os.path.expanduser(expanded)
    return Path(expanded)


def render_core_content(raw_core: str, render_target: str, mode: str) -> str:
    if mode not in ("full", "lite"):
        raise ValueError(f"Unsupported mode {mode!r}; expected 'full' or 'lite'.")

    text = normalize_lf(raw_core)
    if mode == "lite":
        m = LITE_RE.search(text)
        if not m:
            raise ValueError(
                "core-profile.md is missing <!-- core-lite:begin --> / <!-- core-lite:end --> markers."
            )
        text = m.group(1)
    else:
        text = LITE_MARKER_RE.sub("", text)

    def _target_repl(match: re.Match[str]) -> str:
        targets = [item.strip() for item in match.group(1).split(",")]
        if render_target in targets or "all" in targets:
            return match.group(2)
        return ""

    rendered = TARGET_BLOCK_RE.sub(_target_repl, text)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered)
    return rendered.strip()


def show_line_diff(expected: str, actual: str) -> None:
    exp_lines = normalize_lf(expected).split("\n")
    act_lines = normalize_lf(actual).split("\n")
    max_len = max(len(exp_lines), len(act_lines))
    for idx in range(max_len):
        e = exp_lines[idx] if idx < len(exp_lines) else None
        a = act_lines[idx] if idx < len(act_lines) else None
        if e != a:
            if a is not None:
                print(f"  - [local]    L{idx + 1}: {a}")
            if e is not None:
                print(f"  + [rendered] L{idx + 1}: {e}")


def default_targets() -> list[dict[str, str]]:
    home = Path.home()
    return [
        {
            "name": "antigravity",
            "group": "antigravity",
            "renderTarget": "antigravity",
            "mode": "full",
            "strategy": "antigravity_frontmatter_replace",
            "path": str(home / ".gemini" / "config" / "plugins" / "personal-memory" / "rules" / "core-profile.md"),
        },
        {
            "name": "codex",
            "group": "codex",
            "renderTarget": "codex",
            "mode": "full",
            "strategy": "preserve_outside_append",
            "path": str(home / ".codex" / "AGENTS.md"),
        },
    ]


def load_targets(script_dir: Path, manifest_path: str) -> list[dict[str, Any]]:
    local_manifest = script_dir / "sync-targets.local.json"
    chosen: Path | None = None
    if manifest_path:
        chosen = Path(manifest_path)
    elif local_manifest.exists():
        chosen = local_manifest

    if chosen is None:
        return default_targets()

    raw = chosen.read_bytes().decode("utf-8-sig")
    data = json.loads(raw)
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise ValueError(f"Target manifest {chosen} must contain a JSON array of target objects.")
    return data


def read_utf8_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-platform managed-block static core synchronizer for local-agent-memory."
    )
    parser.add_argument("--target", default="all", help="Target name, group, renderTarget, or 'all'")
    parser.add_argument("--apply", action="store_true", help="Write changes to disk (default is dry-run)")
    parser.add_argument("--force", action="store_true", help="Overwrite drifted managed blocks")
    parser.add_argument("--core-path", default="", help="Path to source core/core-profile.md")
    parser.add_argument("--manifest-path", default="", help="Path to target manifest JSON")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    core_path = (
        Path(args.core_path).resolve()
        if args.core_path
        else (script_dir.parent / "core" / "core-profile.md").resolve()
    )
    if not core_path.exists():
        print(f"[ERROR] Core profile not found: {core_path}", file=sys.stderr)
        return 1

    raw_core = read_utf8_text(core_path)
    src_sha = get_source_git_sha(core_path)
    target_list = load_targets(script_dir, args.manifest_path)

    selected = [
        t
        for t in target_list
        if args.target == "all"
        or t.get("group") == args.target
        or t.get("renderTarget") == args.target
        or t.get("name") == args.target
    ]

    mode_banner = "APPLY" if args.apply else "DRY-RUN (pass --apply to write)"
    print(f"=== local-agent-memory Static Core Sync [{mode_banner}] ===")
    print(f"Source: {core_path} (git={src_sha})")

    if not selected:
        print(
            f"[WARN] No targets matched filter '{args.target}'. Copy scripts/sync-targets.example.json "
            "(or sync-targets.posix.example.json) to scripts/sync-targets.local.json to register custom targets."
        )
        return 0

    drift_count = 0
    error_count = 0

    for t in selected:
        t_name = str(t["name"])
        t_path = expand_target_path(str(t["path"]))
        t_render = str(t["renderTarget"])
        t_mode = str(t["mode"])
        t_strategy = str(t["strategy"])

        if t_path.name in ("USER.md", "MEMORY.md"):
            print(
                f"[BLOCKED] {t_name}: Refusing to modify {t_path} "
                "(native agent scratchpads must not be overwritten)."
            )
            error_count += 1
            continue

        try:
            rendered_body = render_core_content(raw_core, t_render, t_mode)
        except ValueError as exc:
            print(f"[ERROR] {t_name}: {exc}")
            error_count += 1
            continue

        new_hash = sha256_hex(rendered_body)
        new_hash_short = new_hash[:16]
        rendered_bytes = len(rendered_body.encode("utf-8"))
        managed_block = (
            f"<!-- AGENTMEMORY:CORE:BEGIN src=core-profile.md@{src_sha} "
            f"target={t_render} sha256={new_hash_short} -->\n"
            f"{rendered_body}\n"
            f"<!-- AGENTMEMORY:CORE:END -->"
        )

        exists = t_path.exists()
        if not exists:
            if t_strategy == "preserve_outside_append" and t_mode == "lite":
                print(f"[SKIP] {t_name}: target not found ({t_path})")
                continue
            existing_text = ""
        else:
            existing_text = normalize_lf(read_utf8_text(t_path))

        has_block = False
        m = BLOCK_RE.search(existing_text)
        if m:
            has_block = True
            recorded_target = m.group(2)
            recorded_hash = m.group(3).lower()
            current_inner = m.group(4)
            actual_full_hash = sha256_hex(current_inner)
            actual_hash = actual_full_hash[:16] if len(recorded_hash) == 16 else actual_full_hash

            if actual_hash != recorded_hash:
                print(f"[DRIFT] {t_name} ({t_path}): recorded={recorded_hash}, actual={actual_hash}")
                show_line_diff(rendered_body, current_inner)
                if not args.force:
                    print(f"  -> Aborted write for {t_name}. Re-run with --force to overwrite local drift.")
                    drift_count += 1
                    continue
                print("  -> --force specified; proceeding to overwrite drifted managed block.")
            elif actual_hash == new_hash_short and recorded_target == t_render:
                print(
                    f"[UNCHANGED] {t_name} ({t_path}) | "
                    f"target={t_render} mode={t_mode} bytes={rendered_bytes} sha256={new_hash_short}"
                )
                continue

            final_text = existing_text[: m.start()] + managed_block + existing_text[m.end() :]
        else:
            if t_strategy == "antigravity_frontmatter_replace":
                fm = "---\ntrigger: always_on\n---"
                fm_match = FRONTMATTER_RE.match(existing_text)
                if fm_match:
                    fm = fm_match.group(0)
                final_text = f"{fm}\n\n{managed_block}\n"
            else:
                if not existing_text.strip():
                    final_text = f"{managed_block}\n"
                else:
                    trimmed = existing_text.rstrip("\n")
                    final_text = f"{trimmed}\n\n{managed_block}\n"

        final_text = normalize_lf(final_text).rstrip("\n") + "\n"

        if t_strategy == "antigravity_frontmatter_replace":
            top_lines = final_text.split("\n")
            if (
                len(top_lines) < 3
                or top_lines[0] != "---"
                or top_lines[1] != "trigger: always_on"
                or top_lines[2] != "---"
            ):
                print(f"[ERROR] {t_name}: Antigravity lines 1-3 frontmatter guard failed!")
                error_count += 1
                continue

        action_tag = "UPDATE" if has_block else "INJECT (first-run)"
        print(
            f"[{action_tag}] {t_name} -> {t_path} | "
            f"target={t_render} mode={t_mode} bytes={rendered_bytes} sha256={new_hash_short}"
        )

        if args.apply:
            t_path.parent.mkdir(parents=True, exist_ok=True)
            if exists and not has_block:
                ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                bak_path = Path(f"{t_path}.{ts}.bak")
                shutil.copy2(t_path, bak_path)
                print(f"  -> Backup created: {bak_path}")
            t_path.write_bytes(final_text.encode("utf-8"))
            print("  -> Written UTF-8 (no BOM) + LF.")
        else:
            if exists and not has_block:
                print("  -> [dry-run] would create timestamped .bak backup before first-run injection")
            print(
                f"  -> [dry-run] final file length: {len(final_text.encode('utf-8'))} bytes (UTF-8 no BOM, LF)"
            )

    if error_count > 0:
        return 1
    if drift_count > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
