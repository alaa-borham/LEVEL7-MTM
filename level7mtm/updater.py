"""Online updates from the project's GitHub Releases (ported from LEVEL7 Nesting's tested updater).

Every Windows build of the default branch publishes a release ``v0.1.<build>`` with the installer
``LEVEL7MTM-<version>-Setup.exe`` and ``SHA256SUMS.txt``. The application asks GitHub for the latest release; a
newer one is offered, downloaded over HTTPS from github.com only, its SHA-256 checked against SHA256SUMS.txt
(checked again just before it runs) and installed silently; LEVEL7 MTM starts again afterwards. The database and
settings (in the data folder) are not touched by the installer. Nothing is sent except the plain requests (no
telemetry, no IDs). The installer is not code-signed, so Windows SmartScreen / UAC may still ask.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from . import __version__

REPO = "alaa-borham/LEVEL7-MTM"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=60"
ALLOWED_HOSTS = ("github.com", "api.github.com")
USER_AGENT = f"LEVEL7-MTM/{__version__}"


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    tag: str
    notes: str
    page_url: str
    setup_name: str
    setup_url: str
    setup_size: int
    sums_url: str | None
    sha256: str | None = None
    host: str | None = None


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    if not nums:
        raise ValueError(f"not a version: {v!r}")
    return tuple(int(n) for n in nums)


def is_newer(remote: str, local: str = __version__) -> bool:
    a, b = parse_version(remote), parse_version(local)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _check_url(url: str, hosts=ALLOWED_HOSTS):
    u = urlparse(url)
    if u.scheme != "https" or u.hostname not in hosts:
        raise UpdateError(f"refusing update URL outside the update host: {url}")


REDIRECT_DOMAINS = ("github.com", "githubusercontent.com")   # GitHub's asset storage behind its redirects


def _redirect_ok(url: str, hosts) -> bool:
    u = urlparse(url)
    h = (u.hostname or "").lower()
    return u.scheme == "https" and (h in hosts or any(h == d or h.endswith("." + d) for d in REDIRECT_DOMAINS))


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Redirects are followed only over https and only to the update host / GitHub's storage."""

    def __init__(self, hosts):
        super().__init__()
        self.hosts = tuple(hosts)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _redirect_ok(newurl, self.hosts):
            raise UpdateError(f"refusing redirect outside the update host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _get(url: str, timeout: float, accept: str = "application/octet-stream", hosts=ALLOWED_HOSTS):
    _check_url(url, hosts)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    return _urlopen(req, timeout, hosts)                       # follows the redirect to GitHub's asset storage


def _urlopen(req, timeout, hosts=ALLOWED_HOSTS):
    return urllib.request.build_opener(_SafeRedirects(hosts)).open(req, timeout=timeout)


def parse_release(data: dict) -> UpdateInfo | None:
    """Pick the installer and checksum file from a GitHub release JSON; None if it has no installer."""
    if data.get("draft") or data.get("prerelease"):
        return None
    assets = {a["name"]: a for a in data.get("assets", [])}
    setup = [a for n, a in assets.items() if n.endswith("-Setup.exe")]
    if not setup:
        return None
    s = setup[0]
    sums = assets.get("SHA256SUMS.txt")
    tag = data.get("tag_name", "")
    return UpdateInfo(version=tag.lstrip("vV"), tag=tag, notes=(data.get("body") or "").strip(),
                      page_url=data.get("html_url", ""), setup_name=s["name"],
                      setup_url=s["browser_download_url"], setup_size=int(s.get("size", 0)),
                      sums_url=sums["browser_download_url"] if sums else None)


def _fetch_json(url: str, timeout: float, hosts, accept="application/json"):
    try:
        with _get(url, timeout, accept, hosts) as r:
            return json.loads(r.read().decode("utf-8"))
    except UpdateError:
        raise
    except Exception as exc:                                    # offline, proxy, rate limit, 404
        raise UpdateError(f"update check failed: {exc}") from exc


def latest_info(timeout: float = 10.0) -> UpdateInfo | None:
    """Latest published release (None when it has no installer)."""
    return parse_release(_fetch_json(LATEST_API, timeout, ALLOWED_HOSTS, "application/vnd.github+json"))


def check_latest(timeout: float = 10.0, current: str = __version__) -> UpdateInfo | None:
    """Return the latest version if it is newer than ``current``, else None. Raises UpdateError when offline."""
    info = latest_info(timeout)
    if info is None:
        return None
    try:
        return info if is_newer(info.version, current) else None
    except ValueError:
        return None


def note_of(body: str, lang: str = "ar") -> list[str]:
    """What one release changed, for the update window: its "ar:" lines in Arabic, else its first line."""
    lines = [ln.strip() for ln in (body or "").splitlines() if ln.strip()]
    lines = [ln for ln in lines if not re.match(r"^(Co-Authored-By|Claude-Session):", ln)]
    if lang == "ar":
        ar = [ln[3:].strip() for ln in lines if ln.lower().startswith("ar:")]
        if ar:
            return ar
    return [ln for ln in lines if not ln.lower().startswith("ar:")][:1]


def notes_since(current: str = __version__, lang: str = "ar", timeout: float = 10.0) -> str:
    """Every change between the installed version and the newest one, newest first:
    "0.1.76  — …" lines. Empty when the list cannot be read (the window still works)."""
    try:
        rels = _fetch_json(RELEASES_API, timeout, ALLOWED_HOSTS, "application/vnd.github+json")
    except UpdateError:
        return ""
    out = []
    for r in rels if isinstance(rels, list) else []:
        v = (r.get("tag_name") or "").lstrip("vV")
        try:
            newer = is_newer(v, current)
        except ValueError:
            continue
        if newer and not r.get("draft") and not r.get("prerelease"):
            out.append((parse_version(v), v, note_of(r.get("body") or "", lang)))
    out.sort(reverse=True)
    return "\n".join(f"• {v}:  " + "\n     ".join(n) for _, v, n in out if n)


def expected_sha256(sums_text: str, name: str) -> str | None:
    for ln in sums_text.splitlines():
        parts = ln.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download(info: UpdateInfo, dest_dir: str | None = None, progress=None, cancelled=None,
             timeout: float = 30.0) -> str:
    """Download and verify the installer. ``progress(done, total)``; ``cancelled()`` -> True aborts."""
    hosts = (info.host,) if info.host else ALLOWED_HOSTS
    if info.sha256:
        want = info.sha256
    else:
        if not info.sums_url:
            raise UpdateError("release has no SHA256SUMS.txt; refusing unverified installer")
        with _get(info.sums_url, timeout) as r:
            want = expected_sha256(r.read().decode("utf-8", "replace"), info.setup_name)
    if not want:
        raise UpdateError(f"no checksum for {info.setup_name}")
    dest_dir = dest_dir or os.path.join(tempfile.gettempdir(), "LEVEL7MTM-update")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, os.path.basename(info.setup_name))
    h = hashlib.sha256()
    try:
        _fetch_to(info, timeout, hosts, path, h, progress, cancelled)
    except BaseException:
        if os.path.exists(path + ".part"):            # a broken connection leaves no half file behind
            os.remove(path + ".part")
        raise
    if h.hexdigest() != want:
        os.remove(path + ".part")
        raise UpdateError("checksum mismatch: downloaded installer rejected")
    os.replace(path + ".part", path)
    _VERIFIED[os.path.abspath(path)] = want
    return path


