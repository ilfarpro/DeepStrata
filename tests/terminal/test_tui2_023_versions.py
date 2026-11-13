"""0.2.3 RC stress test, #3 (H01): the version bump is complete and consistent, and a 0.2.2 install sees the update."""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

import strata_cli
from strata_control import feed as feedmod, updater
from tests.terminal import test_updater

ROOT = Path(__file__).resolve().parents[2]


def first(path, pattern):
    return re.search(pattern, (ROOT / path).read_text(encoding="utf-8"), re.MULTILINE).group(1)


class VersionBump(unittest.TestCase):
    def test_the_terminal_is_0_2_4_2_and_control_is_0_1_49_2_while_the_app_stays_0_2_3(self):
        # 0.2.13 (upstream line) with engine 0.1.61: the fork's sync with Niko1221/Strata keeps the DeepSeek work
        # (Q5_K fmt 4 in 0.1.59, the mixed MXFP4 down of issue #1 in 0.1.60) beside upstream's engine changes; Terminal and
        # page are 0.2.13, Control and every engine package 0.1.61 (the CMake project version).
        self.assertEqual(strata_cli.PRODUCT_VERSION, "0.2.13")
        self.assertEqual(strata_cli.version(), "0.1.61")                # the CMake project version
        self.assertEqual(first("CMakeLists.txt", r"^project\(strata VERSION ([0-9.]+) "), "0.1.61")
        self.assertIn('STRATA_VERSION="0.1.61-metal"', (ROOT / "metal" / "CMakeLists.txt").read_text(encoding="utf-8"))

    def test_every_place_the_page_version_lives_says_0_2_3(self):
        host = json.loads((ROOT / "apps/host/package.json").read_text(encoding="utf-8"))
        self.assertEqual(host["version"], "0.2.13")
        locked = json.loads((ROOT / "apps/host/package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual((locked["version"], locked["packages"][""]["version"]), ("0.2.13", "0.2.13"))

    def test_the_documents_that_state_the_current_version_agree(self):
        self.assertIn("Version 0.2.13, engine 0.1.61.", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        # The fork's front page is English-only: it states our own release and engine version, names no
        # installer file our release folder does not carry, and does not offer the removed desktop app.
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"DeepStrata 0\.2\.13\.1")   # the release the front page describes (tag ds-0.2.13.1)
        self.assertRegex(text, r"\b0\.1\.61\b")            # the Control and the engine that release publishes
        self.assertNotRegex(text, r"Strata-0[.]\d")            # no upstream app installers: we publish none
        self.assertNotIn("desktop app", text)                   # the shell is gone: the page rides in the Terminal
        self.assertNotIn("macos-arm64.dmg", text)               # ... and so are its per-system installers

    def test_version_prints_the_product_and_the_build(self):
        done = subprocess.run([sys.executable, str(ROOT / "strata.py"), "--version"], capture_output=True, text=True, cwd=str(ROOT),
                              env={**os.environ, "STRATA_NO_HANDOVER": "1"})
        self.assertTrue(done.stdout.startswith("strata 0.2.13 (build 0.1.61;"), done.stdout)


class UpdateFromTheLastRelease(test_updater.UpdaterCase):
    def feed_with_control(self, version):
        bundle = self.root / "control-bundle"
        (bundle / "strata_control").mkdir(parents=True)
        (bundle / "strata.py").write_text(f"print('strata 0.2.3 (build {version})')\n")
        (bundle / "strata_control" / "__init__.py").write_text("")
        from feed_tool import build_feed, pack_control
        (self.release / "control").mkdir()
        pack_control(bundle, self.release / "control", version=version, platform=test_updater.PLATFORM)
        build_feed(self.release, key=self.key, sequence=1, control_dir=self.release / "control")

    def test_a_0_2_2_install_is_offered_control_0_1_48_and_a_0_2_3_install_is_current(self):
        self.feed_with_control("0.1.48")
        (self.components / "control").mkdir(parents=True)
        (self.components / "control" / "active.json").write_text(json.dumps({"version": "0.1.47", "folder": "0.1.47"}))
        report = updater.check(self.ctx())
        self.assertEqual((report["control"]["status"], report["control"]["installed"], report["control"]["offered"]),
                         ("available", "0.1.47", "0.1.48"))
        out = updater.run_update(self.ctx(), engine=False, control=True)
        self.assertEqual((out["exit_code"], out["results"][0]["status"]), (0, "updated"), out)
        self.assertEqual(updater.active_control(self.components)["version"], "0.1.48")
        self.assertEqual(updater.check(self.ctx())["control"]["status"], "current")

    def test_the_bumped_numbers_sort_above_the_last_release(self):
        self.assertGreater(feedmod.version_key(strata_cli.version()), feedmod.version_key("0.1.47"))
        self.assertGreater(feedmod.version_key("0.1.48"), feedmod.version_key("0.1.46.3"))
        self.assertGreater(feedmod.version_key("0.1.48"), feedmod.version_key("0.1.45.3"))
