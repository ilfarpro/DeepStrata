r"""DeepStrata installs beside the original Strata (DIVERGENCE.md). One identity module per
language carries the fork's own names — strata_control/fork_identity.py (source of truth),
apps/host/server/fork-identity.mjs (the host).

Three guarantees: the copies say the same values; the surfaces that cannot import a module
(setup.py, serve/server.py, sycl/setup_intel.py, tools/strata_mcp.py, handover.py,
SnapshotDialog.tsx) pin to it; and an original-product value — the upstream release
address, `ai.strata.desktop`, the old ports, the `strata-desktop` binary, `~/.strata` — never
appears in product code again (the scan below), so a merge from upstream that reintroduces one
fails here instead of making the two installs fight over a folder, a port or an update feed.

Kept on purpose: the shared model folder `Documents/Strata` (MODEL_FOLDER, an installed model is
never downloaded twice), the `STRATA_*` environment names (one process, no collision), the words
people read, the engine binary names (strata, strata-dsv41, strata-vision: they live inside each
product's own component folder) and the feed artifact names (they live inside our own release).

The last class guards the documents: commands a person copies and runs name `deepstrata`, install
and release addresses name our repository, and prose and history files keep saying Strata."""
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

    def test_the_ports_are_not_the_original_product(self):
        self.assertEqual(identity.HOST_PORT, 8891)        # the original's page port was 8791

    def test_the_model_folder_stays_shared(self):
        """Documents/Strata holds the model files for both products: an installed model is never downloaded twice."""
        self.assertEqual(identity.MODEL_FOLDER, "Strata")


class MirrorCopies(unittest.TestCase):
    """The Node host copies the values; a drifted copy is a silent collision."""

    def test_the_host_copy(self):
        js = js_constants("apps/host/server/fork-identity.mjs")
        for name, want in [("PRODUCT_NAME", identity.PRODUCT_NAME), ("CLI_NAME", identity.CLI_NAME),
                           ("FOLDER_POSIX", identity.FOLDER_POSIX), ("FOLDER_WINDOWS", identity.FOLDER_WINDOWS),
                           ("HOME_DIR", identity.HOME_DIR), ("REPO", identity.REPO),
                           ("HOST_PORT", identity.HOST_PORT),
                           ("FEED_NAME", identity.FEED_NAME), ("MODEL_FOLDER", identity.MODEL_FOLDER)]:
            self.assertIn(name, js, f"the host module lost {name}")
            self.assertEqual(js[name], want, f"apps/host/server/fork-identity.mjs {name}")


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


# ---------------------------------------------------------------------------------------------
# the purity scan: no original-product identity in product code
# ---------------------------------------------------------------------------------------------

SCAN_DIRS = [
    "strata_control", "strata_cli", "serve", "sycl", "packaging", "docker", "tools", "bin",
    "apps/host/server", "apps/host/src", "apps/host/scripts",
]
SCAN_FILES = ["setup.py", "update.sh", "strata.py", "apps/host/vite.config.ts"]
SKIP_PARTS = {"node_modules", "__pycache__", "target", "dist", "fixtures"}           # fixtures hold capture samples, not product code
TEXT_EXTS = {".py", ".mjs", ".js", ".ts", ".tsx", ".rs", ".nsi", ".sh", ".ps1", ".cmd", ".bat",
             ".toml", ".md", ".txt", ".yml", ".yaml"}                                 # plus extensionless files (bin/deepstrata, docker/Dockerfile)

# the two identity modules document the original's values on purpose
IDENTITY_MODULES = {"strata_control/fork_identity.py", "apps/host/server/fork-identity.mjs"}
# tools that reach the real web or the real upstream on purpose: the agent-tool probe must find the original product's page,
# the research corpus tool says who to contact; neither is something an install runs
LIVE_WEB_TOOLS = {"tools/trellis/make_corpus.py", "apps/host/scripts/agent2-tools-flow.mjs"}

