#!/usr/bin/env python3
"""Make a DeepStrata release: plan, build, sign, pack, verify and publish.

This is the release-side tool. It runs on the release PC (the one that holds the feed key),
builds the artifacts that machine can build natively, signs what it built, writes one signed
update feed, checks it the way a user's PC would, and only then creates the GitHub release.

    python packaging/release.py plan --require-merged BRANCH
    python packaging/release.py build
    python packaging/release.py assemble
    python packaging/release.py verify
    python packaging/release.py publish

`plan` writes RELEASE/<version>/release.json and stops on anything that would make a bad
release: a dirty tree, a branch that is not main, a merge that has not happened, a tag that
already exists, a key that is not the one shipped, or failing tests. Every later step checks
that journal and the commit it names, so a release cannot be built from different sources
half-way through.

Windows parts are built natively on a Windows PC by `packaging/build_windows.ps1`
(the `windows-parts` workflow runs it on a self-hosted Windows runner). That script
writes an unsigned parts folder with the Terminal archive, the Qwen CUDA 13 engine and
the `strata-dsv41` engine, plus a `windows-parts.json` that names the commit and every
hash. `build --windows-parts` imports and checks it here, on this machine; signing still
happens only here, with the feed key. `plan --target` narrows a release to one platform;
with no `--target` it carries linux-x86_64 and windows-x86_64. macOS, HIP and ARM builds
are not part of this tool yet: they are built on their own machines and added by hand.

What this tool does NOT do, on purpose:
* it never builds Windows natively, never sees a Windows toolchain and never puts the
  feed key on a build machine; it only imports the unsigned Windows parts and signs
  them here;
* it does not Authenticode-sign Windows binaries: the Windows parts go out unsigned
  (SmartScreen may warn, as the Mac builds do until they hold a certificate);
* it never prints, copies or stores the private key;
* it never deletes a published release, a tag or an uploaded asset. A wrong release is fixed
  by publishing the next one.

The private key is read from --key, STRATA_FEED_KEY, or ~/deepstrata-keys/feed-key.json. It
must not live inside this git checkout. The GitHub token is read from GH_TOKEN/GITHUB_TOKEN or
the `gh` config (github.com oauth_token); it is never printed.

Steps and what they leave in RELEASE/<version>/:
    release.json      the journal: version, commit, sequence, key id, assets and every step
    notes.md          the GitHub release body (from commits, or --notes FILE)
    parts/            build outputs and signed copies
    feed/             the flat files uploaded to the release (feed, signature, installers,
                      archives, install-manifest.txt)
    logs/             build, test and publish logs
    verify.json       the verification report
    publish.json      what was uploaded and how the latest release checked out
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import stat
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT, Path(__file__).resolve().parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import feed_tool  # noqa: E402
from strata_control import engine_component as ec, feed as feedmod, fork_identity, signing  # noqa: E402

PRODUCT = fork_identity.PRODUCT_NAME
REPO = fork_identity.REPO
LATEST_DOWNLOAD = fork_identity.LATEST_DOWNLOAD
DEFAULT_KEY = Path.home() / "deepstrata-keys" / "feed-key.json"
RELEASE_DIR_NAME = "RELEASE"
LINUX = "linux-x86_64"
WINDOWS = "windows-x86_64"
TARGETS = (LINUX, WINDOWS)
WINDOWS_PARTS_NAME = "windows-parts.json"
WINDOWS_PARTS_SCHEMA = 1
AREAS = ("Engine", "GUI", "TUI", "Benchmark", "Docs", "Other")
TAG_PREFIX = "ds-"
VERSION_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
AREA_RE = re.compile(r"^\[(Engine|GUI|TUI|Benchmark|Docs|Other)\]\s*", re.IGNORECASE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_SEQUENCE_FILE = "strata-feed.json"
FEED_ASSETS = ("strata-feed.json", "strata-feed.json.sig", "install-manifest.txt", "install.sh", "install.ps1")
#: What a release page may carry: the Terminal/Control archives (tar.gz on Linux, zip on Windows), the package manifests,
#: the signed feed with its signature, install-manifest.txt and the two one-line installers.
RELEASE_ASSET_SUFFIXES = (".tar.gz", ".zip", ".json", ".sig", ".txt", ".sh", ".ps1")


def is_release_asset(name: str) -> bool:
    """True when a feed file may be uploaded to the release page. A new archive format has to be added here first."""
    return name.endswith(RELEASE_ASSET_SUFFIXES)


#: The code every engine package must carry, and the driver it needs: the same for both platforms.
ENGINE_ARCHS = ("sm_86", "sm_89", "sm_120", "compute_120")
MIN_NVIDIA_DRIVER = "580.65"


def control_format(platform: str) -> str:
    """The archive format a platform's Terminal/Control package is packed in: `install.ps1` unpacks zip, `install.sh` tar.gz."""
    return "zip" if platform == WINDOWS else "tar.gz"


class ReleaseError(RuntimeError):
    """A step cannot go on. The message is for the person running the release."""


class GitHubError(ReleaseError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(f"GitHub refused the request (HTTP {status}): {message}")


# ------------------------------------------------------------------------------------------------ small helpers


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def say(message: str) -> None:
    print(message, flush=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise ReleaseError(f"{path} is missing") from None
    except ValueError as exc:
        raise ReleaseError(f"{path} is not JSON ({exc})") from None
    if not isinstance(data, dict):
        raise ReleaseError(f"{path} does not hold a JSON object")
    return data


def write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    temporary.replace(path)


def which(name: str) -> str | None:
    return shutil.which(name)


def git(*args: str, root: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=str(root or ROOT), text=True, capture_output=True)
    if check and result.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return (result.stdout or "").strip()


def _tail(path: Path, lines: int = 40) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(text[-lines:])


def run_command(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, log: Path | None = None,
                check: bool = True, timeout: float | None = None, quiet: bool = False) -> int:
    """Run one command, streaming its output to the console and to `log`."""
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open("a", encoding="utf-8")
        stream.write(f"\n$ {shlex.join(cmd)}\n")
        stream.flush()
    else:
        stream = None
    say(f"$ {shlex.join(cmd)}")
    process = subprocess.Popen(cmd, cwd=str(cwd or ROOT), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
            if stream is not None:
                stream.write(line)
                stream.flush()
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        code = 124
        say(f"stopped: the command ran longer than {timeout}s")
    finally:
        if stream is not None:
            stream.close()
    if check and code != 0:
        where = f" (log: {log})" if log else ""
        raise ReleaseError(f"`{shlex.join(cmd)}` failed with exit {code}{where}")
    return code


def ensure_clean(root: Path) -> None:
    status = git("status", "--porcelain", root=root)
    if status:
        first = "\n".join(status.splitlines()[:8])
        raise ReleaseError(f"{root} has uncommitted changes; a release must be built from a commit:\n{first}")


def ensure_commit(root: Path, journal: dict) -> None:
    head = git("rev-parse", "HEAD", root=root)
    if head != journal.get("commit"):
        raise ReleaseError(f"this checkout is at {head[:12]}, but the release was planned from "
                           f"{str(journal.get('commit', '?'))[:12]}; check out that commit (or plan again)")
    ensure_clean(root)


def release_root(root: Path | None = None) -> Path:
    return Path(root or ROOT) / RELEASE_DIR_NAME


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def validate_version(text: str) -> str:
    text = text.removeprefix(TAG_PREFIX)
    if not VERSION_RE.fullmatch(text):
        raise ReleaseError(f"{text!r} is not a release version like 0.2.12.2")
    return text


def project_version(root: Path | None = None) -> str:
    text = (Path(root or ROOT) / "CMakeLists.txt").read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\s*project\([^)]*?VERSION\s+([0-9]+(?:\.[0-9]+)+)", text, re.MULTILINE)
    if not match:
        raise ReleaseError("CMakeLists.txt has no project(... VERSION ...)")
    return match.group(1)


def product_version(root: Path | None = None) -> str:
    text = (Path(root or ROOT) / "strata_cli" / "__init__.py").read_text(encoding="utf-8", errors="replace")
    match = re.search(r'^PRODUCT_VERSION\s*=\s*"([0-9]+(?:\.[0-9]+)+)"', text, re.MULTILINE)
    if not match:
        raise ReleaseError("strata_cli/__init__.py has no PRODUCT_VERSION")
    return match.group(1)


def host_version(root: Path | None = None) -> str:
    data = read_json(Path(root or ROOT) / "apps" / "host" / "package.json")
    version = data.get("version")
    if not isinstance(version, str):
        raise ReleaseError("apps/host/package.json has no version")
    return version


def local_release_tags(root: Path | None = None) -> list[str]:
    return [tag for tag in git("tag", "--list", f"{TAG_PREFIX}*", root=root).splitlines() if tag]


def next_release_version(product: str, tags: list[str]) -> str:
    """0.2.12.1 and ds-0.2.11.9 in the history: the next on the 0.2.12 line is 0.2.12.2."""
    highest = 0
    for tag in tags:
        text = tag.removeprefix(TAG_PREFIX)
        parts = text.split(".")
        if len(parts) != 4 or not all(part.isdigit() for part in parts):
            continue
        if ".".join(parts[:3]) == product:
            highest = max(highest, int(parts[3]))
    return f"{product}.{highest + 1}"


def previous_release_tag(root: Path | None = None) -> str | None:
    tags = [tag for tag in local_release_tags(root) if VERSION_RE.fullmatch(tag.removeprefix(TAG_PREFIX))]
    if not tags:
        return None
    return max(tags, key=lambda tag: version_key(tag.removeprefix(TAG_PREFIX)))


# ------------------------------------------------------------------------------------------------ keys and tokens


def inside_repo(path: Path, root: Path | None = None) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root or ROOT).resolve())
        return True
    except ValueError:
        return False


def default_key_path() -> Path:
    from_env = os.environ.get("STRATA_FEED_KEY")
    return Path(from_env).expanduser() if from_env else DEFAULT_KEY


def resolve_key_path(explicit: str | None, root: Path | None = None) -> Path:
    path = Path(explicit).expanduser() if explicit else default_key_path()
    path = path.resolve()
    if not path.is_file():
        raise ReleaseError(f"the feed key {path} does not exist; pass --key PATH or set STRATA_FEED_KEY")
    if inside_repo(path, root):
        raise ReleaseError(f"the private key {path} is inside the git checkout; a private key must not be where "
                           "it can be committed")
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            raise ReleaseError(f"{path} is readable by other users (mode {mode:o}); run chmod 600 {path}")
    return path


def load_secret_key(path: Path) -> tuple[dict, dict]:
    key = read_json(path)
    if key.get("alg") != signing.ALG or "seed" not in key:
        raise ReleaseError(f"{path} is not a secret feed key (no ed25519 seed)")
    public = signing.public_part(key)
    trusted = signing.load_trusted_keys(environ={})
    shipped = trusted.get(public["key_id"])
    if shipped is None:
        raise ReleaseError(f"the key {path} is not the one this build ships: {public['key_id']} is not in "
                           "strata_control/update_keys.json")
    if base64.b64decode(public["public"]) != shipped:
        raise ReleaseError(f"the key {path} says key id {public['key_id']}, but the shipped public key differs")
    return key, public


def token_from_hosts(path: Path) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*oauth_token:\s*(\S+)\s*$", text)
    return match.group(1) if match else None


def find_token(environ: dict | None = None) -> tuple[str | None, str]:
    environ = os.environ if environ is None else environ
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = environ.get(name)
        if value:
            return value.strip(), name
    config = Path(environ.get("GH_CONFIG_DIR", Path.home() / ".config" / "gh")) / "hosts.yml"
    value = token_from_hosts(config)
    if value:
        return value, str(config)
    return None, "not found (set GH_TOKEN, or log in with gh)"


# ------------------------------------------------------------------------------------------------ GitHub


def parse_repo(url: str) -> str:
    """owner/name from an ssh or https remote."""
    text = url.strip()
    match = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", text)
    if not match:
        raise ReleaseError(f"cannot read a GitHub owner/name from {url!r}")
    return f"{match.group(1)}/{match.group(2)}"


class GitHub:
    def __init__(self, token: str, repo: str, *, timeout: float = 60):
        self.token = token
        self.repo = repo
        self.timeout = timeout

    # -- HTTP
    def _request(self, method: str, path: str, body: dict | None = None, *, allow_404: bool = False,
                 url: str | None = None) -> tuple[int, bytes] | None:
        full = url or f"https://api.github.com{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "DeepStrata-release"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(full, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                return answer.status, answer.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            if exc.code == 404 and allow_404:
                return None
            message = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(message).get("message", message)
            except (ValueError, AttributeError):
                pass
            raise GitHubError(exc.code, str(message)[:400]) from None
        except urllib.error.URLError as exc:
            raise ReleaseError(f"GitHub cannot be reached: {exc.reason}") from None

    def _json(self, method: str, path: str, body: dict | None = None, *, allow_404: bool = False):
        answer = self._request(method, path, body, allow_404=allow_404)
        if answer is None:
            return None
        return json.loads(answer[1] or b"null")

    def get(self, path: str):
        return self._json("GET", path)

    def get_optional(self, path: str):
        return self._json("GET", path, allow_404=True)

    # -- identity
    def user(self) -> dict:
        return self.get("/user")

    def repository(self) -> dict:
        return self.get(f"/repos/{self.repo}")

    def check_push(self) -> str:
        user = self.user().get("login", "?")
        repo = self.repository()
        if repo.get("full_name", "").lower() != self.repo.lower():
            raise ReleaseError(f"GitHub says this is {repo.get('full_name')!r}, not {self.repo!r}")
        permissions = repo.get("permissions") or {}
        if not permissions.get("push"):
            raise ReleaseError(f"the token for {user} cannot push to {self.repo}; it needs repo access")
        return user

    # -- tags and releases
    def tag_commit(self, tag: str) -> str | None:
        ref = self.get_optional(f"/repos/{self.repo}/git/ref/tags/{urllib.parse.quote(tag, safe='')}")
        if ref is None:
            return None
        obj = ref.get("object") or {}
        if obj.get("type") == "commit":
            return obj.get("sha")
        if obj.get("type") == "tag":
            tag_object = self.get(f"/repos/{self.repo}/git/tags/{obj.get('sha')}")
            return (tag_object.get("object") or {}).get("sha")
        raise ReleaseError(f"the remote tag {tag} names an unexpected object ({obj.get('type')})")

    def latest_release(self) -> dict | None:
        return self.get_optional(f"/repos/{self.repo}/releases/latest")

    def release_by_tag(self, tag: str) -> dict | None:
        return self.get_optional(f"/repos/{self.repo}/releases/tags/{urllib.parse.quote(tag, safe='')}")

    def create_release(self, *, tag: str, commit: str, name: str, body: str) -> dict:
        payload = {"tag_name": tag, "target_commitish": commit, "name": name, "body": body, "draft": False,
                   "prerelease": False, "generate_release_notes": False, "make_latest": "true"}
        answer = self._request("POST", f"/repos/{self.repo}/releases", payload)
        assert answer is not None
        return json.loads(answer[1])

    def assets(self, release_id: int) -> list[dict]:
        found: list[dict] = []
        page = 1
        while True:
            chunk = self.get(f"/repos/{self.repo}/releases/{release_id}/assets?per_page=100&page={page}")
            items = chunk if isinstance(chunk, list) else []
            found.extend(items)
            if len(items) < 100:
                return found
            page += 1

    def delete_asset(self, asset_id: int) -> None:
        self._request("DELETE", f"/repos/{self.repo}/releases/assets/{asset_id}")

    def download(self, url: str, *, limit: int | None = None) -> bytes:
        headers = {"User-Agent": "DeepStrata-release"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                return answer.read(limit) if limit else answer.read()
        except urllib.error.HTTPError as exc:
            raise GitHubError(exc.code, f"could not download {url}") from None
        except urllib.error.URLError as exc:
            raise ReleaseError(f"could not download {url}: {exc.reason}") from None

    # -- upload
    def upload_asset(self, release_id: int, path: Path, *, content_type: str = "application/octet-stream") -> dict:
        path = Path(path)
        query = urllib.parse.urlencode({"name": path.name})
        url = f"https://uploads.github.com/repos/{self.repo}/releases/{release_id}/assets?{query}"
        parsed = urllib.parse.urlsplit(url)
        size = path.stat().st_size
        if size > 2 * 1024 ** 3:
            raise ReleaseError(f"{path.name} is {size} bytes; GitHub's asset limit is 2 GiB")
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=1800,
                                                 context=ssl.create_default_context())
        target = parsed.path + "?" + parsed.query
        connection.putrequest("POST", target)
        connection.putheader("Authorization", f"Bearer {self.token}")
        connection.putheader("Accept", "application/vnd.github+json")
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(size))
        connection.putheader("User-Agent", "DeepStrata-release")
        connection.endheaders()
        sent, shown = 0, 0
        started = time.monotonic()
        with path.open("rb") as stream:
            while True:
                block = stream.read(1 << 20)
                if not block:
                    break
                connection.send(block)
                sent += len(block)
                if size >= (64 << 20) and (sent - shown >= (64 << 20) or sent == size):
                    shown = sent
                    say(f"  uploaded {sent / (1 << 20):.0f}/{size / (1 << 20):.0f} MiB "
                        f"({sent * 8 / max(time.monotonic() - started, 0.1) / (1 << 20):.1f} MiB/s)")
        answer = connection.getresponse()
        raw = answer.read()
        connection.close()
        if answer.status not in (200, 201, 202):
            message = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(message).get("message", message)
            except (ValueError, AttributeError):
                pass
            raise GitHubError(answer.status, str(message)[:400])
        data = json.loads(raw or b"{}")
        say(f"  uploaded {path.name} ({size / (1 << 20):.1f} MiB)")
        return data


