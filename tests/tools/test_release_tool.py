"""The release tool's offline checks: versions, notes, journals, tokens and feed assets. Nothing here touches GitHub,
a GPU or the network."""

import hashlib
import importlib.util
import io
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))

_spec = importlib.util.spec_from_file_location("deepstrata_release", ROOT / "packaging" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


class Versions(unittest.TestCase):
    def test_next_version_follows_the_same_upstream_line(self):
        self.assertEqual(release.next_release_version("0.2.12", ["ds-0.2.12.1"]), "0.2.12.2")
        self.assertEqual(release.next_release_version("0.2.12", ["ds-0.2.12.1", "ds-0.2.12.7", "ds-0.2.11.9"]),
                         "0.2.12.8")
        self.assertEqual(release.next_release_version("0.2.13", ["ds-0.2.12.7"]), "0.2.13.1")
        self.assertEqual(release.next_release_version("0.2.12", ["not-a-release"]), "0.2.12.1")

    def test_a_version_is_four_numbers_and_the_tag_prefix_is_optional(self):
        self.assertEqual(release.validate_version("0.2.12.2"), "0.2.12.2")
        self.assertEqual(release.validate_version("ds-0.2.12.2"), "0.2.12.2")
        for bad in ("0.2.12", "v0.2.12.2", "0.2.12.2-rc1", "0.2.12.2.1.1", ""):
            with self.subTest(bad), self.assertRaises(release.ReleaseError):
                release.validate_version(bad)

    def test_major(self):
        self.assertEqual(release._major("13.5"), 13)
        self.assertEqual(release._major("unknown"), None)


class RepoUrls(unittest.TestCase):
    def test_ssh_and_https(self):
        self.assertEqual(release.parse_repo("git@github.com:ilfarpro/DeepStrata.git"), "ilfarpro/DeepStrata")
        self.assertEqual(release.parse_repo("https://github.com/ilfarpro/DeepStrata.git"), "ilfarpro/DeepStrata")
        self.assertEqual(release.parse_repo("https://github.com/ilfarpro/DeepStrata"), "ilfarpro/DeepStrata")
        with self.assertRaises(release.ReleaseError):
            release.parse_repo("https://example.org/elsewhere")


class Tokens(unittest.TestCase):
    def test_the_gh_config_token_is_read_and_nothing_is_printed(self):
        with tempfile.TemporaryDirectory() as temporary:
            hosts = Path(temporary) / "hosts.yml"
            hosts.write_text("github.com:\n    users:\n        someone:\n            oauth_token: secret-one\n"
                             "    oauth_token: secret-two\n    user: someone\n", encoding="utf-8")
            self.assertEqual(release.token_from_hosts(hosts), "secret-one")
            self.assertIsNone(release.token_from_hosts(Path(temporary) / "missing.yml"))

    def test_environment_wins_over_the_file(self):
        token, source = release.find_token({"GH_TOKEN": "from-env"})
        self.assertEqual((token, source), ("from-env", "GH_TOKEN"))
        with tempfile.TemporaryDirectory() as temporary:
            token, source = release.find_token({"GH_CONFIG_DIR": temporary})
        self.assertIsNone(token)
        self.assertIn("not found", source)


class Notes(unittest.TestCase):
    def test_areas_are_canonical_and_unknown_goes_to_other(self):
        records = [{"commit": "a" * 40, "subject": "[GUI] a page change"},
                   {"commit": "b" * 40, "subject": "[Engine] a kernel"},
                   {"commit": "c" * 40, "subject": "no area here"}]
        grouped = release.group_notes(records)
        self.assertEqual([item["text"] for item in grouped["GUI"]], ["a page change"])
        self.assertEqual([item["text"] for item in grouped["Engine"]], ["a kernel"])
        self.assertEqual([item["text"] for item in grouped["Other"]], ["no area here"])

    def test_generated_notes_name_the_release_component_and_installer(self):
        body = release.generate_notes(version="0.2.12.2", component="0.1.60", commit="d" * 40, previous="ds-0.2.12.1",
                                      records=[{"commit": "e" * 40, "subject": "[Docs] " + "long " * 100}],
                                      line="0.2.12")
        self.assertIn("DeepStrata 0.2.12.2", body)
        self.assertIn("`0.1.60`", body)
        self.assertIn("Changes since `ds-0.2.12.1`", body)
        self.assertIn("install.sh", body)
        self.assertIn("...", body)
        self.assertLess(len(body), 120_000)

    def test_every_area_has_a_heading_only_when_it_has_commits(self):
        body = release.generate_notes(version="0.2.12.2", component="0.1.60", commit="f" * 40, previous=None,
                                      records=[{"commit": "1" * 40, "subject": "[TUI] a terminal fix"}], line="0.2.12")
        self.assertIn("### TUI", body)
        self.assertNotIn("### Engine", body)


class FeedFiles(unittest.TestCase):
    def make_feed(self, path: Path):
        for name in release.FEED_ASSETS:
            (path / name).write_text("{}" if name.endswith(".json") else "x", encoding="utf-8")
        (path / "strata-engine-0.1.60-linux-x86_64-cuda-cuda13.tar.gz").write_bytes(b"engine")

    def test_inspect_feed_hashes_every_flat_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary) / "feed"
            feed.mkdir()
            self.make_feed(feed)
            assets = release.inspect_feed(feed)
            self.assertIn("strata-feed.json", assets)
            self.assertEqual(assets["strata-engine-0.1.60-linux-x86_64-cuda-cuda13.tar.gz"]["sha256"],
                             release.sha256_file(feed / "strata-engine-0.1.60-linux-x86_64-cuda-cuda13.tar.gz"))

    def test_a_nested_folder_or_a_missing_asset_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary) / "feed"
            feed.mkdir()
            self.make_feed(feed)
            (feed / "sub").mkdir()
            with self.assertRaises(release.ReleaseError):
                release.inspect_feed(feed)
            (feed / "sub").rmdir()
            (feed / "install.ps1").unlink()
            with self.assertRaises(release.ReleaseError):
                release.inspect_feed(feed)

    def test_content_types(self):
        self.assertEqual(release.content_type("strata-feed.json"), "application/json")
        self.assertEqual(release.content_type("install.sh"), "text/x-shellscript")
        self.assertEqual(release.content_type("engine.tar.gz"), "application/octet-stream")
        self.assertEqual(release.content_type("strata-control-0.1.60-windows-x86_64.zip"), "application/zip")

    def test_asset_match_uses_size_state_and_digest(self):
        info = {"bytes": 3, "sha256": "a" * 64}
        self.assertTrue(release._asset_matches({"size": 3, "state": "uploaded", "digest": "sha256:" + "a" * 64}, info))
        self.assertFalse(release._asset_matches({"size": 3, "state": "uploaded", "digest": "sha256:" + "b" * 64}, info))
        self.assertFalse(release._asset_matches({"size": 4, "state": "uploaded"}, info))
        self.assertFalse(release._asset_matches({"size": 3, "state": "starter"}, info))

    def test_an_archive_must_match_the_feed_entry(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            (feed / "engine.tar.gz").write_bytes(b"three")
            entry = SimpleNamespace(url="engine.tar.gz", size=5, sha256=release.sha256_file(feed / "engine.tar.gz"))
            release._check_entry_archive(feed, entry)
            entry.size = 6
            with self.assertRaises(release.ReleaseError):
                release._check_entry_archive(feed, entry)
            entry.size = 5
            entry.sha256 = "b" * 64
            with self.assertRaises(release.ReleaseError):
                release._check_entry_archive(feed, entry)

    def test_install_manifest_must_match_the_control_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            control = SimpleNamespace(platform="linux-x86_64", version="0.1.60",
                                      url="strata-control-0.1.60-linux-x86_64.tar.gz", sha256="c" * 64, size=99)
            (feed / "install-manifest.txt").write_text(
                "linux-x86_64 0.1.60 strata-control-0.1.60-linux-x86_64.tar.gz " + "c" * 64 + " 99\n", encoding="utf-8")
            release._check_install_manifest(feed, SimpleNamespace(control=[control]))
            (feed / "install-manifest.txt").write_text("windows-x86_64 0.1.60 x.zip " + "d" * 64 + " 1\n",
                                                       encoding="utf-8")
            with self.assertRaises(release.ReleaseError):
                release._check_install_manifest(feed, SimpleNamespace(control=[control]))


class Journals(unittest.TestCase):
    def test_roundtrip_and_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            journal = {"schema_version": 1, "release": "0.2.12.2", "tag": "ds-0.2.12.2"}
            release.save_journal(directory, journal)
            loaded = release.load_journal(directory)
            self.assertEqual(loaded["release"], "0.2.12.2")
            self.assertIn("updated_at", loaded)
            release.set_step(loaded, "plan", {"ok": True})
            release.save_journal(directory, loaded)
            self.assertTrue(release.load_journal(directory)["steps"]["plan"]["ok"])

    def test_an_unknown_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            release.write_json(directory / "release.json", {"schema_version": 99})
            with self.assertRaises(release.ReleaseError):
                release.load_journal(directory)

    def test_release_directory_needs_a_name_when_there_are_several(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base = root / "RELEASE"
            for version in ("0.2.12.2", "0.2.12.3"):
                release.write_json(base / version / "release.json",
                                   {"schema_version": 1, "release": version})
            with self.assertRaises(release.ReleaseError):
                release.release_directory(SimpleNamespace(release=None), root)
            found = release.release_directory(SimpleNamespace(release="0.2.12.2"), root)
            self.assertEqual(found[0], "0.2.12.2")

    def test_a_release_name_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(release.ReleaseError):
                release.release_directory(SimpleNamespace(release="not-a-version"), Path(temporary))


class Keys(unittest.TestCase):
    def test_a_key_inside_the_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = root / "feed-key.json"
            key.write_text("{}", encoding="utf-8")
            os.chmod(key, 0o600)
            with self.assertRaises(release.ReleaseError) as caught:
                release.resolve_key_path(str(key), root)
            self.assertIn("inside the git checkout", str(caught.exception))

    def test_a_world_readable_key_is_refused_outside_the_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            checkout = base / "repo"
            checkout.mkdir()
            key = base / "feed-key.json"
            key.write_text("{}", encoding="utf-8")
            os.chmod(key, 0o644)
            with self.assertRaises(release.ReleaseError) as caught:
                release.resolve_key_path(str(key), checkout)
            self.assertIn("readable by other users", str(caught.exception))

    def test_a_missing_key_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(release.ReleaseError):
                release.resolve_key_path(str(Path(temporary) / "missing.json"), Path(temporary))


class Targets(unittest.TestCase):
    """The platforms a release carries, and what may be put on its page."""

    def test_a_journal_from_before_windows_stays_linux_only(self):
        self.assertEqual(release.journal_targets({"release": "0.2.12.1"}), ("linux-x86_64",))
        self.assertEqual(release.journal_targets({"targets": ["linux-x86_64"]}), ("linux-x86_64",))
        self.assertEqual(release.journal_targets({"targets": ["linux-x86_64", "windows-x86_64"]}),
                         ("linux-x86_64", "windows-x86_64"))
        self.assertEqual(release.journal_targets({"targets": ["macos-arm64"]}), ("macos-arm64",))
        self.assertEqual(release.journal_targets({"targets": ["linux-x86_64", "windows-x86_64", "macos-arm64"]}),
                         ("linux-x86_64", "windows-x86_64", "macos-arm64"))

    def test_the_release_targets_must_be_platforms_this_tool_knows(self):
        self.assertEqual(release._release_targets({"targets": ["macos-arm64"]}), ("macos-arm64",))
        with self.assertRaises(release.ReleaseError) as caught:
            release._release_targets({"targets": ["linux-x86_64", "linux-aarch64"]})
        self.assertIn("linux-aarch64", str(caught.exception))
        self.assertIn("cannot assemble", str(caught.exception))

    def test_every_handoff_points_at_a_file_in_the_tree(self):
        # the messages a release prints tell the operator which script and workflow to run: they must exist
        for platform, handoff in release.PARTS_HANDOFFS.items():
            with self.subTest(platform):
                self.assertTrue((ROOT / handoff["builder"]).is_file(), handoff["builder"])
                self.assertTrue((ROOT / ".github" / "workflows" / f"{handoff['workflow']}.yml").is_file())
                self.assertTrue(handoff["manifest"].endswith("-parts.json"))
                self.assertNotEqual(handoff["short"], "")
        self.assertEqual(release.PARTS_HANDOFFS[release.MAC]["short"], "mac")
        self.assertNotEqual(release.PARTS_HANDOFFS[release.MAC]["short"],
                            release.PARTS_HANDOFFS[release.WINDOWS]["short"])

    def test_a_windows_zip_is_a_release_asset(self):
        for name in ("strata-control-0.1.60-windows-x86_64.zip", "strata-terminal-0.1.60-windows-x86_64.zip",
                     "strata-engine-0.1.60-linux-x86_64-cuda-cuda13.tar.gz", "strata-feed.json",
                     "strata-feed.json.sig", "install-manifest.txt", "install.sh", "install.ps1"):
            with self.subTest(name):
                self.assertTrue(release.is_release_asset(name))
        for name in ("strata.exe", "parts.7z", "notes.md", "setup.tar.xz"):
            with self.subTest(name):
                self.assertFalse(release.is_release_asset(name))

    def test_a_windows_control_package_is_zip_and_a_linux_one_is_tar(self):
        self.assertEqual(release.control_format(release.WINDOWS), "zip")
        self.assertEqual(release.control_format(release.LINUX), "tar.gz")
        self.assertEqual(release.control_format(release.MAC), "tar.gz")

    def test_the_plan_names_the_platforms_it_releases(self):
        linux = release.artifact_plan("0.1.60", (release.LINUX,))
        self.assertTrue(linux)
        self.assertTrue(all("linux-x86_64" in item["artifact"] for item in linux))
        windows = release.artifact_plan("0.1.60", (release.WINDOWS,))
        self.assertTrue(any(item["artifact"].endswith("windows-x86_64.zip") for item in windows))
        self.assertFalse(any("linux" in item["artifact"] for item in windows))
        mac = release.artifact_plan("0.1.60", (release.MAC,))
        self.assertTrue(any(item["artifact"].endswith("macos-arm64-metal.tar.gz") for item in mac))
        self.assertTrue(any(item["artifact"].endswith("macos-arm64.tar.gz") for item in mac))
        self.assertFalse(any("linux" in item["artifact"] or "windows" in item["artifact"] for item in mac))
        for targets in ((release.LINUX,), (release.WINDOWS,), (release.MAC,)):
            with self.subTest(targets):
                self.assertTrue(release.artifact_plan("0.1.60", targets))
        everything = release.artifact_plan("0.1.60")
        self.assertEqual({item["platform"] for item in everything},
                         {release.LINUX, release.WINDOWS, release.MAC})

    def test_notes_follow_the_targets(self):
        windows = release.generate_notes(version="0.2.12.2", component="0.1.60", commit="a" * 40,
                                         previous=None, records=[], targets=(release.WINDOWS,))
        self.assertIn("install.ps1", windows)
        self.assertIn("SmartScreen", windows)
        self.assertNotIn("install.sh", windows)
        linux = release.generate_notes(version="0.2.12.2", component="0.1.60", commit="a" * 40,
                                       previous=None, records=[], targets=(release.LINUX,))
        self.assertIn("install.sh", linux)
        self.assertNotIn("install.ps1", linux)
        mac = release.generate_notes(version="0.2.12.2", component="0.1.60", commit="a" * 40,
                                     previous=None, records=[], targets=(release.LINUX, release.MAC))
        self.assertIn("macOS (Apple Silicon)", mac)
        self.assertIn("Developer ID", mac)
        self.assertIn("Qwen Metal engine", mac)
        self.assertNotIn("install.ps1", mac)
        self.assertNotIn("Qwen CUDA 13 engine `0.1.60` for `macos-arm64`", mac)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


COMMIT = "a" * 40
SMOKE_CHECKS = ("version", "doctor", "profile list", "serve: chat answer")


def make_engine(base: Path, out: Path, *, entry: str, extra: list[str] | None = None) -> Path:
    """One unsigned CUDA engine package, written by the repo's own packager: exactly what build_windows.ps1 calls on Windows."""
    stage = base / f"stage-{Path(entry).stem}"
    stage.mkdir()
    (stage / entry).write_bytes(b"MZ")
    (stage / "LICENSE").write_bytes(b"DeepStrata\n")
    _write_json(stage / "BUILD.json", {"version": "0.1.60", "source": "test", "archs": "86;89;120",
                                       "cuda": "13.0", "portable": True})
    subprocess.run([sys.executable, "packaging/package_engine.py", "--src", str(stage), "--backend", "cuda",
                    "--version", "0.1.60", "--out", str(out), "--platform", "windows-x86_64",
                    "--variant", "cuda13", "--entrypoint", entry,
                    "--arch", "sm_86", "--arch", "sm_89", "--arch", "sm_120", "--arch", "compute_120",
                    "--min-driver", "nvidia=580.65", "--build-id", "abcdef123456", *(extra or [])],
                   cwd=str(ROOT), check=True, capture_output=True, text=True)
    return out / "0.1.60-windows-x86_64-cuda-cuda13"


def make_engine_mac(base: Path, out: Path, *, entry: str, extra: list[str] | None = None) -> Path:
    """One unsigned Metal engine package: what build_mac_parts.sh builds. `strata` carries the picture reader and
    the kernel sources it compiles at start; the DeepSeek one ships text-only (--no-picture-reader)."""
    stage = base / f"stage-mac-{Path(entry).stem}"
    stage.mkdir()
    (stage / entry).write_bytes(b"\xcf\xfa\xed\xfe")          # a Mach-O header, as the real binaries start
    (stage / "LICENSE").write_bytes(b"DeepStrata\n")
    if entry == "strata":
        (stage / "strata-vision").write_bytes(b"\xcf\xfa\xed\xfe")
    kernels = stage / "metal" / "kernels"
    kernels.mkdir(parents=True)
    (kernels / "mm.metal").write_text("// one kernel\n", encoding="utf-8")
    subprocess.run([sys.executable, "packaging/package_engine.py", "--src", str(stage), "--backend", "metal",
                    "--version", "0.1.60", "--out", str(out), "--platform", "macos-arm64",
                    "--arch", "apple-gpu", "--entrypoint", entry, "--build-id", "abcdef123456",
                    "--include", "metal/**", *(extra or [])],
                   cwd=str(ROOT), check=True, capture_output=True, text=True)
    return out / "0.1.60-macos-arm64-metal"


def _tar_gz(archive: Path, members: dict[str, tuple[bytes, int]]) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w:gz") as tar:
        for name, (payload, mode) in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = mode
            info.mtime = 0
            tar.addfile(info, io.BytesIO(payload))


def parts_folder(base: Path, *, platform: str = "windows-x86_64", commit: str = COMMIT, component: str = "0.1.60",
                 product: str = "0.2.12.2", cuda: str = "13.0.88", dirty: bool = False, smoke: bool = True,
                 smoke_ok: bool = True, schema: int = 1, engines: bool = False, wrapped: bool = True) -> Path:
    """A folder shaped like the one build_windows.ps1 / build_mac_parts.sh write: the Terminal archive, its smoke
    report, and the `<platform>-parts.json` manifest. `platform` may name any platform, also one the builder
    would never write the folder for (what a mixed-up hand-off looks like). `wrapped=False` writes the archive's
    members at the root instead of under one top-level folder."""
    handoff = release.PARTS_HANDOFFS[platform]
    is_windows = platform == release.WINDOWS
    parts = base / f"build-{handoff['short']}-parts"
    prefix = "bundle/" if wrapped else ""
    fmt = "zip" if is_windows else "tar.gz"
    suffix = "-windows-x86_64" if is_windows else "-darwin-arm64"
    archive = parts / "terminal" / f"strata-terminal-{component}{suffix}.{fmt}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if is_windows:
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(f"{prefix}bin/deepstrata.cmd", "@echo off\r\n")
            zf.writestr(f"{prefix}host/dist/index.html", "<!doctype html>\n")
            zf.writestr(f"{prefix}node/node.exe", b"MZ")
            zf.writestr(f"{prefix}strata.py", "print('strata')\n")
    else:
        _tar_gz(archive, {f"{prefix}bin/deepstrata": (b"#!/bin/sh\n", 0o755),
                          f"{prefix}host/dist/index.html": (b"<!doctype html>\n", 0o644),
                          f"{prefix}node/bin/node": (b"\xcf\xfa\xed\xfe", 0o755),
                          f"{prefix}strata.py": (b"print('strata')\n", 0o644)})
    components = {"terminal": {"archive": f"terminal/{archive.name}", "sha256": _sha256(archive),
                               "bytes": archive.stat().st_size, "format": fmt}}
    if engines:
        if is_windows:
            builds = (("qwen", "strata.exe", []), ("dsv41", "strata-dsv41.exe", ["--require-dsv41"]))
        else:
            builds = (("qwen", "strata", []), ("dsv41", "strata-dsv41", ["--no-picture-reader"]))
        maker = make_engine if is_windows else make_engine_mac
        for name, entry, extra in builds:
            folder = maker(base, parts / ("engines" if name == "qwen" else "dsv41"), entry=entry, extra=extra)
            components[name] = {"folder": folder.relative_to(parts).as_posix(),
                                "manifest_sha256": _sha256(folder / "engine-component.json")}
    manifest = {"schema_version": schema, "platform": platform, "commit": commit, "dirty": dirty,
                "component_version": component, "product_version": product, "host_version": product,
                "toolchain": {"cuda": cuda} if is_windows else {"clang": "17.0.0"}, "components": components,
                "smoke": {}}
    if smoke:
        report = parts / "smoke" / "smoke-terminal.json"
        _write_json(report, {"ok": smoke_ok, "archive": archive.name,
                             "checks": {name: {"ok": smoke_ok} for name in SMOKE_CHECKS}})
        manifest["smoke"]["terminal"] = {"report": "smoke/smoke-terminal.json", "sha256": _sha256(report)}
    _write_json(parts / handoff["manifest"], manifest)
    return parts


def windows_parts(base: Path, **overrides) -> Path:
    return parts_folder(base, platform=release.WINDOWS, **overrides)


def mac_parts(base: Path, **overrides) -> Path:
    return parts_folder(base, platform=release.MAC, **overrides)


def windows_journal(**overrides) -> dict:
    data = {"commit": COMMIT, "component_version": "0.1.60", "product_version": "0.2.12.2",
            "host_version": "0.2.12.2", "targets": [release.LINUX, release.WINDOWS]}
    data.update(overrides)
    return data


def mac_journal(**overrides) -> dict:
    data = {"commit": COMMIT, "component_version": "0.1.60", "product_version": "0.2.12.2",
            "host_version": "0.2.12.2", "targets": [release.LINUX, release.MAC]}
    data.update(overrides)
    return data


class PartsHandoffMixin:
    """The import tests, run for each platform's hand-off shape."""

    platform = ""
    journal = staticmethod(windows_journal)

    def release_directory(self, base: Path) -> Path:
        directory = base / "RELEASE" / "0.2.12.2"
        directory.mkdir(parents=True)
        return directory

    def import_parts(self, parts: Path, directory: Path, wanted: list[str] | None = None, **overrides):
        return release._import_parts(directory, self.journal(**overrides), parts,
                                     wanted or ["terminal"], self.platform, rebuild=True)


class WindowsParts(PartsHandoffMixin, unittest.TestCase):
    """The hand-off from a Windows build machine: what is imported, and what is refused."""

    platform = release.WINDOWS
    journal = staticmethod(windows_journal)

    def test_the_terminal_archive_is_copied_unpacked_and_its_smoke_report_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base)
            directory = self.release_directory(base)
            imported, record = self.import_parts(parts, directory)
            terminal = imported["terminal"]
            archive = directory / terminal["archive"]
            self.assertTrue(archive.is_file())
            self.assertEqual(terminal["sha256"], _sha256(archive))
            unpack = directory / terminal["unpack"]
            self.assertTrue((unpack / "bin" / "deepstrata.cmd").is_file())
            self.assertTrue((unpack / "host" / "dist" / "index.html").is_file())
            self.assertTrue(terminal["smoke_ok"])
            self.assertTrue((directory / terminal["smoke"]).is_file())
            self.assertEqual(record["components"], ["terminal"])
            self.assertEqual(record["manifest_sha256"], _sha256(parts / "windows-parts.json"))

    def test_the_engines_are_imported_and_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base, engines=True)
            directory = self.release_directory(base)
            imported, record = self.import_parts(parts, directory, ["terminal", "qwen", "dsv41"])
            self.assertEqual(record["components"], ["dsv41", "qwen", "terminal"])
            for name in ("qwen", "dsv41"):
                folder = directory / imported[name]["folder"]
                self.assertTrue((folder / "engine-component.json").is_file())
                self.assertEqual(imported[name]["manifest_sha256"],
                                 _sha256(folder / "engine-component.json"))

    def test_an_engine_that_lost_its_execute_bit_on_the_way_gets_it_back(self):
        """A zip or an SFTP copy of the parts folder drops the bit Windows never had, and verify would refuse the
        engine; the import puts it back."""
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base, engines=True)
            manifest = json.loads((parts / "windows-parts.json").read_text(encoding="utf-8"))
            source = parts / manifest["components"]["qwen"]["folder"] / "strata.exe"
            source.chmod(0o644)
            self.assertFalse(os.access(source, os.X_OK))
            directory = self.release_directory(base)
            imported, _ = self.import_parts(parts, directory, ["qwen"])
            copied = directory / imported["qwen"]["folder"] / "strata.exe"
            self.assertTrue(os.access(copied, os.X_OK))

    def test_an_engine_component_that_changed_after_the_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base, engines=True)
            manifest = json.loads((parts / "windows-parts.json").read_text(encoding="utf-8"))
            component = parts / manifest["components"]["qwen"]["folder"] / "engine-component.json"
            component.write_bytes(component.read_bytes() + b" ")
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(parts, self.release_directory(base), ["qwen"])
            self.assertIn("changed after the parts manifest was written", str(caught.exception))

    def test_an_engine_binary_that_no_longer_matches_its_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base, engines=True)
            manifest = json.loads((parts / "windows-parts.json").read_text(encoding="utf-8"))
            (parts / manifest["components"]["qwen"]["folder"] / "strata.exe").write_bytes(b"other")
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(parts, self.release_directory(base), ["qwen"])
            self.assertIn("did not verify", str(caught.exception))

    def test_the_parts_can_arrive_as_one_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base)
            bundle = base / "windows-parts.zip"
            with zipfile.ZipFile(bundle, "w") as zf:
                for path in sorted(parts.rglob("*")):
                    if path.is_file():
                        zf.write(path, f"build-windows-parts/{path.relative_to(parts).as_posix()}")
            directory = self.release_directory(base)
            imported, _ = self.import_parts(bundle, directory)
            self.assertIn("terminal", imported)

    def test_another_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base, schema=2), self.release_directory(base))
            self.assertIn("unknown schema", str(caught.exception))

    def test_another_platform_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base)
            manifest = parts / "windows-parts.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["platform"] = "linux-x86_64"
            _write_json(manifest, data)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(parts, self.release_directory(base))
            self.assertIn("platform is", str(caught.exception))

    def test_parts_from_an_uncommitted_tree_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base, dirty=True), self.release_directory(base))
            self.assertIn("uncommitted tree", str(caught.exception))

    def test_parts_from_another_commit_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base), self.release_directory(base), commit="b" * 40)
            self.assertIn("was built from", str(caught.exception))

    def test_parts_for_another_component_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base), self.release_directory(base), component_version="0.1.59")
            self.assertIn("carries component", str(caught.exception))

    def test_parts_built_with_cuda_12_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base, cuda="12.9", engines=True), self.release_directory(base))
            self.assertIn("CUDA 12.9", str(caught.exception))

    def test_a_terminal_only_folder_needs_no_cuda(self):
        # build_windows.ps1 -Only terminal writes "cuda": null: there was no engine, so there was no toolkit to check
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = self.release_directory(base)
            imported, _ = self.import_parts(windows_parts(base, cuda=None), directory)
            self.assertEqual(sorted(imported), ["terminal"])
            self.assertTrue(imported["terminal"]["smoke_ok"])

    def test_parts_without_a_terminal_smoke_report_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base, smoke=False), self.release_directory(base))
            self.assertIn("no Terminal smoke report", str(caught.exception))

    def test_a_failed_terminal_smoke_test_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(windows_parts(base, smoke_ok=False), self.release_directory(base))
            self.assertIn("smoke test failed", str(caught.exception))

    def test_an_archive_that_changed_after_the_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = windows_parts(base)
            archive = next((parts / "terminal").glob("*.zip"))
            changed = bytearray(archive.read_bytes())
            changed[-1] ^= 0xFF                     # same size, different bytes: the hash is what catches it
            archive.write_bytes(changed)
            with self.assertRaises(release.ReleaseError) as caught:
                self.import_parts(parts, self.release_directory(base))
            self.assertIn("sha256 does not match", str(caught.exception))

    def test_a_linux_only_journal_entry_is_read_as_linux(self):
        legacy = {"archive": "parts/x.tar.gz", "unpack": "parts/unpack/x"}
        self.assertEqual(release._platform_map(legacy), {release.LINUX: legacy})
        nested = {release.LINUX: {"archive": "a"}, release.WINDOWS: {"archive": "b"}}
        self.assertEqual(release._platform_map(nested), nested)


