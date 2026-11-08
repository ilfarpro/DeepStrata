r"""DeepStrata installs beside the original Strata (DIVERGENCE.md). One identity module per
language carries the fork's own names — strata_control/fork_identity.py (source of truth),
apps/host/server/fork-identity.mjs (the host), apps/desktop/src-tauri/src/identity.rs (the shell).

Three guarantees: the copies say the same values; the surfaces that cannot import a module
(setup.py, serve/server.py, sycl/setup_intel.py, tools/strata_mcp.py, handover.py, tauri.conf.json,
Cargo.toml, SnapshotDialog.tsx) pin to it; and an original-product value — the upstream release
address, `ai.strata.desktop`, the old ports, the `strata-desktop` binary, `~/.strata` — never
appears in product code again (the scan below), so a merge from upstream that reintroduces one
fails here instead of making the two installs fight over a folder, a port or an update feed.

Kept on purpose: the shared model folder `Documents/Strata` (MODEL_FOLDER, an installed model is
never downloaded twice), the `STRATA_*` environment names (one process, no collision), the words
people read, the engine binary names (strata, strata-dsv41, strata-vision: they live inside each
product's own component folder) and the feed artifact names (they live inside our own release)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from strata_control import fork_identity as identity

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------
# the mirror copies
# ---------------------------------------------------------------------------------------------

def js_constants(rel: str) -> dict:
    """`export const NAME = 'value';` / `= 123;` in a module; derived values (template literals) are not returned."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    out = {}
    for name, raw in re.findall(r"^export const (\w+) = ([^;\n]+);", text, re.M):
        m = re.fullmatch(r"'([^']*)'", raw.strip())
        if m:
            out[name] = m.group(1)
        elif re.fullmatch(r"\d+", raw.strip()):
            out[name] = int(raw.strip())
    return out