# ------------------------------------------------------------------------------------------------ notes


def commits_since(root: Path, previous: str | None) -> list[dict]:
    spec = f"{previous}..HEAD" if previous else "HEAD"
    raw = git("log", spec, "--format=%H%x1f%s%x1e", root=root)
    records = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit, _, subject = record.partition("\x1f")
        if not subject.strip():
            continue
        if subject.startswith("Merge "):
            continue
        records.append({"commit": commit.strip(), "subject": subject.strip()})
    return records


def group_notes(records: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {area: [] for area in AREAS}
    canonical = {area.lower(): area for area in AREAS}
    for record in records:
        subject = record["subject"]
        match = AREA_RE.match(subject)
        area = canonical.get(match.group(1).lower(), "Other") if match else "Other"
        text = subject[match.end():].strip() if match else subject
        grouped[area].append({"commit": record["commit"], "text": text})
    return grouped


def generate_notes(*, version: str, component: str, commit: str, previous: str | None, records: list[dict],
                   repo: str = REPO, line: str | None = None,
                   targets: tuple[str, ...] = TARGETS) -> str:
    grouped = group_notes(records)
    lines = [f"{PRODUCT} {version}", ""]
    lines.append(f"Built from `{commit[:12]}` on the {line or product_version()} line. Components inside carry "
                 f"`{component}`.")
    if previous:
        lines.append(f"Changes since `{previous}`:")
    lines.append("")
    lines.append("## What changed")
    for area in AREAS:
        items = grouped[area]
        if not items:
            continue
        lines.append("")
        lines.append(f"### {area}")
        for item in items:
            text = item["text"]
            if len(text) > 220:
                text = text[:217].rstrip() + "..."
            lines.append(f"- {text} (`{item['commit'][:9]}`)")
    lines.append("")
    lines.append("## Install")
    lines.append("")
    if LINUX in targets:
        lines.append("Linux x86_64, no administrator rights:")
        lines.append("")
        lines.append("```sh")
        lines.append(f"curl -fsSL https://github.com/{repo}/releases/latest/download/install.sh -o install.sh && sh install.sh")
        lines.append(f"deepstrata launch")
        lines.append("```")
    if WINDOWS in targets:
        if LINUX in targets:
            lines.append("")
        lines.append("Windows x86_64 (PowerShell), no administrator rights:")
        lines.append("")
        lines.append("```powershell")
        lines.append(f"irm https://github.com/{repo}/releases/latest/download/install.ps1 | iex")
        lines.append(f"deepstrata launch")
        lines.append("```")
        lines.append("")
        lines.append("The Windows build is not Authenticode-signed yet: Windows SmartScreen may ask once before it runs. "
                     "The signed feed still checks the sha256 of every file before anything is installed.")
    lines.append("")
    lines.append("The one-line installer unpacks the Terminal (the page rides inside it) and the signed feed is checked "
                 "before anything is installed. `deepstrata launch` downloads the engine for this PC's card and the "
                 "model you choose.")
    lines.append("")
    lines.append("## Artifacts")
    lines.append("")
    for platform in targets:
        lines.append(f"- Terminal/Control `{component}` for `{platform}`")
    for platform in targets:
        lines.append(f"- Qwen CUDA 13 engine `{component}` for `{platform}`")
    for platform in targets:
        lines.append(f"- DeepSeek V4.1-Flash (`strata-dsv41`) CUDA 13 engine `{component}` for `{platform}`")
    lines.append("")
    lines.append("`strata-feed.json` in this release is signed; its signature is `strata-feed.json.sig`.")
    return "\n".join(lines).rstrip() + "\n"


def write_notes(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


# ------------------------------------------------------------------------------------------------ journal


def load_journal(directory: Path) -> dict:
    journal = read_json(directory / "release.json")
    if journal.get("schema_version") != 1:
        raise ReleaseError(f"{directory / 'release.json'} has an unknown schema")
    return journal


def save_journal(directory: Path, journal: dict) -> None:
    journal["updated_at"] = now()
    write_json(directory / "release.json", journal)


def set_step(journal: dict, name: str, data: dict) -> None:
    journal.setdefault("steps", {})[name] = {"at": now(), **data}


def release_directory(args, root: Path | None = None) -> tuple[str, Path]:
    root = Path(root or ROOT)
    explicit = getattr(args, "release", None)
    if explicit:
        version = validate_version(explicit)
        return version, release_root(root) / version
    base = release_root(root)
    found = sorted(path for path in base.glob("*") if (path / "release.json").is_file()) if base.is_dir() else []
    if len(found) == 1:
        journal = read_json(found[0] / "release.json")
        return journal["release"], found[0]
    if not found:
        raise ReleaseError(f"no release journal under {base}; run `plan` first")
    names = ", ".join(path.name for path in found)
    raise ReleaseError(f"more than one release under {base} ({names}); name one with --release VERSION")


def commit_short(commit: str) -> str:
    return commit[:12]


# ------------------------------------------------------------------------------------------------ checks used by plan


def _major(version_text: str) -> int | None:
    match = re.search(r"(\d+)\.(\d+)", version_text)
    return int(match.group(1)) if match else None


def find_cuda_root(explicit: str | None) -> tuple[Path, str]:
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        found = which("nvcc")
        candidates = [Path(found).resolve() if found else None,
                      Path("/usr/local/cuda/bin/nvcc"),
                      Path("/usr/local/cuda-13/bin/nvcc"),
                      Path("/usr/local/cuda-13.5/bin/nvcc")]
        root = next((path.parent.parent for path in candidates if path and path.is_file()), None)
        if root is None:
            raise ReleaseError("nvcc was not found; a CUDA 13 build needs the toolkit (pass --cuda-root)")
    nvcc = root / "bin" / "nvcc"
    if not nvcc.is_file():
        raise ReleaseError(f"{nvcc} does not exist; pass --cuda-root with the CUDA 13 toolkit")
    result = subprocess.run([str(nvcc), "--version"], text=True, capture_output=True)
    text = result.stdout + result.stderr
    match = re.search(r"release\s+(\d+\.\d+)", text)
    release = match.group(1) if match else "unknown"
    if _major(release) != 13:
        raise ReleaseError(f"{nvcc} is CUDA {release}, but this release builds the cuda13 variant; it needs CUDA 13.x")
    return root, release


def _tool_version(cmd: list[str], pattern: str) -> str | None:
    try:
        result = subprocess.run(cmd, text=True, capture_output=True)
    except OSError:
        return None
    match = re.search(pattern, result.stdout + result.stderr)
    return match.group(1) if match else None


def check_tools(cuda_root: Path | None = None, cuda_release: str | None = None) -> dict:
    """The tools this machine builds with. CUDA is only asked for when the release has a Linux target: a Windows
    release is built on Windows, by packaging/build_windows.ps1, and never needs nvcc here."""
    problems: list[str] = []
    cmake = which("cmake")
    ninja = which("ninja")
    node = which("node")
    npm = which("npm")
    if not cmake:
        problems.append("cmake is missing")
    if not ninja:
        problems.append("ninja is missing")
    if not node:
        problems.append("node is missing (the page build and its tests need Node >= 24.12)")
    if not npm:
        problems.append("npm is missing (the page build and its tests need it)")
    llama = ROOT / "third_party" / "llama.cpp"
    if not (llama / "CMakeLists.txt").is_file():
        problems.append("third_party/llama.cpp is missing (run: python -c \"import setup; setup.get_llama_cpp()\")")
    node_version = _tool_version([node, "--version"], r"v(\d+\.\d+\.\d+)") if node else None
    if node_version and tuple(int(part) for part in node_version.split(".")) < (24, 12, 0):
        problems.append(f"node is {node_version}; the App Host needs >= 24.12.0")
    free_gib = shutil.disk_usage(ROOT).free / (1 << 30)
    if free_gib < 12:
        problems.append(f"only {free_gib:.1f} GiB free; the builds and the release folder need about 12 GiB")
    pytest_version = _tool_version([sys.executable, "-m", "pytest", "--version"], r"pytest\s+(\d+\.\d+)")
    if not pytest_version:
        problems.append(f"pytest is not installed for {sys.executable} (pip install pytest)")
    if problems:
        raise ReleaseError("the build tools are not ready:\n  - " + "\n  - ".join(problems))
    return {"python": sys.version.split()[0], "pytest": pytest_version, "cmake": _tool_version([cmake, "--version"], r"cmake version (\d+\.\d+\.\d+)"),
            "ninja": _tool_version([ninja, "--version"], r"(\d+\.\d+\.\d+)"), "node": node_version, "npm": npm,
            "cuda_root": str(cuda_root) if cuda_root else None, "cuda": cuda_release if cuda_root else None,
            "llama_cpp": str(llama), "free_gib": round(free_gib, 1),
            "app_host_dist": (ROOT / "apps" / "host" / "dist" / "index.html").is_file(),
            "app_host_modules": (ROOT / "apps" / "host" / "node_modules").is_dir()}


def _npm_env(directory: Path) -> dict:
    """npm must not need to write to ~/.npm: the release folder carries its cache and logs."""
    return {**os.environ, "npm_config_cache": str(directory / "npm-cache"),
            "npm_config_logs_dir": str(directory / "logs" / "npm")}


def _host_tests(directory: Path, timeout: float | None) -> dict:
    npm = which("npm")
    if not npm:
        raise ReleaseError("npm is missing; the App Host tests cannot run (install Node 24.12 or newer)")
    host = ROOT / "apps" / "host"
    log_dir = directory / "logs"
    env = _npm_env(directory)
    if not (host / "node_modules").is_dir():
        log = log_dir / "host-npm-ci.log"
        run_command([npm, "ci", "--no-audit", "--no-fund"], cwd=host, env=env, log=log, timeout=timeout)
    results = []
    commands = [([sys.executable, "-m", "pytest", "-q", "tests/terminal", "tests/serve", "tests/tools"], ROOT, "pytest.log"),
                ([npm, "test"], host, "host-npm-test.log")]
    test_env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "PYTHONIOENCODING": "utf-8"}
    test_env.pop("NO_COLOR", None)       # the page and the Terminal tests check colours and the Unicode cursor
    for cmd, cwd, name in commands:
        log = log_dir / name
        started = time.monotonic()
        say(f"running {name} ...")
        code = run_command(cmd, cwd=cwd, env=env if cmd[0] == npm else test_env, log=log, check=False, timeout=timeout)
        item = {"command": shlex.join(cmd), "cwd": str(cwd), "exit": code, "seconds": round(time.monotonic() - started, 1),
                "log": str(log.relative_to(directory))}
        results.append(item)
        if code != 0:
            raise ReleaseError(f"the test gate failed: {shlex.join(cmd)} (exit {code})\n"
                               f"last lines of {log}:\n{_tail(log)}")
    return {"ok": True, "results": results}


def _published_sequence(api: GitHub) -> tuple[int, str | None]:
    release = api.latest_release()
    if not release:
        return 0, None
    assets = {asset.get("name"): asset for asset in release.get("assets") or []}
    feed_asset = assets.get(DEFAULT_SEQUENCE_FILE)
    signature_asset = assets.get(DEFAULT_SEQUENCE_FILE + ".sig")
    if not feed_asset or not signature_asset:
        raise ReleaseError(f"the latest release ({release.get('tag_name')}) has no signed feed; fix the feed first")
    payload = api.download(feed_asset["browser_download_url"], limit=4 << 20)
    signature = api.download(signature_asset["browser_download_url"], limit=64 << 10)
    trusted = signing.load_trusted_keys(environ={})
    signing.verify_bytes(trusted, payload, json.loads(signature))
    document = json.loads(payload)
    sequence = document.get("sequence")
    if not isinstance(sequence, int):
        raise ReleaseError(f"the latest feed has no sequence ({sequence!r})")
    return sequence, release.get("tag_name")


def _github_for_plan(token: str | None) -> GitHub | None:
    return GitHub(token or "", REPO, timeout=30)


def artifact_plan(component: str, targets: tuple[str, ...] = TARGETS) -> list[dict]:
    plan: list[dict] = []
    if LINUX in targets:
        plan += [
            {"name": "terminal", "platform": LINUX,
             "artifact": f"strata-terminal-{component}-linux-x86_64.tar.gz",
             "what": "Terminal archive (page and Node inside; the Control package is the same bundle)"},
            {"name": "control", "platform": LINUX,
             "artifact": f"strata-control-{component}-linux-x86_64.tar.gz",
             "what": "Control package for the update feed and install.sh"},
            {"name": "qwen", "platform": LINUX,
             "artifact": f"strata-engine-{component}-linux-x86_64-cuda-cuda13.tar.gz",
             "what": "Qwen CUDA 13 engine package"},
            {"name": "dsv41", "platform": LINUX,
             "artifact": f"strata-dsv41-{component}-linux-x86_64-cuda-cuda13.tar.gz",
             "what": "DeepSeek V4.1-Flash engine package (feed member dsv41)"},
        ]
    if WINDOWS in targets:
        plan += [
            {"name": "windows-parts", "platform": WINDOWS,
             "artifact": f"strata-terminal-{component}-windows-x86_64.zip + the Windows engine packages",
             "what": "built on Windows by packaging/build_windows.ps1, imported here with build --windows-parts"},
            {"name": "control", "platform": WINDOWS,
             "artifact": f"strata-control-{component}-windows-x86_64.zip",
             "what": "Control package for the update feed and install.ps1"},
            {"name": "qwen", "platform": WINDOWS,
             "artifact": f"strata-engine-{component}-windows-x86_64-cuda-cuda13.tar.gz",
             "what": "Qwen CUDA 13 engine package (sm_75 .. sm_120)"},
            {"name": "dsv41", "platform": WINDOWS,
             "artifact": f"strata-dsv41-{component}-windows-x86_64-cuda-cuda13.tar.gz",
             "what": "DeepSeek V4.1-Flash engine package (feed member dsv41)"},
        ]
    return plan


def journal_targets(journal: dict) -> tuple[str, ...]:
    """The platforms a journal releases. A journal written before Windows was a target has no list: that release is Linux only,
    and must not start asking for Windows parts after an upgrade of this tool."""
    named = journal.get("targets")
    return tuple(named) if named else (LINUX,)


# ------------------------------------------------------------------------------------------------ plan


def cmd_plan(args) -> int:
    root = Path(args.root)
    product = product_version(root)
    component = project_version(root)
    host = host_version(root)
    if host != product:
        raise ReleaseError(f"the page is {host} but the Terminal is {product}; the page and the Terminal carry one version")
    head = git("rev-parse", "HEAD", root=root)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", root=root)
    if branch != "main" and not args.allow_branch:
        raise ReleaseError(f"releases are built from main, this checkout is on {branch!r} (--allow-branch only for a dry run)")
    if not args.allow_dirty:
        ensure_clean(root)
    tags = local_release_tags(root)
    version = validate_version(args.release) if args.release else next_release_version(product, tags)
    if not version.startswith(product + ".") and not args.allow_line:
        raise ReleaseError(f"release {version} is not on the {product} line (the page/Terminal version); "
                           f"the next number would be {next_release_version(product, tags)}")
    tag = TAG_PREFIX + version
    if tag in tags:
        raise ReleaseError(f"the tag {tag} already exists locally; a release tag is never reused")
    if args.require_merged:
        if subprocess.run(["git", "merge-base", "--is-ancestor", args.require_merged, head], cwd=str(root)).returncode != 0:
            raise ReleaseError(f"{args.require_merged} is not merged into HEAD ({head[:12]}); merge it and plan again")
    merged = None
    if args.require_merged:
        merged = {"branch": args.require_merged, "tip": git("rev-parse", args.require_merged, root=root)}

    key_path = resolve_key_path(args.key, root)
    key, public = load_secret_key(key_path)
    key_id = public["key_id"]
    token, token_source = find_token()

    targets = tuple(dict.fromkeys(args.target or TARGETS))
    for target in targets:
        if target not in TARGETS:
            raise ReleaseError(f"{target!r} cannot be built by this tool; this release builds {', '.join(TARGETS)}. "
                               "Windows is built on Windows by packaging/build_windows.ps1 and imported with "
                               "build --windows-parts; macOS/HIP/ARM parts are still added by hand")

    if LINUX in targets:
        cuda_root, cuda_release = find_cuda_root(args.cuda_root)
        tools = check_tools(cuda_root, cuda_release)
    else:
        tools = check_tools()
        say("CUDA is not checked: this release has no linux-x86_64 target, and the Windows engine is built on Windows")

    latest_tag = None
    published = 0
    api = _github_for_plan(token)
    if not args.offline:
        if not token:
            say("warning: no GitHub token found; plan can build, but publish will need GH_TOKEN or ~/.config/gh/hosts.yml")
        try:
            if token:
                user = api.check_push()
                say(f"GitHub: {user} can push to {REPO}")
            remote_main = api.get(f"/repos/{REPO}/commits/main").get("sha")
            if remote_main != head and not args.allow_unpushed:
                raise ReleaseError(f"origin/main is {str(remote_main)[:12]}, HEAD is {head[:12]}: push main first "
                                   "(--allow-unpushed only for a dry run)")
            remote_tag = api.tag_commit(tag)
            if remote_tag:
                raise ReleaseError(f"the tag {tag} already exists on GitHub (it names {remote_tag[:12]})")
            published, latest_tag = _published_sequence(api)
        except ReleaseError:
            raise
        except Exception as exc:  # noqa: BLE001 - any network/API problem must stop a release, with its text
            raise ReleaseError(f"the online checks failed ({type(exc).__name__}: {exc}); fix them or pass --offline") from None
        if latest_tag:
            latest_version = latest_tag.removeprefix(TAG_PREFIX)
            if VERSION_RE.fullmatch(latest_version) and version_key(version) <= version_key(latest_version):
                raise ReleaseError(f"the latest release is {latest_tag}; the new version {tag} must be newer")
        if args.sequence is None:
            sequence = published + 1
        else:
            sequence = args.sequence
        if sequence <= published:
            raise ReleaseError(f"sequence {sequence} would not be newer than the published feed (sequence {published})")
    else:
        sequence = args.sequence
        if sequence is None:
            raise ReleaseError("--offline needs --sequence (the published feed cannot be read)")
        say(f"offline plan: sequence {sequence}; remote tag, remote main and the published feed are not checked")

    directory = release_root(root) / version
    directory.mkdir(parents=True, exist_ok=True)
    old = (directory / "release.json")
    preserved_build = None
    if old.is_file():
        existing = read_json(old)
        if existing.get("commit") != head and not args.replan:
            raise ReleaseError(f"{old} was planned from {str(existing.get('commit'))[:12]}, not {head[:12]}; "
                               "use a new version, or --replan to replace that journal")
        if (existing.get("steps") or {}).get("publish", {}).get("ok"):
            raise ReleaseError(f"{tag} was already published; a published release is never rewritten")
        prior = (existing.get("steps") or {}).get("build") or {}
        if prior.get("ok") and prior.get("commit") == head:
            preserved_build = prior       # same commit: plan again without throwing the built parts away
    journal = {
        "schema_version": 1,
        "release": version,
        "tag": tag,
        "component_version": component,
        "product_version": product,
        "host_version": host,
        "commit": head,
        "branch": branch,
        "targets": list(targets),
        "sequence": sequence,
        "repo": REPO,
        "key_id": key_id,
        "notes": "notes.md",
        "created_at": now(),
        "updated_at": now(),
        "steps": {},
        "artifacts": {},
        "warnings": [],
    }
    if preserved_build:
        journal["steps"]["build"] = preserved_build
    if latest_tag:
        journal["previous_release"] = latest_tag
    if merged:
        journal["merged"] = merged
    set_step(journal, "plan", {"ok": False, "started_at": now()})
    save_journal(directory, journal)
    say(f"{PRODUCT} {version} (tag {tag})")
    say(f"  commit {head[:12]} on {branch}; component {component}; product {product}")
    if merged:
        say(f"  merged: {merged['branch']} at {merged['tip'][:12]}")
    say(f"  feed sequence {sequence}; key {key_id}; targets {', '.join(targets)}")
    say(f"  GitHub token: {token_source}")

    try:
        if args.skip_tests:
            say("tests: skipped (--skip-tests)")
            tests = {"ok": False, "skipped": True}
        else:
            tests = _host_tests(directory, args.test_timeout)
            say("tests: passed")
        journal["tools"] = tools
        notes_path = directory / "notes.md"
        if args.notes:
            source = Path(args.notes).expanduser()
            if not source.is_file():
                raise ReleaseError(f"--notes {source} does not exist")
            write_notes(notes_path, source.read_text(encoding="utf-8"))
            journal["notes_source"] = str(source)
        elif not (notes_path.is_file() and args.keep_notes):
            previous = previous_release_tag(root)
            notes = generate_notes(version=version, component=component, commit=head, previous=previous,
                                   records=commits_since(root, previous), line=product, targets=targets)
            write_notes(notes_path, notes)
        set_step(journal, "plan", {"ok": True, "started_at": journal["steps"]["plan"]["started_at"], "tests": tests})
        save_journal(directory, journal)
    except ReleaseError as exc:
        set_step(journal, "plan", {"ok": False, "error": str(exc)})
        save_journal(directory, journal)
        raise
    say("")
    for item in artifact_plan(component, targets):
        say(f"  will build {item['artifact']}")
    say("")
    say(f"journal: {directory / 'release.json'}")
    say("next: python3 packaging/release.py build" + (f" --release {version}" if args.release else ""))
    if args.json:
        print(json.dumps(journal, indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ build


def _require_step(journal: dict, name: str) -> dict:
    step = (journal.get("steps") or {}).get(name) or {}
    if not step.get("ok"):
        raise ReleaseError(f"the {name} step has not succeeded in this release journal; run it first")
    return step


def _rel(path: Path, directory: Path) -> str:
    return Path(path).resolve().relative_to(directory.resolve()).as_posix()


def _root_rel(path: Path) -> str:
    return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()


def _extract_terminal(archive: Path, directory: Path, name: str) -> Path:
    unpack = directory / "parts" / "unpack"
    target = unpack / name
    if target.exists():
        shutil.rmtree(target)
    unpack.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(unpack, filter="data")
    if not target.is_dir():
        children = [path for path in unpack.iterdir() if path.is_dir()]
        if len(children) != 1:
            raise ReleaseError(f"{archive} did not unpack to one folder ({[p.name for p in children]})")
        target = children[0]
    if not (target / "bin" / "deepstrata").is_file():
        raise ReleaseError(f"{target} has no bin/deepstrata; it is not a Terminal archive")
    return target


def _verify_engine(folder: Path, log: Path) -> None:
    code = run_command([sys.executable, "packaging/package_engine.py", "--verify", str(folder)], log=log, check=False)
    if code != 0:
        raise ReleaseError(f"{folder} did not verify; the build log is {log}")


def _build_host(directory: Path, *, rebuild: bool) -> dict:
    host = ROOT / "apps" / "host"
    dist = host / "dist" / "index.html"
    logs = directory / "logs"
    npm = which("npm")
    if not npm:
        raise ReleaseError("npm is missing; the App Host (the page in the Terminal archive) cannot be built")
    built = False
    if rebuild or not dist.is_file():
        env = _npm_env(directory)
        if not (host / "node_modules").is_dir():
            run_command([npm, "ci", "--no-audit", "--no-fund"], cwd=host, env=env, log=logs / "host-npm-ci.log")
        run_command([npm, "run", "build"], cwd=host, env=env, log=logs / "host-build.log")
        built = True
    if not dist.is_file():
        raise ReleaseError("apps/host/dist/index.html is still missing after the build")
    return {"dist": _root_rel(dist), "rebuilt": built}


def _build_terminal(directory: Path, journal: dict, *, rebuild: bool) -> dict:
    component = journal["component_version"]
    commit = journal["commit"]
    parts = directory / "parts"
    name = f"strata-terminal-{component}-linux-x86_64"
    archive = parts / f"{name}.tar.gz"
    unpack = parts / "unpack" / name
    logs = directory / "logs"
    if unpack.is_dir() and (unpack / "bin" / "deepstrata").is_file() and not rebuild:
        say(f"reusing {unpack.relative_to(directory)}")
        if not archive.is_file():
            raise ReleaseError(f"{archive} is missing but {unpack} exists; rebuild with --rebuild")
        return {"archive": _rel(archive, directory), "unpack": _rel(unpack, directory),
                "sha256": sha256_file(archive), "bytes": archive.stat().st_size, "reused": True}
    run_command([sys.executable, "packaging/package_terminal.py", "--out", str(parts), "--target", "linux-x86_64",
                 "--version", component, "--commit", commit], log=logs / "build-terminal.log")
    if not archive.is_file():
        raise ReleaseError(f"package_terminal.py finished but {archive} is missing")
    unpack = _extract_terminal(archive, directory, name)
    return {"archive": _rel(archive, directory), "unpack": _rel(unpack, directory),
            "sha256": sha256_file(archive), "bytes": archive.stat().st_size}


def _build_qwen(directory: Path, journal: dict, cuda_root: Path, *, rebuild: bool) -> dict:
    component = journal["component_version"]
    parts = directory / "parts"
    folder = parts / "engine" / f"{component}-linux-x86_64-cuda-cuda13"
    logs = directory / "logs"
    if folder.is_dir() and not rebuild:
        say(f"reusing {folder.relative_to(directory)}")
        _verify_engine(folder, logs / "verify-qwen.log")
        return {"folder": _rel(folder, directory), "reused": True}
    env = {**os.environ, "VARIANT": "cuda13", "OUT": str(parts / "engine"),
           "BUILD": str(directory / "build" / "cuda13"), "PYTHON": sys.executable,
           "PATH": f"{cuda_root / 'bin'}:{os.environ.get('PATH', '')}"}
    run_command(["sh", "packaging/build_cuda_linux.sh"], env=env, log=logs / "build-qwen.log")
    if not folder.is_dir():
        found = sorted((parts / "engine").glob("*cuda13*"))
        if len(found) != 1:
            raise ReleaseError(f"the Qwen build finished but {folder} is missing and {parts / 'engine'} holds "
                               f"{[p.name for p in found]}")
        folder = found[0]
    _verify_engine(folder, logs / "verify-qwen.log")
    return {"folder": _rel(folder, directory)}


def _build_dsv41(directory: Path, journal: dict, cuda_root: Path, cuda_release: str, *, rebuild: bool) -> dict:
    component = journal["component_version"]
    commit = journal["commit"]
    parts = directory / "parts"
    folder = parts / "dsv41" / f"{component}-linux-x86_64-cuda-cuda13"
    logs = directory / "logs"
    if folder.is_dir() and not rebuild:
        say(f"reusing {folder.relative_to(directory)}")
        _verify_engine(folder, logs / "verify-dsv41.log")
        return {"folder": _rel(folder, directory), "reused": True}
    build = directory / "build" / "dsv41"
    env = {**os.environ, "SRC": str(ROOT), "B": str(build), "CUDA": str(cuda_root),
           "GG": str(ROOT / "third_party" / "llama.cpp"), "COMMIT": commit,
           "JOBS": str(os.cpu_count() or 4), "PATH": f"{cuda_root / 'bin'}:{os.environ.get('PATH', '')}"}
    run_command(["bash", "packaging/build_dsv41_cuda.sh"], env=env, log=logs / "build-dsv41.log")
    binary = build / "strata-dsv41"
    if not binary.is_file():
        raise ReleaseError(f"build_dsv41_cuda.sh finished but {binary} is missing")
    stage = parts / "dsv41-stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    run_command([sys.executable, "packaging/collect_libs.py", "--binary", str(binary), "--search",
                 str(cuda_root / "lib64"), "--out", str(stage), "--also", "libcublasLt.so*"],
                log=logs / "collect-dsv41.log")
    shutil.copy2(binary, stage / "strata-dsv41")
    (stage / "strata-dsv41").chmod(0o755)
    license_file = ROOT / "LICENSE"
    if license_file.is_file():
        shutil.copy2(license_file, stage / "LICENSE")
    build_json = {"version": component, "source": "packaging/release.py",
                  "archs": "sm_86;sm_89;sm_120;compute_120", "cuda": cuda_release, "portable": True,
                  "commit": commit}
    (stage / "BUILD.json").write_text(json.dumps(build_json) + "\n", encoding="utf-8")
    run_command([sys.executable, "packaging/package_engine.py", "--src", str(stage), "--backend", "cuda",
                 "--version", component, "--out", str(parts / "dsv41"), "--platform", "linux-x86_64",
                 "--variant", "cuda13", "--entrypoint", "strata-dsv41", "--arch", "sm_86", "--arch", "sm_89",
                 "--arch", "sm_120", "--arch", "compute_120", "--min-driver", "nvidia=580.65",
                 "--build-id", commit[:9], "--include", "SYSTEM-LIBS.txt"],
                log=logs / "package-dsv41.log")
    if not folder.is_dir():
        found = sorted((parts / "dsv41").glob(f"{component}-linux-x86_64-cuda*"))
        if len(found) != 1:
            raise ReleaseError(f"the dsv41 package was not written where expected ({parts / 'dsv41'})")
        folder = found[0]
    _verify_engine(folder, logs / "verify-dsv41.log")
    return {"folder": _rel(folder, directory)}


def _platform_map(value: dict | None) -> dict[str, dict]:
    """A build component as {platform: info}. A journal from before Windows stored the Linux entry flat."""
    if not value:
        return {}
    if any(platform in value for platform in TARGETS):
        return {name: info for name, info in value.items() if name in TARGETS}
    return {LINUX: value}


def _put_platform(components: dict, name: str, platform: str, info: dict) -> None:
    entry = dict(_platform_map(components.get(name)))
    entry[platform] = info
    components[name] = entry


def _parts_source(source: Path, directory: Path) -> Path:
    """The folder that holds windows-parts.json; accepts the folder, an artifact zip, or one wrapper folder."""
    source = Path(source).expanduser().resolve()
    if source.is_file() and source.suffix.lower() == ".zip":
        target = directory / "parts" / "windows-artifact"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        with zipfile.ZipFile(source) as archive:
            archive.extractall(target)
        return _parts_source(target, directory)
    if source.is_dir():
        if (source / WINDOWS_PARTS_NAME).is_file():
            return source
        nested = sorted(path for path in source.iterdir() if path.is_dir())
        for path in nested:
            if (path / WINDOWS_PARTS_NAME).is_file():
                return path
        raise ReleaseError(f"{source} has no {WINDOWS_PARTS_NAME}; it is not the folder build_windows.ps1 wrote")
    raise ReleaseError(f"{source} is neither a Windows parts folder nor the artifact zip")


def _read_windows_parts(source: Path) -> dict:
    try:
        parts = read_json(source / WINDOWS_PARTS_NAME)
    except ReleaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - a broken hand-off must name the file
        raise ReleaseError(f"{source / WINDOWS_PARTS_NAME} cannot be read ({exc})") from None
    if parts.get("schema_version") != WINDOWS_PARTS_SCHEMA:
        raise ReleaseError(f"{source / WINDOWS_PARTS_NAME}: unknown schema {parts.get('schema_version')!r}; "
                           "rebuild with this checkout's packaging/build_windows.ps1")
    if parts.get("platform") != WINDOWS:
        raise ReleaseError(f"{source / WINDOWS_PARTS_NAME}: platform is {parts.get('platform')!r}, not {WINDOWS}")
    for key in ("commit", "component_version", "product_version", "host_version", "components"):
        if not parts.get(key):
            raise ReleaseError(f"{source / WINDOWS_PARTS_NAME} has no {key!r}")
    return parts


def _check_windows_parts(parts: dict, journal: dict, source: Path) -> None:
    if parts.get("dirty"):
        raise ReleaseError(f"{source} was built from an uncommitted tree (dirty); build the committed tree "
                           "(packaging/build_windows.ps1 without -AllowDirty)")
    if parts["commit"] != journal["commit"]:
        raise ReleaseError(f"{source} was built from {parts['commit'][:12]}, but this release is "
                           f"{journal['commit'][:12]}; rebuild the Windows parts from the release commit")
    if parts["component_version"] != journal["component_version"]:
        raise ReleaseError(f"{source} carries component {parts['component_version']}, but this release is "
                           f"{journal['component_version']}; rebuild from the same commit")
    if parts.get("product_version") != journal["product_version"] or parts.get("host_version") != journal["host_version"]:
        raise ReleaseError(f"{source} carries product {parts.get('product_version')}/page {parts.get('host_version')}, "
                           f"but this release is {journal['product_version']}/{journal['host_version']}")
    # a parts folder with no engine was never near CUDA (build_windows.ps1 -Only terminal), so only an engine makes
    # the toolkit something to check
    if "qwen" in (parts.get("components") or {}) or "dsv41" in (parts.get("components") or {}):
        cuda = str((parts.get("toolchain") or {}).get("cuda") or "")
        if not cuda.startswith("13."):
            raise ReleaseError(f"{source} was built with CUDA {cuda or 'unknown'}; the release's Windows package is "
                               "cuda13 (CUDA 13.x)")


def _check_file(path: Path, *, sha256: str | None = None, size: int | None = None) -> None:
    if not path.is_file():
        raise ReleaseError(f"{path} is missing")
    if size is not None and path.stat().st_size != size:
        raise ReleaseError(f"{path}: {path.stat().st_size} bytes, expected {size}")
    if sha256 and sha256_file(path) != sha256:
        raise ReleaseError(f"{path}: sha256 does not match the Windows parts manifest")


def _extract_windows_terminal(archive: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(destination)
    children = [path for path in destination.iterdir() if path.is_dir()]
    root = children[0] if len(children) == 1 else destination
    if not (root / "bin" / "deepstrata.cmd").is_file():
        raise ReleaseError(f"{archive} has no bin/deepstrata.cmd; it is not a Windows Terminal archive")
    if not (root / "host" / "dist" / "index.html").is_file():
        raise ReleaseError(f"{archive} has no host/dist/index.html; the page is not inside it")
    if not (root / "node" / "node.exe").is_file():
        raise ReleaseError(f"{archive} has no node/node.exe; the pinned Node is not inside it")
    return root


def _windows_smoke(source: Path, parts: dict, archive_name: str) -> dict:
    info = (parts.get("smoke") or {}).get("terminal")
    if not info:
        raise ReleaseError("the Windows parts have no Terminal smoke report; build again with build_windows.ps1 "
                           "without -SkipSmoke (the release tool refuses an untested Terminal)")
    report_path = source / info["report"]
    _check_file(report_path, sha256=info.get("sha256"))
    report = read_json(report_path)
    if not report.get("ok"):
        raise ReleaseError(f"the Windows Terminal smoke test failed ({report_path})")
    if report.get("archive") != archive_name:
        raise ReleaseError(f"{report_path} says it tested {report.get('archive')!r}, not {archive_name!r}")
    checks = report.get("checks") or {}
    for name in ("version", "doctor", "profile list", "serve: chat answer"):
        if not (checks.get(name) or {}).get("ok"):
            raise ReleaseError(f"the Windows Terminal smoke test did not pass {name!r} ({report_path})")
    return report


def _windows_exec_bits(directory: Path) -> None:
    """Windows has no execute permission: every file is one there. A parts folder that travelled as an artifact zip or
    over SFTP comes back with the bit cleared, and `package_engine.py --verify` refuses an engine whose entrypoint is
    not executable (os.access on POSIX), so the bit is set again here. The package itself carries no permission."""
    for path in directory.rglob("*"):
        if path.is_file() and not os.access(path, os.X_OK):
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _import_windows_parts(directory: Path, journal: dict, source_arg: Path,
                          wanted: list[str], *, rebuild: bool) -> tuple[dict, dict]:
    """Copy and check the unsigned Windows parts into the release folder. Returns ({component: info}, record)."""
    source = _parts_source(source_arg, directory)
    parts = _read_windows_parts(source)
    _check_windows_parts(parts, journal, source)
    target = directory / "parts" / "windows"
    if rebuild and target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    imported: dict[str, dict] = {}
    for name in wanted:
        info = (parts.get("components") or {}).get(name)
        if not info:
            raise ReleaseError(f"{source} has no {name} component; build it (packaging/build_windows.ps1 -Only {name})")
        if name == "terminal":
            archive = source / info["archive"]
            _check_file(archive, sha256=info.get("sha256"), size=info.get("bytes"))
            folder = target / "terminal"
            folder.mkdir(parents=True, exist_ok=True)
            copied = folder / archive.name
            shutil.copy2(archive, copied)
            unpack = _extract_windows_terminal(copied, target / "unpack" / archive.name[:-4])
            smoke = _windows_smoke(source, parts, archive.name)
            smoke_path = target / "smoke" / "smoke-terminal.json"
            smoke_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / (parts["smoke"]["terminal"]["report"]), smoke_path)
            imported[name] = {
                "archive": _rel(copied, directory),
                "unpack": _rel(unpack, directory),
                "sha256": sha256_file(copied),
                "bytes": copied.stat().st_size,
                "smoke": _rel(smoke_path, directory),
                "smoke_ok": bool(smoke.get("ok")),
            }
            continue
        folder = source / info["folder"]
        if not folder.is_dir():
            raise ReleaseError(f"{folder} is missing from the Windows parts")
        manifest_sha = sha256_file(folder / ec.MANIFEST) if (folder / ec.MANIFEST).is_file() else None
        if info.get("manifest_sha256") and manifest_sha != info["manifest_sha256"]:
            raise ReleaseError(f"{folder}: engine-component.json changed after the parts manifest was written")
        destination = target / ("engines" if name == "qwen" else "dsv41") / folder.name
        if destination.exists():
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(folder, destination)
        _windows_exec_bits(destination)
        _verify_engine(destination, directory / "logs" / f"verify-windows-{name}.log")
        imported[name] = {"folder": _rel(destination, directory), "manifest_sha256": manifest_sha}
    record = {"source": str(source), "manifest_sha256": sha256_file(source / WINDOWS_PARTS_NAME),
              "imported_at": now(), "components": sorted(imported)}
    return imported, record


def cmd_build(args) -> int:
    version, directory = release_directory(args, Path(args.root))
    journal = load_journal(directory)
    _require_step(journal, "plan")
    ensure_commit(Path(args.root), journal)
    (directory / "parts").mkdir(parents=True, exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    targets = journal_targets(journal)
    wanted = args.only or ["terminal", "qwen", "dsv41"]
    wanted = [name for name in ("terminal", "qwen", "dsv41") if name in wanted]
    previous_build = (journal.get("steps") or {}).get("build") or {}
    same_commit = previous_build.get("commit") == journal["commit"]
    if not same_commit:
        # build outputs are reused only for the commit they were built from: a replanned journal must rebuild
        args.rebuild = True
    previous_components: dict = {}
    for name, value in ((previous_build.get("components") or {}) if same_commit else {}).items():
        previous_components[name] = value if name == "host" else _platform_map(value)
    previous_wanted = (previous_build.get("wanted") or []) if same_commit else []
    components = dict(previous_components)
    windows_parts_record = previous_build.get("windows_parts")

    if args.rebuild:
        if LINUX in targets and "terminal" in wanted:
            for path in (directory / "parts").glob("strata-terminal-*.tar.gz"):
                path.unlink()
            shutil.rmtree(directory / "parts" / "unpack", ignore_errors=True)
        if LINUX in targets and "qwen" in wanted:
            shutil.rmtree(directory / "parts" / "engine", ignore_errors=True)
        if LINUX in targets and "dsv41" in wanted:
            shutil.rmtree(directory / "parts" / "dsv41", ignore_errors=True)
            shutil.rmtree(directory / "parts" / "dsv41-stage", ignore_errors=True)
        if WINDOWS in targets:
            shutil.rmtree(directory / "parts" / "windows", ignore_errors=True)
            for name in wanted:
                entry = dict(_platform_map(components.get(name)))
                entry.pop(WINDOWS, None)
                components[name] = entry

    started = now()
    if LINUX in targets and wanted:
        cuda_root, cuda_release = find_cuda_root(args.cuda_root or (journal.get("tools") or {}).get("cuda_root"))
        if "terminal" in wanted:
            components["host"] = _build_host(directory, rebuild=args.rebuild_host or args.rebuild)
            _put_platform(components, "terminal", LINUX, _build_terminal(directory, journal, rebuild=args.rebuild))
        if "qwen" in wanted:
            _put_platform(components, "qwen", LINUX, _build_qwen(directory, journal, cuda_root, rebuild=args.rebuild))
        if "dsv41" in wanted:
            _put_platform(components, "dsv41", LINUX,
                          _build_dsv41(directory, journal, cuda_root, cuda_release, rebuild=args.rebuild))
    if WINDOWS in targets and wanted:
        if args.windows_parts:
            imported, windows_parts_record = _import_windows_parts(
                directory, journal, Path(args.windows_parts), wanted, rebuild=args.rebuild)
            for name, info in imported.items():
                _put_platform(components, name, WINDOWS, info)
        else:
            have = {name for name in wanted if WINDOWS in _platform_map(components.get(name))}
            missing = [name for name in wanted if name not in have]
            if missing:
                raise ReleaseError(
                    "the Windows parts are missing (" + ", ".join(missing) + "). Build them on Windows with "
                    "`powershell -ExecutionPolicy Bypass -File packaging\\build_windows.ps1` (or run the "
                    "`windows-parts` workflow on a self-hosted Windows runner), then run build again with "
                    "--windows-parts <folder or artifact.zip>. Nothing Windows is built on this machine.")
    set_step(journal, "build", {"ok": True, "started_at": started, "commit": journal["commit"],
                                "targets": list(targets),
                                "wanted": sorted(set(previous_wanted) | set(wanted)),
                                "components": components,
                                "windows_parts": windows_parts_record})
    save_journal(directory, journal)
    say("")
    say(f"built: {directory / 'parts'}")
    say("next: python3 packaging/release.py assemble" + (f" --release {version}" if args.release else ""))
    return 0


# ------------------------------------------------------------------------------------------------ assemble


def content_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {".json": "application/json", ".sig": "application/json", ".txt": "text/plain",
            ".sh": "text/x-shellscript", ".ps1": "text/plain", ".zip": "application/zip"}.get(
                suffix, "application/octet-stream")


def _flat(path: Path) -> list[Path]:
    return sorted((item for item in Path(path).iterdir() if item.is_file()), key=lambda item: item.name)


def inspect_feed(feed_dir: Path) -> dict:
    files = _flat(feed_dir)
    nested = [item.name for item in Path(feed_dir).iterdir() if item.is_dir()]
    if nested:
        raise ReleaseError(f"{feed_dir} must be flat for GitHub release assets; it has folders: {nested}")
    names = [item.name for item in files]
    missing = [name for name in FEED_ASSETS if name not in names]
    if missing:
        raise ReleaseError(f"{feed_dir} is missing {missing}")
    assets = {}
    for item in files:
        assets[item.name] = {"bytes": item.stat().st_size, "sha256": sha256_file(item),
                             "content_type": content_type(item.name)}
    return assets


def _release_targets(journal: dict) -> tuple[str, ...]:
    targets = journal_targets(journal)
    for target in targets:
        if target not in TARGETS:
            raise ReleaseError(f"the journal names {target!r}, which this release tool cannot assemble")
    return targets


def _platform_component(journal: dict, name: str) -> dict[str, dict]:
    value = (journal["steps"]["build"]["components"]).get(name)
    return {platform: info for platform, info in _platform_map(value).items() if platform in TARGETS}


def _require_built(component: dict[str, dict], name: str, platform: str) -> dict:
    info = component.get(platform)
    if not info:
        raise ReleaseError(f"the {name} part for {platform} was not built or imported; run `build` first")
    return info


def _signed_copy(source: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return destination


def _copy_flat(source_dir: Path, feed_dir: Path) -> list[str]:
    copied = []
    for path in _flat(source_dir):
        if path.name.endswith(".entry.json"):
            continue
        target = feed_dir / path.name
        if target.exists():
            raise ReleaseError(f"two artifacts would be uploaded as {path.name}")
        shutil.copy2(path, target)
        copied.append(path.name)
    return copied


def cmd_assemble(args) -> int:
    version, directory = release_directory(args, Path(args.root))
    journal = load_journal(directory)
    _require_step(journal, "plan")
    _require_step(journal, "build")
    ensure_commit(Path(args.root), journal)
    key_path = resolve_key_path(args.key, Path(args.root))
    key, public = load_secret_key(key_path)
    if public["key_id"] != journal.get("key_id"):
        raise ReleaseError(f"{key_path} signs as {public['key_id']}, but the plan named {journal.get('key_id')}")
    component = journal["component_version"]
    targets = _release_targets(journal)
    terminals = _platform_component(journal, "terminal")
    qwens = _platform_component(journal, "qwen")
    dsv41s = _platform_component(journal, "dsv41")
    feed = directory / "feed"
    entries = directory / "parts" / "entries"
    signed = directory / "parts" / "signed"
    for path in (feed, entries, signed):
        shutil.rmtree(path, ignore_errors=True)
    feed.mkdir(parents=True)

    say("signing the engine packages")
    entries_engine = entries / "engine"
    entries_dsv41 = entries / "dsv41"
    entries_control = entries / "control"
    for name, packages, prefix, out in (("qwen", qwens, "strata-engine", entries_engine),
                                        ("dsv41", dsv41s, "strata-dsv41", entries_dsv41)):
        for platform in targets:
            info = _require_built(packages, name, platform)
            folder = directory / info["folder"]
            if not folder.is_dir():
                raise ReleaseError(f"{folder} is missing; run `build` first")
            signed_folder = _signed_copy(folder, signed / name / platform / folder.name)
            feed_tool.sign_package(signed_folder, key)
            _verify_engine(signed_folder, directory / "logs" / f"verify-signed-{platform}-{name}.log")
            feed_tool.pack_engine(signed_folder, out, url_prefix="", prefix=prefix)
    _copy_flat(entries_engine, feed)
    _copy_flat(entries_dsv41, feed)

    say("packing the Control archives")
    for platform in targets:
        info = _require_built(terminals, "terminal", platform)
        unpack = directory / info["unpack"]
        launcher = "bin/deepstrata.cmd" if platform == WINDOWS else "bin/deepstrata"
        if not (unpack / launcher).is_file():
            raise ReleaseError(f"{unpack} has no {launcher}; the Terminal archive is not usable")
        feed_tool.pack_control(unpack, entries_control, version=component, platform=platform,
                               fmt="zip" if platform == WINDOWS else "tar.gz", url_prefix="")
    _copy_flat(entries_control, feed)
    for name in ("install.sh", "install.ps1"):
        source = ROOT / "packaging" / name
        if not source.is_file():
            raise ReleaseError(f"{source} is missing; the release must carry both one-line installers")
        shutil.copy2(source, feed / name)

    notes = directory / "notes.md"
    if not notes.is_file():
        previous = previous_release_tag(Path(args.root))
        write_notes(notes, generate_notes(version=version, component=component, commit=journal["commit"],
                                          previous=previous, records=commits_since(Path(args.root), previous),
                                          line=journal["product_version"], targets=targets))
    body = notes.read_text(encoding="utf-8")
    if not body.strip():
        raise ReleaseError(f"{notes} is empty; a release needs release notes")
    if len(body.encode("utf-8")) > 120_000:
        raise ReleaseError(f"{notes} is too long for a GitHub release body ({len(body)} characters)")

    say("building the signed feed")
    feed_tool.build_feed(feed, key=key, sequence=journal["sequence"], engine_dir=entries_engine,
                         control_dir=entries_control, dsv41_dir=entries_dsv41,
                         expires_days=args.expires_days)
    assets = inspect_feed(feed)
    for name in assets:
        if not is_release_asset(name):
            raise ReleaseError(f"unexpected release asset {name}")
    journal["assets"] = assets
    journal["feed_name"] = DEFAULT_SEQUENCE_FILE
    set_step(journal, "assemble", {"ok": True, "sequence": journal["sequence"], "assets": sorted(assets)})
    save_journal(directory, journal)
    say("")
    for name in sorted(assets):
        say(f"  {assets[name]['bytes'] / (1 << 20):9.1f} MiB  {name}")
    say(f"\nfeed: {feed}")
    say("next: python3 packaging/release.py verify" + (f" --release {version}" if args.release else ""))
    return 0


# ------------------------------------------------------------------------------------------------ verify


def _parse_manifest(folder: Path) -> dict:
    return read_json(Path(folder) / ec.MANIFEST)


def _verify_manifest_signature(folder: Path, trusted: dict) -> None:
    manifest = _parse_manifest(folder)
    signature = manifest.get("signature")
    payload = ec.signing_payload(manifest)
    try:
        signing.verify_bytes(trusted, payload, signature)
    except signing.SignatureError as exc:
        raise ReleaseError(f"{folder / ec.MANIFEST} is not validly signed: {exc}") from None


def _check_entry_archive(feed: Path, entry) -> None:
    target = feed / entry.url
    if not target.is_file():
        raise ReleaseError(f"the feed names {entry.url}, but it is not beside the feed")
    size = target.stat().st_size
    if size != entry.size:
        raise ReleaseError(f"{entry.url}: feed says {entry.size} bytes, the file has {size}")
    digest = sha256_file(target)
    if digest != entry.sha256:
        raise ReleaseError(f"{entry.url}: feed says sha256 {entry.sha256}, the file is {digest}")


def _check_install_manifest(feed_dir: Path, parsed) -> None:
    text = (feed_dir / "install-manifest.txt").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    control = {package.platform: package for package in parsed.control}
    expected = [f"{package.platform} {package.version} {package.url} {package.sha256} {package.size}"
                for package in control.values()]
    if sorted(lines) != sorted(expected):
        raise ReleaseError(f"install-manifest.txt does not match the feed's Control packages:\n"
                           f"  manifest: {lines}\n  feed:     {expected}")


def _verify_engine_entry(parsed, member: str, feed_dir: Path, platform: str, component: str) -> None:
    """One engine member (Qwen's `engine` or `dsv41`) for one platform, as the feed serves it."""
    entries = [package for package in (getattr(parsed, "dsv41") if member == "dsv41" else parsed.engine)
               if package.platform == platform]
    if len(entries) != 1:
        raise ReleaseError(f"the feed has {len(entries)} {member} packages for {platform}; this release carries one")
    package = entries[0]
    if package.backend != "cuda" or package.variant != "cuda13":
        raise ReleaseError(f"{member} for {platform}: unexpected package {package.backend}/{package.variant}")
    if package.version != component:
        raise ReleaseError(f"{member} for {platform}: {package.version} does not match the Control version {component}")
    missing = [arch for arch in ENGINE_ARCHS if arch not in package.archs]
    if missing:
        raise ReleaseError(f"{member} for {platform}: archs {package.archs} do not cover {', '.join(missing)}")
    if package.min_driver.get("nvidia") != MIN_NVIDIA_DRIVER:
        raise ReleaseError(f"{member} for {platform}: min_driver is {package.min_driver}, expected nvidia {MIN_NVIDIA_DRIVER}")
    _check_entry_archive(feed_dir, package)


def _verify_control_entries(parsed, component: str, targets: tuple[str, ...]) -> None:
    """Exactly one Control package per released platform, at the release's component version, in the format its installer unpacks."""
    controls: dict[str, object] = {}
    for package in parsed.control:
        if package.platform in controls:
            raise ReleaseError(f"the feed carries two Control packages for {package.platform}")
        controls[package.platform] = package
    if sorted(controls) != sorted(targets):
        raise ReleaseError(f"the feed carries Control packages for {sorted(controls)}, but this release targets "
                           f"{sorted(targets)}")
    for platform, package in controls.items():
        if package.version != component:
            raise ReleaseError(f"the {platform} Control package is {package.version}, not {component}")
        wanted = control_format(platform)
        if package.format != wanted:
            raise ReleaseError(f"the {platform} Control package is {package.format}; {platform} must be {wanted}")


def _selection_checks(parsed, targets: tuple[str, ...]) -> list[str]:
    problems = []
    for platform in targets:
        for driver, archs in ((MIN_NVIDIA_DRIVER, ["sm_86"]), ("590.10", ["sm_120"]), ("615.71", ["sm_89"])):
            hardware = {"platform": platform, "backend": "cuda", "runtime": None, "nvidia_driver": driver,
                        "cuda_archs": archs, "gfx": []}
            best, reasons = feedmod.select_engine(parsed.engine, hardware)
            if best is None:
                problems.append(f"{platform}: {archs} on driver {driver}: no engine ({'; '.join(reasons)})")
            elif best.platform != platform:
                problems.append(f"{platform}: {archs} on driver {driver}: chose the {best.platform} package")
            elif best.variant != "cuda13":
                problems.append(f"{platform}: {archs} on driver {driver}: chose {best.variant}, not cuda13")
        old, _ = feedmod.select_engine(parsed.engine, {"platform": platform, "backend": "cuda",
                                                       "runtime": None, "nvidia_driver": "570.10",
                                                       "cuda_archs": ["sm_86"], "gfx": []})
        if old is not None:
            problems.append(f"{platform}: driver 570.10 would receive {old.version}, but the package needs {MIN_NVIDIA_DRIVER}")
    if problems:
        raise ReleaseError("the signed feed does not serve the release as intended:\n  - " + "\n  - ".join(problems))
    return [f"select_engine picks cuda13 for sm_86/sm_89/sm_120 on drivers {MIN_NVIDIA_DRIVER} and newer, on "
            + ", ".join(targets)]


def cmd_verify(args) -> int:
    version, directory = release_directory(args, Path(args.root))
    journal = load_journal(directory)
    _require_step(journal, "assemble")
    ensure_commit(Path(args.root), journal)
    feed = directory / "feed"
    assets = inspect_feed(feed)
    if assets != journal.get("assets"):
        changed = sorted(set(assets) ^ set(journal.get("assets") or {})) or \
            [name for name in assets if (journal.get("assets") or {}).get(name) != assets[name]]
        raise ReleaseError(f"the feed folder changed after assemble ({changed}); run assemble again")
    trusted = signing.load_trusted_keys(environ={})
    checks: list[str] = []
    targets = _release_targets(journal)
    component = journal["component_version"]
    parsed = feedmod.fetch(str(feed), trusted)
    if parsed.sequence != journal["sequence"]:
        raise ReleaseError(f"the feed's sequence is {parsed.sequence}, not {journal['sequence']}")
    checks.append(f"feed signature and sequence {parsed.sequence}")
    for member, entries in (("engine", parsed.engine), ("dsv41", parsed.dsv41)):
        if len(entries) != len(targets):
            raise ReleaseError(f"the feed carries {len(entries)} {member} packages, but the release has "
                               f"{len(targets)} platform(s): {', '.join(targets)}")
    _verify_control_entries(parsed, component, targets)
    controls = {package.platform: package for package in parsed.control}
    for platform in targets:
        _check_entry_archive(feed, controls[platform])
    checks.append(f"Control {component} archives match the feed ({', '.join(f'{p} {control_format(p)}' for p in targets)})")
    _check_install_manifest(feed, parsed)
    checks.append("install-manifest.txt matches the Control packages")
    for member in ("engine", "dsv41"):
        for platform in targets:
            _verify_engine_entry(parsed, member, feed, platform, component)
    checks.append("Qwen and dsv41 engine entries, archs and archives match on " + ", ".join(targets))

    for name in ("qwen", "dsv41"):
        packages = _platform_component(journal, name)
        for platform in targets:
            info = _require_built(packages, name, platform)
            folder = directory / "parts" / "signed" / name / platform / Path(info["folder"]).name
            if not folder.is_dir():
                raise ReleaseError(f"{folder} is missing; run assemble again")
            _verify_manifest_signature(folder, trusted)
            _verify_engine(folder, directory / "logs" / f"verify-{platform}-{name}.log")
    checks.append("every engine manifest is signed by the shipped feed key and hash-checks")

    smoke_path = directory / "verify" / "smoke.json"
    smoke_path.parent.mkdir(exist_ok=True)
    terminals = _platform_component(journal, "terminal")
    if LINUX in targets:
        archive = directory / _require_built(terminals, "terminal", LINUX)["archive"]
        if args.skip_smoke:
            checks.append("Linux Terminal smoke test skipped")
        else:
            run_command([sys.executable, "packaging/smoke_terminal.py", str(archive), "--json", str(smoke_path)],
                        log=directory / "logs" / "smoke-terminal.log")
            smoke = read_json(smoke_path)
            if not smoke.get("ok", True):
                raise ReleaseError(f"the Terminal smoke test failed; see {smoke_path}")
            checks.append("Linux Terminal archive: smoke test (bundled Python, Node, page, mock serve) passed")
    if WINDOWS in targets:
        info = _require_built(terminals, "terminal", WINDOWS)
        if not info.get("smoke_ok"):
            raise ReleaseError("the imported Windows Terminal has no passing smoke report; build the parts on Windows again "
                               "without -SkipSmoke and import them")
        archive = directory / info["archive"]
        if sha256_file(archive) != info.get("sha256"):
            raise ReleaseError(f"{archive} changed after it was imported; import the Windows parts again")
        checks.append("Windows Terminal archive: the one the Windows build smoke-tested (a Windows run is not repeated here)")
    checks.extend(_selection_checks(parsed, targets))

    for name in ("install.sh", "install.ps1"):
        if (feed / name).read_bytes() != (ROOT / "packaging" / name).read_bytes():
            raise ReleaseError(f"{name} in the release differs from packaging/{name}; upload it unchanged")
    checks.append("install.sh and install.ps1 are the unchanged packaging copies")

    ran_smoke = LINUX in targets and not args.skip_smoke       # the Windows Terminal is smoke-tested on Windows, not here
    report = {"ok": True, "at": now(), "sequence": journal["sequence"], "targets": list(targets), "checks": checks,
              "assets": {name: assets[name] for name in sorted(assets)}, "smoke": ran_smoke}
    write_json(directory / "verify.json", report)
    set_step(journal, "verify", {"ok": True, "checks": checks, "smoke": ran_smoke,
                                "report": "verify.json"})
    save_journal(directory, journal)
    say("")
    for item in checks:
        say(f"  ok  {item}")
    say(f"\nreport: {directory / 'verify.json'}")
    say("next: python3 packaging/release.py publish" + (f" --release {version}" if args.release else ""))
    return 0


# ------------------------------------------------------------------------------------------------ publish


def _local_tag_commit(tag: str, root: Path | None = None) -> str | None:
    result = subprocess.run(["git", "rev-list", "-n", "1", tag], cwd=str(root or ROOT), text=True, capture_output=True)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _push_tag(tag: str, token: str, directory: Path) -> None:
    """Push one tag over HTTPS with an askpass helper: the token is in the environment, never in argv or a file."""
    with tempfile.TemporaryDirectory(prefix="strata-release-push-") as temporary:
        askpass = Path(temporary) / "askpass.sh"
        askpass.write_text('#!/bin/sh\n'
                           'case "$1" in\n'
                           '  *sername*) printf "%s\\n" "x-access-token" ;;\n'
                           '  *) printf "%s\\n" "$STRATA_RELEASE_TOKEN" ;;\n'
                           'esac\n', encoding="utf-8")
        askpass.chmod(0o700)
        env = {**os.environ, "GIT_ASKPASS": str(askpass), "GIT_ASKPASS_REQUIRE": "force",
               "GIT_TERMINAL_PROMPT": "0", "STRATA_RELEASE_TOKEN": token}
        command = ["git", "-c", "credential.helper=", "-c", "core.askpass=", "push",
                   f"https://github.com/{REPO}.git", f"refs/tags/{tag}:refs/tags/{tag}"]
        code = run_command(command, env=env, log=directory / "logs" / "publish-push-tag.log", check=False)
        if code != 0:
            raise ReleaseError(f"pushing the tag {tag} failed (exit {code}); see the log above. The release was not created")


def _asset_matches(asset: dict, info: dict) -> bool:
    if asset.get("size") != info["bytes"] or asset.get("state") != "uploaded":
        return False
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower() == info["sha256"]
    return True


def _install_latest(directory: Path, version: str) -> dict:
    work = directory / "install-test"
    shutil.rmtree(work, ignore_errors=True)
    home = work / "home"
    data = work / "data"
    bins = work / "bin"
    for path in (home, data, bins):
        path.mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / ".local" / "share"),
           "XDG_CONFIG_HOME": str(home / ".config"), "XDG_STATE_HOME": str(home / ".local" / "state"),
           "XDG_CACHE_HOME": str(home / ".cache"), "STRATA_FEED": LATEST_DOWNLOAD,
           "STRATA_DATA_DIR": str(data), "STRATA_BIN_DIR": str(bins)}
    log = directory / "logs" / "publish-install.log"
    run_command(["sh", str(ROOT / "packaging" / "install.sh")], env=env, log=log)
    launcher = bins / "deepstrata"
    if not launcher.is_file():
        raise ReleaseError(f"install.sh finished but {launcher} is missing; see {log}")
    result = subprocess.run([str(launcher), "--version"], env=env, text=True, capture_output=True)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"\n$ {launcher} --version\n{result.stdout}{result.stderr}\n")
    if result.returncode != 0:
        raise ReleaseError(f"the installed Terminal does not start (`--version` exit {result.returncode}); see {log}")
    run_command([str(launcher), "update", "--check", "--no-engine", "--feed", LATEST_DOWNLOAD],
                env=env, log=log)
    installed = data / "terminal" / version
    if not installed.is_dir():
        raise ReleaseError(f"the installed Terminal is not in {installed}; see {log}")
    return {"ok": True, "version_output": (result.stdout or result.stderr).strip().splitlines()[:2],
            "installed": str(installed.relative_to(directory)), "log": "logs/publish-install.log"}


