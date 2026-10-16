"""Online update from GitHub Releases (ported from LEVEL7 Nesting's tests)."""
import hashlib
import re
import io
import json

import pytest

from level7mtm import updater

SETUP = b"MZ fake installer" * 1000
SUM = hashlib.sha256(SETUP).hexdigest()


def release(tag="v0.2.99", sums=True, **kw):
    assets = [{"name": f"LEVEL7MTM-{tag[1:]}-Setup.exe", "size": len(SETUP),
               "browser_download_url": f"https://github.com/alaa-borham/LEVEL7-MTM/releases/download/{tag}/x.exe"}]
    if sums:
        assets.append({"name": "SHA256SUMS.txt",
                       "browser_download_url": f"https://github.com/alaa-borham/LEVEL7-MTM/releases/download/{tag}/s"})
    d = {"tag_name": tag, "body": "notes", "html_url": "https://github.com/x", "assets": assets}
    d.update(kw)
    return d


class Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def fake_net(monkeypatch, rel, setup=SETUP, sums_name=None):
    seen = []

    def urlopen(req, timeout=None):
        url = req.full_url
        seen.append(url)
        if url == updater.LATEST_API:
            return Resp(json.dumps(rel).encode())
        if url.endswith("/s"):
            name = sums_name or rel["assets"][0]["name"]
            return Resp(f"{SUM}  {name}\n".encode())
        return Resp(setup)
    monkeypatch.setattr(updater, "_urlopen", lambda req, timeout=None, hosts=None: urlopen(req, timeout))
    return seen


def test_version_compare():
    assert updater.is_newer("0.2.15", "0.2.9")
    assert updater.is_newer("0.3", "0.2.40")
    assert not updater.is_newer("0.2.15", "0.2.15")
    assert not updater.is_newer("0.2.0", "0.2")
    assert updater.is_newer("0.1.13", "0.1.0")       # first online build replaces a dev / manual 0.1.0 install


def test_check_latest_newer_and_not_newer(monkeypatch):
    fake_net(monkeypatch, release("v0.2.99"))
    info = updater.check_latest(current="0.2.5")
    assert info and info.version == "0.2.99" and info.setup_name.endswith("-Setup.exe") and info.sums_url
    assert updater.check_latest(current="0.2.99") is None


def test_prerelease_draft_and_no_installer_are_ignored(monkeypatch):
    for rel in (release(prerelease=True), release(draft=True), {**release(), "assets": []}):
        fake_net(monkeypatch, rel)
        assert updater.check_latest(current="0.0.1") is None


def test_offline_raises_update_error(monkeypatch):
    def urlopen(req, timeout=None):
        raise OSError("no network")
    monkeypatch.setattr(updater, "_urlopen", lambda req, timeout=None, hosts=None: urlopen(req, timeout))
    with pytest.raises(updater.UpdateError):
        updater.check_latest()


def test_download_verified(monkeypatch, tmp_path):
    fake_net(monkeypatch, release())
    info = updater.check_latest(current="0.0.1")
    prog = []
    p = updater.download(info, str(tmp_path), progress=lambda d, t: prog.append((d, t)))
    assert open(p, "rb").read() == SETUP and prog[-1] == (len(SETUP), len(SETUP))


def test_download_rejects_bad_checksum_missing_sums_and_foreign_hosts(monkeypatch, tmp_path):
    fake_net(monkeypatch, release(), setup=SETUP + b"tampered")
    info = updater.check_latest(current="0.0.1")
    with pytest.raises(updater.UpdateError, match="checksum"):
        updater.download(info, str(tmp_path))
    assert not list(tmp_path.iterdir())                     # nothing left behind
    fake_net(monkeypatch, release(sums=False))
    with pytest.raises(updater.UpdateError, match="SHA256SUMS"):
        updater.download(updater.check_latest(current="0.0.1"), str(tmp_path))
    fake_net(monkeypatch, release(), sums_name="other.exe")
    with pytest.raises(updater.UpdateError, match="no checksum"):
        updater.download(updater.check_latest(current="0.0.1"), str(tmp_path))
    bad = release()
    bad["assets"][0]["browser_download_url"] = "https://evil.example.com/x.exe"
    fake_net(monkeypatch, bad)
    with pytest.raises(updater.UpdateError, match="outside the update host"):
        updater.download(updater.check_latest(current="0.0.1"), str(tmp_path))
    bad["assets"][0]["browser_download_url"] = "http://github.com/x.exe"
    with pytest.raises(updater.UpdateError, match="outside the update host"):
        updater.download(updater.check_latest(current="0.0.1"), str(tmp_path))


def test_cancel(monkeypatch, tmp_path):
    fake_net(monkeypatch, release())
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(updater.check_latest(current="0.0.1"), str(tmp_path), cancelled=lambda: True)
    assert not list(tmp_path.iterdir())


def test_installer_arguments_relaunch_app():
    a = updater.installer_arguments()
    assert "/SILENT" in a and "/LAUNCH=1" in a and "/CLOSEAPPLICATIONS" in a


def test_installer_verified_again_before_start(monkeypatch, tmp_path):
    fake_net(monkeypatch, release())
    p = updater.download(updater.check_latest(current="0.0.1"), str(tmp_path))
    with open(p, "ab") as fh:
        fh.write(b"changed after download")
    monkeypatch.setattr(updater.os, "name", "nt")
    with pytest.raises(updater.UpdateError, match="changed after it was verified"):
        updater.start_installer(p)


def test_version_has_build_number():
    import level7mtm
    assert re.fullmatch(r"0\.1\.\d+", level7mtm.__version__)

