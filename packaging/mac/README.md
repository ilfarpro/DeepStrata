# Signing and notarising the Mac builds

`sign-and-notarize.sh` signs the Mac Terminal archive and Mac engine packages (and, for older desktop artifacts, a
`.dmg` or a `.app`) with the Developer ID Application certificate, sends them to Apple's notary service, staples the
ticket where that is possible, and checks the result. After it, macOS opens the file without the
"damaged" / "unidentified developer" warning.

It has to run on the Mac that holds the certificate. It never asks for, reads or stores a password or a key: `codesign` and
`notarytool` take them from the Keychain. The only names in it are the certificate (`Developer ID Application: Niko Veit (79LR4R7RSC)`)
and the notarytool profile (`strata-notary`); `SIGN_ID` and `NOTARY_PROFILE` override them.

**The fork holds no Developer ID certificate yet**, and those two names are the original product's. Until the fork has a certificate of
its own the Mac builds ship without a Developer ID and without notarisation. On release day: build the Terminal archive
(`python packaging/package_terminal.py --target darwin-arm64`), then run

    bash sign-and-notarize.sh --terminal <the built archive> --dry-run

which checks the archive with an ad-hoc signature and touches no Apple account. A browser-downloaded archive may warn on the
first open; [docs/TROUBLESHOOTING.md](../../docs/TROUBLESHOOTING.md) shows what to press. An install through `install.sh` is not
quarantined at all. When the fork holds its own certificate, set `SIGN_ID` and `NOTARY_PROFILE` to its name and profile and drop
`--dry-run` — everything below is that procedure.

## What to type (once per build)

1. Put these on the Mac in one folder: `sign-and-notarize.sh`, `entitlements-node.plist` (they must stay together), and the file(s) to sign,
   for example `strata-terminal-0.2.12-darwin-arm64.tar.gz`.
2. Open Terminal and go to that folder:  `cd ~/Downloads/sign`
3. Check the two things the script needs (both should answer without an error):

       security find-identity -v -p codesigning        # lists "Developer ID Application: Niko Veit (79LR4R7RSC)"
       xcrun notarytool history --keychain-profile strata-notary

4. Run it (the Terminal archive and any engine packages together; either part can be left out):

       bash sign-and-notarize.sh --terminal strata-terminal-0.2.12-darwin-arm64.tar.gz --engine strata-engine-0.1.58-macos-arm64-metal-folder.tar.gz

   macOS may ask once whether `codesign` may use the key in your Keychain: type your Mac login password and choose "Always Allow".
   Then wait: Apple's check takes a few minutes per file (up to about 15 minutes each; the script waits and prints when it is done).
5. Read the summary at the end. Every line is `OK`; the finished files are in the `signed` folder next to the input, with their sha256
   (also in `signed/SHA256SUMS.txt`) and the full log (`signed/sign-and-notarize.log`).
6. If a line says `FAIL`, the run stopped there. If Apple rejected a file, the script prints Apple's log and saves it as
   `signed/notary-log-*.json`. Send us that file (it contains file names and reasons, no secrets).

A trial run that signs ad-hoc and sends nothing to Apple: add `--dry-run` (no certificate needed).

## The DeepSeek engine for the Mac

The Mac DeepSeek engine (`strata-dsv41` with its `metal/` sources and `engine-component.json`) comes from the update feed, not from
any installed bundle, so it is signed here as its own input: `--engine strata-dsv41-0.1.53-macos-arm64-metal-unsigned-folder.tar.gz`, alone or
together with `--terminal` in one run. The script signs every program in the folder with the same identity and options (hardened
runtime, timestamp, no entitlements), writes the new SHA-256 and size into `engine-component.json`,
runs `codesign --verify --strict`, starts the signed program once (no model), sends a zip of the folder to Apple and waits, and
writes `signed/strata-dsv41-0.1.53-macos-arm64-metal-folder.tar.gz` (also listed in `signed/SHA256SUMS.txt`). On the PC, sign that
folder with the feed key (this adds the release signature to the manifest) and build the feed from it; the unsigned folder must not be published.
`--dry-run` signs ad-hoc and skips Apple, as for everything else.

## What it does

For a Terminal or engine archive: sign every Mach-O inside with the hardened runtime and a secure timestamp (Node gets its
one entitlement, [below](#entitlements-each-one-is-a-hole-in-the-hardened-runtime)), pack it again in the format it came in
(`.zip` or `.tar.gz`), and notarise it. A finished file is written to `signed/` only after every step for it has passed.

For a desktop `.app`/`.dmg` (the removed desktop app; kept so old release artifacts can be re-verified): copy the `.app` out of
the dmg, sign every Mach-O inside it deepest first, rewrite `engine-component.json` for the engine just signed (Control checks
every engine file against the SHA-256 in that manifest, and signing changes the binary), verify, run the signed copy's self-test,
notarise, staple, rebuild and sign the dmg. The old in-app updater steps (packing `Strata.app.tar.gz` for the Tauri updater)
went with the desktop app.

## Entitlements (each one is a hole in the hardened runtime)

Only the bundled Node gets one: `com.apple.security.cs.allow-jit` (`entitlements-node.plist`, with the measurements). Python, the engine
and all libraries get none. `disable-library-validation` is not used: library validation only refuses a library signed
by somebody else, and every library in these bundles is re-signed with the one identity. If a tester ever reports a "different Team IDs"
error for a library that is not in the bundle (for example an addon an MCP server installs and loads with this node), add that key to
`entitlements-node.plist` and run the script again.

The engine components Strata downloads later (`strata update`) are unpacked by Control without the download mark, run as their own
processes, and are not hardened by their parent's runtime (a child process is not bound by its parent's hardened runtime), so unsigned
components keep working. A signed engine keeps our signature; it needs no entitlement (Metal compiles the
kernel sources through the system compiler service, tested under the hardened runtime).

## Terminal archives

Apple cannot staple a ticket to bare command-line programs in an archive. The archive is still notarised (Apple records the programs by hash),
and macOS looks that up online the first time a browser-downloaded copy is opened. Installs done with `curl ... | sh` are not
quarantined at all, so this mainly helps archives downloaded in a browser.

Signing changes the bytes of the archive and of every program inside, so:

* a checksum list or signed feed (`strata-feed.json`, `*.manifest.json`) that names the old archive must be rebuilt from the new one;
* an archive that holds a *feed* (the tester Terminal archive: scripts plus `feed/*.tar.gz`) has no program at its top level. By default
  the nested `.tar.gz` files are left alone (they are unpacked by command-line tools and carry no download mark). `--sign-nested`
  signs inside them and packs them again, but then the feed's `sha256` entries and its signature (`strata-feed.json.sig`, made with the
  release key) no longer match and the feed has to be regenerated and re-signed.

## History

The desktop app (up to 0.2.12) shipped as `Strata.app` in a notarised dmg and updated itself from a Tauri-signed
`Strata.app.tar.gz`; `--updater-archive` packed that file after signing and notarisation, and the release PC signed it with
the Tauri updater key and `feed_tool.py set-app-platform` recorded it. The desktop app is removed and the feed has no `app`
member; `--updater-archive` still exists in the script for old artifacts but makes no release file today.
