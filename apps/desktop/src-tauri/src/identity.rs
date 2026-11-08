//! DeepStrata fork identity: the one place this shell's own names live.
//!
//! DeepStrata is a fork of Strata (DIVERGENCE.md at the repository root) and installs beside the
//! original on the same machine. Everything that would otherwise match the original product — the
//! per-user folders, the preferred port, the executable's name — comes from this module. The words
//! people read (help lines, tray menus, log files) keep saying Strata: the fork is a minimal
//! rebrand, and the zone in DIVERGENCE.md keeps the list.
//!
//! ``strata_control/fork_identity.py`` is the source of these values; ``tests/terminal/
//! test_fork_identity.py`` fails when the copies drift apart.

/// What the person reads in the window title and the installer.
pub const PRODUCT_NAME: &str = "DeepStrata";
/// The folder name under `$XDG_DATA_HOME` (and the XDG bases generally).
pub const FOLDER_POSIX: &str = "deepstrata";
/// The folder name under `%LOCALAPPDATA%` and `%APPDATA%`.
pub const FOLDER_WINDOWS: &str = "DeepStrata";
/// The desktop session file's home folder (`~/.deepstrata/app-session.json`).
/// Only the Linux supervisor writes it (its module is `cfg(target_os = "linux")`), so the other platforms see it unused.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
pub const HOME_DIR: &str = ".deepstrata";
/// Our repository; the app updater reads its releases (tauri.conf.json names the same folder).
/// The shell itself never spells the address (the updater plugin reads it from tauri.conf.json),
/// so this copy exists for the mirror and tests/terminal/test_fork_identity.py.
#[allow(dead_code)]
pub const REPO: &str = "ilfarpro/DeepStrata";
/// The shell's preferred port: two products on one machine must not both want the original's 47841.
pub const DESKTOP_PORT: u16 = 47841;
/// The executable's name (Cargo package name; NSIS takes it as MAINBINARYNAME).
pub const BINARY_NAME: &str = "deepstrata-desktop";