class MacParts(PartsHandoffMixin, unittest.TestCase):
    """The hand-off from an Apple Silicon Mac: a tar.gz Terminal, Metal engines, the same manifest shape."""

    platform = release.MAC
    journal = staticmethod(mac_journal)

    def test_the_mac_terminal_archive_is_copied_unpacked_and_its_smoke_report_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = mac_parts(base)
            directory = self.release_directory(base)
            imported, record = self.import_parts(parts, directory)
            self.assertEqual(sorted(imported), ["terminal"])
            info = imported["terminal"]
            self.assertTrue(info["archive"].endswith("strata-terminal-0.1.60-darwin-arm64.tar.gz"))
            self.assertTrue((directory / info["unpack"] / "bin" / "deepstrata").is_file())
            self.assertTrue((directory / info["unpack"] / "node" / "bin" / "node").is_file())
            self.assertTrue((directory / info["smoke"]).is_file())
            self.assertTrue(info["smoke_ok"])
            self.assertTrue(record["manifest_sha256"])

    def test_the_mac_engines_are_imported_and_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = mac_parts(base, engines=True)
            directory = self.release_directory(base)
            imported, _ = self.import_parts(parts, directory, ["terminal", "qwen", "dsv41"])
            self.assertEqual(sorted(imported), ["dsv41", "qwen", "terminal"])
            self.assertTrue((directory / imported["qwen"]["folder"] / "engine-component.json").is_file())
            self.assertTrue((directory / imported["dsv41"]["folder"] / "engine-component.json").is_file())

    def test_mac_parts_can_arrive_as_one_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = mac_parts(base, engines=True)
            bundle = base / "mac-parts.zip"
            with zipfile.ZipFile(bundle, "w") as zf:
                for path in sorted(parts.rglob("*")):
                    if path.is_file():
                        zf.write(path, f"build-mac-parts/{path.relative_to(parts).as_posix()}")
            directory = self.release_directory(base)
            imported, _ = self.import_parts(bundle, directory, ["terminal", "qwen", "dsv41"])
            self.assertEqual(sorted(imported), ["dsv41", "qwen", "terminal"])

    def test_mac_parts_are_not_windows_parts(self):
        # the manifest names the platform, and the file itself is in the folder's own name: a mixed-up hand-off is refused
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(release.ReleaseError) as caught:
                release._import_parts(self.release_directory(base), mac_journal(), mac_parts(base),
                                      ["terminal"], release.WINDOWS, rebuild=True)
            self.assertIn("windows-parts.json", str(caught.exception))

    def test_an_archive_without_a_top_level_folder_is_unpacked_too(self):
        # package_terminal.py wraps the bundle in one folder today (tarfile.add(root, arcname=...)); a change there must
        # not leave the imported Terminal's `unpack` pointing at a folder that holds bin/ only one level deeper
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory = self.release_directory(base)
            imported, _ = release._import_parts(directory, mac_journal(),
                                                mac_parts(base, wrapped=False), ["terminal"], release.MAC,
                                                rebuild=True)
            unpack = directory / imported["terminal"]["unpack"]
            self.assertTrue((unpack / "bin" / "deepstrata").is_file())
            self.assertTrue((unpack / "node" / "bin" / "node").is_file())

    def test_an_archive_that_is_not_a_terminal_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = mac_parts(base)
            archive = next((parts / "terminal").glob("*.tar.gz"))
            _tar_gz(archive, {"bundle/README.txt": (b"nothing here\n", 0o644)})
            manifest = parts / "mac-parts.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["components"]["terminal"]["sha256"] = _sha256(archive)
            data["components"]["terminal"]["bytes"] = archive.stat().st_size
            _write_json(manifest, data)
            with self.assertRaises(release.ReleaseError) as caught:
                release._import_parts(self.release_directory(base), mac_journal(), parts, ["terminal"],
                                      release.MAC, rebuild=True)
            self.assertIn("bin/deepstrata", str(caught.exception))


