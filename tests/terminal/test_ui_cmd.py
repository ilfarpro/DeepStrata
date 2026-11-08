"""`strata ui`: starting and stopping the App Host page (no real host, Node or browser is involved).

The tests drive the command through the CLI parser with an isolated Home; the App Host's health answer,
the child process and the browser are replaced, so nothing listens on a port and nothing opens."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from tests.terminal.launch_support import Home, run_cli

import strata_cli.ui_cmd as U

HEALTH = {"ok": True, "backend": {"kind": "strata"}}


def fake_host(tmp: Path) -> Path:
    """A folder that looks like a built App Host (the command only checks these two files)."""
    d = tmp / "host"
    (d / "server").mkdir(parents=True)
    (d / "dist").mkdir(parents=True)
    (d / "server" / "index.mjs").write_text("// host\n", encoding="utf-8")
    (d / "dist" / "index.html").write_text("<html></html>", encoding="utf-8")
    return d


def health_then(*values):
    """The health answers one after another (the command asks it more than once)."""
    left = list(values)
    return lambda port, timeout=1.0: left.pop(0) if left else None


class FakeChild:
    pid = 4242

    def __init__(self, *args, **kw):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class Resp:
    def __init__(self, status=202):
        self.status = status

    def read(self):
        return b"{}"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StopTest(unittest.TestCase):
    def test_stop_when_nothing_runs(self):
        with Home(), mock.patch.object(U, "_health", return_value=None):
            code, out, _ = run_cli(["ui", "--stop", "--output", "json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"schema_version": 1, "ok": True, "stopped": False,
                                           "message": "the page's host is not running"})

    def test_stop_asks_the_host_to_quit_like_the_page_does(self):
        with Home():
            U._write_record({"schema_version": 1, "pid": 4242, "port": U.DEFAULT_PORT})
            seen = {}

            def urlopen(req, timeout=None):
                seen["url"] = req.full_url
                seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
                return Resp(202)

            with mock.patch.object(U, "_health", side_effect=health_then(HEALTH, None)), \
                    mock.patch.object(urllib.request, "urlopen", urlopen):
                code, out, _ = run_cli(["ui", "--stop", "--output", "json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["stopped"])
            self.assertEqual(seen["url"], f"http://127.0.0.1:{U.DEFAULT_PORT}/api/app/quit")
            self.assertEqual(seen["headers"].get("x-strata-client"), "strata-ui")   # what the host requires
            self.assertFalse(U._record_path().exists())                              # the record is gone

    def test_stop_falls_back_to_the_process_this_command_started(self):
        with Home():
            U._write_record({"schema_version": 1, "pid": 4242, "port": U.DEFAULT_PORT})

            def refuse(req, timeout=None):
                raise OSError("no quit on this host")

            with mock.patch.object(U, "_health", side_effect=health_then(HEALTH, None)), \
                    mock.patch.object(urllib.request, "urlopen", refuse), \
                    mock.patch.object(U.os, "kill") as kill:
                code, out, _ = run_cli(["ui", "--stop", "--output", "json"])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out)["stopped"])
            kill.assert_called_once_with(4242, 15)          # the host of this command only, never a stranger's pid


class StartTest(unittest.TestCase):
    def test_needs_a_host(self):
        with Home() as home:
            empty = home.base / "nothing"
            empty.mkdir()
            with mock.patch.object(U, "_health", return_value=None):     # nothing answers on the page's port
                code, out, _ = run_cli(["ui", "--host-dir", str(empty), "--url", "http://127.0.0.1:9",
                                        "--output", "json"])
            self.assertEqual(code, 5)
            body = json.loads(out)
            self.assertEqual(body["error"]["code"], "host_not_found")
            self.assertIn(str(empty), body["error"]["message"])          # the named folder is named back

    def test_needs_node(self):
        with Home() as home:
            host = fake_host(home.base)
            with mock.patch.object(U, "_health", return_value=None):
                code, out, _ = run_cli(["ui", "--host-dir", str(host), "--node", str(home.base / "no-node"),
                                        "--url", "http://127.0.0.1:9", "--output", "json"])
            self.assertEqual(code, 5)
            self.assertEqual(json.loads(out)["error"]["code"], "node_not_found")

    def test_needs_a_server(self):
        with Home() as home:
            host = fake_host(home.base)
            with mock.patch.object(U, "_health", return_value=None):
                code, out, _ = run_cli(["ui", "--host-dir", str(host), "--node", sys.executable,
                                        "--output", "json"])     # no --url, and nothing from `strata launch`
            self.assertEqual(code, 5)
            self.assertEqual(json.loads(out)["error"]["code"], "no_server")

    def test_start_serves_the_page_and_offers_the_model_to_the_chat(self):
        with Home() as home:
            host = fake_host(home.base)
            with mock.patch.object(U, "_health", side_effect=health_then(None, HEALTH)), \
                    mock.patch.object(U.subprocess, "Popen", FakeChild), \
                    mock.patch.object(U, "_offer_as_api_model", return_value="added") as offer, \
                    mock.patch("webbrowser.open") as browser:
                code, out, err = run_cli(["ui", "--host-dir", str(host), "--node", sys.executable,
                                          "--port", "8799", "--url", "http://127.0.0.1:8095", "--no-browser"])
            self.assertEqual(code, 0, err)
            self.assertIn("http://127.0.0.1:8799", out)
            self.assertIn("strata ui --stop", out)
            browser.assert_not_called()                            # --no-browser kept the tab closed
            rec = json.loads(U._record_path().read_text(encoding="utf-8"))
            self.assertEqual((rec["pid"], rec["port"], rec["server_url"]), (4242, 8799, "http://127.0.0.1:8095"))
            offer.assert_called_once_with(8799, "http://127.0.0.1:8095", None)

    def test_start_passes_the_browser_mode_and_the_server_to_the_host(self):
        with Home() as home:
            host = fake_host(home.base)
            started = {}

            def popen(cmd, **kw):
                started["cmd"], started["env"] = cmd, kw["env"]
                return FakeChild(cmd, **kw)

            with mock.patch.object(U, "_health", side_effect=health_then(None, HEALTH)), \
                    mock.patch.object(U.subprocess, "Popen", popen), \
                    mock.patch.object(U, "_offer_as_api_model", return_value=None):
                code, out, err = run_cli(["ui", "--host-dir", str(host), "--node", sys.executable,
                                          "--url", "http://127.0.0.1:8095/", "--no-browser", "--output", "json"])
            self.assertEqual(code, 0, err)
            body = json.loads(out)
            self.assertEqual((body["ok"], body["ui_url"], body["chat_model"]), (True, f"http://127.0.0.1:{U.DEFAULT_PORT}", None))
            self.assertEqual(started["env"]["STRATA_UI_MODE"], "browser")     # the page with its own "Quit Strata"
            self.assertEqual(started["env"]["STRATA_SERVER_URL"], "http://127.0.0.1:8095")
            self.assertEqual(started["env"]["STRATA_PORT"], str(U.DEFAULT_PORT))
            self.assertEqual(started["cmd"][0], sys.executable)               # the --node one is what runs
            self.assertTrue(str(started["cmd"][1]).endswith("index.mjs"))
            self.assertIn("--dist", started["cmd"])

    def test_second_run_shows_the_page_that_is_up(self):
        with Home():
            with mock.patch.object(U, "_health", return_value=HEALTH), \
                    mock.patch.object(U.subprocess, "Popen", mock.Mock(return_value=FakeChild())) as popen, \
                    mock.patch.object(U, "_offer_as_api_model", return_value="present"):
                code, out, _ = run_cli(["ui", "--url", "http://127.0.0.1:8095", "--no-browser", "--output", "json"])
            self.assertEqual(code, 0)
            body = json.loads(out)
            self.assertTrue(body["already_running"])
            self.assertEqual(body["ui_url"], f"http://127.0.0.1:{U.DEFAULT_PORT}")
            popen.assert_not_called()                               # nothing was started twice

    def test_the_browser_opens_when_not_refused(self):
        with Home() as home:
            host = fake_host(home.base)
            opened = threading.Event()
            with mock.patch.object(U, "_health", side_effect=health_then(None, HEALTH)), \
                    mock.patch.object(U.subprocess, "Popen", FakeChild), \
                    mock.patch.object(U, "_offer_as_api_model", return_value=None), \
                    mock.patch("webbrowser.open", side_effect=lambda url: opened.set()):
                code, out, err = run_cli(["ui", "--host-dir", str(host), "--node", sys.executable,
                                          "--url", "http://127.0.0.1:8095", "--output", "json"])
            self.assertEqual(code, 0, err)
            self.assertTrue(opened.wait(2), "the browser was never asked")


class HelpTest(unittest.TestCase):
    def test_ui_is_a_command(self):
        code, out, _ = run_cli(["ui", "--help"])
        self.assertEqual(code, 0)
        for flag in ("--stop", "--port", "--url", "--no-browser"):
            self.assertIn(flag, out)


if __name__ == "__main__":
    unittest.main()