def cmd_publish(args) -> int:
    version, directory = release_directory(args, Path(args.root))
    journal = load_journal(directory)
    for name in ("plan", "build", "assemble", "verify"):
        _require_step(journal, name)
    ensure_commit(Path(args.root), journal)
    branch = git("rev-parse", "--abbrev-ref", "HEAD", root=Path(args.root))
    if branch != "main":
        raise ReleaseError(f"publishing is from main, this checkout is on {branch!r}")
    feed = directory / "feed"
    assets = inspect_feed(feed)
    if assets != journal.get("assets"):
        raise ReleaseError("the feed folder changed after verify; run assemble and verify again")
    report = read_json(directory / "verify.json")
    if not report.get("ok"):
        raise ReleaseError(f"{directory / 'verify.json'} is not a passing report")
    if report.get("assets") != assets:
        raise ReleaseError("the verified assets are not the ones in the feed folder; verify again")
    token, source = find_token()
    if not token:
        raise ReleaseError("no GitHub token: set GH_TOKEN, or log in with gh (the token is read from ~/.config/gh/hosts.yml)")
    api = GitHub(token, REPO)
    user = api.check_push()
    tag = journal["tag"]
    commit = journal["commit"]
    say(f"publishing {PRODUCT} {version} to {REPO} as {user}")

    local = _local_tag_commit(tag, Path(args.root))
    if local is None:
        run_command(["git", "tag", "-a", tag, "-m", f"{PRODUCT} {version}", commit],
                    cwd=Path(args.root), log=directory / "logs" / "publish-tag.log")
        local = _local_tag_commit(tag, Path(args.root))
    if local != commit:
        raise ReleaseError(f"the local tag {tag} names {str(local)[:12]}, not the release commit {commit[:12]}")
    remote = api.tag_commit(tag)
    pushed = False
    if remote is None:
        say(f"pushing tag {tag}")
        _push_tag(tag, token, directory)
        pushed = True
        remote = api.tag_commit(tag)
    if remote != commit:
        raise ReleaseError(f"GitHub's tag {tag} names {str(remote)[:12]}, not {commit[:12]}")

    existing = api.release_by_tag(tag)
    body = (directory / "notes.md").read_text(encoding="utf-8")
    if existing is None:
        say(f"creating release {tag}")
        release = api.create_release(tag=tag, commit=commit, name=f"{PRODUCT} {version}", body=body)
    else:
        if not args.resume:
            raise ReleaseError(f"the release {tag} already exists; use --resume to upload the missing assets, "
                               "or publish a new version")
        if existing.get("draft") or existing.get("prerelease"):
            raise ReleaseError(f"the existing release {tag} is a draft or a pre-release; publish as a normal latest release")
        release = existing
    release_id = release["id"]
    html_url = release["html_url"]
    current = {item["name"]: item for item in api.assets(release_id)}
    uploaded, skipped = [], []
    for name in sorted(assets):
        info = assets[name]
        path = feed / name
        old = current.get(name)
        if old is not None and _asset_matches(old, info):
            skipped.append(name)
            say(f"  already there: {name}")
            continue
        if old is not None:
            if not args.replace:
                raise ReleaseError(f"the release already has {name} with different content; "
                                   "run publish --resume --replace to replace it")
            api.delete_asset(old["id"])
        say(f"uploading {name} ({info['bytes'] / (1 << 20):.1f} MiB)")
        api.upload_asset(release_id, path, content_type=info["content_type"])
        uploaded.append(name)

    final = {item["name"]: item for item in api.assets(release_id)}
    missing = [name for name in assets if name not in final]
    bad = [name for name in assets if name in final and final[name].get("size") != assets[name]["bytes"]]
    bad_digest = [name for name in assets if name in final and isinstance(final[name].get("digest"), str)
                  and final[name]["digest"].startswith("sha256:")
                  and final[name]["digest"].split(":", 1)[1].lower() != assets[name]["sha256"]]
    if missing or bad or bad_digest:
        raise ReleaseError(f"the release is missing {missing}, has a wrong size for {bad} or a wrong sha256 for "
                           f"{bad_digest}; rerun with --resume")
    latest = api.latest_release()
    if not latest or latest.get("tag_name") != tag:
        raise ReleaseError(f"the release is not GitHub's latest (latest is {(latest or {}).get('tag_name')}); "
                           "it must be a normal latest release, not a pre-release")
    sequence, latest_tag = _published_sequence(api)
    if sequence != journal["sequence"]:
        raise ReleaseError(f"the published feed has sequence {sequence}, expected {journal['sequence']}")
    install = {"ok": False, "skipped": True}
    if LINUX not in _release_targets(journal):
        say("install test skipped: this release has no linux-x86_64 Terminal, and the test installs with install.sh here")
    elif not args.skip_install_test:
        say("installing the published release from latest/download (a real client run) ...")
        install = _install_latest(directory, journal["component_version"])
    else:
        say("install test skipped (--skip-install-test)")
    publish = {"ok": True, "at": now(), "tag": tag, "release_url": html_url, "release_id": release_id,
               "tag_pushed": pushed, "uploaded": uploaded, "skipped": skipped,
               "latest_sequence": sequence, "install": install}
    write_json(directory / "publish.json", publish)
    set_step(journal, "publish", {"ok": True, "tag": tag, "url": html_url, "uploaded": uploaded,
                                 "skipped": skipped, "install": install.get("ok", False)})
    save_journal(directory, journal)
    say("")
    say(f"published: {html_url}")
    say(f"  uploaded now: {', '.join(uploaded) if uploaded else 'nothing (all assets were there)'}")
    say(f"  latest feed: sequence {sequence}")
    if install.get("ok"):
        say(f"  install.sh from latest: ok ({install['version_output'][0] if install.get('version_output') else ''})")
    say(f"report: {directory / 'publish.json'}")
    return 0


