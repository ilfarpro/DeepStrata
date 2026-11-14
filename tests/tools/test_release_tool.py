"""The release tool's offline checks: versions, notes, journals, tokens and feed assets. Nothing here touches GitHub,
a GPU or the network."""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
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
                 smoke_ok: bool = True, schema: int = 1, engines: bool = False) -> Path:
    """A folder shaped like the one build_windows.ps1 / build_mac_parts.sh write: the Terminal archive, its smoke
    report, and the `<platform>-parts.json` manifest. `platform` may name any platform, also one the builder
    would never write the folder for (what a mixed-up hand-off looks like)."""
    handoff = release.PARTS_HANDOFFS[platform]
    is_windows = platform == release.WINDOWS
    parts = base / f"build-{handoff['short']}-parts"
    fmt = "zip" if is_windows else "tar.gz"
    suffix = "-windows-x86_64" if is_windows else "-darwin-arm64"
    archive = parts / "terminal" / f"strata-terminal-{component}{suffix}.{fmt}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if is_windows:
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("bundle/bin/deepstrata.cmd", "@echo off\r\n")
            zf.writestr("bundle/host/dist/index.html", "<!doctype html>\n")
            zf.writestr("bundle/node/node.exe", b"MZ")
            zf.writestr("bundle/strata.py", "print('strata')\n")
    else:
        _tar_gz(archive, {"bundle/bin/deepstrata": (b"#!/bin/sh\n", 0o755),
                          "bundle/host/dist/index.html": (b"<!doctype html>\n", 0o644),
                          "bundle/node/bin/node": (b"\xcf\xfa\xed\xfe", 0o755),
                          "bundle/strata.py": (b"print('strata')\n", 0o644)})
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