FORBIDDEN = {
    # not case-sensitive: the container registry namespace is lower-case (ghcr), and the scan must see that too
    "the upstream repository": re.compile(r"Niko1221/Strata", re.IGNORECASE),
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


# ---------------------------------------------------------------------------------------------
# the documents: a person copies a command or an install line and runs it (prose may say Strata)
# ---------------------------------------------------------------------------------------------

DOC_ROOT_FILES = ["README.md", "AGENTS.md", "CONTRIBUTING.md", "SECURITY.md"]      # README.*.md (translations) join the scan
# records of upstream runs and releases: the addresses they carry are facts about what happened, not instructions
DOC_HISTORY = re.compile(r"release-notes-|IMPLEMENTATION_STATUS")
# documents that quote raw-engine command lines: `strata --serve`, `--pack`, `--version` there name the engine
# binary, which keeps its name (DIVERGENCE.md, zone 11); only flag-form matches pass in these files
RAW_ENGINE_DOCS = {"docs/AMD_HIP.md", "docs/BATCHING.md", "docs/GYRO.md", "docs/INTEL.md",
                   "docs/NVFP4.md", "docs/KVA.md", "docs/work/QUALIFICATION-KIT.md"}
# the saved-connection label the shipped code writes (LAUNCH_CONNECTION in strata_cli/ui_cmd.py): the doc must match the code
DOC_LABEL = "Strata (strata launch)"
DOC_ENGINE_VALUE = re.compile(r"--engine\s*$")          # `--engine strata`: the value is the engine binary's name
# the Terminal's subcommands (strata_cli/cli.py add_parser names and aliases); `strata` before one of them is a command a person types
DOC_SUBS = (r"launch|start|stop|serve|status|ui|connect|update|monitor|timeskip|tune|profiles?|presets|engines?|"
            r"models|plan|bench|doctor|report|self-test|components|history|restart|rollback|cancel|repair-access|"
            r"verify|validate|sessions|list|show|set|edit|create|delete|import|move|inspect|run|ops|calibrate|"
            r"compare|code|use-config|--[a-z0-9-]+")
DOC_CMD = re.compile(r"(?<![\w./-])strata(?= (?:" + DOC_SUBS + r"\b))")
DOC_UPSTREAM_URL = re.compile(r"Niko1221/Strata", re.IGNORECASE)
DOC_URL_KEPT = re.compile(r"github\.com/sponsors/Niko1221|Niko1221/Strata/pull/\d+|Pagoda\.mp4|fetched on 11 October 2026")
DOC_OLD_FACTS = {
    "the original page port": re.compile(r"\b8791\b"),
    "the original shell port": re.compile(r"\b47831\b"),
    "the original install path": re.compile(r"(?<!deep)bin/strata(?![\w-])|~/\.strata"),
}


def docs_files():
    for rel in DOC_ROOT_FILES:
        yield ROOT / rel
    for path in sorted(ROOT.glob("README.*.md")):            # the translations carry the same copyable one-liners
        yield path
    yield from sorted((ROOT / "docs").rglob("*.md"))


class DocsCarryForkCommands(unittest.TestCase):
    """Commands inside code blocks and backticks name `deepstrata`, and install, release and issue addresses
    name our repository (a 404 until our first release is accepted, and the ghcr images until they are published).
    Prose keeps saying Strata; the history files keep the upstream addresses they recorded."""

    def test_docs_name_the_fork_command_and_the_fork_addresses(self):
        offenders = []
        for path in docs_files():
            rel = path.relative_to(ROOT).as_posix()
            if DOC_HISTORY.search(rel) or not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                for m in DOC_CMD.finditer(line):
                    if (rel == "docs/TERMINAL_LAUNCH.md" and DOC_LABEL in line
                            or rel in RAW_ENGINE_DOCS and line[m.end():].lstrip().startswith("--")
                            or DOC_ENGINE_VALUE.search(line[:m.start()])):
                        continue
                    offenders.append(f"{rel}: a command says strata: …{line[max(0, m.start() - 40):m.end() + 50]}…")
                if DOC_UPSTREAM_URL.search(line) and not DOC_URL_KEPT.search(line):
                    offenders.append(f"{rel}: an address names the upstream repository: {line.strip()[:110]}")
                for name, pattern in DOC_OLD_FACTS.items():
                    if pattern.search(line):
                        offenders.append(f"{rel}: {name} ({pattern.search(line).group(0)!r})")
        self.assertEqual(offenders, [], "a reader would copy an original-product command or address from these docs:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
