#!/usr/bin/env python3
"""End-to-end contract tests for local-agent-memory scripts and demo vault."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEMO_VAULT = REPO_ROOT / "examples" / "demo-vault"
DEMO_CORE = DEMO_VAULT / "core" / "core-profile.md"


def run_py(script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def find_powershell() -> str | None:
    for candidate in ("pwsh", "powershell"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


class RepoContractTests(unittest.TestCase):
    def test_repo_hygiene_gate(self) -> None:
        res = run_py(SCRIPTS_DIR / "check-repo-hygiene.py", "--root", str(REPO_ROOT))
        self.assertEqual(res.returncode, 0, msg=f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")
        self.assertIn("[OK]", res.stdout)

    def test_repo_hygiene_catches_leaks_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad_root = Path(tmp)
            win_sample = "C:" + "\\Users\\realperson\\secret"
            linux_sample = "/ho" + "me/johndoe/.ssh"
            (bad_root / "leak.md").write_bytes(
                b"\xef\xbb\xbfPath " + win_sample.encode("utf-8") + b"\r\nLinux " + linux_sample.encode("utf-8") + b"\n"
            )
            res = run_py(SCRIPTS_DIR / "check-repo-hygiene.py", "--root", str(bad_root))
            self.assertEqual(res.returncode, 1)
            self.assertIn("UTF-8 BOM", res.stdout)
            self.assertIn("CRLF/CR", res.stdout)
            self.assertIn("Windows user path", res.stdout)
            self.assertIn("Linux home path", res.stdout)

    def test_demo_vault_strict_audit(self) -> None:
        res = run_py(SCRIPTS_DIR / "audit-freshness.py", "--root", str(DEMO_VAULT), "--strict")
        self.assertEqual(res.returncode, 0, msg=f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

    def test_sync_static_core_lifecycle_and_guards(self) -> None:
        sync_py = SCRIPTS_DIR / "sync-static-core.py"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ag_rule = tmp_dir / "antigravity" / "core-profile.md"
            codex_agents = tmp_dir / "codex" / "AGENTS.md"
            hermes_soul = tmp_dir / "hermes" / "SOUL.md"
            hermes_missing = tmp_dir / "hermes-missing" / "SOUL.md"

            codex_agents.parent.mkdir(parents=True, exist_ok=True)
            codex_agents.write_bytes(b"# Existing Codex Rules\n- Keep commits atomic.\n")
            hermes_soul.parent.mkdir(parents=True, exist_ok=True)
            hermes_soul.write_bytes(b"# Hermes Persona\nStay concise.\n")

            manifest = [
                {
                    "name": "antigravity",
                    "group": "antigravity",
                    "renderTarget": "agent-mcp",
                    "mode": "full",
                    "strategy": "antigravity_frontmatter_replace",
                    "path": str(ag_rule),
                },
                {
                    "name": "codex",
                    "group": "codex",
                    "renderTarget": "agent-mcp",
                    "mode": "full",
                    "strategy": "preserve_outside_append",
                    "path": str(codex_agents),
                },
                {
                    "name": "hermes-default",
                    "group": "hermes",
                    "renderTarget": "agent-mcp",
                    "mode": "lite",
                    "strategy": "preserve_outside_append",
                    "path": str(hermes_soul),
                },
                {
                    "name": "hermes-uninstalled",
                    "group": "hermes",
                    "renderTarget": "agent-nomcp",
                    "mode": "lite",
                    "strategy": "preserve_outside_append",
                    "path": str(hermes_missing),
                },
            ]
            manifest_path = tmp_dir / "targets.json"
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8", newline="\n")

            # 1. Dry-run must not create or mutate files
            dry = run_py(sync_py, "--core-path", str(DEMO_CORE), "--manifest-path", str(manifest_path))
            self.assertEqual(dry.returncode, 0, msg=dry.stdout + dry.stderr)
            self.assertFalse(ag_rule.exists())
            self.assertNotIn("AGENTMEMORY:CORE:BEGIN", codex_agents.read_text(encoding="utf-8"))

            # 2. First apply injects blocks, creates .bak, preserves frontmatter & outside rules, enforces LF
            applied = run_py(
                sync_py, "--core-path", str(DEMO_CORE), "--manifest-path", str(manifest_path), "--apply"
            )
            self.assertEqual(applied.returncode, 0, msg=applied.stdout + applied.stderr)
            self.assertTrue(ag_rule.exists())
            self.assertFalse(hermes_missing.exists())

            ag_raw = ag_rule.read_bytes()
            self.assertFalse(ag_raw.startswith(b"\xef\xbb\xbf"))
            self.assertNotIn(b"\r", ag_raw)
            ag_lines = ag_raw.decode("utf-8").split("\n")
            self.assertEqual(ag_lines[:3], ["---", "trigger: always_on", "---"])

            codex_text = codex_agents.read_text(encoding="utf-8")
            self.assertIn("# Existing Codex Rules", codex_text)
            self.assertIn("AGENTMEMORY:CORE:BEGIN", codex_text)
            baks = list(codex_agents.parent.glob("AGENTS.md.*.bak"))
            self.assertEqual(len(baks), 1)

            hermes_bytes = hermes_soul.read_bytes()
            self.assertLessEqual(len(hermes_bytes), 2400)
            self.assertNotIn(b"Local Workspace Map", hermes_bytes)

            # 3. Second apply is idempotent ([UNCHANGED])
            second = run_py(
                sync_py, "--core-path", str(DEMO_CORE), "--manifest-path", str(manifest_path), "--apply"
            )
            self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
            self.assertEqual(second.stdout.count("[UNCHANGED]"), 3)

            # 4. Manual edit inside managed block triggers [DRIFT] (exit 2) until --force
            tampered = codex_text.replace("Read-only first", "TAMPERED RULE")
            codex_agents.write_bytes(tampered.encode("utf-8"))
            drift = run_py(
                sync_py, "--core-path", str(DEMO_CORE), "--manifest-path", str(manifest_path), "--apply"
            )
            self.assertEqual(drift.returncode, 2, msg=drift.stdout + drift.stderr)
            self.assertIn("[DRIFT]", drift.stdout)
            self.assertIn("TAMPERED RULE", codex_agents.read_text(encoding="utf-8"))

            forced = run_py(
                sync_py,
                "--core-path",
                str(DEMO_CORE),
                "--manifest-path",
                str(manifest_path),
                "--apply",
                "--force",
            )
            self.assertEqual(forced.returncode, 0, msg=forced.stdout + forced.stderr)
            self.assertNotIn("TAMPERED RULE", codex_agents.read_text(encoding="utf-8"))

            # 5. Native scratchpads USER.md and MEMORY.md must be hard-blocked (exit 1)
            blocked_manifest = tmp_dir / "blocked.json"
            blocked_manifest.write_text(
                json.dumps(
                    [
                        {
                            "name": "bad-target",
                            "group": "hermes",
                            "renderTarget": "agent-mcp",
                            "mode": "lite",
                            "strategy": "preserve_outside_append",
                            "path": str(tmp_dir / "MEMORY.md"),
                        }
                    ]
                ),
                encoding="utf-8",
                newline="\n",
            )
            blocked = run_py(
                sync_py, "--core-path", str(DEMO_CORE), "--manifest-path", str(blocked_manifest), "--apply"
            )
            self.assertEqual(blocked.returncode, 1)
            self.assertIn("[BLOCKED]", blocked.stdout)

    def test_example_manifests_dry_run(self) -> None:
        sync_py = SCRIPTS_DIR / "sync-static-core.py"
        for manifest_name in ("sync-targets.example.json", "sync-targets.posix.example.json"):
            res = run_py(
                sync_py,
                "--core-path",
                str(DEMO_CORE),
                "--manifest-path",
                str(SCRIPTS_DIR / manifest_name),
            )
            self.assertEqual(res.returncode, 0, msg=f"{manifest_name}:\n{res.stdout}\n{res.stderr}")

    def test_powershell_and_python_hash_parity(self) -> None:
        ps_bin = find_powershell()
        if not ps_bin:
            self.skipTest("PowerShell not installed on this runner")

        sync_ps1 = SCRIPTS_DIR / "sync-static-core.ps1"
        sync_py = SCRIPTS_DIR / "sync-static-core.py"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ag_path = tmp_dir / "core-profile.md"
            codex_path = tmp_dir / "AGENTS.md"
            manifest_path = tmp_dir / "parity.json"
            manifest_path.write_text(
                json.dumps(
                    [
                        {
                            "name": "antigravity",
                            "group": "antigravity",
                            "renderTarget": "agent-mcp",
                            "mode": "full",
                            "strategy": "antigravity_frontmatter_replace",
                            "path": str(ag_path),
                        },
                        {
                            "name": "codex",
                            "group": "codex",
                            "renderTarget": "agent-mcp",
                            "mode": "full",
                            "strategy": "preserve_outside_append",
                            "path": str(codex_path),
                        },
                    ]
                ),
                encoding="utf-8",
                newline="\n",
            )

            ps_res = subprocess.run(
                [
                    ps_bin,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(sync_ps1),
                    "-CorePath",
                    str(DEMO_CORE),
                    "-ManifestPath",
                    str(manifest_path),
                    "-Apply",
                ],
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(ps_res.returncode, 0, msg=ps_res.stdout + ps_res.stderr)

            py_res = run_py(
                sync_py,
                "--core-path",
                str(DEMO_CORE),
                "--manifest-path",
                str(manifest_path),
                "--apply",
            )
            self.assertEqual(py_res.returncode, 0, msg=py_res.stdout + py_res.stderr)
            self.assertEqual(py_res.stdout.count("[UNCHANGED]"), 2, msg=py_res.stdout)

    def test_patcher_idempotent_lifecycle_on_synthetic_stub(self) -> None:
        patcher = SCRIPTS_DIR / "apply-zh-fts-patch.py"
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "basic_memory"
            (pkg / "repository").mkdir(parents=True)
            (pkg / "mcp" / "tools").mkdir(parents=True)

            (pkg / "__init__.py").write_text("", encoding="utf-8", newline="\n")
            (pkg / "ignore_utils.py").write_text(
                "def strict_stub(bmignore_path, base_path, use_gitignore):\n"
                '        paths = [bmignore_path, base_path / ".gitignore"] if use_gitignore else [bmignore_path]\n'
                "def nonstrict_stub(base_path, use_gitignore):\n"
                "    # Start with patterns from .bmignore\n"
                "    patterns = load_bmignore_patterns()\n"
                "\n"
                "    if use_gitignore:\n"
                "        pass\n",
                encoding="utf-8",
                newline="\n",
            )
            (pkg / "repository" / "sqlite_search_query.py").write_text(
                "def is_fts5_syntax_error(exc: Exception) -> bool:\n"
                "    return False\n\n"
                "async def search_stub(active_session, sql, params, search_text, relaxed_fallback_used):\n"
                "                    result = await active_session.execute(text(sql), params)\n"
                "                    rows = result.fetchall()\n"
                "            return rows, relaxed_fallback_used\n\n"
                "async def count_stub(session, sql, params, search_text, compiled):\n"
                "                        result = await session.execute(text(sql), params)\n"
                "                        total = int(result.scalar_one())\n"
                "                return total\n",
                encoding="utf-8",
                newline="\n",
            )
            (pkg / "mcp" / "server.py").write_text(
                "import time\n\n"
                "def lifespan(app, config):\n"
                "    set_project_delete_visibility(app, config.enable_project_delete)\n\n"
                "mcp.add_middleware(MCPClientInfoMiddleware())\n"
                "mcp.add_middleware(EmptyListResultMiddleware())\n",
                encoding="utf-8",
                newline="\n",
            )
            (pkg / "mcp" / "tools" / "projects.py").write_text(
                "def handle(cmd, name, path, output_format):\n"
                "    match cmd:\n"
                '        case "create":\n'
                "            if name is None or path is None:\n"
                "                return None\n",
                encoding="utf-8",
                newline="\n",
            )

            pre = run_py(patcher, "--check", "--package-dir", str(pkg))
            self.assertEqual(pre.returncode, 1)

            app1 = run_py(patcher, "--apply", "--package-dir", str(pkg))
            self.assertEqual(app1.returncode, 0, msg=app1.stdout + app1.stderr)

            chk = run_py(patcher, "--check", "--package-dir", str(pkg))
            self.assertEqual(chk.returncode, 0, msg=chk.stdout + chk.stderr)
            self.assertEqual(chk.stdout.count("[OK]"), 4)

            app2 = run_py(patcher, "--apply", "--package-dir", str(pkg))
            self.assertEqual(app2.returncode, 0, msg=app2.stdout + app2.stderr)
            self.assertEqual(app2.stdout.count("[ALREADY_APPLIED]"), 4)

            rev = run_py(patcher, "--revert", "--package-dir", str(pkg))
            self.assertEqual(rev.returncode, 0, msg=rev.stdout + rev.stderr)
            post_rev = run_py(patcher, "--check", "--package-dir", str(pkg))
            self.assertEqual(post_rev.returncode, 1)


if __name__ == "__main__":
    unittest.main()
