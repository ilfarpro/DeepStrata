"""Unsloth's four 2- and 3-bit XL DeepSeek-V4.1-Flash sets (2026-11-10): their pins, their own n-gram table in the last shard, the
fit numbers, the 0.1.60 engine-version gate for the mixed expert layers, and the app's file recognition.
Run: python -m pytest tests/tools/test_setup_udtable.py"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("setup_udtable", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

from strata_control import dsv41_fit, dsv41_sets  # noqa: E402

# name: (model bytes = every shard except the last, the set's own table bytes, the number of shards, the first shard's bytes)
SETS = {
    "UD-Q2_K_XL": (199_621_523_044, 135_172_021_792, 7, 5_780_868),
    "UD-IQ3_XXS": (233_451_798_175, 161_284_798_692, 7, 5_780_927),
    "UD-IQ3_S": (262_346_221_302, 161_284_798_692, 8, 5_780_886),
    "UD-Q3_K_XL": (282_310_606_623, 202_758_032_592, 8, 5_780_927),
}


def md(model):
    return setup.MULTI_FILE_FAMILIES["deepseek41"]["models"][model]


class Pins(unittest.TestCase):
    def test_every_set_has_all_its_files_pinned_once(self):
        self.assertIs(setup.REPO_PINS["unsloth/DeepSeek-V4.1-Flash-1017-GGUF"], setup.DSV41_FILES)
        for model, (model_bytes, table_bytes, n, shard1) in SETS.items():
            m = md(model)
            self.assertEqual(m["recipe"], "udtable")
            names = [*m["shards"], m["engram_shard"]]
            self.assertEqual(len(names), n, model)
            self.assertEqual(len(set(names)), n, model)                 # the table is not also one of --shards
            self.assertEqual(sum(setup.DSV41_FILES[x][0] for x in m["shards"]), model_bytes, model)
            self.assertEqual(setup.DSV41_FILES[m["engram_shard"]][0], table_bytes, model)
            self.assertEqual(setup.DSV41_FILES[m["shards"][0]][0], shard1, model)
            for x in names:
                self.assertIn(x, setup.DSV41_ALL_FILES, x)

    def test_the_table_is_the_sets_own_and_the_shared_iq4nl_file_plays_no_part(self):
        for model in SETS:
            m = md(model)
            self.assertEqual(m["shared"], [setup.DSV41_DSPARK], model)
            self.assertNotIn(m["engram_shard"], m["shards"], model)
            self.assertEqual(m["ngram"], {"UD-Q2_K_XL": "Q5_K", "UD-IQ3_XXS": "Q6_K", "UD-IQ3_S": "Q6_K", "UD-Q3_K_XL": "MXFP8"}[model])


class Pack(unittest.TestCase):
    def test_the_table_is_the_engram_not_one_of_the_shards(self):
        for model, (_model_bytes, _table_bytes, n, _shard1) in SETS.items():
            m = md(model)
            shards = [Path(x) for x in m["shards"]]
            table = Path(m["engram_shard"])
            drafter = Path(setup.DSV41_DSPARK)
            cmd = setup.dsv41_pack_cmd(Path("/models") / model, [p.name for p in shards], table, shards[0], drafter, Path("/packs") / model.lower())
            names = cmd[cmd.index("--shards") + 1:cmd.index("--engram")]
            self.assertEqual(names, [p.name for p in shards], model)      # the table is NOT one of --shards
            self.assertEqual(cmd[cmd.index("--engram") + 1], str(table), model)
            self.assertEqual(cmd[cmd.index("--engram-meta") + 1], str(shards[0]), model)
            self.assertEqual(cmd[cmd.index("--dspark-gguf") + 1], str(drafter), model)


class EngineGate(unittest.TestCase):
    def test_the_mixed_layers_need_engine_0_1_60(self):
        for model in SETS:
            self.assertEqual(md(model)["min_engine"], (0, 1, 60), model)
        self.assertIs(setup.dsv41_version_at_least("0.1.59", (0, 1, 60)), False)
        self.assertIs(setup.dsv41_version_at_least("0.1.59.1", (0, 1, 60)), False)
        self.assertIs(setup.dsv41_version_at_least("0.1.60", (0, 1, 60)), True)
        self.assertIs(setup.dsv41_version_at_least("0.2.0", (0, 1, 60)), True)
        self.assertIsNone(setup.dsv41_version_at_least("?", (0, 1, 60)))     # a source build carries the code


class Fit(unittest.TestCase):
    def test_the_numbers_cross_check_with_the_pinned_files(self):
        for model, (model_bytes, table_bytes, _n, _shard1) in SETS.items():
            v = dsv41_fit.ISTA_SETS[model]
            self.assertEqual(v["set_bytes"], model_bytes, model)
            self.assertEqual(v["engram_bytes"], table_bytes, model)
            self.assertEqual(dsv41_fit.ista_download_bytes(model),
                             model_bytes + table_bytes + dsv41_fit.ISTA_DSPARK_BYTES, model)
            self.assertEqual(dsv41_fit.variant_key(model.lower()), model)

    def test_the_plan_tells_the_own_table(self):
        for model, (_model_bytes, table_bytes, _n, _shard1) in SETS.items():
            plan = dsv41_fit.plan([{"index": 0, "name": "NVIDIA GeForce RTX 4080 SUPER", "vendor": "nvidia", "vram_gb": 16.0}],
                                  64.0, 1000.0, 131072, model=model)
            self.assertAlmostEqual(plan["engram_gb"], round(table_bytes / 1e9, 1), places=1, msg=model)
            text = " ".join(plan["lines"])
            self.assertIn("its own last shard", text, model)

    def test_these_sets_are_not_offered_on_a_mac_or_a_spark_yet(self):
        for model in SETS:
            self.assertIn(model, dsv41_fit.MAC_NOT_YET)
            self.assertIn(model, dsv41_fit.AARCH64_NOT_YET)


class Catalog(unittest.TestCase):
    def test_the_app_counts_the_whole_download_including_the_table(self):
        import strata_mcp as M
        s = M.Strata(ROOT)
        for model, (_model_bytes, table_bytes, _n, _shard1) in SETS.items():
            row = s.own_size("deepseek41", model)
            self.assertAlmostEqual(row["download_gb"], (dsv41_fit.ista_download_bytes(model) - 0) / 1e9, places=1, msg=model)
            self.assertGreater(row["download_gb"], table_bytes / 1e9, model)


class ExpectedFiles(unittest.TestCase):
    def test_the_app_download_plan_has_the_own_table_too(self):
        import strata_mcp as M
        s = M.Strata(ROOT)
        for model, (_model_bytes, _table_bytes, n, _shard1) in SETS.items():
            files = M.expected_files(s.setup_module(), "deepseek41", model, Path("/models"), False)
            self.assertEqual(len(files), n + len(md(model).get("shared", [])), model)
            self.assertTrue(any(Path(f["path"]).name == md(model)["engram_shard"] for f in files), model)


class Recognised(unittest.TestCase):
    def test_a_whole_set_with_its_own_table(self):
        for model, (_model_bytes, _table_bytes, n, _shard1) in SETS.items():
            t = dsv41_sets.tables()["sets"][model]
            files = {**t["shards"], **t["engram_alt"]}
            self.assertEqual(len(files), n, model)
            rows = [r for r in dsv41_sets.match(files) if r["model"] == model]
            self.assertTrue(rows and rows[0]["complete"], model)
            self.assertEqual(rows[0]["engram"], next(iter(t["engram_alt"])), model)

    def test_the_shared_iq4nl_file_is_not_this_sets_table(self):
        t = dsv41_sets.tables()
        for model in SETS:
            files = dict(t["sets"][model]["shards"])
            files[setup.DSV41_ISTA_ENGRAM] = t["engram"][setup.DSV41_ISTA_ENGRAM]
            row = next(r for r in dsv41_sets.match(files) if r["model"] == model)
            self.assertIsNone(row["engram"], model)


if __name__ == "__main__":
    unittest.main()