class ImportedTerminal(unittest.TestCase):
    """`verify` trusts the smoke report the building platform wrote, and re-hashes the archive so a swap is caught."""

    def imported(self, base: Path, **overrides) -> tuple[Path, dict]:
        directory = base / "RELEASE" / "0.2.12.2"
        directory.mkdir(parents=True)
        archive = directory / "parts" / "mac" / "terminal.tar.gz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"the Mac build's Terminal archive")
        info = {"archive": "parts/mac/terminal.tar.gz", "sha256": _sha256(archive), "smoke_ok": True}
        info.update(overrides)
        return directory, {release.MAC: info}

    def test_a_smoke_tested_archive_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, terminals = self.imported(Path(temporary))
            line = release.check_imported_terminal(directory, terminals, release.MAC)
            self.assertIn("macOS Terminal archive", line)
            self.assertIn("not repeated here", line)

    def test_an_archive_without_a_passing_smoke_report_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, terminals = self.imported(Path(temporary), smoke_ok=False)
            with self.assertRaises(release.ReleaseError) as caught:
                release.check_imported_terminal(directory, terminals, release.MAC)
            self.assertIn("no passing smoke report", str(caught.exception))
            self.assertIn("--skip-smoke", str(caught.exception))

    def test_an_archive_replaced_after_the_import_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, terminals = self.imported(Path(temporary))
            (directory / "parts" / "mac" / "terminal.tar.gz").write_bytes(b"a different archive, same name")
            with self.assertRaises(release.ReleaseError) as caught:
                release.check_imported_terminal(directory, terminals, release.MAC)
            self.assertIn("changed after it was imported", str(caught.exception))

    def test_a_missing_archive_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, terminals = self.imported(Path(temporary))
            (directory / "parts" / "mac" / "terminal.tar.gz").unlink()
            with self.assertRaises(release.ReleaseError) as caught:
                release.check_imported_terminal(directory, terminals, release.MAC)
            self.assertIn("is missing", str(caught.exception))

    def test_a_platform_that_was_never_imported_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory, _ = self.imported(Path(temporary))
            with self.assertRaises(release.ReleaseError) as caught:
                release.check_imported_terminal(directory, {}, release.MAC)
            self.assertIn("was not built or imported", str(caught.exception))