# ------------------------------------------------------------------------------------------------ status and next


def cmd_status(args) -> int:
    version, directory = release_directory(args, Path(args.root))
    journal = load_journal(directory)
    say(f"{PRODUCT} {version} ({journal['tag']})")
    say(f"  commit {journal['commit'][:12]} on {journal['branch']}; component {journal['component_version']}; "
        f"sequence {journal['sequence']}")
    order = ("plan", "build", "assemble", "verify", "publish")
    next_step = None
    for name in order:
        step = (journal.get("steps") or {}).get(name) or {}
        state = "ok" if step.get("ok") else ("failed" if step.get("error") else "not done")
        if step.get("error"):
            state += f": {step['error'].splitlines()[0][:120]}"
        say(f"  {name:9} {state}")
        if not step.get("ok") and next_step is None:
            next_step = name
    if journal.get("assets"):
        say(f"  assets: {len(journal['assets'])} ({sum(v['bytes'] for v in journal['assets'].values()) / (1 << 20):.1f} MiB)")
    if journal.get("merged"):
        say(f"  merged: {journal['merged']['branch']} at {journal['merged']['tip'][:12]}")
    if next_step:
        say(f"next: {next_step}")
    else:
        say("release complete")
    return 0


def cmd_next(args) -> int:
    product = product_version(Path(args.root))
    component = project_version(Path(args.root))
    tags = local_release_tags(Path(args.root))
    version = next_release_version(product, tags)
    say(f"next release: {version} (tag {TAG_PREFIX}{version}); components {component}")
    say(f"  page/Terminal {product}; upstream line {product}; existing tags: {', '.join(tags) or 'none'}")
    return 0


