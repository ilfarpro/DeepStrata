"""Unsloth's UD-Q4_K_XL DeepSeek-V4.1-Flash set (2026-11-06): pins against the Hub file list, the catalog entry, the fit numbers, and that the UD-Q8_K_XL plan did not move.
Run: python -m pytest tools/test_setup_udq4.py"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("setup_udq4", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

from strata_control import dsv41_fit, dsv41_sets  # noqa: E402

TOTAL = 500_707_576_573      # the nine pinned shards (the same nine files tools/dsv41/download_gguf.json has)
ENGRAM = 202_758_032_592     # shard 8: the set's own MXFP8 n-gram table, byte-identical to the Q8 set's shard 8


class Pins(unittest.TestCase):
    def setUp(self):
        self.md = setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]["UD-Q4_K_XL"]

    def test_nine_shards_are_pinned_in_the_repository_and_in_the_family(self):
        self.assertEqual(self.md["recipe"], "ud")
        self.assertEqual(len(self.md["shards"]), 9)
        for n in self.md["shards"]:
            self.assertIn(n, setup.DSV41_FILES)
            self.assertIn(n, setup.DSV41_ALL_FILES)
        self.assertIs(setup.REPO_PINS["unsloth/DeepSeek-V4.1-Flash-1017-GGUF"], setup.DSV41_FILES)
        self.assertEqual(sum(setup.DSV41_FILES[n][0] for n in self.md["shards"]), TOTAL)
        self.assertEqual(setup.DSV41_FILES[self.md["shards"][7]][0], ENGRAM)

    def test_the_pins_are_the_files_the_hub_download_list_has(self):
        want = json.loads((ROOT / "tools" / "dsv41" / "download_gguf.json").read_text(encoding="utf-8"))["quants"]["UD-Q4_K_XL"]["files"]
        for n in self.md["shards"]:
            self.assertEqual(setup.DSV41_FILES[n], tuple(want[f"UD-Q4_K_XL/{n}"]), n)

    def test_it_shares_its_metadata_and_engram_bytes_with_the_q8_set(self):
        q8 = setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]["UD-Q8_K_XL"]
        for i in (0, 7):     # shard 1 (the metadata) and shard 8 (the n-gram table) are the same bytes in both sets
            self.assertEqual(setup.DSV41_FILES[self.md["shards"][i]], setup.DSV41_FILES[q8["shards"][i]])

    def test_no_ista_pin_moved(self):
        self.assertEqual(sum(setup.DSV41_FILES[n][0] for n in setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]["UD-Q8_K_XL"]["shards"]),
                         508_376_763_197)


class Catalog(unittest.TestCase):
    def test_it_is_offered_before_the_q8_set(self):
        names = list(setup.MULTI_FILE_FAMILIES["deepseek41"]["models"])
        self.assertLess(names.index("UD-Q4_K_XL"), names.index("UD-Q8_K_XL"))

    def test_the_entry_names_its_own_folder_at_the_repository(self):
        md = setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]["UD-Q4_K_XL"]
        self.assertEqual(md["subdir"], "UD-Q4_K_XL")
        self.assertIn("UD-Q4_K_XL/", md["hf"])
        self.assertEqual(md["shared"], [])
        self.assertIn("not measured", md["about"].lower())

    def test_the_app_does_not_claim_the_files_it_cannot_pack(self):     # recipe "ud" stays out of the "I already have it" preview (strata_control/dsv41_sets.py)
        self.assertNotIn("UD-Q4_K_XL", dsv41_sets.tables()["sets"])


class Fit(unittest.TestCase):
    CARDS = [{"name": "R9700", "vram_gb": 32.0}] * 4

    def test_the_sizes_are_the_pinned_ones(self):
        p = dsv41_fit.plan(self.CARDS, 251.0, 600.0, model="UD-Q4_K_XL")
        self.assertEqual((p["download_gb"], p["weights_gb"], p["experts_gb"], p["dense_gb"], p["engram_gb"]), (500.7, 297.9, 288.8, 9.2, 202.8))
        self.assertEqual(p["disk_need_gb"], 506.7)
        self.assertEqual(p["verdict"], "fits")            # 4 x R9700 with 251 GB: the 9.2 GB of dense weights leave the cards room

    def test_it_says_it_was_not_timed_instead_of_borrowing_the_q8_line(self):
        p = dsv41_fit.plan(self.CARDS, 251.0, None, model="UD-Q4_K_XL")
        words = p["words"]
        self.assertIn("a 501 GB download", words)
        self.assertIn("has not been timed on a computer like this one yet", words)
        self.assertNotIn("Measured on 4 x Radeon", words)
        self.assertEqual(p["measured"], "")

    def test_the_q8_plan_did_not_move(self):
        p = dsv41_fit.plan(self.CARDS, 251.0, 900.0, model="UD-Q8_K_XL")
        self.assertIn("(UD-Q8_K_XL): a 508 GB download", p["words"])
        self.assertIn("Measured on 4 x Radeon AI PRO R9700", p["words"])
        self.assertNotIn("has not been timed", p["words"])
        self.assertEqual(p["measured"], dsv41_fit.MEASURED)
        p2 = dsv41_fit.plan(self.CARDS, 251.0, 900.0)     # no name: the 508 GB set, as it was
        self.assertEqual(p["words"], p2["words"])

    def test_a_name_that_is_not_a_set_keeps_the_q8_figures(self):
        self.assertEqual(dsv41_fit.plan(None, None, None, model="SOMETHING")["download_gb"], 508.4)


if __name__ == "__main__":
    unittest.main()
