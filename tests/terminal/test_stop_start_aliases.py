"""`strata stop` and `strata start`: the plain words for `strata launch --stop` and `strata launch`.

The stop cases run against the runtime double of test_launch_stop_exit_0213 (nothing, stopped, failed, mixed,
and the JSON object `strata stop --output json` prints); the start alias is checked where no model is needed:
--status, --list-presets, --help and the unknown-flag sentence. `launch --stop` keeps its plain lines either way."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from strata_cli.launch import access
from tests.terminal.launch_support import Home, run_cli

NOTHING = "Nothing from `strata launch` is running."


class Runtime:
    """profile id -> (alive?, stop succeeds?)"""

    def __init__(self, profiles):
        self.alive = {p: ok is not None for p, ok in profiles.items()}
        self.stops_ok = {p: bool(ok) for p, ok in profiles.items()}

    def list_profiles(self):
        return [{"id": p} for p in self.alive]

    def status(self, prof):
        return {"state": "ready" if self.alive.get(prof) else "stopped"}

    def stop(self, prof, timeout=None):
        if self.stops_ok.get(prof):
            self.alive[prof] = False
            return {"ok": True}
        return {"ok": False, "error": "leftover_process_not_ended"}

    def live(self):
        return sorted(p for p, a in self.alive.items() if a)


def patched(rt: Runtime):
    return (mock.patch("strata_control.profiles.list_profiles", rt.list_profiles),
            mock.patch("strata_control.runtime.status", rt.status),
            mock.patch("strata_control.runtime.stop", rt.stop),
            mock.patch.object(access, "stop_tunnel"))


class StopCommand(unittest.TestCase):
    def _run(self, profiles, *args):
        rt = Runtime(profiles)
        patches = patched(rt)
        for p in patches:
            p.start()
        try:
            code, out, err = run_cli(["stop", *args])
        finally:
            for p in patches:
                p.stop()
        return rt, code, out, err

    def test_nothing_running_says_so_and_exits_zero(self):
        rt, code, out, _ = self._run({})
        self.assertEqual(code, 0)
        self.assertIn(NOTHING, out)
        rt, code, out, _ = self._run({"launch-office": None})          # a profile exists but nothing runs
        self.assertEqual((code, rt.live()), (0, []))
        self.assertIn(NOTHING, out)

    def test_stops_every_running_launch_and_leaves_nothing_alive(self):
        rt, code, out, _ = self._run({"launch-office": True, "launch-home": True})
        self.assertEqual((code, rt.live()), (0, []))
        self.assertIn("Stopped office.", out)
        self.assertIn("Stopped home.", out)
        self.assertNotIn(NOTHING, out)

    def test_a_preset_name_stops_that_one_only(self):
        rt, code, out, _ = self._run({"launch-office": True, "launch-home": True}, "--preset", "Office")
        self.assertEqual((code, rt.live()), (0, ["launch-home"]))
        self.assertIn("Stopped office.", out)
        self.assertNotIn("home", out)

    def test_a_failed_stop_exits_nonzero_and_does_not_claim_nothing_runs(self):
        rt, code, out, _ = self._run({"launch-office": False})
        self.assertEqual((code, rt.live()), (1, ["launch-office"]))
        self.assertIn("Could not stop office: leftover_process_not_ended.", out)
        self.assertNotIn(NOTHING, out)

    def test_json_names_what_stopped_and_what_failed(self):
        rt, code, out, _ = self._run({"launch-office": True, "launch-home": False}, "--output", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        self.assertEqual(data["schema_version"], 1)
        self.assertFalse(data["ok"])
        self.assertEqual(data["stopped"], ["office"])
        self.assertEqual(data["failed"], [{"name": "home", "error": "leftover_process_not_ended"}])
        self.assertFalse(data["nothing_running"])

    def test_json_when_nothing_runs(self):
        rt, code, out, _ = self._run({}, "--json")                      # --json is the alias for --output json
        data = json.loads(out)
        self.assertEqual((code, data["ok"], data["nothing_running"]), (0, True, True))

    def test_stop_help_lists_its_flags(self):
        code, out, _ = run_cli(["stop", "--help"])
        self.assertEqual(code, 0)
        for flag in ("--preset", "--output"):
            self.assertIn(flag, out)

    def test_unknown_flag_gets_the_plain_sentence(self):
        code, _, err = run_cli(["stop", "--halting"])
        self.assertEqual(code, 2)
        self.assertIn("not a strata stop option", err)
        self.assertIn("strata stop --help", err)

    def test_launch_output_json_stop_keeps_the_plain_lines(self):   # byte-identical default: only `stop` prints JSON
        rt = Runtime({"launch-office": True})
        patches = patched(rt)
        for p in patches:
            p.start()
        try:
            code, out, _ = run_cli(["launch", "--output", "json", "--stop"])
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(code, 0)
        self.assertIn("Stopped office.", out)
        self.assertNotIn("schema_version", out)


class StartAlias(unittest.TestCase):
    def test_start_answers_like_launch_where_no_model_is_needed(self):
        with Home():
            for word in ("start", "launch"):
                code, out, err = run_cli([word, "--status"])
                self.assertEqual((code, word), (0, word), err)
                self.assertIn(NOTHING, out)
                code, out, err = run_cli([word, "--list-presets"])
                self.assertEqual((code, word), (0, word), err)
                self.assertIn("No saved presets", out)

    def test_start_help_is_the_launch_help(self):
        code, out, _ = run_cli(["start", "--help"])
        self.assertEqual(code, 0)
        for flag in ("--preset", "--model", "--no-tui", "--plan", "examples:"):
            self.assertIn(flag, out)

    def test_unknown_flag_on_start_points_at_the_launch_help(self):
        code, _, err = run_cli(["start", "--no-dash"])
        self.assertEqual(code, 2)
        self.assertIn("not a strata launch option", err)
        self.assertIn("strata launch --help", err)

    def test_start_routes_to_the_same_runner_with_its_flags(self):
        from strata_cli.launch import cmd as launch_cmd
        seen = {}

        def fake(a):
            seen["command"] = a.command
            return 0

        with mock.patch.object(launch_cmd, "run_launch", fake):
            code, _, err = run_cli(["start", "--no-tui", "--model", "q2_0"])
        self.assertEqual(code, 0, err)
        self.assertEqual(seen["command"], "start")          # run_launch, the very function `strata launch` calls


class HelpMentionsTheNewWords(unittest.TestCase):
    def test_top_level_help_lists_stop_and_start(self):
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("stop", out)
        self.assertIn("start", out)

    def test_launch_help_examples_show_the_plain_words(self):
        code, out, _ = run_cli(["launch", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("strata stop", out)
        self.assertIn("strata start", out)


if __name__ == "__main__":
    unittest.main()