def release_checkout(base: Path, *, targets: tuple[str, ...] = (release.MAC,), **journal_fields) -> tuple[Path, str]:
    """A throwaway git checkout holding a plan journal, so `build`/`assemble`/`verify` run their commit and clean-tree
    checks for real. Returns (the release directory, the commit)."""
    root = base / "checkout"
    root.mkdir()
    (root / ".gitignore").write_text("/RELEASE/\n", encoding="utf-8")
    (root / "README.txt").write_text("a stand-in checkout\n", encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.email=test@example.com", "-c", "user.name=Test"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "."], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "a stand-in commit"], check=True)
    commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, text=True,
                            capture_output=True).stdout.strip()
    directory = root / "RELEASE" / "0.2.12.2"
    directory.mkdir(parents=True)
    journal = {"schema_version": 1, "release": "0.2.12.2", "commit": commit,
               "component_version": "0.1.60", "product_version": "0.2.12.2", "host_version": "0.2.12.2",
               "targets": list(targets), "steps": {"plan": {"ok": True}}}
    journal.update(journal_fields)
    _write_json(directory / "release.json", journal)
    return directory, commit


def build_mac_release(root_of_release: Path, parts: Path | None) -> None:
    args = SimpleNamespace(release=None, root=str(root_of_release), only=None, rebuild=False,
                           rebuild_host=False, cuda_root=None, windows_parts=None,
                           mac_parts=None if parts is None else str(parts))
    release.cmd_build(args)


