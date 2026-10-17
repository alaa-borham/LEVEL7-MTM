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
            "save_timeout": float(ws.db.get_setting("robot.save_timeout_s", 120)),
            # background: never move the mouse (GRAFIS menu commands / clicks sent to the button itself);
            # auto: background where possible, else the mouse as before; mouse: always the real mouse
            "mode": ws.db.get_setting("robot.mode", "auto"),
            "menu": ws.db.get_setting("robot.menu", {}) or {}}


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
        under = self.u.WindowFromPoint(p)
        h = self.u.GetAncestor(under, 2)                               # GA_ROOT
        left, top, _, _ = self.rect(h)
        spec = {"title": self._text(h), "cls": self._cls(h), "dx": p.x - left, "dy": p.y - top}
        if under and under != h:                                       # the button's own window: background clicks
            self._setup_bg()
            cp = self.wt.POINT(p.x, p.y)
            self.u.ScreenToClient(under, self.ct.byref(cp))
            path = self._child_path(h, under)
            if path:
                spec["child"] = {"path": path, "x": cp.x, "y": cp.y}
        return spec

    # -- background (no mouse, no keyboard): menu commands and clicks posted to the button's own window
    def _setup_bg(self):
        if getattr(self, "_bg", False):
            return
        u, wt = self.u, self.wt
        u.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        u.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
        u.FindWindowExW.restype = wt.HWND
        u.GetMenu.argtypes = [wt.HWND]
        u.GetMenu.restype = wt.HMENU
        u.GetSubMenu.argtypes = [wt.HMENU, self.ct.c_int]
        u.GetSubMenu.restype = wt.HMENU
        u.GetMenuItemCount.argtypes = [wt.HMENU]
        u.GetMenuItemID.argtypes = [wt.HMENU, self.ct.c_int]
        u.GetMenuItemID.restype = wt.UINT
        u.GetMenuStringW.argtypes = [wt.HMENU, wt.UINT, wt.LPWSTR, self.ct.c_int, wt.UINT]
        u.GetParent.argtypes = [wt.HWND]
        u.GetParent.restype = wt.HWND
        u.GetDlgCtrlID.argtypes = [wt.HWND]
        u.ScreenToClient.argtypes = [wt.HWND, self.ct.POINTER(wt.POINT)]
        self._bg = True

    def menu_items(self, h) -> list[dict]:
        """Every command of the window's menu: {"path": "Datei > Speichern", "text": "Speichern", "id": 57603}."""
        self._setup_bg()
        out = []

        def walk(m, prefix, depth):
            n = self.u.GetMenuItemCount(m)
            for i in range(max(n, 0)):
                b = self.ct.create_unicode_buffer(256)
                self.u.GetMenuStringW(m, i, b, 256, 0x400)                   # MF_BYPOSITION
                text = b.value.replace("&", "").split("\t")[0].strip()
                sub = self.u.GetSubMenu(m, i)
                if sub and depth < 4:
                    walk(sub, (prefix + " > " if prefix else "") + text, depth + 1)
                elif text:
                    mid = self.u.GetMenuItemID(m, i)
                    if mid not in (0, 0xFFFFFFFF):
                        out.append({"path": (prefix + " > " if prefix else "") + text, "text": text, "id": int(mid)})
        m = self.u.GetMenu(h)
        if m:
            walk(m, "", 0)
        return out

    def command(self, h, cmd_id: int):
        self._setup_bg()
        self.u.PostMessageW(h, 0x0111, cmd_id, 0)                            # WM_COMMAND, as the menu does

    def _child_path(self, top, ch) -> list:
        """(class, n-th of that class under its parent) from the top window down to the control."""
        self._setup_bg()
        chain, h = [], ch
        while h and h != top:
            parent = self.u.GetParent(h)
            if not parent:
                break
            cls, k, c = self._cls(h), 0, None
            while True:
                c = self.u.FindWindowExW(parent, c, cls, None)
                if not c or c == h:
                    break
                k += 1
            chain.append([cls, k])
            h = parent
        return list(reversed(chain)) if h == top else []

    def find_child(self, top, path: list):
        self._setup_bg()
        h = top
        for cls, k in path:
            c = None
            for _ in range(k + 1):
                c = self.u.FindWindowExW(h, c, cls, None)
                if not c:
                    return None
            h = c
        return h

    def post_click(self, h, x: int, y: int):
        """A click sent to the control itself: the mouse pointer does not move, the window need not be in front."""
        self._setup_bg()
        lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
        self.u.PostMessageW(h, 0x0200, 0, lp)                                # WM_MOUSEMOVE
        self.u.PostMessageW(h, 0x0201, 1, lp)                                # WM_LBUTTONDOWN (MK_LBUTTON)
        self.u.PostMessageW(h, 0x0202, 0, lp)                                # WM_LBUTTONUP

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