# ------------------------------------------------------------------------------------------------ command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--release", help="release version, like 0.2.12.2 (default: the journal under RELEASE/)")
    common.add_argument("--root", default=str(ROOT), help=argparse.SUPPRESS)

    plan = sub.add_parser("plan", parents=[common], help="check the release and write its journal")
    plan.add_argument("--key", help="the feed private key (default: STRATA_FEED_KEY or ~/deepstrata-keys/feed-key.json)")
    plan.add_argument("--sequence", type=int, help="the new feed's sequence (default: the published sequence + 1)")
    plan.add_argument("--target", action="append", choices=TARGETS,
                      help="platform to release (default: linux-x86_64 and windows-x86_64)")
    plan.add_argument("--require-merged", metavar="BRANCH", help="fail unless BRANCH is merged into HEAD")
    plan.add_argument("--offline", action="store_true", help="do not read GitHub (needs --sequence)")
    plan.add_argument("--allow-branch", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--allow-dirty", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--allow-unpushed", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--allow-line", action="store_true", help=argparse.SUPPRESS)
    plan.add_argument("--skip-tests", action="store_true", help="do not run pytest and the App Host tests")
    plan.add_argument("--test-timeout", type=float, default=5400, help="seconds per test command (default: 5400)")
    plan.add_argument("--notes", help="a Markdown file to use as the release notes")
    plan.add_argument("--keep-notes", action="store_true", help="keep the existing notes.md instead of regenerating it")
    plan.add_argument("--replan", action="store_true", help="replace the journal of an unpublished release")
    plan.add_argument("--cuda-root", help="the CUDA 13 toolkit root (default: find nvcc)")
    plan.add_argument("--json", action="store_true", help="print the journal as JSON")
    plan.set_defaults(func=cmd_plan)

    build = sub.add_parser("build", parents=[common], help="build the Terminal, the Control bundle and the engines")
    build.add_argument("--only", action="append", choices=("terminal", "qwen", "dsv41"),
                       help="build only these parts (repeatable; default: all)")
    build.add_argument("--rebuild", action="store_true", help="throw away and rebuild the selected parts")
    build.add_argument("--rebuild-host", action="store_true", help="rebuild the App Host even when dist/ exists")
    build.add_argument("--cuda-root", help="the CUDA 13 toolkit root")
    build.add_argument("--windows-parts", type=Path, metavar="FOLDER_OR_ZIP",
                       help="the unsigned Windows parts folder (or artifact zip) that packaging/build_windows.ps1 "
                            "wrote on a Windows PC; required when windows-x86_64 is a target and no earlier import is in the journal")
    build.set_defaults(func=cmd_build)

    assemble = sub.add_parser("assemble", parents=[common], help="sign, pack and write the signed feed")
    assemble.add_argument("--key", help="the feed private key")
    assemble.add_argument("--expires-days", type=int, help="the signed feed's expiry (default: none)")
    assemble.set_defaults(func=cmd_assemble)

    verify = sub.add_parser("verify", parents=[common], help="check the feed, packages and a real Terminal run")
    verify.add_argument("--skip-smoke", action="store_true", help="do not run the Terminal smoke test")
    verify.set_defaults(func=cmd_verify)

    publish = sub.add_parser("publish", parents=[common], help="tag, create the GitHub release and upload the assets")
    publish.add_argument("--resume", action="store_true", help="continue a release that already exists")
    publish.add_argument("--replace", action="store_true", help="replace an asset whose content differs (with --resume)")
    publish.add_argument("--skip-install-test", action="store_true", help="do not run install.sh from latest/download")
    publish.set_defaults(func=cmd_publish)

    status = sub.add_parser("status", parents=[common], help="show the journal and the next step")
    status.set_defaults(func=cmd_status)

    nxt = sub.add_parser("next", parents=[common], help="print the next version, tag and component version")
    nxt.set_defaults(func=cmd_next)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ReleaseError as exc:
        print(f"release: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("release: stopped", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
