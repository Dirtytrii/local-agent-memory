#!/usr/bin/env python3
"""Repository hygiene, encoding (UTF-8 no BOM + LF), and path/secret leak gate.

Checks all Git-tracked files (plus untracked non-ignored files) in the repository:
1. Valid UTF-8 without BOM (`\\xef\\xbb\\xbf`) and strictly LF (`\\n`) line endings.
2. Zero hardcoded real user home paths (`C:\\Users\\<user>`, `/Users/<user>`, `/home/<user>`).
3. Zero private keys or common API token literals.
4. Optional local blocklist pattern via `AGENTMEMORY_EXTRA_DENY_REGEX` environment variable
   (keeps private personal terms out of public repository source while supporting local pre-push sweeps).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SAFE_PLACEHOLDER_USERS = {
    "<user>",
    "<username>",
    "your-user",
    "your-username",
    "username",
    "example",
    "demo",
    "alex",
    "public",
    "default",
    "shared",
    "runner",
}

WIN_USER_PATH_RE = re.compile(r"(?i)\b[a-z]:[\\/]+users[\\/]+([A-Za-z0-9_.<>%${}-]+)")
MAC_USER_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/Users/([A-Za-z0-9_.<>%${}-]+)")
LINUX_HOME_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/home/([A-Za-z0-9_.<>%${}-]+)")

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("OpenAI-style secret key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("GitHub personal access token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("AWS access key ID", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
]

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".woff",
    ".woff2",
}


def list_repo_files(root: Path) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True,
            check=True,
        )
        rels = [p.decode("utf-8") for p in proc.stdout.split(b"\x00") if p]
        return sorted((root / rel) for rel in rels if (root / rel).is_file())
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            p
            for p in root.rglob("*")
            if p.is_file()
            and ".git" not in p.parts
            and "__pycache__" not in p.parts
            and not p.name.endswith((".bak", ".orig", ".pyc", ".db"))
        )


def _check_user_paths(rel: str, line_no: int, line: str, errors: list[str]) -> None:
    for label, regex in (
        ("Windows user path", WIN_USER_PATH_RE),
        ("macOS user path", MAC_USER_PATH_RE),
        ("Linux home path", LINUX_HOME_PATH_RE),
    ):
        for m in regex.finditer(line):
            user_seg = m.group(1).strip().lower()
            if user_seg in SAFE_PLACEHOLDER_USERS or user_seg.startswith(("<", "$", "%", "{")):
                continue
            errors.append(f"[{rel}:{line_no}] Potential {label} leak: {m.group(0)!r}")


def audit_repository(root: Path, extra_deny_regex: str = "") -> tuple[int, list[str]]:
    files = list_repo_files(root)
    errors: list[str] = []
    extra_re = re.compile(extra_deny_regex) if extra_deny_regex else None

    for path in files:
        rel = str(path.relative_to(root)).replace("\\", "/")
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue

        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"[{rel}] UTF-8 BOM detected; must be UTF-8 without BOM")
        if b"\r\n" in raw or b"\r" in raw:
            errors.append(f"[{rel}] CRLF/CR line endings detected; must be LF")

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            errors.append(f"[{rel}] Invalid UTF-8 encoding: {exc}")
            continue

        for idx, line in enumerate(text.split("\n"), start=1):
            _check_user_paths(rel, idx, line, errors)
            for secret_label, secret_re in SECRET_PATTERNS:
                if secret_re.search(line):
                    errors.append(f"[{rel}:{idx}] Potential {secret_label} detected")
            if extra_re and extra_re.search(line):
                errors.append(f"[{rel}:{idx}] Matched AGENTMEMORY_EXTRA_DENY_REGEX")

    return len(files), errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify UTF-8/LF hygiene and zero path/secret leaks.")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repository root directory",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    extra_deny = os.environ.get("AGENTMEMORY_EXTRA_DENY_REGEX", "").strip()
    scanned, errors = audit_repository(root, extra_deny_regex=extra_deny)

    if errors:
        print(f"[FAIL] Repository hygiene check failed ({len(errors)} issue(s) across {scanned} files):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"[OK] Repository hygiene passed: {scanned} files verified (UTF-8 no BOM, LF, 0 path/secret leaks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
