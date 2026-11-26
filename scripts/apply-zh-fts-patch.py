#!/usr/bin/env python3
"""Idempotent patcher for pinned basic-memory==1.0.0.

Applies three minimal, verified patches to the installed basic-memory==1.0.0 package:
  1. ignore_utils.py: also read project-root `<base_path>/.bmignore` in addition to
     `<basic-memory data dir>/.bmignore` and `<base_path>/.gitignore`.
  2. repository/sqlite_search_query.py: add CJK bigram + BM25 OR-relaxed fallback with
     stop-character filtering and multi-term coverage guard for natural-language CJK queries.
  3. mcp/server.py & mcp/tools/projects.py: enforce read-only MCP tool surface by default
     (hide bm_write/bm_edit/bm_move/bm_delete/bm_project_delete and block bm_project create),
     overridable only via BASIC_MEMORY_READ_ONLY=false.

Note: This script does not redistribute any basic-memory source code. basic-memory is an
independent third-party dependency licensed under AGPL-3.0-or-later.

Usage:
  python apply-zh-fts-patch.py --check
  python apply-zh-fts-patch.py --apply
  python apply-zh-fts-patch.py --revert
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import shutil
import sys
from pathlib import Path

PINNED_VERSION = "1.0.0"
PATCH_VERSION = "local-agent-memory-patch-v1.0"

MARKER_IGNORE = "# AGENTMEMORY_PATCH:PROJECT_BMIGNORE:"
MARKER_ZH_FTS = "# AGENTMEMORY_PATCH:ZH_FTS_BIGRAM:"
MARKER_RO_SERVER = "# AGENTMEMORY_PATCH:READONLY_SERVER:"
MARKER_RO_PROJECT = "# AGENTMEMORY_PATCH:READONLY_PROJECT_CREATE:"


def _candidate_uv_tool_sites() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = [
        # Windows default uv tool path
        home / "AppData" / "Roaming" / "uv" / "tools" / "basic-memory" / "Lib" / "site-packages" / "basic_memory",
    ]
    # Linux / macOS default uv tool path (~/.local/share/uv/tools/basic-memory/lib/python*/site-packages/basic_memory)
    posix_lib = home / ".local" / "share" / "uv" / "tools" / "basic-memory" / "lib"
    if posix_lib.exists():
        for py_dir in sorted(posix_lib.glob("python*"), reverse=True):
            candidates.append(py_dir / "site-packages" / "basic_memory")
    return candidates


def resolve_basic_memory_root(explicit_root: str | None) -> tuple[Path, str]:
    if explicit_root:
        root = Path(explicit_root)
        if not (root / "__init__.py").exists():
            raise SystemExit(f"[ERROR] Invalid basic_memory root: {root}")
        return root, "explicit"

    spec = importlib.util.find_spec("basic_memory")
    if spec and spec.submodule_search_locations:
        root = Path(list(spec.submodule_search_locations)[0])
        try:
            ver = importlib.metadata.version("basic-memory")
        except importlib.metadata.PackageNotFoundError:
            ver = "unknown"
        return root, ver

    for site_pkg in _candidate_uv_tool_sites():
        if (site_pkg / "__init__.py").exists():
            dist_infos = list(site_pkg.parent.glob("basic_memory-*.dist-info"))
            ver = (
                dist_infos[0].name.removeprefix("basic_memory-").removesuffix(".dist-info")
                if dist_infos
                else "unknown"
            )
            return site_pkg, ver

    raise SystemExit(
        "[ERROR] Cannot locate basic_memory package. Run using basic-memory venv python "
        "or pass --package-dir."
    )


def backup_once(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".agentmemory.orig")
    if not bak.exists():
        shutil.copy2(path, bak)


def restore_backup(path: Path) -> bool:
    bak = path.with_suffix(path.suffix + ".agentmemory.orig")
    if bak.exists():
        shutil.copy2(bak, path)
        return True
    return False


# --- Patch 1: ignore_utils.py (project-root .bmignore) ---

IGNORE_STRICT_OLD = '        paths = [bmignore_path, base_path / ".gitignore"] if use_gitignore else [bmignore_path]'
IGNORE_STRICT_NEW = (
    f"        {MARKER_IGNORE}{PATCH_VERSION}\n"
    '        paths = [bmignore_path, base_path / ".bmignore"]\n'
    '        if use_gitignore:\n'
    '            paths.append(base_path / ".gitignore")'
)

IGNORE_NONSTRICT_OLD = """    # Start with patterns from .bmignore
    patterns = load_bmignore_patterns()

    if use_gitignore:"""

IGNORE_NONSTRICT_NEW = f"""    # Start with patterns from .bmignore
    patterns = load_bmignore_patterns()

    {MARKER_IGNORE}{PATCH_VERSION}
    project_bmignore = base_path / ".bmignore"
    if project_bmignore.exists():
        try:
            with project_bmignore.open("r", encoding="utf-8-sig") as f:
                for line in f:
                    pattern = _parse_ignore_pattern_line(line)
                    if pattern:
                        patterns.add(pattern)
        except Exception:
            pass

    if use_gitignore:"""


# --- Patch 2: repository/sqlite_search_query.py (CJK Bigram + BM25 relaxed fallback) ---

ZH_FTS_HELPER_ANCHOR = "def is_fts5_syntax_error(exc: Exception) -> bool:"
ZH_FTS_HELPER_CODE = f'''{MARKER_ZH_FTS}{PATCH_VERSION}
_ZH_STOP_CHARS = frozenset(
    "的是了吗呢吧啊呀和与及或把被让给在从向往到对跟比按就都也还又再更最很太真好有没不"
    "什么哪些怎么如何为什么是不是能不能会可以要需要目前已经现在最近今天下周帮我看看关于"
)
_ZH_STOP_BIGRAMS = frozenset({{
    "平时", "去年", "多少", "什么", "哪些", "怎么", "如何", "是不是", "目前", "已经", "现在",
    "最近", "今天", "下周", "帮我", "看看", "关于", "一个", "一些", "一下", "一点", "一般",
    "状态", "处理", "注意", "事项", "要求", "规则", "问题", "方法", "情况", "可以", "需要",
    "应该", "有没有", "哪个", "在哪", "哪里", "所有", "还是", "或者", "用的", "的是", "有什么",
}})


def _extract_relaxed_zh_terms(search_text: str | None) -> tuple[list[str], list[str]]:
    """Extract ASCII/Latin word tokens and content-bearing CJK bigrams for BM25 relaxation."""
    if not search_text or not search_text.strip():
        return [], []
    stripped = search_text.strip()
    if '"' in stripped or any(op in f" {{stripped}} " for op in (" AND ", " OR ", " NOT ")):
        return [], []
    sq = analyze_script_query(stripped)
    if not sq.gram_phrases:
        return [], []

    word_terms: list[str] = []
    if sq.word_text:
        for raw_w in sq.word_text.split():
            w = raw_w.strip("?!.,;:\\"'()[]{{}}")
            if w and any(c.isalnum() for c in w) and len(w) >= 2:
                if w.lower() not in {{"and", "or", "not", "the", "for", "with"}}:
                    word_terms.append(w)
    word_terms = list(dict.fromkeys(word_terms))

    raw_grams = [g for phrase in sq.gram_phrases for g in phrase]
    content_grams = [
        g
        for g in raw_grams
        if g not in _ZH_STOP_BIGRAMS and not all(c in _ZH_STOP_CHARS for c in g)
    ]
    if not content_grams:
        content_grams = raw_grams
    content_grams = list(dict.fromkeys(content_grams))
    return word_terms, content_grams


def _build_relaxed_zh_fts_expr(word_terms: list[str], content_grams: list[str]) -> str | None:
    or_terms: list[str] = []
    for w in word_terms:
        esc = w.replace('"', '""')
        or_terms.append(f'{{SQLITE_WORD_COLUMNS}}: ("{{esc}}"*)')
    for g in content_grams:
        esc = g.replace('"', '""')
        or_terms.append(f'script_ngrams: "{{esc}}"')
    return " OR ".join(or_terms) if or_terms else None


async def _filter_relaxed_zh_rows(
    active_session: AsyncSession,
    rows: list[Any],
    word_terms: list[str],
    content_grams: list[str],
) -> list[Any]:
    """Filter out single-term coincidental hits on multi-term natural-language CJK queries."""
    if not rows:
        return rows
    total_terms = len(word_terms) + len(content_grams)
    min_hits = 2 if total_terms >= 4 else 1
    if min_hits <= 1:
        return rows

    row_ids = list({{row._mapping["id"] for row in rows if row._mapping.get("id") is not None}})
    if not row_ids:
        return rows

    placeholders = ", ".join(f":zh_id_{{i}}" for i in range(len(row_ids)))
    id_params = {{f"zh_id_{{i}}": rid for i, rid in enumerate(row_ids)}}
    res = await active_session.execute(
        text(
            f"SELECT project_id, type, id, title, content_snippet FROM search_index "
            f"WHERE id IN ({{placeholders}})"
        ),
        id_params,
    )
    corpus_by_key: dict[tuple[Any, Any, Any], str] = {{}}
    for r in res.fetchall():
        m = r._mapping
        hay = f"{{m.get('title') or ''}}\\n{{m.get('content_snippet') or ''}}".lower()
        corpus_by_key[(m["project_id"], m["type"], m["id"])] = hay

    kept = []
    lower_words = [w.lower() for w in word_terms]
    for row in rows:
        m = row._mapping
        hay = corpus_by_key.get((m["project_id"], m["type"], m["id"]), "")
        if not hay:
            hay = f"{{m.get('title') or ''}}\\n{{m.get('content_snippet') or ''}}".lower()
        hits = sum(1 for w in lower_words if w in hay) + sum(1 for g in content_grams if g in hay)
        if hits >= min_hits:
            kept.append(row)
    return kept


def is_fts5_syntax_error(exc: Exception) -> bool:'''

ZH_FTS_SEARCH_OLD = """                    result = await active_session.execute(text(sql), params)
                    rows = result.fetchall()
            return rows, relaxed_fallback_used"""

ZH_FTS_SEARCH_NEW = f"""                    result = await active_session.execute(text(sql), params)
                    rows = result.fetchall()
            {MARKER_ZH_FTS}{PATCH_VERSION}
            if not rows and "script_text" in params and search_text:
                zh_words, zh_grams = _extract_relaxed_zh_terms(search_text)
                zh_expr = _build_relaxed_zh_fts_expr(zh_words, zh_grams)
                if zh_expr:
                    relaxed_fallback_used = True
                    zh_params = dict(params)
                    zh_params["text"] = zh_expr
                    zh_params["script_text"] = ""
                    zh_params["limit"] = max(int(params.get("limit", 10)), 25)
                    zh_params["offset"] = 0
                    result = await active_session.execute(text(sql), zh_params)
                    cand_rows = await _filter_relaxed_zh_rows(
                        active_session, result.fetchall(), zh_words, zh_grams
                    )
                    off = int(params.get("offset", 0))
                    lim = int(params.get("limit", 10))
                    rows = cand_rows[off : off + lim]
            return rows, relaxed_fallback_used"""

ZH_FTS_COUNT_OLD = """                        result = await session.execute(text(sql), params)
                        total = int(result.scalar_one())
                return total"""

ZH_FTS_COUNT_NEW = f"""                        result = await session.execute(text(sql), params)
                        total = int(result.scalar_one())
                {MARKER_ZH_FTS}{PATCH_VERSION}
                if total == 0 and "script_text" in params and search_text:
                    zh_words, zh_grams = _extract_relaxed_zh_terms(search_text)
                    zh_expr = _build_relaxed_zh_fts_expr(zh_words, zh_grams)
                    if zh_expr:
                        zh_params = dict(params)
                        zh_params["text"] = zh_expr
                        zh_params["script_text"] = ""
                        id_sql = (
                            f"SELECT search_index.project_id, search_index.type, search_index.id, "
                            f"search_index.title, search_index.content_snippet "
                            f"FROM {{compiled.from_clause}} WHERE {{compiled.where_clause}} LIMIT 50"
                        )
                        cand = await session.execute(text(id_sql), zh_params)
                        kept = await _filter_relaxed_zh_rows(
                            session, cand.fetchall(), zh_words, zh_grams
                        )
                        total = len(kept)
                return total"""


# --- Patch 3: mcp/server.py & mcp/tools/projects.py (Server-side read-only enforcement) ---

SERVER_IMPORT_OLD = "import time\n"
SERVER_IMPORT_NEW = f"import os\nimport time\n{MARKER_RO_SERVER}{PATCH_VERSION}\n"

SERVER_LIFESPAN_OLD = "    set_project_delete_visibility(app, config.enable_project_delete)"
SERVER_LIFESPAN_NEW = f"""    set_project_delete_visibility(app, config.enable_project_delete)
    {MARKER_RO_SERVER}{PATCH_VERSION}
    if os.getenv("BASIC_MEMORY_READ_ONLY", "true").strip().lower() not in {{"0", "false", "no", "off"}}:
        app.add_transform(
            Visibility(
                False,
                names={{"bm_write", "bm_edit", "bm_move", "bm_delete", "bm_project_delete"}},
            )
        )"""

SERVER_BOTTOM_OLD = """mcp.add_middleware(MCPClientInfoMiddleware())
mcp.add_middleware(EmptyListResultMiddleware())"""

SERVER_BOTTOM_NEW = f"""mcp.add_middleware(MCPClientInfoMiddleware())
mcp.add_middleware(EmptyListResultMiddleware())
{MARKER_RO_SERVER}{PATCH_VERSION}
if os.getenv("BASIC_MEMORY_READ_ONLY", "true").strip().lower() not in {{"0", "false", "no", "off"}}:
    mcp.add_transform(
        Visibility(
            False,
            names={{"bm_write", "bm_edit", "bm_move", "bm_delete", "bm_project_delete"}},
        )
    )"""

PROJECTS_CREATE_OLD = """        case "create":
            if name is None or path is None:"""

PROJECTS_CREATE_NEW = f"""        case "create":
            {MARKER_RO_PROJECT}{PATCH_VERSION}
            import os as _bm_os
            if _bm_os.getenv("BASIC_MEMORY_READ_ONLY", "true").strip().lower() not in {{"0", "false", "no", "off"}}:
                raise refusal(
                    "FORBIDDEN",
                    "MCP server is running in read-only mode; bm_project(command='create') is disabled.",
                    output_format=output_format,
                )
            if name is None or path is None:"""


def check_status(root: Path) -> dict[str, bool]:
    return {
        "ignore_utils.py (project .bmignore)": MARKER_IGNORE
        in (root / "ignore_utils.py").read_text(encoding="utf-8"),
        "repository/sqlite_search_query.py (CJK FTS bigram)": MARKER_ZH_FTS
        in (root / "repository" / "sqlite_search_query.py").read_text(encoding="utf-8"),
        "mcp/server.py (read-only tool visibility)": MARKER_RO_SERVER
        in (root / "mcp" / "server.py").read_text(encoding="utf-8"),
        "mcp/tools/projects.py (block project create)": MARKER_RO_PROJECT
        in (root / "mcp" / "tools" / "projects.py").read_text(encoding="utf-8"),
    }


def apply_single_patch(path: Path, marker: str, replacements: list[tuple[str, str]]) -> str:
    content = path.read_text(encoding="utf-8")
    if marker in content:
        return "already_applied"
    backup_once(path)
    updated = content
    for old, new in replacements:
        count = updated.count(old)
        if count != 1:
            raise RuntimeError(
                f"Patch anchor mismatch in {path.name}: expected 1 occurrence, found {count} for:\n{old[:120]}"
            )
        updated = updated.replace(old, new, 1)
    path.write_text(updated, encoding="utf-8", newline="\n")
    return "applied"


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotent patcher for basic-memory==1.0.0")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="Verify patch status without writing")
    group.add_argument("--apply", action="store_true", help="Apply patches idempotently")
    group.add_argument("--revert", action="store_true", help="Restore .agentmemory.orig backups")
    parser.add_argument("--package-dir", default=None, help="Explicit basic_memory package path")
    parser.add_argument("--force", action="store_true", help="Bypass pinned version check")
    args = parser.parse_args()

    root, ver = resolve_basic_memory_root(args.package_dir)
    print(f"Target basic_memory: {root} (version={ver})")
    if not args.force and ver not in (PINNED_VERSION, "explicit"):
        raise SystemExit(
            f"[ERROR] Pinned version is {PINNED_VERSION}, found {ver}. "
            "Verify compatibility or pass --force."
        )

    targets = [
        (
            root / "ignore_utils.py",
            MARKER_IGNORE,
            [(IGNORE_STRICT_OLD, IGNORE_STRICT_NEW), (IGNORE_NONSTRICT_OLD, IGNORE_NONSTRICT_NEW)],
        ),
        (
            root / "repository" / "sqlite_search_query.py",
            MARKER_ZH_FTS,
            [
                (ZH_FTS_HELPER_ANCHOR, ZH_FTS_HELPER_CODE),
                (ZH_FTS_SEARCH_OLD, ZH_FTS_SEARCH_NEW),
                (ZH_FTS_COUNT_OLD, ZH_FTS_COUNT_NEW),
            ],
        ),
        (
            root / "mcp" / "server.py",
            MARKER_RO_SERVER,
            [
                (SERVER_IMPORT_OLD, SERVER_IMPORT_NEW),
                (SERVER_LIFESPAN_OLD, SERVER_LIFESPAN_NEW),
                (SERVER_BOTTOM_OLD, SERVER_BOTTOM_NEW),
            ],
        ),
        (
            root / "mcp" / "tools" / "projects.py",
            MARKER_RO_PROJECT,
            [(PROJECTS_CREATE_OLD, PROJECTS_CREATE_NEW)],
        ),
    ]

    if args.check:
        status = check_status(root)
        all_ok = True
        for name, ok in status.items():
            print(f"  [{'OK' if ok else 'MISSING'}] {name}")
            all_ok = all_ok and ok
        return 0 if all_ok else 1

    if args.revert:
        for path, _, _ in targets:
            restored = restore_backup(path)
            print(f"  [{'RESTORED' if restored else 'NO_BACKUP'}] {path.name}")
        return 0

    if args.apply:
        for path, marker, repls in targets:
            outcome = apply_single_patch(path, marker, repls)
            print(f"  [{outcome.upper()}] {path.relative_to(root)}")
        status = check_status(root)
        assert all(status.values()), f"Post-apply verification failed: {status}"
        print(f"All patches verified ({PATCH_VERSION}).")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
