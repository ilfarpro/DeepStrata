"""``strata ui``: open Strata's full page (Chat, Monitor, About) in your browser.

The address ``strata launch`` prints is the API. The full page is served by the App Host - the same Node host the
desktop app shows in its own window. Here it starts with ``STRATA_UI_MODE=browser`` (apps/host/server/app-ui.mjs),
so the page opens as an ordinary browser tab and its "Quit Strata" - like this command's ``--stop`` - ends the host.
The model keeps serving either way: stopping the model stays ``strata launch --stop``.

The App Host is found at ``--host-dir``, ``STRATA_HOST_DIR``, this repository's ``apps/host`` (a source checkout)
or the ``host/`` folder of an installed Strata; it needs its built UI (``dist``). Node 24.12 or newer is taken from
``--node``, ``STRATA_NODE`` or the PATH, or from the desktop bundle's private copy when one is there. Nothing is
downloaded; when a piece is missing the command says which and what to do.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from strata_control import fork_identity

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = fork_identity.HOST_PORT      # apps/host/server/index.mjs: STRATA_PORT ?? the fork's host port

NODE_NAMES = ("node", "node.exe")


def _record_path() -> Path:
    from strata_control import paths
    return paths.state_base() / "ui-host.json"


def _read_record() -> dict | None:
    try:
        rec = json.loads(_record_path().read_text(encoding="utf-8"))
        return rec if isinstance(rec, dict) and rec.get("port") else None
    except (OSError, ValueError):
        return None


def _write_record(rec: dict) -> None:
    p = _record_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    os.replace(tmp, p)


def _clear_record() -> None:
    try:
        _record_path().unlink()
    except OSError:
        pass


def _health(port: int, timeout: float = 1.0) -> dict | None:
    """The App Host's health answer, or None when no host answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
        return body if body.get("ok") else None
    except (OSError, ValueError):
        return None


LAUNCH_CONNECTION = "Strata (strata launch)"


