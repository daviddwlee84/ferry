"""Isolated bootstrap safety tests and a pinned real-builder smoke test."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

SKILL = Path(__file__).resolve().parents[1]
INIT = SKILL / "scripts" / "init-docs-site.py"


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def initialize(self, *args):
        return subprocess.run([
            sys.executable, "-I", str(INIT), "--target-dir", str(self.root),
            "--site-name", 'Example "Docs"', "--site-url", "https://example.test/project/",
            "--repo-slug", "owner/repo", *args,
        ], capture_output=True, text=True)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()}

    def test_dry_run_writes_nothing(self):
        result = self.initialize("--ci", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "planned")
        self.assertEqual(self.snapshot(), {})

    def test_fresh_site_and_build_only_ci(self):
        result = self.initialize("--ci")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = tomllib.loads((self.root / "zensical.toml").read_text())
        self.assertEqual(config["project"]["site_name"], 'Example "Docs"')
        self.assertEqual(config["project"]["theme"]["language"], "en")
        deps = tomllib.loads((self.root / "pyproject.toml").read_text())
        self.assertEqual(deps["dependency-groups"]["docs"], ["zensical==0.0.73"])
        self.assertEqual(deps["project"]["requires-python"], ">=3.11")
        self.assertEqual(deps["project"]["dependencies"], [])
        workflow = (self.root / ".github/workflows/docs-check.yml").read_text()
        self.assertIn("pull_request:", workflow)
        self.assertIn("--locked --only-group docs", workflow)
        self.assertNotIn("pages: write", workflow)
        self.assertNotIn("actions/deploy-pages", workflow)
        self.assertNotIn(".github/workflows/docs.yml", self.snapshot())
        before = self.snapshot()
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(self.snapshot(), before)

    def test_existing_docs_require_opt_in_and_preserve_dependencies(self):
        (self.root / "docs").mkdir()
        (self.root / "docs/index.md").write_text("# User content\n")
        (self.root / "pyproject.toml").write_text('[project]\nname="app"\nversion="0.1.0"\ndependencies=[]\n')
        (self.root / ".gitignore").write_text("custom-file")
        original = self.snapshot()
        self.assertEqual(self.initialize().returncode, 2)
        self.assertEqual(self.snapshot(), original)
        result = self.initialize("--existing", "keep")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("docs/index.md", "pyproject.toml"):
            self.assertEqual(self.snapshot()[name], original[name])
        self.assertTrue((self.root / ".gitignore").read_text().startswith("custom-file\n"))
        self.assertIn("uv add --group docs", json.loads(result.stdout)["next_steps"][0])

    def test_ci_honors_existing_python_requirement(self):
        content = '[project]\nname="newer-python"\nversion="0.1.0"\nrequires-python=">=3.12"\n'
        (self.root / "pyproject.toml").write_text(content)
        result = self.initialize("--ci")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "pyproject.toml").read_text(), content)
        workflow = (self.root / ".github/workflows/docs-check.yml").read_text()
        self.assertNotIn("python-version:", workflow)
        self.assertIn("uv sync --locked --only-group docs", workflow)

    def test_config_or_output_conflict_fails_before_writes(self):
        for name in ("mkdocs.yml", "mkdocs.yaml", "scripts/check-docs-site.py",
                     ".github/workflows/docs-check.yml"):
            with self.subTest(name=name):
                path = self.root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("user owned\n")
                before = self.snapshot()
                self.assertEqual(self.initialize("--ci").returncode, 2)
                self.assertEqual(self.snapshot(), before)
                path.unlink()

    def test_symlinks_fail_closed(self):
        for name in ("docs", "scripts", ".gitignore", "pyproject.toml"):
            with self.subTest(name=name):
                path = self.root / name
                path.symlink_to(self.root / "missing")
                self.assertEqual(self.initialize().returncode, 2)
                self.assertFalse((self.root / "zensical.toml").exists())
                path.unlink()

    def test_docs_symlink_is_not_followed(self):
        (self.root / "docs").mkdir()
        (self.root / "docs/index.md").symlink_to(self.root / "missing")
        self.assertEqual(self.initialize("--existing", "keep").returncode, 2)
        self.assertFalse((self.root / "zensical.toml").exists())

    def test_invalid_url_fails_without_writes(self):
        for url in ("https://example.test/project", "https://user:pass@example.test/", "file:///tmp/"):
            with self.subTest(url=url):
                self.assertEqual(self.initialize("--site-url", url).returncode, 2)
                self.assertEqual(self.snapshot(), {})

    @unittest.skipUnless(os.environ.get("ZENSICAL_INTEGRATION") == "1", "set ZENSICAL_INTEGRATION=1 for real builds")
    def test_real_build_and_artifact_failures(self):
        self.assertEqual(self.initialize().returncode, 0)
        (self.root / "docs/guides").mkdir()
        (self.root / "docs/guides/setup.md").write_text(
            "# Nested setup\n\nNested export marker.\n\n[Home](../index.md)\n\n"
            "```mermaid\nflowchart TD\n    Start[Start] --> Done[Done]\n```\n")
        result = subprocess.run([
            "uvx", "--from", "zensical==0.0.73", "zensical", "build", "--strict",
            "--config-file", str(self.root / "zensical.toml"),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('class="mermaid"', (self.root / "site/guides/setup/index.html").read_text())
        self.assertIn("flowchart TD", (self.root / "site/guides/setup/index.md").read_text())
        checker = [sys.executable, "-I", str(self.root / "scripts/check-docs-site.py"),
                   "--target-dir", str(self.root)]
        result = subprocess.run(checker, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["llm_pages"], 2)
        search = self.root / "site/search.json"
        original_search = search.read_text()
        invalid_search = json.loads(original_search)
        for items in ([], "not-an-index"):
            invalid_search["items"] = items
            search.write_text(json.dumps(invalid_search))
            self.assertNotEqual(subprocess.run(checker, capture_output=True).returncode, 0)
        search.write_text(original_search)
        llms = self.root / "site/llms.txt"
        original = llms.read_text()
        llms.write_text("# Empty\n")
        self.assertNotEqual(subprocess.run(checker, capture_output=True).returncode, 0)
        llms.write_text(original)
        llms.write_text(original.replace("https://example.test/project/", "https://example.test/"))
        self.assertNotEqual(subprocess.run(checker, capture_output=True).returncode, 0)
        llms.write_text(original)
        markdown = self.root / "site/guides/setup/index.md"
        markdown.unlink()
        self.assertNotEqual(subprocess.run(checker, capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
