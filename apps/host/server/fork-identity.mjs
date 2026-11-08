/**
 * DeepStrata fork identity: the one place this fork's own names live.
 *
 * DeepStrata is a fork of Strata (DIVERGENCE.md at the repository root) and installs beside the
 * original on the same machine. Everything that would otherwise match the original product — the
 * per-user folders, the home store, the default port, the update feed and release addresses —
 * comes from this module. The words people read (labels, dialogs, menu lines) keep saying
 * Strata: the fork is a minimal rebrand, and the zone in DIVERGENCE.md keeps the list.
 *
 * `strata_control/fork_identity.py` is the source of these values and `apps/desktop/src-tauri/
 * src/identity.rs` carries the desktop shell's copy; `tests/terminal/test_fork_identity.py`
 * fails when the copies drift apart, and when an original-product value (the Niko1221/Strata
 * release address, `ai.strata.desktop`, the old ports, `~/.strata`) appears in product code
 * again.
 *
 * Model files are shared with the original on purpose: both products read the same
 * `Documents/Strata` folder (MODEL_FOLDER), which is why that name stays. The environment
 * variables keep the `STRATA_*` names: they live in one process and never collide between two
 * installs.
 */

/** What the person reads in the window title and the installer. */
export const PRODUCT_NAME = 'DeepStrata';
/** The command the person types (`deepstrata launch`). */
export const CLI_NAME = 'deepstrata';
/** The folder name under `~/.local/{state,share,config}` and the XDG bases. */
export const FOLDER_POSIX = 'deepstrata';
/** The folder name under `%LOCALAPPDATA%` and `%APPDATA%`, and the fork's own folder inside Documents. */
export const FOLDER_WINDOWS = 'DeepStrata';
/** The host's home store and the desktop session file (`~/.deepstrata`). */
export const HOME_DIR = '.deepstrata';

/** Our repository and its release folder. The fork never updates from the original's releases. */
export const REPO = 'ilfarpro/DeepStrata';
export const RELEASES_URL = `https://github.com/${REPO}/releases`;
export const LATEST_DOWNLOAD = `${RELEASES_URL}/latest/download`;
/** The signed feed's artifact name stays (it lives inside our own release folder). */
export const FEED_NAME = 'strata-feed.json';
export const FEED_URL = `${LATEST_DOWNLOAD}/${FEED_NAME}`;

/** Ports two products on one machine must not both want. The inference server's defaults
 * (8080 / 8095) stay as they are: the person picks one, and a port-in-use error is plain and
 * belongs to one install. */
export const HOST_PORT = 8891;      // this host's page; `strata ui` (strata_cli/ui_cmd.py) starts it on the same port
export const DESKTOP_PORT = 47841;  // the desktop shell's preferred port (apps/desktop/src-tauri/src/identity.rs)

/** The desktop shell's executable (Cargo package name; NSIS takes it as MAINBINARYNAME). */
export const BINARY_NAME = 'deepstrata-desktop';

/** Model files are shared on purpose: both products use the same `Documents/Strata` folder,
 * so an installed model is never downloaded twice. Settings, chats and state stay apart. */
export const MODEL_FOLDER = 'Strata';
