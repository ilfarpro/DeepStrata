#!/usr/bin/env python3
"""Repository hygiene checks that can run on every pull request.

- tracked files larger than 10 MiB
- relative Markdown links that a change *introduces* broken (legacy breakage does not fail the build)

Usage: python .github/scripts/repo_hygiene.py [--base git-ref]

The link check reads the version at --base for renamed and modified files, so an
old broken link that was only moved with its file stays allowed.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

MAX_BYTES = 10 * 1024 * 1024
LINK = re.compile(r'!?\[[^\]]*\]\(\s*(?:<([^>\n]+)>|([^)\s]+))')
FENCE = re.compile(r'(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$')
INLINE = re.compile(r'`[^`\n]*`')
SKIP = ('http://', 'https://', 'mailto:', 'tel:', 'data:', 'ftp://', '//')


def git(*args: str, check: bool = True) -> str:
    proc = subprocess.run(['git', *args], capture_output=True, text=True)
    if check and proc.returncode:
        raise RuntimeError(proc.stderr.strip() or 'git failed: ' + ' '.join(args))
    return proc.stdout


def strip_code(text: str) -> str:
    """Remove fenced blocks and inline code so examples and commands are not read as links."""
    text = FENCE.sub('', text)
    return INLINE.sub('', text)


def targets(text: str) -> list[str]:
    found: list[str] = []
    for match in LINK.finditer(strip_code(text)):
        raw = match.group(1) or match.group(2) or ''
        raw = raw.strip()
        if not raw or raw.startswith(SKIP + ('#', '/')):
            continue
        found.append(raw)
    return found


def exists(base_dir: Path, raw: str) -> bool:
    path = unquote(raw.split('#', 1)[0].split('?', 1)[0])
    if not path:
        return True
    for candidate in (path, path + '.md', str(Path(path) / 'README.md')):
        target = (base_dir / candidate).resolve()
        if target.is_file() or target.is_dir():
            return True
    return False


def broken(text: str, markdown: Path) -> set[str]:
    return {raw for raw in targets(text) if not exists(markdown.parent, raw)}


def changed_markdown(base: str) -> list[tuple[str, Path, Path | None]]:
    """Return (status, old_path, new_path) for Markdown files changed since base."""
    out: list[tuple[str, Path, Path | None]] = []
    for line in git('diff', '--name-status', '--find-renames=50', base, '--', '*.md').splitlines():
        parts = line.split('\t')
        if not parts:
            continue
        status = parts[0][0]
        if status == 'D':
            continue
        if status == 'R' and len(parts) >= 3:
            out.append((status, Path(parts[1]), Path(parts[2])))
        elif len(parts) >= 2:
            out.append((status, Path(parts[1]), None))
    return out


def old_text(base: str, status: str, old: Path, new: Path | None) -> str | None:
    if status == 'A':
        return None
    proc = subprocess.run(['git', 'show', f'{base}:{old}'], capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=os.environ.get('HYGIENE_BASE', 'HEAD'))
    args = parser.parse_args()
    failures: list[str] = []

    for raw in git('ls-files', '-z').split('\0'):
        if not raw or not os.path.isfile(raw):
            continue
        size = os.path.getsize(raw)
        if size > MAX_BYTES:
            failures.append(f'tracked file over 10 MiB: {raw} ({size / 1048576:.1f} MiB)')

    for status, old, new in changed_markdown(args.base):
        current_path = new or old
        try:
            current = current_path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
        current_broken = broken(current, current_path)
        before = old_text(args.base, status, old, new)
        before_broken = broken(before, old) if before is not None else set()
        introduced = current_broken - before_broken
        for raw in sorted(introduced):
            failures.append(f'broken relative link in {current_path}: {raw}')

    if failures:
        print('Repository hygiene failed:', file=sys.stderr)
        for item in failures:
            print(f'  - {item}', file=sys.stderr)
        return 1
    print('Repository hygiene passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