def rust_constants(rel: str) -> dict:
    """`pub const NAME: &str = "value";` / `u16 = 123;` in a module."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    out = {}
    for name, kind, raw in re.findall(r'pub const (\w+): (&str|u16) = (.*?);', text):
        out[name] = int(raw) if kind == "u16" else raw.strip('"')
    return out


class ModuleValues(unittest.TestCase):
    """The values themselves, pinned: changing one is a deliberate act, not a typo."""

    def test_the_names(self):
        self.assertEqual(identity.PRODUCT_NAME, "DeepStrata")
        self.assertEqual(identity.CLI_NAME, "deepstrata")
        self.assertEqual(identity.FOLDER_POSIX, "deepstrata")
        self.assertEqual(identity.FOLDER_WINDOWS, "DeepStrata")
        self.assertEqual(identity.HOME_DIR, ".deepstrata")
        self.assertEqual(identity.REPO, "ilfarpro/DeepStrata")

    def test_the_addresses(self):
        self.assertEqual(identity.RELEASES_URL, "https://github.com/ilfarpro/DeepStrata/releases")
        self.assertEqual(identity.ISSUES_URL, identity.RELEASES_URL + "/issues")
        self.assertEqual(identity.FEED_NAME, "strata-feed.json")          # our release folder holds it; the name stays
        self.assertEqual(identity.FEED_URL, identity.LATEST_DOWNLOAD + "/strata-feed.json")
        self.assertEqual(identity.APP_UPDATER_URL, identity.LATEST_DOWNLOAD + "/latest.json")

    def test_the_ports_are_not_the_original_product(self):
        self.assertEqual(identity.HOST_PORT, 8891)        # the original's page port was 8791
        self.assertEqual(identity.DESKTOP_PORT, 47841)    # the original's shell port was 47831

    def test_the_model_folder_stays_shared(self):
        """Documents/Strata holds the model files for both products: an installed model is never downloaded twice."""
        self.assertEqual(identity.MODEL_FOLDER, "Strata")


class MirrorCopies(unittest.TestCase):
    """The Node host and the Rust shell copy the values; a drifted copy is a silent collision."""

    def test_the_host_copy(self):
        js = js_constants("apps/host/server/fork-identity.mjs")
        for name, want in [("PRODUCT_NAME", identity.PRODUCT_NAME), ("CLI_NAME", identity.CLI_NAME),
                           ("FOLDER_POSIX", identity.FOLDER_POSIX), ("FOLDER_WINDOWS", identity.FOLDER_WINDOWS),
                           ("HOME_DIR", identity.HOME_DIR), ("REPO", identity.REPO),
                           ("HOST_PORT", identity.HOST_PORT), ("DESKTOP_PORT", identity.DESKTOP_PORT),
                           ("FEED_NAME", identity.FEED_NAME), ("MODEL_FOLDER", identity.MODEL_FOLDER)]:
            self.assertIn(name, js, f"the host module lost {name}")
            self.assertEqual(js[name], want, f"apps/host/server/fork-identity.mjs {name}")

    def test_the_shell_copy(self):
        rs = rust_constants("apps/desktop/src-tauri/src/identity.rs")
        for name, want in [("PRODUCT_NAME", identity.PRODUCT_NAME), ("FOLDER_POSIX", identity.FOLDER_POSIX),
                           ("FOLDER_WINDOWS", identity.FOLDER_WINDOWS), ("HOME_DIR", identity.HOME_DIR),
                           ("REPO", identity.REPO), ("DESKTOP_PORT", identity.DESKTOP_PORT)]:
            self.assertIn(name, rs, f"the shell module lost {name}")
            self.assertEqual(rs[name], want, f"apps/desktop/src-tauri/src/identity.rs {name}")
        self.assertEqual(rs["BINARY_NAME"], js_constants("apps/host/server/fork-identity.mjs")["BINARY_NAME"])

    def test_the_cargo_package_is_the_binary_the_copies_name(self):
        rs = rust_constants("apps/desktop/src-tauri/src/identity.rs")
        cargo = (ROOT / "apps/desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
        m = re.search(r'\[package\][^\[]*?name = "([^"]+)"', cargo, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), rs["BINARY_NAME"])     # MAINBINARYNAME and NSIS follow the package name


class StandaloneCopies(unittest.TestCase):
    """The surfaces that cannot import strata_control carry a literal; each is pinned here."""

    def test_handover_before_any_import(self):
        text = (ROOT / "strata_cli/handover.py").read_text(encoding="utf-8")
        self.assertIn(f'"{identity.FOLDER_WINDOWS}" / "components"', text)      # %LOCALAPPDATA%\DeepStrata\components
        self.assertIn(f'"{identity.FOLDER_POSIX}" / "components"', text)        # ~/.local/share/deepstrata/components

    def test_the_mcp_server_settings_file(self):
        text = (ROOT / "tools/strata_mcp.py").read_text(encoding="utf-8")
        self.assertIn(f'"{identity.FOLDER_WINDOWS}" / "settings.json"', text)   # %APPDATA%\DeepStrata
        self.assertIn(f'"{identity.FOLDER_POSIX}" / "settings.json"', text)     # ~/.config/deepstrata

    def test_the_standalone_tools_name_our_issue_page(self):
        needle = f"github.com/{identity.REPO}/issues"
        for rel in ("serve/server.py", "sycl/setup_intel.py"):
            self.assertIn(needle, (ROOT / rel).read_text(encoding="utf-8"), rel)

    def test_the_snapshot_share_names_our_repository(self):
        text = (ROOT / "apps/host/src/components/SnapshotDialog.tsx").read_text(encoding="utf-8")
        m = re.search(r"const project = '([^']+)'", text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), f"https://github.com/{identity.REPO}")

    def test_the_dev_proxy_wants_the_same_page_port_as_the_host(self):
        """`npm run dev` starts the host on HOST_PORT (scripts/dev.mjs reads the module); the vite proxy cannot import it, so its literal is pinned."""
        text = (ROOT / "apps/host/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn(f"http://127.0.0.1:{identity.HOST_PORT}", text)

    def test_the_desktop_config_is_the_fork_config(self):
        import json
        conf = json.loads((ROOT / "apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(conf["productName"], identity.PRODUCT_NAME)
        self.assertEqual(conf["identifier"], "ai.deepstrata.desktop")           # not ai.strata.desktop: macOS/Linux keep settings per identifier
        updater = conf["plugins"]["updater"]
        self.assertEqual(updater["endpoints"], [identity.APP_UPDATER_URL])
        keys = json.loads((ROOT / "strata_control/update_keys.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(updater["pubkey"], keys["app_minisign_public"])         # one app key on both sides


# ---------------------------------------------------------------------------------------------
# the purity scan: no original-product identity in product code
# ---------------------------------------------------------------------------------------------

SCAN_DIRS = [
    "strata_control", "strata_cli", "serve", "sycl", "packaging", "docker", "tools", "bin",
    "apps/host/server", "apps/host/src", "apps/host/scripts",
    "apps/desktop/src-tauri/src", "apps/desktop/src-tauri/nsis", "apps/desktop/scripts",
]
SCAN_FILES = ["setup.py", "update.sh", "strata.py", "apps/host/vite.config.ts"]
SKIP_PARTS = {"node_modules", "__pycache__", "target", "dist", "fixtures"}           # fixtures hold capture samples, not product code
TEXT_EXTS = {".py", ".mjs", ".js", ".ts", ".tsx", ".rs", ".nsi", ".sh", ".ps1", ".cmd", ".bat",
             ".toml", ".md", ".txt", ".yml", ".yaml"}                                 # plus extensionless files (bin/deepstrata, docker/Dockerfile)

# the three identity modules document the original's values on purpose
IDENTITY_MODULES = {"strata_control/fork_identity.py", "apps/host/server/fork-identity.mjs",
                    "apps/desktop/src-tauri/src/identity.rs"}
# tools that reach the real web or the real upstream on purpose: the agent-tool probe must find the original product's page,
# the research corpus tool says who to contact; neither is something an install runs
LIVE_WEB_TOOLS = {"tools/trellis/make_corpus.py", "apps/host/scripts/agent2-tools-flow.mjs"}

FORBIDDEN = {
    "the upstream repository": re.compile(r"Niko1221/Strata"),
    "the original app identifier": re.compile(r"ai\.strata\.desktop"),
    "the original shell binary": re.compile(r"(?<!deep)strata-desktop"),
    # the home-store token as a path of its own (`'~/.strata'`, `$PROFILE\.strata`, `'/ … /.strata/'`).
    # Not the JS property `.strata` of the update-result object (an in-memory API key, never a path),
    # not `...strata` spreads, not `{tag}.strata.gen` research dump names: those have a token of their own before the dot.
    "the original home store": re.compile(r"(?<![\w.?!}])\.strata(?![\w-])"),
    "the original page port": re.compile(r"\b8791\b"),
    "the original shell port": re.compile(r"\b47831\b"),
}

# the workspace's own hidden folders are the person's project convention, shared by every tool that opens the folder (like .git):
# DeepStrata reads and skips what the original reads and skips (.strata/checks.json, walk skip lists), so a project is set up once.
WORKSPACE_CHECKS_FILE = re.compile(r'\.strata(/|" / ")checks\.json')
WORKSPACE_SKIP_LINE = re.compile(r"\.git\b")


def scanned_files():
    paths = [ROOT / rel for rel in SCAN_FILES]
    for rel in SCAN_DIRS:
        for path in sorted((ROOT / rel).rglob("*")):
            if path.is_file():
                yield path


def scan_text(raw: bytes) -> str:
    """Product text with the shared workspace conventions blanked out, line numbers kept."""
    lines = raw.decode("utf-8", errors="ignore").splitlines()
    out = []
    for line in lines:
        line = WORKSPACE_CHECKS_FILE.sub(" ", line)
        if WORKSPACE_SKIP_LINE.search(line):
            line = re.sub(r"(?<![\w.?!}])\.strata(?![\w-])", " ", line)
        out.append(line)
    return "\n".join(out)


class ProductCodeCarriesNoOriginalIdentity(unittest.TestCase):
    def test_no_original_product_value_in_product_code(self):
        offenders = []
        for path in scanned_files():
            try:
                rel = path.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            if rel in IDENTITY_MODULES or rel in LIVE_WEB_TOOLS or path.suffix == ".json":
                continue
            if set(path.relative_to(ROOT).parts) & SKIP_PARTS:
                continue
            if path.suffix and path.suffix not in TEXT_EXTS:
                continue
            raw = path.read_bytes()
            if b"\0" in raw[:8192] or len(raw) > 512 * 1024:
                continue
            text = scan_text(raw)
            for name, pattern in FORBIDDEN.items():
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    offenders.append(f"{rel}:{line}: {name} ({m.group(0)!r})")
        self.assertEqual(offenders, [], "product code names the original product where the two installs would collide:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