class MacOnlyBuild(unittest.TestCase):
    """`release.py build` end to end for a release that carries only macOS: no CUDA, no npm, no Mac."""

    def make_release(self, base: Path, **overrides) -> tuple[Path, str]:
        return release_checkout(base, **overrides)

    def build(self, root_of_release: Path, parts: Path | None) -> None:
        build_mac_release(root_of_release, parts)

    def test_the_mac_parts_are_imported_and_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory, commit = self.make_release(base)
            self.build(directory.parent.parent, mac_parts(base, commit=commit, engines=True))
            journal = json.loads((directory / "release.json").read_text(encoding="utf-8"))
            build = journal["steps"]["build"]
            self.assertTrue(build["ok"])
            self.assertEqual(build["targets"], [release.MAC])
            self.assertEqual(sorted(build["components"]), ["dsv41", "qwen", "terminal"])
            terminal = build["components"]["terminal"][release.MAC]
            self.assertTrue((directory / terminal["unpack"] / "bin" / "deepstrata").is_file())
            self.assertTrue(terminal["smoke_ok"])
            self.assertEqual(build["mac_parts"]["components"], ["dsv41", "qwen", "terminal"])
            for name in ("qwen", "dsv41"):
                self.assertTrue((directory / "logs" / f"verify-mac-{name}.log").is_file())

    def test_a_second_build_reuses_the_journal_without_the_parts_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory, commit = self.make_release(base)
            root = directory.parent.parent
            self.build(root, mac_parts(base, commit=commit, engines=True))
            before = json.loads((directory / "release.json").read_text(encoding="utf-8"))["steps"]["build"]
            self.build(root, None)                     # the journal already names the imported parts
            after = json.loads((directory / "release.json").read_text(encoding="utf-8"))["steps"]["build"]
            self.assertEqual(sorted(after["components"]), sorted(before["components"]))
            self.assertEqual(after["components"]["terminal"][release.MAC]["sha256"],
                             before["components"]["terminal"][release.MAC]["sha256"])

    def test_a_mac_target_without_parts_is_refused_with_what_to_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory, _ = self.make_release(base)
            with self.assertRaises(release.ReleaseError) as caught:
                self.build(directory.parent.parent, None)
            message = str(caught.exception)
            self.assertIn("the macOS parts are missing", message)
            self.assertIn("build_mac_parts.sh", message)
            self.assertIn("--mac-parts", message)

    def test_parts_from_another_commit_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory, _ = self.make_release(base)
            with self.assertRaises(release.ReleaseError) as caught:
                self.build(directory.parent.parent, mac_parts(base, commit="b" * 40, engines=True))
            self.assertIn("was built from", str(caught.exception))

    def test_a_directory_with_uncommitted_changes_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            directory, commit = self.make_release(base)
            (directory.parent.parent / "README.txt").write_text("changed\n", encoding="utf-8")
            with self.assertRaises(release.ReleaseError) as caught:
                self.build(directory.parent.parent, mac_parts(base, commit=commit))
            self.assertIn("uncommitted changes", str(caught.exception))


