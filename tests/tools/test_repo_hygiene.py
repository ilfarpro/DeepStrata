"""Tests for .github/scripts/repo_hygiene.py's checks: the gate that runs on every pull request.  No GPU, no downloads.

    python -m pytest tests/tools/test_repo_hygiene.py

The unit cases pin what counts as damage; the last case runs the script against a throwaway git repository, because
the cheap failure mode of a check like this is a check that inspects nothing and prints "passed".
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "repo_hygiene.py"
# The samples name pages this tree does not carry, which is the point of them; they are built from parts so that
# repo_hygiene.py does not read this file as a citation of a page that has to exist.
MISSING_PAGE = "docs/" + "pack-format.md"
spec = importlib.util.spec_from_file_location("repo_hygiene", SCRIPT)
hygiene = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hygiene)


class MixedEndings(unittest.TestCase):
    def test_one_file_one_line_ending(self):
        self.assertFalse(hygiene.mixed_endings(b"a\nb\n"))
        self.assertFalse(hygiene.mixed_endings(b"a\r\nb\r\n"))
        self.assertFalse(hygiene.mixed_endings(b"one line, no ending"))

    def test_both_endings_in_one_file_is_damage(self):
        self.assertTrue(hygiene.mixed_endings(b"a\r\nb\n"))
        self.assertTrue(hygiene.mixed_endings(b"a\nb\r\nc\n"))


class Mojibake(unittest.TestCase):
    def test_doubly_decoded_section_sign_is_found_with_its_line(self):
        blob = "clean\n".encode() + "see \u00c2\u00a72.4\n".encode()      # the section sign, decoded twice
        self.assertEqual(hygiene.mojibake(blob), {0: 2})

    def test_a_real_section_sign_is_not_damage(self):
        self.assertEqual(hygiene.mojibake("see \u00a72.4\n".encode()), {})

    def test_clean_text_has_no_findings(self):
        self.assertEqual(hygiene.mojibake(b"plain ascii\n"), {})


class DanglingDocsReferences(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="strata-hygiene-"))
        (self.root / "docs").mkdir()
        (self.root / "docs" / "MODELS.md").write_text("# Models\n", encoding="utf-8")
        (self.root / "include").mkdir()
        self.where = self.root / "include" / "layer.hpp"

    def test_an_existing_page_is_not_a_finding(self):
        self.assertEqual(hygiene.dangling("see docs/MODELS.md", self.where, self.root), set())

    def test_a_page_the_tree_does_not_carry_is_a_finding(self):
        self.assertEqual(hygiene.dangling(f"see {MISSING_PAGE}", self.where, self.root), {"pack-format.md"})

    def test_a_page_beside_a_guide_counts(self):
        guide = self.root / "docs" / "GUIDE.md"
        guide.write_text("x", encoding="utf-8")
        self.assertEqual(hygiene.dangling("see MODELS.md", guide, self.root), set())


class EndToEnd(unittest.TestCase):
    """A throwaway repository: the script must fail on damage this change brings and pass on what was already there."""

    def git(self, *args: str, cwd: Path) -> None:
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       cwd=cwd, check=True, capture_output=True)

    def run_script(self, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), "--base", "HEAD"],
                              cwd=cwd, capture_output=True, text=True)

    def test_introduced_damage_fails_and_legacy_damage_does_not(self):
        with tempfile.TemporaryDirectory(prefix="strata-hygiene-repo-") as folder:
            root = Path(folder)
            (root / "docs").mkdir()
            (root / "docs" / "KEEP.md").write_text("# Kept\n", encoding="utf-8")
            # legacy: a page that was never in the tree, in a file this test does not touch again
            (root / "legacy.txt").write_text(f"see {MISSING_PAGE}\n", encoding="utf-8")
            self.git("init", "-q", cwd=root)
            self.git("add", "-A", cwd=root)
            self.git("commit", "-q", "-m", "base", cwd=root)
            self.assertEqual(self.run_script(root).returncode, 0)

            # a touched file keeps its own legacy damage: allowed, the gate is about what this change brings.
            # Staged, not committed: the script reads the working tree against --base, as it does on a pull request.
            (root / "legacy.txt").write_text(f"see {MISSING_PAGE}\nand nothing else\n", encoding="utf-8")
            self.git("add", "-A", cwd=root)
            self.assertEqual(self.run_script(root).returncode, 0)

            # new damage: a page the tree does not carry, a damaged section sign, and two line endings in one file
            (root / "new.txt").write_text(f"see {MISSING_PAGE}\n", encoding="utf-8")
            (root / "damaged.txt").write_text("a \u00c2\u00a7 b\n", encoding="utf-8")
            (root / "endings.txt").write_bytes(b"a\r\nb\n")
            self.git("add", "-A", cwd=root)
            result = self.run_script(root)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(MISSING_PAGE, result.stderr)
            self.assertIn("mojibake in damaged.txt", result.stderr)
            self.assertIn("mixed CRLF and LF", result.stderr)


if __name__ == "__main__":
    unittest.main()
