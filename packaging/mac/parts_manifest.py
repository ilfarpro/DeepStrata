#!/usr/bin/env python3
"""Write `mac-parts.json`: the hand-off manifest `python packaging/release.py build --mac-parts` reads.

`packaging/build_mac_parts.sh` calls this last, once the Terminal archive, its smoke report and the engine packages are in
the parts folder. It hashes what is there and writes one file with the commit, the versions, the toolchain and every hash,
so the release PC can check that the folder is exactly what that Mac built, and that no file changed on the way.

The manifest is written last, and through a temporary name, so a folder handed over early never holds a half-written
manifest. `tests/tools/test_release_tool.py` runs this against a fabricated folder and then reads it with
`release._read_parts_manifest`, so the two sides of that contract stay in step.

Usage:  python3 packaging/mac/parts_manifest.py --out DIR --commit SHA \
            --component 0.1.61 --product 0.2.13 --host 0.10.10 \
            [--terminal terminal/strata-terminal-<v>-darwin-arm64.tar.gz] \
            [--smoke smoke/smoke-terminal.json] \
            [--engine qwen=engines/<v>-macos-arm64-metal] [--engine dsv41=dsv41/<v>-macos-arm64-metal] \
            [--toolchain "clang=Apple clang version 17.0.0"] ...
        `--dirty` records that the tree had uncommitted changes (the release tool then refuses the parts).
        Paths are relative to --out.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

PLATFORM = "macos-arm64"
MANIFEST = "mac-parts.json"
SCHEMA = 1
ENGINE_NAMES = ("qwen", "dsv41")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_engine(text: str) -> tuple[str, str]:
    name, _, folder = text.partition("=")
    if name not in ENGINE_NAMES or not folder:
        raise SystemExit(f"--engine expects {' or '.join(ENGINE_NAMES)} = <folder>, not {text!r}")
    return name, folder


def write_manifest(folder: Path, *, commit: str, dirty: bool, component: str, product: str, host: str,
                   toolchain: dict[str, str], terminal: str | None, smoke: str | None,
                   engines: dict[str, str]) -> Path:
    components: dict[str, dict] = {}
    if terminal:
        archive = folder / terminal
        if not archive.is_file():
            raise SystemExit(f"--terminal {terminal} is not a file under {folder}")
        components["terminal"] = {"archive": terminal, "sha256": sha256(archive),
                                  "bytes": archive.stat().st_size, "format": "tar.gz"}
    for name, relative in engines.items():
        engine = folder / relative
        manifest = engine / "engine-component.json"
        if not manifest.is_file():
            raise SystemExit(f"--engine {name}={relative}: {manifest} is missing (package_engine.py did not run?)")
        components[name] = {"folder": relative, "manifest_sha256": sha256(manifest)}

    smoke_section: dict[str, dict] = {}
    if smoke:
        report = folder / smoke
        if not report.is_file():
            raise SystemExit(f"--smoke {smoke} is not a file under {folder}")
        smoke_section["terminal"] = {"report": smoke, "sha256": sha256(report)}
    if not components:
        # release.py refuses a manifest without components; say why here, where the operator can see which step was skipped
        raise SystemExit("nothing to record: pass --terminal and/or at least one --engine")

    manifest = {
        "schema_version": SCHEMA,
        "platform": PLATFORM,
        "commit": commit,
        "dirty": bool(dirty),
        "component_version": component,
        "product_version": product,
        "host_version": host,
        "built_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "packaging/build_mac_parts.sh",
        "toolchain": {name: value for name, value in toolchain.items() if value},
        "components": components,
        "smoke": smoke_section,
    }
    target = folder / MANIFEST
    temporary = folder / (MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path, help="the parts folder")
    parser.add_argument("--commit", required=True, help="the commit the build came from")
    parser.add_argument("--component", required=True, help="the component version (CMakeLists.txt)")
    parser.add_argument("--product", required=True, help="the product version (strata_cli/__init__.py)")
    parser.add_argument("--host", required=True, help="the page version (apps/host/package.json)")
    parser.add_argument("--terminal", help="the Terminal archive, relative to --out")
    parser.add_argument("--smoke", help="the Terminal smoke report, relative to --out")
    parser.add_argument("--engine", action="append", default=[], metavar="NAME=FOLDER",
                        help="an engine package folder, relative to --out (qwen, dsv41; repeatable)")
    parser.add_argument("--toolchain", action="append", default=[], metavar="NAME=VALUE",
                        help="one toolchain line (clang, macos, node, cmake, git; repeatable)")
    parser.add_argument("--dirty", action="store_true", help="the tree had uncommitted changes")
    args = parser.parse_args(argv)

    folder = args.out
    if not folder.is_dir():
        raise SystemExit(f"--out {folder} is not a folder")
    engines: dict[str, str] = {}
    for item in args.engine:
        name, relative = parse_engine(item)
        engines[name] = relative
    toolchain: dict[str, str] = {}
    for item in args.toolchain:
        name, _, value = item.partition("=")
        if not name:
            raise SystemExit(f"--toolchain expects name=value, not {item!r}")
        toolchain[name] = value

    target = write_manifest(folder, commit=args.commit, dirty=args.dirty, component=args.component,
                            product=args.product, host=args.host, toolchain=toolchain,
                            terminal=args.terminal, smoke=args.smoke, engines=engines)
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