def _seen(drv, btn: dict) -> str:
    """What was on the screen instead (GRAFIS windows and dialogs by title), for the reason of a failure."""
    classes = {b.get("cls") for b in btn.values() if isinstance(b, dict)}
    try:
        ws_ = [w for w in drv.windows() if w["title"] and ("grafis" in w["title"].lower() or w["cls"] in classes
                                                          or w["cls"] == "#32770")]          # #32770 = dialog box
    except Exception:  # noqa: BLE001
        return "windows not readable"
    if not ws_:
        return "no GRAFIS window open"
    return "windows: " + " | ".join(f"\"{w['title'][:90]}\"" + (" (dialog)" if w["cls"] == "#32770" else "")
                                    for w in ws_[:8])


MENU_TEXT = {"test_run": ["test run all parts", "testlauf alle teile", "alle teile testen"],
             "save": ["save", "speichern", "sichern"], "close": ["close", "schließen", "schliessen"]}


def _menu_item(drv, h, name: str, chosen: dict) -> dict | None:
    """The GRAFIS menu command for ``name``: the one chosen in the setup, else by its usual text."""
    if not hasattr(drv, "menu_items") or h is None:
        return None
    try:
        items = drv.menu_items(h)
    except Exception:  # noqa: BLE001 - no readable menu: not available in the background
        return None
    want = chosen.get(name)
    if want:
        return next((i for i in items if i["path"] == want or i["text"] == want), None)
    texts = MENU_TEXT[name]
    return next((i for i in items if i["text"].lower().rstrip(".… ") in texts), None)


def _act(drv, st, main, name: str, path: str, required: bool) -> str | None:
    """Do one GRAFIS step (test_run / save / close). Background first (no mouse); the real mouse only in mode
    auto / mouse. Returns how it was done, None when an optional step is not available."""
    btn = st["buttons"].get(name)
    if st["mode"] in ("auto", "background"):
        item = _menu_item(drv, main, name, st["menu"])
        if item:
            drv.command(main, item["id"])
            return f"menu {item['path']}"
        if btn and btn.get("child") and hasattr(drv, "find_child"):
            top = _find(drv, btn, path)
            c = drv.find_child(top, btn["child"]["path"]) if top else None
            if c:
                drv.post_click(c, btn["child"]["x"], btn["child"]["y"])
                return "background click"
        if st["mode"] == "background":
            if not required:
                return None
            raise RobotError(f"'{name}' cannot be done without the mouse: no GRAFIS menu command for it and the button "
                             f"was taught without its control — choose the menu command or teach the button again")
    if btn:
        h = _find(drv, btn, path)
        if h is None:
            raise RobotError(f"the window of the '{name}' button was not found (teach it again)")
        drv.click(h, btn["dx"], btn["dy"])
        return "mouse"
    if name == "save":
        drv.ctrl_s(main)
        return "Ctrl+S"
    if required:
        raise RobotError(f"the '{name}' button was not taught yet (GRAFIS screen → Robot)")
    return None


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
        drv = drv or Win32()
        if "test_run" not in btn and st["mode"] == "mouse":
            raise RobotError("the 'Test Run All Parts' button was not taught yet (GRAFIS screen → Robot)")
        stem = os.path.splitext(os.path.basename(job["path"]))[0]
        drv.open(job["path"])
        t0, main = clock(), None
        while clock() - t0 < st["open_timeout"]:
            main = next((w["hwnd"] for w in drv.windows() if shows_file(w["title"], job["path"])), None)
            if main and drv.responsive(main):
                break
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not show {stem} within {st['open_timeout']:g} s — "
                             f"{_seen(drv, btn)}")
        sleep(2)
        how = [_act(drv, st, main, "test_run", job["path"], True)]
        sleep(st["run_wait"])
        t0 = clock()
        while not drv.responsive(main) and clock() - t0 < 300:
            sleep(1)
        how.append(_act(drv, st, main, "save", job["path"], True))
        t0 = clock()
        while clock() - t0 < st["save_timeout"]:
            if os.path.isfile(job["path"]) and _sha(job["path"]) != job["sha256"]:
                break
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not save {stem} within {st['save_timeout']:g} s")
        try:
            how.append(_act(drv, st, main, "close", job["path"], False))
        except RobotError:
            pass                                      # closing is a courtesy: the model is saved
        res.update(status="done", message="opened, Test Run All Parts, saved — " +
                   ", ".join(h for h in how if h))
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
