"""The keys shipped with a release build (strata_control/update_keys.json): the release feed key is there,
the tester / local-test keys are not (they come with STRATA_UPDATE_TRUSTED_KEYS), and a feed is verified against them."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from strata_control import feed as feedmod, signing
from tests.terminal import update_fixture as fx

ROOT = Path(__file__).resolve().parents[2]
RELEASE_KEY_ID = "9badd1d2b37076b5"


class ShippedKeys(unittest.TestCase):
    def test_the_release_feed_key_is_shipped_and_valid(self):
        with mock.patch.dict(os.environ, {signing.ENV_EXTRA_KEYS: ""}):
            keys = signing.load_trusted_keys()
        self.assertEqual(list(keys), [RELEASE_KEY_ID])           # only the release key: no tester or local-test key in a release build
        self.assertEqual(signing.key_id_of(keys[RELEASE_KEY_ID]), RELEASE_KEY_ID)

    def test_the_app_updater_key_is_gone_with_the_desktop_app(self):
        """The Tauri app key lived in update_keys.json as app_minisign_public; with no desktop app, no such key is shipped."""
        shipped = json.loads((ROOT / "strata_control" / "update_keys.json").read_text(encoding="utf-8-sig"))
        self.assertNotIn("app_minisign_public", shipped)
        self.assertFalse(hasattr(signing, "app_public_key"))

    def test_a_feed_signed_by_another_key_is_refused_and_a_tester_key_works_through_the_override(self):
        other = fx.make_key()
        with tempfile.TemporaryDirectory() as t:
            extra = Path(t) / "tester-keys.json"
            extra.write_text(json.dumps({"keys": [signing.public_part(other)]}))
            with mock.patch.dict(os.environ, {signing.ENV_EXTRA_KEYS: ""}):
                self.assertNotIn(other["key_id"], signing.load_trusted_keys())          # a tester key is not part of a release build
            with mock.patch.dict(os.environ, {signing.ENV_EXTRA_KEYS: str(extra)}):
                keys = signing.load_trusted_keys()
            self.assertIn(other["key_id"], keys)                                         # private tester builds: their key via the override
            self.assertIn(RELEASE_KEY_ID, keys)                                          # ... beside the release key


if __name__ == "__main__":
    unittest.main()