def _offer_as_api_model(port: int, server_url: str, key: str | None) -> str | None:
    """Show the serving model in the page's chat: register the server as one of the host's API models
    (kind "custom", the model-providers store, apps/host/server/model-providers.mjs). The page's own chat
    talks to a model the person picks; a model served from the terminal is such an API model to it.
    Returns "added", "present" or None; a page or a server that does not answer never breaks `strata ui`."""
    base = server_url.rstrip("/") + "/v1"
    try:
        req = urllib.request.Request(base + "/models",
                                     headers=({"authorization": f"Bearer {key}"} if key else {}))
        with urllib.request.urlopen(req, timeout=3) as r:
            ids = [m.get("id") for m in json.loads(r.read().decode("utf-8")).get("data", []) if m.get("id")]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/model-providers", timeout=3) as r:
            known = json.loads(r.read().decode("utf-8")).get("connections", [])
        if any(c.get("baseUrl") == base for c in known):
            return "present"
        if not ids:
            return None
        body = json.dumps({"kind": "custom", "name": LAUNCH_CONNECTION, "baseUrl": base,
                           "protocol": "openai-chat", "apiKey": key or "",
                           "models": [{"id": i} for i in ids[:100]]}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/model-providers", method="POST", data=body,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
        return "added"
    except (OSError, ValueError):
        return None


def _host_dir(cli_value: str | None) -> Path | None:
    """The App Host folder (server/index.mjs + built dist), or None with the places that were checked.
    A named folder (--host-dir, STRATA_HOST_DIR) is taken on its own: it never falls back to the search."""
    if cli_value or os.environ.get("STRATA_HOST_DIR"):
        candidates = [Path(cli_value or os.environ["STRATA_HOST_DIR"])]
    else:
        candidates = [ROOT / "apps" / "host", ROOT / "host"]
    for d in candidates:
        if (d / "server" / "index.mjs").is_file() and (d / "dist" / "index.html").is_file():
            return d.resolve()
    return None


HOST_SEARCHED = "apps/host (source checkout), the installed host/ folder; name one with --host-dir or STRATA_HOST_DIR"


def _node_path(cli_value: str | None) -> Path | None:
    if cli_value or os.environ.get("STRATA_NODE"):
        p = Path(cli_value or os.environ["STRATA_NODE"])
        return p if p.is_file() else None
    found = shutil.which("node")
    if found:
        return Path(found)
    for base in (ROOT / "apps" / "desktop" / "src-tauri" / "resources" / "node", ROOT / "resources" / "node"):
        for name in NODE_NAMES:
            p = base / name
            if p.is_file():
                return p
    return None


def _server_url(cli_value: str | None) -> tuple[str | None, bool, str | None]:
    """(API url, running?, api key) from --url or from the running `strata launch`."""
    if cli_value:
        return cli_value.rstrip("/"), True, None
    from strata_cli.launch import cmd as launch_cmd
    info = launch_cmd.launch_info()
    if not info:
        return None, False, None
    return info["url"], bool(info.get("running")), info.get("key")


def add_parser(sp) -> None:
    p = sp.add_parser("ui", help="open Strata's full page (Chat, Monitor, About) in your browser; --stop ends the page, never the model")
    p.add_argument("--port", type=int, help=f"the port the page is served on (default {DEFAULT_PORT}, or STRATA_PORT)")
    p.add_argument("--url", metavar="URL", help="the model's API address when it did not come from `strata launch`")
    p.add_argument("--host-dir", metavar="DIR", help="the App Host folder (its server/index.mjs and built dist)")
    p.add_argument("--node", metavar="EXE", help="the Node runtime to run the host with (24.12 or newer)")
    p.add_argument("--no-browser", action="store_true", help="print the address instead of opening the browser")
    p.add_argument("--stop", action="store_true", help="stop the page's host (the model keeps serving)")
    p.add_argument("--output", choices=("human", "json"), default="human")


def run(a) -> int:
    machine = a.output == "json"
    port = a.port or int(os.environ.get("STRATA_PORT") or DEFAULT_PORT)

    def fail(message: str, code: str = "ui_failed") -> int:
        if machine:
            print(json.dumps({"schema_version": 1, "ok": False, "error": {"code": code, "message": message}},
                             ensure_ascii=False))
        else:
            print(f"strata: {message}", file=sys.stderr)
        return 5

    if a.stop:
        return _stop(port, machine, fail)
    return _start(a, port, machine, fail)


def _stop(port: int, machine, fail) -> int:
    health = _health(port)
    rec = _read_record()
    if health is None:
        _clear_record()
        if machine:
            print(json.dumps({"schema_version": 1, "ok": True, "stopped": False,
                              "message": "the page's host is not running"}, ensure_ascii=False))
        else:
            print("Nothing to stop: the page's host is not running. (The model is not touched by this command; "
                  "stop it with `strata launch --stop`.)")
        return 0
    # ask the host itself, the way the page's "Quit Strata" does (apps/host/server/app-ui.mjs)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/app/quit", method="POST",
                                 headers={"x-strata-client": "strata-ui", "content-length": "0"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            quit_ok = r.status == 202
    except urllib.error.HTTPError as e:
        quit_ok = False
        e.close()
    except OSError:
        quit_ok = False
    if not quit_ok:                                      # an older host or a mode that cannot quit itself:
        pid = int((rec or {}).get("pid") or 0)           # end the process this command started, and only that one
        if rec and rec.get("port") == port and pid > 0:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    for _ in range(50):
        if _health(port, timeout=0.4) is None:
            break
        time.sleep(0.1)
    _clear_record()
    if machine:
        print(json.dumps({"schema_version": 1, "ok": True, "stopped": True, "port": port}, ensure_ascii=False))
    else:
        print(f"Stopped the page's host on port {port}. The model keeps serving; stop it with `strata launch --stop`.")
    return 0


def _start(a, port: int, machine, fail) -> int:
    health = _health(port)
    if health is not None:                               # the page is up already: just show it again
        url0, _, key0 = _server_url(getattr(a, "url", None))
        return _announce(port, url_of=None, machine=machine, already=True,
                         open_browser=not (a.no_browser or os.environ.get("STRATA_UI_NO_BROWSER")),
                         offer=(port, url0, key0))
    _clear_record()                                      # nothing answered: an old record is a stale one

    host = _host_dir(getattr(a, "host_dir", None))
    if host is None:
        named = getattr(a, "host_dir", None) or os.environ.get("STRATA_HOST_DIR")
        if named:
            return fail(f"The App Host was not found at {named}: it needs server/index.mjs and a built dist "
                        f"(cd into it and run  npm ci && npm run build).", code="host_not_found")
        return fail("The App Host was not found (checked: " + HOST_SEARCHED + "). In a source checkout, build it "
                    "once:  cd apps/host && npm ci && npm run build", code="host_not_found")
    node = _node_path(getattr(a, "node", None))
    if node is None:
        return fail("Node 24.12 or newer was not found (PATH, STRATA_NODE, --node, or the desktop bundle's copy). "
                    "Install Node from nodejs.org, or use the desktop app, which carries its own.", code="node_not_found")

    url, running, key = _server_url(getattr(a, "url", None))
    if not running:
        return fail("Nothing from `strata launch` is serving. Start your model first:  strata launch --family "
                    "<family> --model <model> --yes --no-tui  (or pass --url for a server that runs elsewhere)",
                    code="no_server")

    state = _record_path().parent
    state.mkdir(parents=True, exist_ok=True)
    log = state / "ui-host.log"
    env = dict(os.environ)
    env.update({"STRATA_UI_MODE": "browser", "STRATA_UI_REASON": "chosen",
                "STRATA_SERVER_URL": url, "STRATA_PORT": str(port)})
    log_f = open(log, "ab")
    popen_kw: dict = {"stdout": log_f, "stderr": log_f, "cwd": str(host),
                      "stdin": subprocess.DEVNULL, "env": env}
    if os.name == "nt":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        popen_kw["start_new_session"] = True
    try:
        child = subprocess.Popen([str(node), str(host / "server" / "index.mjs"), "--dist", str(host / "dist")],
                                 **popen_kw)
    except OSError as exc:
        log_f.close()
        return fail(f"The App Host could not be started ({type(exc).__name__}).")
    log_f.close()

    # wait for the page; a port already busy makes the host take the next free one, which its first line names
    up_port, health = port, None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if child.poll() is not None:
            break
        health = _health(port)
        if health is not None:
            up_port = port
            break
        try:                                            # "strata host  ·  http://127.0.0.1:<port>" (index.mjs)
            line = log.read_text(encoding="utf-8", errors="replace").splitlines()
            line = next((l for l in reversed(line) if "http://127.0.0.1:" in l), "")
            named = line.split("http://127.0.0.1:", 1)[1].split("/", 1)[0]
            named_port = int(named)
        except (ValueError, OSError, IndexError):
            named_port = None
        if named_port and named_port != port and _health(named_port) is not None:
            up_port, health = named_port, _health(named_port)
            break
        time.sleep(0.2)
    if health is None:
        tail = ""
        try:
            tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-10:])
        except OSError:
            pass
        if child.poll() is None:
            try:
                child.terminate()
            except OSError:
                pass
        return fail(f"The App Host did not answer on port {port}.\n{tail}", code="host_unhealthy")

    _write_record({"schema_version": 1, "pid": child.pid, "port": up_port, "started_at": time.time(),
                   "host": str(host), "node": str(node), "server_url": url})
    return _announce(up_port, url_of=url, machine=machine, already=False,
                     open_browser=not (a.no_browser or os.environ.get("STRATA_UI_NO_BROWSER")),
                     offer=(up_port, url, key))


