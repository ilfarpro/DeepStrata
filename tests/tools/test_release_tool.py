"""The release tool's offline checks: versions, notes, journals, tokens and feed assets. Nothing here touches GitHub,
a GPU or the network."""

import importlib.util
import os
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
