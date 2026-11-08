"""DeepStrata fork identity: the one place this fork's own names live.

DeepStrata is a fork of Strata (DIVERGENCE.md) and installs beside the original on the same
machine. Everything that would otherwise match the original product — the per-user folders,
the command name, the update feed, the default ports the two products would fight over —
comes from this module. The words people read (sentences, help texts, UI strings) keep
saying Strata: the fork is a minimal rebrand, and the zone in DIVERGENCE.md keeps the list.

The Node host (``apps/host/server/fork-identity.mjs``) carries the same values.
``tests/terminal/test_fork_identity.py`` fails when the copies drift apart, and when an
original-product value (the Niko1221/Strata release address, ``ai.strata.desktop``, the old
ports, ``~/.strata``) appears in product code again. ``setup.py``, ``serve/server.py``,
``sycl/setup_intel.py`` and ``tools/strata_mcp.py`` run without this package, so they carry
their own literal; the test pins those literals to the values below.

Model files are shared with the original on purpose: both products read the same
``Documents/Strata`` folder (``MODEL_FOLDER``), which is why that name stays. The environment
variables keep the ``STRATA_*`` names: they live in one process and never collide between two
installs.
"""

#: What the person reads in the window title, the installer and the Dock.
PRODUCT_NAME = "DeepStrata"
#: The command the person types (``bin/deepstrata``, a link in ``~/.local/bin``).
CLI_NAME = "deepstrata"
#: The folder name under ``~/.local/{state,share,config}`` and the XDG bases.
FOLDER_POSIX = "deepstrata"
#: The folder name under ``%LOCALAPPDATA%`` and ``%APPDATA%``.
FOLDER_WINDOWS = "DeepStrata"
#: The host's home store and the desktop session file (``~/.deepstrata``).
HOME_DIR = ".deepstrata"

#: Our repository and its release folder. The fork never updates from the original's releases.
REPO = "ilfarpro/DeepStrata"
RELEASES_URL = f"https://github.com/{REPO}/releases"
ISSUES_URL = f"{RELEASES_URL}/issues"
LATEST_DOWNLOAD = f"{RELEASES_URL}/latest/download"
#: The signed feed's artifact name stays (it lives inside our own release folder).
FEED_NAME = "strata-feed.json"
FEED_URL = f"{LATEST_DOWNLOAD}/{FEED_NAME}"

#: Ports two products on one machine must not both want. The inference server's defaults
# (8080 / 8095) stay as they are: the person picks one, and a port-in-use error is plain
# and belongs to one install.
HOST_PORT = 8891        # the App Host page (``strata ui``); the share and tunnel ports follow at +1/+2

#: Model files are shared on purpose: both products use the same ``Documents/Strata`` folder,
# so an installed model is never downloaded twice. Settings, chats and state stay apart.
MODEL_FOLDER = "Strata"
