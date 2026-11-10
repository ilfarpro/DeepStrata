#!/usr/bin/env python3
"""Repository hygiene checks that can run on every pull request.

- tracked files larger than 10 MiB
- relative Markdown links that a change *introduces* broken (legacy breakage does not fail the build)
- `docs/*.md` references that a change *introduces* dangling (the pages this repository no longer carries)
- mojibake in a changed text file: UTF-8 read as Windows-1252 and saved back (a section sign or a dash, damaged)
- mixed line endings in a changed text file: one file uses one of them

Usage: python .github/scripts/repo_hygiene.py [--base git-ref]

Every check compares against --base and fails only on what this change brings, so the
repository's own history neither blocks a pull request nor becomes invisible: a legacy broken
link that was only moved with its file stays allowed, and a file already broken is not reported
again. Fixing one is always welcome; the checks never ask for it.
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
MAX_READ = 4 * 1024 * 1024                      # a text file this size is not hand-edited: read nothing, check nothing
LINK = re.compile(r'!?\[[^\]]*\]\(\s*(?:<([^>\n]+)>|([^)\s]+))')
FENCE = re.compile(r'(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[ \t]*$')
INLINE = re.compile(r'`[^`\n]*`')
SKIP = ('http://', 'https://', 'mailto:', 'tel:', 'data:', 'ftp://', '//')
# a design page named by path in a code comment, a guide named in a README: `docs/<name>.md`, wherever it is written
DOC_REF = re.compile(r'(?<![\w/])docs/([A-Za-z0-9_][A-Za-z0-9_./-]*\.md)')
# UTF-8 text decoded as Windows-1252 and saved again. These byte runs do not occur in real text, so they are a
# finding and never a false alarm. Escapes on purpose: this file must not carry the damage it looks for.
MOJIBAKE = (
    (b'\xc3\x82\xc2\xa7', 'a section sign decoded twice'),
    (b'\xc3\xa2\xe2\x82\xac', 'a dash or a quote decoded twice'),
    (b'\xc4\x82\xe2\x80\x9a', 'the first character of the same damage'),
)


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


def dangling(text: str, where: Path, root: Path) -> set[str]:
    """`docs/*.md` pages this text names that are not in the tree, read from the file's folder or the repo root.

    Both, because a guide writes `MODELS.md` beside itself and an engine header writes `docs/MODELS.md`,
    which is the same page from the root of the checkout."""
    missing: set[str] = set()
    for m in DOC_REF.finditer(text):
        rel = m.group(1)
        if (where.parent / 'docs' / rel).is_file() or (root / 'docs' / rel).is_file():
            continue
        missing.add(rel)
    return missing


def mixed_endings(raw: bytes) -> bool:
    """True when a file carries both CRLF and bare-LF line endings: one file, one of them."""
    crlf = raw.count(b'\r\n')
    return 0 < crlf < raw.count(b'\n')


def mojibake(raw: bytes) -> dict[int, int]:
    """The damage kinds in this file, each with the line of its first occurrence."""
    found: dict[int, int] = {}
    for kind, (needle, _) in enumerate(MOJIBAKE):
        start = raw.find(needle)
        if start != -1:
            found[kind] = raw.count(b'\n', 0, start) + 1
    return found


def changed_files(base: str) -> list[tuple[str, Path, Path | None]]:
    """Return (status, old_path, new_path) for every file changed since base."""
    out: list[tuple[str, Path, Path | None]] = []
    for line in git('diff', '--name-status', '--find-renames=50', base).splitlines():
        parts = line.split('\t')
        if not parts:
            continue
        status = parts[0][0]
        if status == 'D':
            out.append((status, Path(parts[1]), None))
        elif status == 'R' and len(parts) >= 3:
            out.append((status, Path(parts[1]), Path(parts[2])))
        elif len(parts) >= 2:
            out.append((status, Path(parts[1]), None))
    return out


def old_text(base: str, status: str, old: Path) -> str | None:
    raw = old_bytes(base, status, old)
    return None if raw is None else raw.decode('utf-8', 'surrogateescape')


def old_bytes(base: str, status: str, old: Path) -> bytes | None:
    """The file as it was at base, or None when it is new here."""
    if status == 'A':
        return None
    proc = subprocess.run(['git', 'show', f'{base}:{old}'], capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=os.environ.get('HYGIENE_BASE', 'HEAD'))
    args = parser.parse_args()
    root = Path(git('rev-parse', '--show-toplevel').strip() or '.').resolve()
    os.chdir(root)                                  # every path below is relative to the checkout, as git reports it
    failures: list[str] = []

    for raw in git('ls-files', '-z').split('\0'):
        if not raw or not os.path.isfile(raw):
            continue
        size = os.path.getsize(raw)
        if size > MAX_BYTES:
            failures.append(f'tracked file over 10 MiB: {raw} ({size / 1048576:.1f} MiB)')

    for status, old, new in changed_files(args.base):
        path = new or old
        if status == 'D' or not path.is_file():
            continue
        before = old_text(args.base, status, old)

        # a text file the change touched: the damage checks below read it as bytes, once
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if b'\0' in blob[:8192] or len(blob) > MAX_READ:
            continue

        before_blob = old_bytes(args.base, status, old)
        if before_blob is None or b'\0' not in before_blob[:8192]:
            was = mojibake(before_blob) if before_blob else {}
            for kind, line in mojibake(blob).items():
                if kind not in was:
                    failures.append(f'mojibake in {path}:{line}: {MOJIBAKE[kind][1]}')
            if mixed_endings(blob) and not (before_blob and mixed_endings(before_blob)):
                failures.append(f'mixed CRLF and LF line endings in {path}: use one of them')
            try:
                text = blob.decode('utf-8')
            except UnicodeDecodeError:
                text = ''
            if text:
                was_dangling = dangling(before, old, root) if before is not None else set()
                for rel in sorted(dangling(text, path, root) - was_dangling):
                    failures.append(f'docs/{rel} is referenced by {path}, which is not in the tree')

        # the Markdown link check works on the decoded text so a broken link can be reported with its target
        if path.suffix != '.md':
            continue
        try:
            current = blob.decode('utf-8')
        except UnicodeDecodeError:
            continue
        current_broken = broken(current, path)
        before_broken = broken(before, old) if before is not None else set()
        for raw in sorted(current_broken - before_broken):
            failures.append(f'broken relative link in {path}: {raw}')

    if failures:
        print('Repository hygiene failed:', file=sys.stderr)
        for item in failures:
            print(f'  - {item}', file=sys.stderr)
        return 1
    print('Repository hygiene passed.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
