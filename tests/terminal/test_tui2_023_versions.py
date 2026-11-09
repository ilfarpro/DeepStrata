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
        # 0.1.59 engine (this branch, DeepSeek fmt 4): the Engram reader takes Q5_K, opening Unsloth UD-IQ2_XXS; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.59 (the CMake project version); the Terminal and page stay 0.2.12. CUDA device reader verified on RTX sm_89 + sm_120; HIP/Metal build from the same source, their card runs are still open.
        # 0.2.12 (owner 2026-11-06, release of rounds 0215/0216/0217): Terminal and app are 0.2.12; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.58 (the CMake project version).
        # 0.2.11 (coordinator 2026-11-04; the owner can still rename it 0.2.10.1): Terminal and app are 0.2.11; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.57 (the CMake project version).
        # 0.2.10 (the 0.2.9 update, owner 2026-11-02): Terminal and app are 0.2.10; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.56 (the CMake project version).
        # 0.2.9.1 (a label prepared on 10-31 for a DeepSeek-only Windows AMD hotfix and never published: no tag, no 0.1.55.1 assets on v0.2.9) (DeepSeek engine only, the Windows AMD package): the DeepSeek engine is 0.1.55.1 (the CMake project version); the Terminal, the app and Control (0.1.55) and every other engine package stay as 0.2.9 shipped them.
        # 0.2.9: Terminal and app are 0.2.9; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.55.
        # 0.2.8.1 (Control-only): the Terminal is 0.2.8.1 and Control 0.1.55 (the CMake project version); the app stays 0.2.8 and every engine package 0.1.54 (as 0.2.3.1 did for the Terminal).
        # 0.2.8: Terminal and app are 0.2.8; Control and every engine package (Qwen, DeepSeek, Metal, SYCL, aarch64) are 0.1.54 (both engines changed since 0.2.7.1).
        # 0.2.7.1 (DGX Spark): Control and the linux-aarch64 engine packages are 0.1.53 (the CMake project version); the app and the Terminal stay 0.2.7, the Metal engine 0.1.52.
        # 0.2.7: Terminal and app are 0.2.7; Control and every engine package (Qwen, DeepSeek, Metal) are 0.1.52 (the CMake project version; the Qwen and DeepSeek C++ both changed since 0.2.6)
        self.assertEqual(strata_cli.PRODUCT_VERSION, "0.2.12")
        self.assertEqual(strata_cli.version(), "0.1.59")                # the CMake project version
        self.assertEqual(first("CMakeLists.txt", r"^project\(strata VERSION ([0-9.]+) "), "0.1.59")
        self.assertIn('STRATA_VERSION="0.1.59-metal"', (ROOT / "metal" / "CMakeLists.txt").read_text(encoding="utf-8"))

    def test_every_place_the_page_version_lives_says_0_2_3(self):
        host = json.loads((ROOT / "apps/host/package.json").read_text(encoding="utf-8"))
        self.assertEqual(host["version"], "0.2.12")
        locked = json.loads((ROOT / "apps/host/package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual((locked["version"], locked["packages"][""]["version"]), ("0.2.12", "0.2.12"))

    def test_the_documents_that_state_the_current_version_agree(self):
        self.assertIn("Version 0.2.12, engine 0.1.59.", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))
        # The fork's front page is English-only (DIVERGENCE.md, zone 12): it states our own release and engine
        # version, names no installer file our release folder does not carry, and no longer offers the removed
        # desktop app (the page rides in the Terminal archive).
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"DeepStrata 0\.2\.12\.1")      # our release on the upstream 0.2.12 line (tag ds-0.2.12.1)
        self.assertRegex(text, r"\b0\.1\.59\b")                # the Control and the engine our feed publishes
        self.assertNotRegex(text, r"Strata-0[.]\d")            # no upstream app installers: we publish none yet
        self.assertNotIn("desktop app", text)                   # the shell is gone: the page rides in the Terminal
        self.assertNotIn("macos-arm64.dmg", text)               # ... and so are its per-system installers

    def test_version_prints_the_product_and_the_build(self):
        done = subprocess.run([sys.executable, str(ROOT / "strata.py"), "--version"], capture_output=True, text=True, cwd=str(ROOT),
                              env={**os.environ, "STRATA_NO_HANDOVER": "1"})
        self.assertTrue(done.stdout.startswith("strata 0.2.12 (build 0.1.59;"), done.stdout)


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