_VERIFIED: dict = {}        # installer path -> SHA-256 it was verified with (checked again just before it runs)


def _fetch_to(info, timeout, hosts, path, h, progress, cancelled):
    done = 0
    with _get(info.setup_url, timeout, hosts=hosts) as r, open(path + ".part", "wb") as fh:
        total = int(r.headers.get("Content-Length") or info.setup_size or 0)
        while True:
            if cancelled and cancelled():
                raise UpdateError("cancelled")
            chunk = r.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def installer_arguments() -> list[str]:
    """Inno Setup: show progress only, close the running app, and start LEVEL7 MTM again afterwards."""
    return ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/FORCECLOSEAPPLICATIONS", "/LAUNCH=1"]


def start_installer(path: str):
    """Start the verified installer (Windows only); the caller quits the application afterwards."""
    if os.name != "nt":
        raise UpdateError("installing updates is only supported on Windows")
    want = _VERIFIED.get(os.path.abspath(path))
    h = hashlib.sha256()
    with open(path, "rb") as fh:                       # the file run is the file that was verified
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    if not want or h.hexdigest() != want:
        raise UpdateError("the installer changed after it was verified - not started")
    args = " ".join(installer_arguments())
    os.startfile(path, "open", args)        # ShellExecute: lets the installer request elevation (UAC) itself