class CommandLine(unittest.TestCase):
    """The flags an operator types: the three platforms and both parts hand-offs must be accepted by the parser."""

    def parse(self, *argv):
        parser = release.build_parser()
        return parser.parse_args(list(argv))

    def test_every_target_is_accepted_and_macos_is_the_default_release(self):
        for target in release.TARGETS:
            with self.subTest(target):
                self.assertEqual(self.parse("plan", "--target", target).target, [target])
        self.assertEqual(self.parse("plan").target, None)
        with self.assertRaises(SystemExit):
            self.parse("plan", "--target", "linux-aarch64")

    def test_both_parts_hand_offs_take_a_folder_or_a_zip(self):
        parsed = self.parse("build", "--windows-parts", "a.zip", "--mac-parts", "b")
        self.assertEqual(parsed.windows_parts, Path("a.zip"))
        self.assertEqual(parsed.mac_parts, Path("b"))
        self.assertIsNone(self.parse("build").mac_parts)

    def test_the_help_names_the_third_platform(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.suppress(SystemExit):
            release.main(["plan", "--help"])
        self.assertIn("macos-arm64", output.getvalue())


class ManifestWriter(unittest.TestCase):
    """`packaging/mac/parts_manifest.py` and `release.py` are two sides of one contract: this writes a folder's manifest
    with the script the Mac build calls last, then imports that folder with the release tool."""

    script = ROOT / "packaging" / "mac" / "parts_manifest.py"

    def release_directory(self, base: Path) -> Path:
        directory = base / "RELEASE" / "0.2.12.2"
        directory.mkdir(parents=True)
        return directory

    def write(self, parts: Path, *extra, **overrides) -> subprocess.CompletedProcess:
        args = {"--commit": COMMIT, "--component": "0.1.60", "--product": "0.2.12.2", "--host": "0.2.12.2"}
        args.update(overrides)
        command = [sys.executable, str(self.script), "--out", str(parts)]
        for name, value in args.items():
            command += [name, value]
        command += ["--toolchain", "clang=Apple clang version 17.0.0", *extra]
        return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)

    def parts_with_terminal(self, base: Path) -> Path:
        parts = base / "build-mac-parts"
        archive = parts / "terminal" / "strata-terminal-0.1.60-darwin-arm64.tar.gz"
        archive.parent.mkdir(parents=True, exist_ok=True)
        _tar_gz(archive, {"bundle/bin/deepstrata": (b"#!/bin/sh\n", 0o755),
                          "bundle/host/dist/index.html": (b"<!doctype html>\n", 0o644),
                          "bundle/node/bin/node": (b"\xcf\xfa\xed\xfe", 0o755)})
        _write_json(parts / "smoke" / "smoke-terminal.json",
                    {"ok": True, "archive": archive.name,
                     "checks": {name: {"ok": True} for name in SMOKE_CHECKS}})
        return parts

    def test_the_manifest_it_writes_is_the_one_release_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            result = self.write(parts, "--terminal", "terminal/strata-terminal-0.1.60-darwin-arm64.tar.gz",
                                "--smoke", "smoke/smoke-terminal.json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((parts / "mac-parts.json").is_file())
            self.assertFalse((parts / "mac-parts.json.tmp").exists())      # written through a temporary name, then renamed
            manifest = release._read_parts_manifest(parts, release.MAC)
            self.assertEqual(manifest["platform"], release.MAC)
            self.assertEqual(list(manifest["components"]), ["terminal"])
            self.assertEqual(manifest["toolchain"], {"clang": "Apple clang version 17.0.0"})
            release._check_parts(manifest, mac_journal(), parts, release.MAC)
            imported, _ = release._import_parts(self.release_directory(base), mac_journal(), parts,
                                                ["terminal"], release.MAC, rebuild=True)
            self.assertTrue(imported["terminal"]["smoke_ok"])

    def test_engine_folders_are_hashed_and_imported(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            folder = make_engine_mac(base, parts / "engines", entry="strata")
            result = self.write(parts, "--terminal", "terminal/strata-terminal-0.1.60-darwin-arm64.tar.gz",
                                "--smoke", "smoke/smoke-terminal.json",
                                "--engine", f"qwen=engines/{folder.name}")
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = release._read_parts_manifest(parts, release.MAC)
            self.assertEqual(manifest["components"]["qwen"]["manifest_sha256"],
                             _sha256(folder / "engine-component.json"))
            imported, record = release._import_parts(self.release_directory(base), mac_journal(), parts,
                                                     ["terminal", "qwen"], release.MAC, rebuild=True)
            self.assertEqual(sorted(imported), ["qwen", "terminal"])
            self.assertEqual(record["components"], ["qwen", "terminal"])

    def test_a_folder_with_nothing_in_it_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            parts = self.parts_with_terminal(Path(temporary))
            result = self.write(parts)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("nothing to record", result.stderr)
            self.assertFalse((parts / "mac-parts.json").exists())

    def test_a_missing_file_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            result = self.write(parts, "--terminal", "terminal/not-there.tar.gz")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not a file", result.stderr)
            self.assertFalse((parts / "mac-parts.json").exists(), "a refused run must write no manifest")

    def test_an_engine_folder_without_a_manifest_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            (parts / "engines" / "half-built").mkdir(parents=True)
            result = self.write(parts, "--engine", "qwen=engines/half-built")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("engine-component.json", result.stderr)

    def test_an_engine_name_it_does_not_know_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            result = self.write(parts, "--engine", "qwen3=engines/x")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--engine expects", result.stderr)

    def test_a_dirty_tree_is_recorded_and_then_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            result = self.write(parts, "--terminal", "terminal/strata-terminal-0.1.60-darwin-arm64.tar.gz", "--dirty")
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = release._read_parts_manifest(parts, release.MAC)
            self.assertTrue(manifest["dirty"])
            with self.assertRaises(release.ReleaseError) as caught:
                release._check_parts(manifest, mac_journal(), parts, release.MAC)
            self.assertIn("uncommitted tree", str(caught.exception))

    def test_another_component_version_is_refused_by_the_release_tool(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            parts = self.parts_with_terminal(base)
            self.assertEqual(self.write(parts, "--terminal", "terminal/strata-terminal-0.1.60-darwin-arm64.tar.gz",
                                        "--component", "0.1.59").returncode, 0)
            manifest = release._read_parts_manifest(parts, release.MAC)
            with self.assertRaises(release.ReleaseError) as caught:
                release._check_parts(manifest, mac_journal(), parts, release.MAC)
            self.assertIn("carries component", str(caught.exception))


class MacReleaseChain(unittest.TestCase):
    """The whole release side for a macOS-only release, end to end and offline: a plan journal, `build` importing a Mac
    parts folder, `assemble` with a throwaway key, and `verify`. Nothing here touches GitHub, a GPU or the network."""

    def make_key(self, base: Path) -> tuple[Path, str]:
        keys = base / "keys"
        subprocess.run([sys.executable, "packaging/feed_tool.py", "keygen", "--out", str(keys)],
                       cwd=str(ROOT), check=True, capture_output=True, text=True)
        public = json.loads((keys / "feed-key.pub.json").read_text(encoding="utf-8"))
        return keys / "feed-key.json", public["keys"][0]["key_id"]

    @contextlib.contextmanager
    def trusted(self, keys: Path):
        """Trust the throwaway key for this run: `assemble` and `verify` otherwise insist on the key the build ships."""
        trusted = release.signing.load_trusted_keys(str(keys / "feed-key.pub.json"))
        with unittest.mock.patch.object(release.signing, "load_trusted_keys", return_value=trusted):
            yield

    def test_a_macos_only_release_assembles_into_a_feed_that_serves_the_mac(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key_path, key_id = self.make_key(base)
            directory, commit = release_checkout(base, sequence=1, key_id=key_id)
            root = directory.parent.parent
            build_mac_release(root, mac_parts(base, commit=commit, engines=True))

            with self.trusted(base / "keys"):
                release.cmd_assemble(SimpleNamespace(release=None, root=str(root), key=str(key_path),
                                                     expires_days=None))
            feed = directory / "feed"
            self.assertTrue((feed / "strata-feed.json").is_file())
            self.assertTrue((feed / "strata-feed.json.sig").is_file())
            self.assertTrue((feed / "strata-control-0.1.60-macos-arm64.tar.gz").is_file())
            self.assertTrue((feed / "strata-engine-0.1.60-macos-arm64-metal.tar.gz").is_file())
            self.assertTrue((feed / "strata-dsv41-0.1.60-macos-arm64-metal.tar.gz").is_file())
            manifest = (feed / "install-manifest.txt").read_text(encoding="utf-8")
            self.assertIn("macos-arm64", manifest)
            journal = json.loads((directory / "release.json").read_text(encoding="utf-8"))
            self.assertTrue(journal["steps"]["assemble"]["ok"])

            with self.trusted(base / "keys"):
                release.cmd_verify(SimpleNamespace(release=None, root=str(root), skip_smoke=True))
            report = json.loads((directory / "verify.json").read_text(encoding="utf-8"))
            self.assertTrue(report["ok"])
            self.assertIn("macOS Terminal archive: the one the macOS build smoke-tested (a macOS run is not repeated here)",
                          report["checks"])
            self.assertIn("select_engine picks the metal engine on macos-arm64", report["checks"])

    def test_verify_refuses_a_mac_archive_replaced_after_assemble(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            key_path, key_id = self.make_key(base)
            directory, commit = release_checkout(base, sequence=1, key_id=key_id)
            root = directory.parent.parent
            build_mac_release(root, mac_parts(base, commit=commit, engines=True))
            with self.trusted(base / "keys"):
                release.cmd_assemble(SimpleNamespace(release=None, root=str(root), key=str(key_path),
                                                     expires_days=None))
            journal = json.loads((directory / "release.json").read_text(encoding="utf-8"))
            archive = directory / journal["steps"]["build"]["components"]["terminal"][release.MAC]["archive"]
            archive.write_bytes(archive.read_bytes() + b"a late edit")
            with self.trusted(base / "keys"):
                with self.assertRaises(release.ReleaseError) as caught:
                    release.cmd_verify(SimpleNamespace(release=None, root=str(root), skip_smoke=True))
            self.assertIn("changed after it was imported", str(caught.exception))


class MacPackageLayout(unittest.TestCase):
    """The stage `packaging/build_mac_parts.sh` builds for the Qwen engine, with the tree's real kernel sources and
    shipped tables (only the two binaries are stand-ins). It must package and verify, and the package must carry
    everything `metal/src/resource_path.h` says the engine reads at start."""

    def test_the_staged_layout_packages_and_carries_the_runtime_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            stage = base / "stage"
            (stage / "metal").mkdir(parents=True)
            for binary in ("strata", "strata-vision"):
                (stage / binary).write_bytes(b"\xcf\xfa\xed\xfe")
                (stage / binary).chmod(0o755)
            (stage / "LICENSE").write_text("DeepStrata\n", encoding="utf-8")
            shutil.copytree(ROOT / "metal" / "kernels", stage / "metal" / "kernels")
            shutil.copytree(ROOT / "metal" / "src" / "engine" / "msl", stage / "metal" / "engine-msl")
            shutil.copy2(ROOT / "third_party" / "ggml" / "ggml-common.h", stage / "metal" / "ggml-common.h")
            (stage / "metal" / "data").mkdir()
            for name in ("expert-profile.bin", "draft_vocab.bin"):
                shutil.copy2(ROOT / "data" / name, stage / "metal" / "data" / name)
            subprocess.run([sys.executable, "packaging/package_engine.py", "--src", str(stage), "--backend", "metal",
                            "--version", "0.1.60", "--out", str(base / "engines"), "--platform", "macos-arm64",
                            "--arch", "apple-gpu", "--entrypoint", "strata", "--build-id", "abcdef123456",
                            "--include", "metal/**"],
                           cwd=str(ROOT), check=True, capture_output=True, text=True)
            folder = base / "engines" / "0.1.60-macos-arm64-metal"
            subprocess.run([sys.executable, "packaging/package_engine.py", "--verify", str(folder)],
                           cwd=str(ROOT), check=True, capture_output=True, text=True)
            files = {path.relative_to(folder).as_posix() for path in folder.rglob("*") if path.is_file()}
            for wanted in ("strata", "strata-vision", "metal/ggml-common.h",
                           "metal/data/expert-profile.bin", "metal/data/draft_vocab.bin",
                           "metal/kernels/quant.metal", "metal/engine-msl/mtp.metal"):
                with self.subTest(wanted):
                    self.assertIn(wanted, files)

    def test_a_stage_without_the_picture_reader_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            stage = base / "stage"
            stage.mkdir()
            (stage / "strata").write_bytes(b"\xcf\xfa\xed\xfe")
            result = subprocess.run([sys.executable, "packaging/package_engine.py", "--src", str(stage),
                                     "--backend", "metal", "--version", "0.1.60", "--out", str(base / "engines"),
                                     "--platform", "macos-arm64", "--arch", "apple-gpu",
                                     "--entrypoint", "strata"],
                                    cwd=str(ROOT), capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("strata-vision", result.stdout + result.stderr)


class HandoffScripts(unittest.TestCase):
    """The parts builders themselves: the flags release.py's messages tell an operator to pass must exist, and the macOS
    one must refuse to run anywhere but an Apple Silicon Mac. Its build is only validated on real hardware."""

    script = ROOT / "packaging" / "build_mac_parts.sh"
    workflow = ROOT / ".github" / "workflows" / "mac-parts.yml"

    def test_the_script_is_there_and_executable(self):
        self.assertTrue(self.script.is_file())
        self.assertTrue(os.access(self.script, os.X_OK), "packaging/build_mac_parts.sh must be executable")

    def test_the_shell_syntax_is_valid(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        result = subprocess.run([bash, "-n", str(self.script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_help_lists_every_flag_the_release_can_ask_for(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        result = subprocess.run([bash, str(self.script), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        for flag in ("--out", "--commit", "--version", "--only", "--skip-host", "--skip-smoke", "--allow-dirty"):
            with self.subTest(flag):
                self.assertIn(flag, result.stdout)
        self.assertIn(release.PARTS_HANDOFFS[release.MAC]["manifest"], result.stdout)

    def test_an_unknown_flag_is_refused(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        result = subprocess.run([bash, str(self.script), "--not-a-flag"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)

    def test_the_script_names_the_manifest_and_takes_the_flags_release_mentions(self):
        text = self.script.read_text(encoding="utf-8")
        self.assertIn(release.PARTS_HANDOFFS[release.MAC]["manifest"], text)
        for flag in (release.PARTS_HANDOFFS[release.MAC]["dirty_flag"],
                     release.PARTS_HANDOFFS[release.MAC]["skip_smoke"],
                     release.PARTS_HANDOFFS[release.MAC]["only_flag"]):
            with self.subTest(flag):
                self.assertIn(flag, text)
        # the manifest itself is written by a script of its own, so the schema can be tested on any machine
        writer = ROOT / "packaging" / "mac" / "parts_manifest.py"
        self.assertIn("parts_manifest.py", text)
        writer_text = writer.read_text(encoding="utf-8")
        self.assertIn(f'PLATFORM = "{release.MAC}"', writer_text)
        self.assertIn(f"SCHEMA = {release.PARTS_SCHEMA}", writer_text)
        self.assertIn(f'MANIFEST = "{release.PARTS_HANDOFFS[release.MAC]["manifest"]}"', writer_text)

    def test_it_refuses_to_build_off_an_apple_silicon_mac(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is not installed")
        if sys.platform == "darwin":
            # on a Mac the script would really build: its live run is the mac-parts workflow, not this test
            self.skipTest("this check only means something away from macOS")
        result = subprocess.run([bash, str(self.script)], capture_output=True, text=True, timeout=120)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Apple Silicon Mac", result.stderr)

    def test_the_workflow_runs_the_script_on_a_hosted_mac(self):
        text = self.workflow.read_text(encoding="utf-8")
        self.assertIn("packaging/build_mac_parts.sh", text)
        self.assertIn("macos-26", text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("build-mac-parts", text)
        # the pins the Metal build check uses: metal-cpp and llama.cpp fetched outside the checkout the script requires clean
        self.assertIn("METAL_CPP_SHA256", text)
        self.assertIn("LLAMA_CPP_COMMIT", text)
        self.assertIn("RUNNER_TEMP", text)


def engine_package(feed: Path, platform: str, *, version: str = "0.1.60",
                   archs=("sm_86", "sm_89", "sm_120", "compute_120")):
    """An EnginePackage whose archive exists in `feed`, so the feed entry and the file can be checked."""
    name = f"strata-engine-{version}-{platform}-cuda-cuda13.tar.gz"
    payload = f"payload for {platform}".encode()
    (feed / name).write_bytes(payload)
    return release.feedmod.EnginePackage(
        version=version, platform=platform, backend="cuda", variant="cuda13", archs=tuple(archs),
        min_driver={"nvidia": "580.65"}, min_runtime=None, build_id="b" * 12, url=name, format="tar.gz",
        size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), manifest_sha256="0" * 64,
        installed_bytes=len(payload))


def mac_engine_package(feed: Path, *, version: str = "0.1.60", archs=("apple-gpu",)):
    """The macOS entry: a Metal package has no variant, no NVIDIA driver to pin and one arch, `apple-gpu`."""
    name = f"strata-engine-{version}-{release.MAC}-metal.tar.gz"
    payload = b"payload for the Mac"
    (feed / name).write_bytes(payload)
    return release.feedmod.EnginePackage(
        version=version, platform=release.MAC, backend="metal", variant="", archs=tuple(archs),
        min_driver={}, min_runtime=None, build_id="b" * 12, url=name, format="tar.gz",
        size=len(payload), sha256=hashlib.sha256(payload).hexdigest(), manifest_sha256="0" * 64,
        installed_bytes=len(payload))


class FeedChecks(unittest.TestCase):
    """What verify asks of a feed that carries more than one platform."""

    def test_an_engine_entry_is_checked_per_platform(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            package = engine_package(feed, release.LINUX)
            parsed = SimpleNamespace(engine=[package], dsv41=[])
            release._verify_engine_entry(parsed, "engine", feed, release.LINUX, "0.1.60")
            with self.assertRaises(release.ReleaseError) as caught:
                release._verify_engine_entry(parsed, "engine", feed, release.WINDOWS, "0.1.60")
            self.assertIn("0 engine packages for windows-x86_64", str(caught.exception))

    def test_an_engine_without_the_release_arches_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            package = engine_package(feed, release.WINDOWS, archs=("sm_120",))
            parsed = SimpleNamespace(engine=[package], dsv41=[])
            with self.assertRaises(release.ReleaseError) as caught:
                release._verify_engine_entry(parsed, "engine", feed, release.WINDOWS, "0.1.60")
            self.assertIn("do not cover", str(caught.exception))

    def control(self, platform: str, *, version: str = "0.1.60", fmt: str | None = None):
        return SimpleNamespace(platform=platform, version=version, format=fmt or release.control_format(platform),
                               url=f"strata-control-{version}-{platform}", size=1, sha256="0" * 64)

    def test_one_control_package_per_platform(self):
        parsed = SimpleNamespace(control=[self.control(release.LINUX), self.control(release.WINDOWS)])
        release._verify_control_entries(parsed, "0.1.60", (release.LINUX, release.WINDOWS))
        for bad, targets in ((parsed, (release.LINUX,)),                                    # a missing platform
                             (SimpleNamespace(control=[self.control(release.LINUX)]), (release.LINUX, release.WINDOWS)),
                             (SimpleNamespace(control=[self.control(release.LINUX), self.control(release.LINUX)]),
                              (release.LINUX,)),
                             (SimpleNamespace(control=[self.control(release.WINDOWS, fmt="tar.gz"),
                                                       self.control(release.LINUX)]), (release.LINUX, release.WINDOWS)),
                             (SimpleNamespace(control=[self.control(release.LINUX, version="0.1.59")]), (release.LINUX,))):
            with self.subTest(targets), self.assertRaises(release.ReleaseError):
                release._verify_control_entries(bad, "0.1.60", targets)

    def test_selection_serves_each_platform_its_own_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            packages = [engine_package(feed, release.LINUX), engine_package(feed, release.WINDOWS)]
            lines = release._selection_checks(SimpleNamespace(engine=packages, dsv41=[]),
                                              (release.LINUX, release.WINDOWS))
            self.assertEqual(len(lines), 1)
            with self.assertRaises(release.ReleaseError):
                release._selection_checks(SimpleNamespace(engine=packages[:1], dsv41=[]),
                                          (release.LINUX, release.WINDOWS))

    def test_the_mac_entry_must_be_metal(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            package = mac_engine_package(feed)
            release._verify_engine_entry(SimpleNamespace(engine=[package], dsv41=[]),
                                         "engine", feed, release.MAC, "0.1.60")
            cuda_as_mac = engine_package(feed, release.MAC)
            with self.assertRaises(release.ReleaseError) as caught:
                release._verify_engine_entry(SimpleNamespace(engine=[cuda_as_mac], dsv41=[]),
                                             "engine", feed, release.MAC, "0.1.60")
            self.assertIn("unexpected package cuda/cuda13", str(caught.exception))
            missing_arch = mac_engine_package(feed, archs=())
            with self.assertRaises(release.ReleaseError) as caught:
                release._verify_engine_entry(SimpleNamespace(engine=[missing_arch], dsv41=[]),
                                             "engine", feed, release.MAC, "0.1.60")
            self.assertIn("apple-gpu", str(caught.exception))

    def test_selection_serves_the_mac_its_metal_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            feed = Path(temporary)
            packages = [engine_package(feed, release.LINUX), mac_engine_package(feed)]
            lines = release._selection_checks(SimpleNamespace(engine=packages, dsv41=[]),
                                              (release.LINUX, release.MAC))
            self.assertEqual(len(lines), 2)          # one line for the CUDA target, one for the Mac
            with self.assertRaises(release.ReleaseError) as caught:
                release._selection_checks(SimpleNamespace(engine=packages[:1], dsv41=[]),
                                          (release.LINUX, release.MAC))
            self.assertIn("macos-arm64", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