def _announce(port: int, url_of: str | None, machine, already: bool, open_browser: bool = True,
              offer: tuple | None = None) -> int:
    page = f"http://127.0.0.1:{port}"
    rec = _read_record() or {}
    rec.setdefault("pid", None)
    listed = None
    if offer and offer[1]:
        listed = _offer_as_api_model(offer[0], offer[1], offer[2])
    if machine:
        print(json.dumps({"schema_version": 1, "ok": True, "ui_url": page, "port": port, "pid": rec.get("pid"),
                          "server_url": url_of or rec.get("server_url"), "already_running": already,
                          "chat_model": listed}, ensure_ascii=False))
    else:
        print(f"Strata's page:  {page}")
        if (url_of or rec.get("server_url")):
            print(f"        model:  {url_of or rec.get('server_url')}")
        if listed == "added":
            print(f" The serving model was added to the page's chat: pick \"{LAUNCH_CONNECTION}\" in the model picker.")
        print("Closing the tab keeps the model serving.  Stop the page:  strata ui --stop    "
              "Stop the model:  strata launch --stop")
    if open_browser:
        import webbrowser
        # on its own thread: webbrowser.open waits for the launcher, which on Linux can run for as long as the
        # browser does, and this command must return (the same rule as serve/server.py's --open)
        threading.Thread(target=webbrowser.open, daemon=True, name="strata-ui-browser", args=(page,)).start()
    return 0
