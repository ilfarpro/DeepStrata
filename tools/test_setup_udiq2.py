"""Unsloth's UD-IQ2_XXS DeepSeek-V4.1-Flash set (2026-11-09): the pins, the pack call with the set's own Q5_K shard as the
Engram, the fit numbers, the engine-version gate for fmt 4, and the app's file recognition.
Run: python -m pytest tools/test_setup_udiq2.py"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("setup_udiq2", ROOT / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

from strata_control import dsv41_fit, dsv41_sets  # noqa: E402

MODEL_BYTES = 165_303_443_003         # shards 1-5 (shard 1 is the 5.78 MB metadata file, no tensors)
TABLE_BYTES = 135_172_021_792         # shard 6: the model's OWN n-gram table, Q5_K (the first fmt-4 set)


def md():
    return setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]["UD-IQ2_XXS"]


class Pins(unittest.TestCase):
    def test_six_files_pinned(self):
        m = md()
        self.assertEqual(m["recipe"], "udiq2")
        names = [*m["shards"], m["engram_shard"]]
        self.assertEqual(len(names), 6)
        self.assertIs(setup.REPO_PINS["unsloth/DeepSeek-V4.1-Flash-1017-GGUF"], setup.DSV41_FILES)
        for n in names:
            self.assertIn(n, setup.DSV41_FILES)
            self.assertIn(n, setup.DSV41_ALL_FILES)
        self.assertEqual(sum(setup.DSV41_FILES[n][0] for n in m["shards"]), MODEL_BYTES)
        self.assertEqual(setup.DSV41_FILES[m["engram_shard"]][0], TABLE_BYTES)
        self.assertEqual(setup.DSV41_FILES[m["shards"][0]][0], 5_780_923)

    def test_shared_is_only_the_drafter_the_shared_iq4nl_engram_plays_no_part(self):
        self.assertEqual(md()["shared"], [setup.DSV41_DSPARK])

    def test_the_other_sets_are_unchanged(self):
        fam = setup.MULTI_FILE_FAMILIES["deepseek41"]["models"]
        self.assertEqual(fam["UD-IQ1_M"]["recipe"], "udlow")
        self.assertEqual(fam["UD-IQ1_M"]["shared"], [setup.DSV41_ISTA_ENGRAM, setup.DSV41_DSPARK])
        self.assertEqual(fam["IST-DAS-2.00bpw"]["recipe"], "ista")


class Pack(unittest.TestCase):
    def test_the_recipe_is_the_ista_one_with_the_own_q5k_shard_as_the_engram(self):
        m = md()
        model = [Path(n) for n in m["shards"]]
        table = Path(m["engram_shard"])
        drafter = Path(setup.DSV41_DSPARK)
        cmd = setup.dsv41_pack_cmd(Path("/models/UD-IQ2_XXS"), [p.name for p in model], table, model[0], drafter, Path("/packs/udiq2"))
        shard_names = cmd[cmd.index("--shards") + 1:cmd.index("--engram")]
        self.assertEqual(shard_names, [p.name for p in model])            # the table is NOT one of --shards
        self.assertEqual(cmd[cmd.index("--engram") + 1], str(table))      # ... it is --engram
        self.assertEqual(cmd[cmd.index("--engram-meta") + 1], str(model[0]))
        self.assertEqual(cmd[cmd.index("--dspark-gguf") + 1], str(drafter))
        self.assertEqual(cmd[cmd.index("--dspark-noise-token") + 1], "128799")


class EngineGate(unittest.TestCase):
    def test_fmt_4_needs_engine_0_1_59(self):
        self.assertIs(setup.dsv41_reads_q5k("0.1.58"), False)
        self.assertIs(setup.dsv41_reads_q5k("0.1.58.1"), False)
        self.assertIs(setup.dsv41_reads_q5k("0.1.59"), True)
        self.assertIs(setup.dsv41_reads_q5k("0.1.59-metal"), True)
        self.assertIs(setup.dsv41_reads_q5k("0.2.0"), True)

    def test_an_unknown_version_is_not_a_refusal(self):       # a source build has no BUILD.json version: it carries the reader
        self.assertIsNone(setup.dsv41_reads_q5k("?"))
        self.assertIsNone(setup.dsv41_reads_q5k(None))


class Fit(unittest.TestCase):
    def test_the_numbers_of_the_set(self):
        v = dsv41_fit.ISTA_SETS["UD-IQ2_XXS"]
        self.assertEqual(v["set_bytes"], MODEL_BYTES)
        self.assertEqual(v["engram_bytes"], TABLE_BYTES)
        self.assertEqual(dsv41_fit.ista_download_bytes("UD-IQ2_XXS"), MODEL_BYTES + TABLE_BYTES + dsv41_fit.ISTA_DSPARK_BYTES)
        self.assertEqual(dsv41_fit.variant_key("ud-iq2_xxs"), "UD-IQ2_XXS")

    def test_the_plan_tells_the_own_table(self):
        plan = dsv41_fit.plan([{"index": 0, "name": "NVIDIA GeForce RTX 4080 SUPER", "vendor": "nvidia", "vram_gb": 16.0}],
                              64.0, 1000.0, 131072, model="UD-IQ2_XXS")
        self.assertAlmostEqual(plan["engram_gb"], 135.2, places=1)
        text = " ".join(plan["lines"])
        self.assertIn("135 GB", text)
        self.assertIn("its own last shard", text)                 # not the shared-table sentence of the other sets


class Recognised(unittest.TestCase):
    def test_a_whole_set_with_its_own_q5k_table(self):
        t = dsv41_sets.tables()["sets"]["UD-IQ2_XXS"]
        files = {**t["shards"], **t["engram_alt"]}
        rows = [r for r in dsv41_sets.match(files) if r["model"] == "UD-IQ2_XXS"]
        self.assertTrue(rows and rows[0]["complete"], "six files")
        self.assertEqual(rows[0]["engram"], next(iter(t["engram_alt"])))

    def test_the_shared_iq4nl_file_is_not_this_sets_table(self):
        t = dsv41_sets.tables()
        files = dict(t["sets"]["UD-IQ2_XXS"]["shards"])
        files[setup.DSV41_ISTA_ENGRAM] = t["engram"][setup.DSV41_ISTA_ENGRAM]
        row = next(r for r in dsv41_sets.match(files) if r["model"] == "UD-IQ2_XXS")
        self.assertIsNone(row["engram"])
        row1 = next(r for r in dsv41_sets.match({**t["sets"]["UD-IQ1_M"]["shards"], setup.DSV41_ISTA_ENGRAM: t["engram"][setup.DSV41_ISTA_ENGRAM]})
                    if r["model"] == "UD-IQ1_M")
        self.assertEqual(row1["engram"], setup.DSV41_ISTA_ENGRAM)     # the other sets still take the shared file


if __name__ == "__main__":
    unittest.main()
