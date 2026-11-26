#!/usr/bin/env python3
"""Read-only freshness, contract, link, Git, and pending/ auditor for local-agent-memory.

Default mode is strictly read-only (zero disk writes).
Pass --write-digest to write pending/_digest.md, or --expire-pending to move >=30d pending files
into pending/_expired/<agent>/.

Domains are auto-discovered from `<root>/domains/*/` unless explicitly constrained with --domains.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import subprocess
import sys
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_SENSITIVITIES = ("normal", "private", "restricted")
VALID_STATUSES = ("active", "stale", "archived", "superseded")
VALID_TTL_TIERS = ("L0", "L1", "L2")
VALID_PUBLISH_STATUSES = ("raw", "drafted", "published", "skip")
VALID_SOURCE_TYPES = ("BUILT", "MEASURED", "LIVED")


@dataclass
class AuditReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def notice(self, msg: str) -> None:
        self.notices.append(msg)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse simple flat YAML frontmatter without external dependencies."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        if text.endswith("\n---"):
            end = len(text) - 4
            body = ""
        else:
            return {}, text
    else:
        body = text[end + 5 :]
    raw_fm = text[4:end]
    data: dict[str, str] = {}
    for line in raw_fm.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            val = v.strip()
            if not (val.startswith("[") or val.startswith('"') or val.startswith("'")):
                if " #" in val:
                    val = val.split(" #", 1)[0].strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            data[k.strip()] = val
    return data, body


def parse_date(val: str) -> dt.date | None:
    if not val:
        return None
    try:
        return dt.date.fromisoformat(val.strip()[:10])
    except ValueError:
        return None


def count_cjk(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def render_core_target(text: str, target: str) -> str:
    def _rep(match: re.Match[str]) -> str:
        targets = [x.strip() for x in match.group(1).split(",")]
        return match.group(2) if (target in targets or "all" in targets) else ""

    out = re.sub(
        r"<!-- target: ([^>]*?) -->\n(.*?)<!-- /target -->\n?",
        _rep,
        text,
        flags=re.S,
    )
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip() + "\n"


def check_encoding_and_eol(path: Path, rel: str, report: AuditReport) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        report.error(f"[{rel}] Cannot read file: {exc}")
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        report.error(f"[{rel}] UTF-8 BOM detected; must be UTF-8 without BOM")
    if b"\r\n" in raw or b"\r" in raw:
        report.error(f"[{rel}] CRLF/CR line endings detected; must be LF")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        report.error(f"[{rel}] UTF-8 decode failed: {exc}")
        return None


def strip_fenced_code(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`]+`", "", text)
    return text


def check_relative_links(path: Path, rel: str, body: str, report: AuditReport) -> int:
    cleaned = strip_fenced_code(body)
    checked = 0
    for m in re.finditer(r"(?<!!)\[[^\]]+\]\(([^)]+)\)", cleaned):
        target = m.group(1).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:", "file://")):
            continue
        target_path = target.split(" ", 1)[0].split("#", 1)[0]
        if not target_path:
            continue
        target_path = urllib.parse.unquote(target_path)
        resolved = (path.parent / target_path).resolve()
        checked += 1
        if not resolved.exists():
            report.error(f"[{rel}] Broken relative link: ({target}) -> {resolved}")
    return checked


def audit_core(
    root: Path,
    report: AuditReport,
    full_bytes_limit: int,
    lite_bytes_limit: int,
) -> None:
    core_path = root / "core" / "core-profile.md"
    if not core_path.exists():
        report.error("[core/core-profile.md] Missing canonical static core file")
        return
    txt = check_encoding_and_eol(core_path, "core/core-profile.md", report)
    if txt is None:
        return

    full_rendered = render_core_target(
        re.sub(r"<!-- core-lite:(?:begin|end) -->\n?", "", txt), "default"
    )
    full_bytes = len(full_rendered.encode("utf-8"))
    report.stats["core_full_bytes"] = full_bytes
    report.stats["core_full_cjk"] = count_cjk(full_rendered)
    if full_bytes > full_bytes_limit:
        report.warn(
            f"[core/core-profile.md] Full static core is {full_bytes}B, exceeding budget {full_bytes_limit}B"
        )

    m = re.search(r"<!-- core-lite:begin -->\n(.*?)<!-- core-lite:end -->", txt, flags=re.S)
    if not m:
        report.error("[core/core-profile.md] Missing <!-- core-lite:begin --> / <!-- core-lite:end --> markers")
        return
    lite_raw = m.group(1)
    for tg in ("agent-mcp", "agent-nomcp"):
        rendered = render_core_target(lite_raw, tg)
        bsize = len(rendered.encode("utf-8"))
        report.stats[f"core_lite_{tg}_bytes"] = bsize
        if bsize > lite_bytes_limit:
            report.warn(f"[core/core-profile.md] core-lite({tg}) = {bsize}B > {lite_bytes_limit}B")


def audit_domains(
    root: Path,
    today: dt.date,
    report: AuditReport,
    explicit_domains: list[str] | None,
    index_bytes_limit: int,
) -> None:
    domains_dir = root / "domains"
    if not domains_dir.exists():
        report.error("[domains/] Directory missing")
        return

    if explicit_domains:
        domain_names = explicit_domains
    else:
        domain_names = sorted(
            d.name for d in domains_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
    if not domain_names:
        report.warn("[domains/] No domain subdirectories found")
        return

    card_count = 0
    link_count = 0
    permalinks: dict[str, str] = {}

    for dom in domain_names:
        dom_dir = domains_dir / dom
        if not dom_dir.exists():
            report.error(f"[domains/{dom}/] Domain directory missing")
            continue
        idx = dom_dir / "_index.md"
        if not idx.exists():
            report.error(f"[domains/{dom}/_index.md] Fallback _index.md missing")
        else:
            rel_idx = str(idx.relative_to(root)).replace("\\", "/")
            txt = check_encoding_and_eol(idx, rel_idx, report)
            if txt is not None:
                bsize = len(txt.encode("utf-8"))
                if bsize > index_bytes_limit:
                    report.warn(f"[{rel_idx}] Size {bsize}B exceeds {index_bytes_limit}B budget")
                fm, body = parse_frontmatter(txt)
                if fm.get("kind") != "index":
                    report.error(f"[{rel_idx}] frontmatter kind should be 'index', got {fm.get('kind')!r}")
                if fm.get("domain") != dom:
                    report.error(f"[{rel_idx}] frontmatter domain should be {dom!r}, got {fm.get('domain')!r}")
                link_count += check_relative_links(idx, rel_idx, body, report)

        for md in sorted(dom_dir.glob("*.md")):
            if md.name == "_index.md":
                continue
            card_count += 1
            rel = str(md.relative_to(root)).replace("\\", "/")
            txt = check_encoding_and_eol(md, rel, report)
            if txt is None:
                continue
            fm, body = parse_frontmatter(txt)
            if not fm:
                report.error(f"[{rel}] Missing YAML frontmatter")
                continue

            for req in (
                "title",
                "type",
                "permalink",
                "kind",
                "domain",
                "sensitivity",
                "status",
                "ttl_tier",
                "updated_at",
                "verified_at",
            ):
                if not fm.get(req):
                    report.error(f"[{rel}] Missing required frontmatter field: {req}")

            perm = fm.get("permalink", "")
            if perm:
                if not perm.startswith(f"{dom}-"):
                    report.error(f"[{rel}] permalink {perm!r} must start with '{dom}-'")
                if perm in permalinks:
                    report.error(f"[{rel}] Duplicate permalink {perm!r} (also in {permalinks[perm]})")
                else:
                    permalinks[perm] = rel

            if fm.get("type") != "note":
                report.error(f"[{rel}] type must be 'note', got {fm.get('type')!r}")
            if fm.get("kind") != "card":
                report.error(f"[{rel}] kind must be 'card', got {fm.get('kind')!r}")
            if fm.get("domain") != dom:
                report.error(f"[{rel}] domain must be {dom!r}, got {fm.get('domain')!r}")
            if fm.get("sensitivity") not in VALID_SENSITIVITIES:
                report.error(f"[{rel}] Invalid sensitivity: {fm.get('sensitivity')!r}")

            status = fm.get("status", "")
            if status not in VALID_STATUSES:
                report.error(f"[{rel}] Invalid status: {status!r}")
            elif status in ("archived", "superseded"):
                report.error(
                    f"[{rel}] status={status} still resides in domains/; move physically to archive/ and reindex"
                )
            elif status == "stale":
                report.warn(f"[{rel}] Card is marked stale; verify before citing")

            ttl = fm.get("ttl_tier", "")
            if ttl not in VALID_TTL_TIERS:
                report.error(f"[{rel}] Invalid ttl_tier: {ttl!r} (allowed: L0|L1|L2)")

            upd = parse_date(fm.get("updated_at", ""))
            ver = parse_date(fm.get("verified_at", ""))
            if not upd:
                report.error(f"[{rel}] Invalid updated_at date: {fm.get('updated_at')!r}")
            if not ver:
                report.error(f"[{rel}] Invalid verified_at date: {fm.get('verified_at')!r}")
            elif status == "active":
                age = (today - ver).days
                if ttl == "L1" and age > 180:
                    report.warn(f"[{rel}] L1 card unverified for {age}d (>180d review window)")
                elif ttl == "L2" and age > 90:
                    report.warn(f"[{rel}] L2 card unverified for {age}d (>90d; verify or mark stale)")
                elif ttl == "L2" and age > 60:
                    report.notice(f"[{rel}] L2 card unverified for {age}d (entering 60-90d watch window)")

            link_count += check_relative_links(md, rel, body, report)

    report.stats["domains"] = domain_names
    report.stats["active_cards"] = card_count
    report.stats["links_checked"] = report.stats.get("links_checked", 0) + link_count


def audit_episodes(root: Path, today: dt.date, report: AuditReport) -> None:
    ep_dir = root / "episodes"
    if not ep_dir.exists():
        report.stats["episodes"] = 0
        return

    ep_count = 0
    link_count = 0
    for md in sorted(ep_dir.rglob("*.md")):
        ep_count += 1
        rel = str(md.relative_to(root)).replace("\\", "/")
        txt = check_encoding_and_eol(md, rel, report)
        if txt is None:
            continue
        fm, body = parse_frontmatter(txt)
        for req in ("title", "type", "permalink", "kind", "status", "publish_status", "source_type", "created_at"):
            if not fm.get(req):
                report.error(f"[{rel}] episode missing required field: {req}")
        if "ttl_tier" in fm and fm["ttl_tier"]:
            report.error(f"[{rel}] episode must not define ttl_tier (decoupled lifecycle)")
        perm = fm.get("permalink", "")
        if perm and not re.match(r"^episode-\d{8}-", perm):
            report.error(f"[{rel}] episode permalink {perm!r} must start with 'episode-YYYYMMDD-'")
        if fm.get("kind") != "episode":
            report.error(f"[{rel}] kind must be 'episode'")
        if fm.get("publish_status") not in VALID_PUBLISH_STATUSES:
            report.error(f"[{rel}] Invalid publish_status: {fm.get('publish_status')!r}")
        if fm.get("source_type") not in VALID_SOURCE_TYPES:
            report.error(f"[{rel}] Invalid source_type: {fm.get('source_type')!r}")
        status = fm.get("status", "")
        if status in ("archived", "superseded"):
            report.error(f"[{rel}] status={status} still resides in episodes/; move physically to archive/episodes/")
        cdate = parse_date(fm.get("created_at", ""))
        if not cdate:
            report.error(f"[{rel}] Invalid created_at date: {fm.get('created_at')!r}")
        elif status == "active":
            age = (today - cdate).days
            if age >= 30 and fm.get("publish_status") == "raw":
                report.notice(
                    f"[{rel}] episode is {age}d old with publish_status=raw; confirm troubleshooting value or archive"
                )
        link_count += check_relative_links(md, rel, body, report)

    report.stats["episodes"] = ep_count
    report.stats["links_checked"] = report.stats.get("links_checked", 0) + link_count


def audit_pending(
    root: Path,
    today: dt.date,
    report: AuditReport,
    write_digest: bool,
    expire_pending: bool,
) -> None:
    pending_dir = root / "pending"
    if not pending_dir.exists():
        report.stats["pending_files"] = 0
        report.stats["pending_entries"] = 0
        return

    entries: list[dict[str, Any]] = []
    expired_files: list[Path] = []
    file_count = 0

    for md in sorted(pending_dir.rglob("*.md")):
        rel = str(md.relative_to(root)).replace("\\", "/")
        parts = md.relative_to(pending_dir).parts
        if md.name == "_digest.md" or (parts and parts[0] == "_expired"):
            continue
        file_count += 1
        agent = parts[0] if len(parts) > 1 else "unknown"
        m_date = re.match(r"^(\d{4}-\d{2}-\d{2})", md.stem)
        fdate = parse_date(m_date.group(1)) if m_date else dt.date.fromtimestamp(md.stat().st_mtime)
        age = (today - fdate).days if fdate else 0

        if age >= 30:
            report.warn(f"[{rel}] pending draft unreviewed for {age}d (>=30d; eligible for pending/_expired/)")
            expired_files.append(md)
        elif age >= 14:
            report.warn(f"[{rel}] pending draft unreviewed for {age}d (>=14d warning threshold)")

        txt = md.read_text(encoding="utf-8", errors="replace")
        dedupes = re.findall(r"dedupe_key:\s*(.+)", txt)
        targets = re.findall(r"target:\s*(.+)", txt)
        ops = re.findall(r"op:\s*(.+)", txt)
        count = max(len(dedupes), 1 if txt.strip() else 0)
        for i in range(count):
            entries.append(
                {
                    "file": rel,
                    "agent": agent,
                    "date": str(fdate),
                    "age_days": age,
                    "dedupe_key": dedupes[i].strip() if i < len(dedupes) else "(unspecified)",
                    "target": targets[i].strip() if i < len(targets) else "(unspecified)",
                    "op": ops[i].strip() if i < len(ops) else "(unspecified)",
                }
            )

    report.stats["pending_files"] = file_count
    report.stats["pending_entries"] = len(entries)
    if len(entries) > 15:
        report.warn(f"[pending/] {len(entries)} pending entries (>15 noise threshold; tighten write gates/quotas)")

    if expire_pending and expired_files:
        for src in expired_files:
            rel_sub = src.relative_to(pending_dir)
            dst = pending_dir / "_expired" / rel_sub
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            report.notice(f"[pending/] Moved expired draft to {dst.relative_to(root)}")

    if write_digest:
        lines = [
            f"# Pending Review Digest ({today.isoformat()})",
            "",
            "> Mark each row with `APPROVE` / `EDIT` / `REJECT`, then merge approved items and make one Git commit.",
            "",
            f"- Pending files: {file_count}",
            f"- Pending entries: {len(entries)}",
            "",
        ]
        if not entries:
            lines.append("_No pending candidate memory entries._")
        else:
            lines.append("| Decision | Agent | Date (age) | target | op | dedupe_key | File |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- |")
            for e in entries:
                lines.append(
                    f"| [ ] | `{e['agent']}` | {e['date']} ({e['age_days']}d) | `{e['target']}` | `{e['op']}` | `{e['dedupe_key']}` | `{e['file']}` |"
                )
        digest_path = pending_dir / "_digest.md"
        digest_path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        report.notice(f"[pending/_digest.md] Wrote latest digest ({len(entries)} entries)")


def audit_git(root: Path, today: dt.date, report: AuditReport) -> None:
    try:
        top = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        report.notice("[git] git executable not found; skipping Git integrity checks")
        return

    if top.returncode != 0:
        report.notice("[git] Root is not inside a Git repository; skipping Git integrity checks")
        return

    git_root = Path(top.stdout.strip()).resolve()
    try:
        rel_root = root.resolve().relative_to(git_root).as_posix()
    except ValueError:
        return

    pathspec = "." if rel_root == "." else rel_root
    st = subprocess.run(
        ["git", "-C", str(git_root), "status", "--porcelain", "--", pathspec],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if st.returncode != 0:
        return

    domain_changes: list[str] = []
    staged_pending: list[str] = []
    modified_tracked_md: list[Path] = []

    for line in st.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        raw_path = line[3:].strip().strip('"')
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip('"')
        sub = raw_path[len(rel_root) + 1 :] if (rel_root != "." and raw_path.startswith(rel_root + "/")) else raw_path

        if sub.startswith("domains/"):
            domain_changes.append(f"{xy} {sub}")
        if sub.startswith("pending/") and xy[0] not in (" ", "?"):
            staged_pending.append(f"{xy} {sub}")
        if (sub.startswith("domains/") or sub.startswith("episodes/")) and sub.endswith(".md"):
            if xy != "??" and "D" not in xy:
                modified_tracked_md.append(git_root / raw_path)

    if domain_changes:
        report.warn(
            f"[git] {len(domain_changes)} uncommitted change(s) under domains/ (verify human approval): "
            + ", ".join(domain_changes[:8])
            + (" ..." if len(domain_changes) > 8 else "")
        )
    if staged_pending:
        report.error(
            "[git] pending/ files staged in Git index (violates pending/ Git boundary): "
            + ", ".join(staged_pending)
        )

    for md in modified_tracked_md:
        if not md.exists():
            continue
        rel_md = md.relative_to(git_root).as_posix()
        diff = subprocess.run(
            ["git", "-C", str(git_root), "diff", "HEAD", "--", rel_md],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if diff.returncode == 0 and diff.stdout.strip():
            has_upd_change = any(ln.startswith("+updated_at:") for ln in diff.stdout.splitlines())
            fm, _ = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            if not has_upd_change and fm.get("updated_at") != today.isoformat():
                report.warn(
                    f"[git] {rel_md} modified without updating frontmatter updated_at ({fm.get('updated_at')})"
                )


def audit_required_files(root: Path, report: AuditReport) -> None:
    for req in (".gitattributes", ".bmignore", "README.md", "index-roots.yaml"):
        p = root / req
        if not p.exists():
            report.error(f"[{req}] Required root file missing")
        else:
            txt = check_encoding_and_eol(p, req, report)
            if req == "README.md" and txt is not None:
                check_relative_links(p, req, txt, report)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only freshness, contract, link, and Git auditor for local-agent-memory."
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="Memory vault root directory (the directory containing core/, domains/, .bmignore).",
    )
    parser.add_argument(
        "--domains",
        type=str,
        default="",
        help="Optional comma-separated list of expected domain names (default: auto-discover under domains/).",
    )
    parser.add_argument(
        "--today",
        type=str,
        default=dt.date.today().isoformat(),
        help="Override today's date (YYYY-MM-DD) for deterministic testing.",
    )
    parser.add_argument("--core-full-bytes", type=int, default=8192, help="Max UTF-8 bytes for full static core.")
    parser.add_argument("--core-lite-bytes", type=int, default=2048, help="Max UTF-8 bytes for core-lite block.")
    parser.add_argument("--index-bytes", type=int, default=1536, help="Max UTF-8 bytes for each domain _index.md.")
    parser.add_argument(
        "--write-digest",
        action="store_true",
        help="Explicitly write pending/_digest.md (disabled in default read-only mode).",
    )
    parser.add_argument(
        "--expire-pending",
        action="store_true",
        help="Explicitly move >=30d pending drafts to pending/_expired/ (disabled by default).",
    )
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any warnings are present.")
    args = parser.parse_args()

    root = args.root.resolve()
    today = dt.date.fromisoformat(args.today)
    explicit_domains = [d.strip() for d in args.domains.split(",") if d.strip()] or None
    report = AuditReport()

    audit_required_files(root, report)
    audit_core(root, report, args.core_full_bytes, args.core_lite_bytes)
    audit_domains(root, today, report, explicit_domains, args.index_bytes)
    audit_episodes(root, today, report)
    audit_pending(root, today, report, args.write_digest, args.expire_pending)
    audit_git(root, today, report)

    mode_label = "READ-ONLY" if not (args.write_digest or args.expire_pending) else "WRITE-ENABLED"
    print(f"=== local-agent-memory Freshness Audit ({mode_label}) ===")
    print(f"Root: {root} | Date: {today.isoformat()}")
    print(
        f"Stats: domains={report.stats.get('domains', [])}, "
        f"active_cards={report.stats.get('active_cards', 0)}, "
        f"episodes={report.stats.get('episodes', 0)}, "
        f"links_checked={report.stats.get('links_checked', 0)}, "
        f"pending_entries={report.stats.get('pending_entries', 0)}, "
        f"core_full={report.stats.get('core_full_bytes', '?')}B, "
        f"core_lite_mcp={report.stats.get('core_lite_agent-mcp_bytes', '?')}B, "
        f"core_lite_nomcp={report.stats.get('core_lite_agent-nomcp_bytes', '?')}B"
    )

    if report.errors:
        print(f"\n[ERRORS: {len(report.errors)}]")
        for e in report.errors:
            print(f"  - {e}")
    if report.warnings:
        print(f"\n[WARNINGS: {len(report.warnings)}]")
        for w in report.warnings:
            print(f"  - {w}")
    if report.notices:
        print(f"\n[NOTICES: {len(report.notices)}]")
        for n in report.notices:
            print(f"  - {n}")

    if not report.errors and not report.warnings:
        print("\n[OK] All contract, freshness, link, encoding, and Git checks passed.")

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
