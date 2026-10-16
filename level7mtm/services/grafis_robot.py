"""GRAFIS robot: does the one remaining step in GRAFIS for each order model, so nobody has to.

For every order model written into the MDL folder and not yet saved back by GRAFIS:
    open it (as a double-click does) → wait for GRAFIS → click "Test Run All Parts" → save → (close)
The saved model is then picked up by the MDL folder watch (mdl_intake) → nesting → PLT / ISO.

"Test Run All Parts" has no keyboard shortcut (owner), so the robot clicks the button. Where the buttons are is
*taught* once on this PC: the owner points the mouse at the button; the position is stored relative to the window
under the mouse (window class + title), so a moved window does not matter. Save uses the taught Save button or,
if none was taught, Ctrl+S (owner: GRAFIS saves under the same name without asking).

The robot moves the mouse and types: it is switched on per PC, works on one file at a time, and every failure
(GRAFIS did not open the file, did not save it in time) goes to the ER folder with the reason — never a retry loop.
Windows only (user32 through ctypes; no extra package).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

from ..storage.db import now

BUTTONS = ("test_run", "save", "close")


def settings(ws) -> dict:
    return {"enabled": bool(ws.db.get_setting("robot.enabled", False)),
            "buttons": ws.db.get_setting("robot.buttons", {}) or {},
            "open_timeout": float(ws.db.get_setting("robot.open_timeout_s", 60)),
            "run_wait": float(ws.db.get_setting("robot.run_wait_s", 8)),
            "save_timeout": float(ws.db.get_setting("robot.save_timeout_s", 120))}


class RobotError(RuntimeError):
    pass


# ------------------------------------------------------------------ Windows driver (user32 via ctypes)
class Win32:
    def __init__(self):
        if sys.platform != "win32":
            raise RobotError("the GRAFIS robot runs on Windows only")
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u = ctypes.windll.user32
        self.u.WindowFromPoint.argtypes = [wintypes.POINT]
        self.u.WindowFromPoint.restype = wintypes.HWND
        self.u.GetAncestor.restype = wintypes.HWND

    def _text(self, h):
        b = self.ct.create_unicode_buffer(512)
        self.u.GetWindowTextW(h, b, 512)
        return b.value

    def _cls(self, h):
        b = self.ct.create_unicode_buffer(256)
        self.u.GetClassNameW(h, b, 256)
        return b.value

    def windows(self) -> list[dict]:
        out = []
        cb_t = self.ct.WINFUNCTYPE(self.wt.BOOL, self.wt.HWND, self.wt.LPARAM)

        def cb(h, _):
            if self.u.IsWindowVisible(h):
                out.append({"hwnd": h, "title": self._text(h), "cls": self._cls(h)})
            return True
        self.u.EnumWindows(cb_t(cb), 0)
        return out

    def rect(self, h):
        r = self.wt.RECT()
        self.u.GetWindowRect(h, self.ct.byref(r))
        return r.left, r.top, r.right, r.bottom

    def cursor_window(self) -> dict:
        p = self.wt.POINT()
        self.u.GetCursorPos(self.ct.byref(p))
        h = self.u.GetAncestor(self.u.WindowFromPoint(p), 2)          # GA_ROOT
        left, top, _, _ = self.rect(h)
        return {"title": self._text(h), "cls": self._cls(h), "dx": p.x - left, "dy": p.y - top}

    def responsive(self, h, timeout_ms=2000) -> bool:
        res = self.wt.DWORD()
        return bool(self.u.SendMessageTimeoutW(h, 0, 0, 0, 0x0002, timeout_ms, self.ct.byref(res)))  # WM_NULL

    def foreground(self, h):
        self.u.keybd_event(0x12, 0, 0, 0)            # ALT: lets this process bring another window to the front
        self.u.keybd_event(0x12, 0, 2, 0)
        self.u.ShowWindow(h, 5)
        self.u.SetForegroundWindow(h)
        time.sleep(0.3)

    def click(self, h, dx, dy):
        left, top, _, _ = self.rect(h)
        self.foreground(h)
        self.u.SetCursorPos(left + dx, top + dy)
        time.sleep(0.2)
        self.u.mouse_event(0x0002, 0, 0, 0, 0)       # left down
        self.u.mouse_event(0x0004, 0, 0, 0, 0)       # left up

    def ctrl_s(self, h):
        self.foreground(h)
        self.u.keybd_event(0x11, 0, 0, 0)
        self.u.keybd_event(0x53, 0, 0, 0)
        self.u.keybd_event(0x53, 0, 2, 0)
        self.u.keybd_event(0x11, 0, 2, 0)

    def open(self, path):
        os.startfile(path)                           # as a double-click: opens in GRAFIS (owner)


# ------------------------------------------------------------------ robot
def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def shows_file(title: str, path: str) -> bool:
    """GRAFIS shortens the path in its title: "Grafis - [A: ..9-52-1_0__9-52-1_0.mdl] - 50-Werte-OPTIMASS"."""
    name = os.path.basename(path).lower()
    t = title.lower()
    if name in t or os.path.splitext(name)[0] in t:
        return True
    if "[" in t and "]" in t:
        inner = t[t.index("[") + 1:t.index("]")]
        tail = inner.split("..")[-1].replace("\\", "/").split("/")[-1].strip()
        return len(tail) >= 8 and name.endswith(tail)
    return False


def _find(drv, spec: dict, prefer: str = "") -> int | None:
    """The window a taught button belongs to: same class; same title, or the one showing the current file."""
    ws_ = [w for w in drv.windows() if w["cls"] == spec["cls"]]
    for w in ws_:
        if w["title"] == spec["title"]:
            return w["hwnd"]
    for w in ws_:
        if prefer and shows_file(w["title"], prefer):
            return w["hwnd"]
    return ws_[0]["hwnd"] if len(ws_) == 1 else None


def pending(ws) -> list[dict]:
    """Order models written by this program and still not saved by GRAFIS (and not tried by the robot)."""
    out = []
    for r in ws.db.q("SELECT * FROM grafis_orders WHERE status='written' AND archived=0 AND id IN "
                     "(SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) ORDER BY id"):
        info = json.loads(r["info"])
        p = r["path"]
        if not p or not os.path.isfile(p) or info.get("sha256") != _sha(p):
            continue
        if ws.db.one("SELECT id FROM robot_jobs WHERE sha256=? AND path=?", (info["sha256"], p)):
            continue
        out.append({"path": p, "sha256": info["sha256"], "line_number": info.get("line_number")})
    return out


def run_one(ws, job: dict, drv=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Open → Test Run All Parts → save → (close) for one model. Returns {status, message}."""
    from . import errors_folder
    st = settings(ws)
    btn = st["buttons"]
    res = {"status": "failed", "message": "", "path": job["path"]}
    try:
        if "test_run" not in btn:
            raise RobotError("the 'Test Run All Parts' button was not taught yet (GRAFIS screen → Robot)")
        drv = drv or Win32()
        stem = os.path.splitext(os.path.basename(job["path"]))[0]
        drv.open(job["path"])
        t0, main = clock(), None
        while clock() - t0 < st["open_timeout"]:
            main = next((w["hwnd"] for w in drv.windows() if shows_file(w["title"], job["path"])), None)
            if main and drv.responsive(main):
                break
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not show {stem} within {st['open_timeout']:g} s")
        sleep(2)
        h = _find(drv, btn["test_run"], job["path"])
        if h is None:
            raise RobotError("the window of the 'Test Run All Parts' button was not found (teach it again)")
        drv.click(h, btn["test_run"]["dx"], btn["test_run"]["dy"])
        sleep(st["run_wait"])
        t0 = clock()
        while not drv.responsive(main) and clock() - t0 < 300:
            sleep(1)
        if "save" in btn:
            hs = _find(drv, btn["save"], job["path"])
            if hs is None:
                raise RobotError("the window of the Save button was not found (teach it again)")
            drv.click(hs, btn["save"]["dx"], btn["save"]["dy"])
        else:
            drv.ctrl_s(main)
        t0 = clock()
        while clock() - t0 < st["save_timeout"]:
            if os.path.isfile(job["path"]) and _sha(job["path"]) != job["sha256"]:
                break
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not save {stem} within {st['save_timeout']:g} s")
        if "close" in btn:
            hc = _find(drv, btn["close"], job["path"])
            if hc is not None:
                drv.click(hc, btn["close"]["dx"], btn["close"]["dy"])
        res.update(status="done", message="opened, Test Run All Parts, saved")
    except Exception as e:  # noqa: BLE001 - every failure is reported, never retried silently
        res["message"] = str(e)
        errors_folder.report(ws, os.path.splitext(os.path.basename(job["path"]))[0], [res["message"]],
                             "GRAFIS robot")
    with ws.db.tx() as c:
        c.execute("INSERT INTO robot_jobs(sha256, path, at, status, message) VALUES (?,?,?,?,?)",
                  (job["sha256"], job["path"], now(), res["status"], res["message"]))
        ws.db.audit(c, ws.actor, f"robot.{res['status']}", "file", os.path.basename(job["path"]),
                    {"message": res["message"]})
    return res


def step(ws, drv=None) -> dict | None:
    """One robot step from the watch timer: the next pending model, if the robot is on."""
    if not settings(ws)["enabled"]:
        return None
    jobs = pending(ws)
    return run_one(ws, jobs[0], drv) if jobs else None


def teach(ws, name: str, drv=None) -> dict:
    """Store the button under the mouse pointer as ``name`` (test_run / save / close)."""
    if name not in BUTTONS:
        raise ValueError(name)
    spec = (drv or Win32()).cursor_window()
    b = dict(settings(ws)["buttons"])
    b[name] = spec
    ws.db.set_setting("robot.buttons", b)
    return spec
