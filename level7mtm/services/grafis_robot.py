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
import re
import string
import sys
import threading
import time

from ..storage.db import now

BUTTONS = ("test_run", "save", "close")


def settings(ws) -> dict:
    return {"enabled": bool(ws.db.get_setting("robot.enabled", False)),
            "buttons": ws.db.get_setting("robot.buttons", {}) or {},
            "open_timeout": float(ws.db.get_setting("robot.open_timeout_s", 120)),   # GRAFIS starts slowly
            "dialogs": ws.db.get_setting("robot.dialogs", {}) or {},
            "run_wait": float(ws.db.get_setting("robot.run_wait_s", 8)),
            "save_timeout": float(ws.db.get_setting("robot.save_timeout_s", 120)),
            "run_timeout": float(ws.db.get_setting("robot.run_timeout_s", 300)),     # Test Run All Parts
            # background: never move the mouse (GRAFIS menu commands / clicks sent to the button itself);
            # auto: background where possible, else the mouse as before; mouse: always the real mouse
            "mode": ws.db.get_setting("robot.mode", "auto"),
            # background mode: GRAFIS stays behind the owner's windows (he keeps working)
            "behind": bool(ws.db.get_setting("robot.behind", True)),
            "menu": ws.db.get_setting("robot.menu", {}) or {},
            # GRAFIS keeps scratch files of every model it opened: after so many models it is closed and the next
            # model starts a new GRAFIS (owner: "lm4modl_z: Zu viele Scratch-Files!" after a few hundred)
            "restart_every": int(ws.db.get_setting("robot.restart_every", 20) or 0)}


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
        H, U, P, L = wintypes.HWND, wintypes.UINT, ctypes.c_void_p, wintypes.LPARAM    # 64-bit window handles
        for name, args in (("GetAncestor", [H, U]), ("GetWindowTextW", [H, wintypes.LPWSTR, ctypes.c_int]),
                           ("GetClassNameW", [H, wintypes.LPWSTR, ctypes.c_int]), ("IsWindowVisible", [H]),
                           ("GetWindowRect", [H, P]), ("ShowWindow", [H, ctypes.c_int]), ("IsZoomed", [H]),
                           ("SetForegroundWindow", [H]), ("GetWindowThreadProcessId", [H, P]),
                           ("SendMessageTimeoutW", [H, U, wintypes.WPARAM, L, U, U, P]),
                           ("SetWindowPos", [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, U])):
            getattr(self.u, name).argtypes = args
        self.u.SendMessageTimeoutW.restype = L
        self.u.GetForegroundWindow.restype = H

    def process_of(self, h) -> str:
        """The program a window belongs to (e.g. werfault.exe), "" when it cannot be read."""
        k = self.ct.windll.kernel32
        pid = self.wt.DWORD()
        self.u.GetWindowThreadProcessId(h, self.ct.byref(pid))
        hp = k.OpenProcess(0x1000, False, pid.value)                   # PROCESS_QUERY_LIMITED_INFORMATION
        if not hp:
            return ""
        try:
            b = self.ct.create_unicode_buffer(520)
            n = self.wt.DWORD(520)
            return os.path.basename(b.value) if k.QueryFullProcessImageNameW(hp, 0, b, self.ct.byref(n)) else ""
        finally:
            k.CloseHandle(hp)

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
            spec["text"] = self._text(under).replace("&", "").strip()
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

    def buttons_of(self, h) -> list[dict]:
        """The buttons of a dialog box: {"hwnd", "text"}."""
        self._setup_bg()
        out, c = [], None
        while True:
            c = self.u.FindWindowExW(h, c, "Button", None)
            if not c:
                break
            out.append({"hwnd": c, "text": self._text(c).replace("&", "").strip()})
        return out

    def texts_of(self, h) -> list[str]:
        """The texts of a dialog box (its Static lines): what GRAFIS says in it."""
        self._setup_bg()
        out, c = [], None
        while True:
            c = self.u.FindWindowExW(h, c, "Static", None)
            if not c:
                break
            t = self._text(c).strip()
            if t:
                out.append(t)
        return out

    def terminate(self, h) -> bool:
        """End the program of a window (GRAFIS left running with too many models): True when done."""
        k = self.ct.windll.kernel32
        pid = self.wt.DWORD()
        self.u.GetWindowThreadProcessId(h, self.ct.byref(pid))
        hp = k.OpenProcess(0x0001, False, pid.value)                    # PROCESS_TERMINATE
        if not hp:
            return False
        try:
            return bool(k.TerminateProcess(hp, 0))
        finally:
            k.CloseHandle(hp)

    def press(self, h):
        self._setup_bg()
        self.u.PostMessageW(h, 0x00F5, 0, 0)                                 # BM_CLICK: the button itself

    def close(self, h):
        self._setup_bg()
        self.u.PostMessageW(h, 0x0010, 0, 0)                                 # WM_CLOSE

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
        # as a double-click: opens in GRAFIS (owner) — started in GRAFIS's own folder: GRAFIS finds its measurement
        # charts as \\grafis\\basis_d\\mas\\… on the *current* drive (started from this program on another drive it
        # did not find them, then stopped with "grave error mu5Tset")
        home = grafis_home()
        if home:
            os.startfile(path, cwd=home)
        else:
            os.startfile(path)

    def active_window(self):
        return self.u.GetForegroundWindow()

    def send_back(self, h, prev=None):
        """GRAFIS behind the other windows, not maximized over the screen, the owner's window in front again:
        the robot works on it in the background (menu commands / clicks sent to its controls)."""
        if self.u.IsZoomed(h):
            self.u.ShowWindow(h, 4)                  # SW_SHOWNOACTIVATE: not maximized any more
        self.u.SetWindowPos(h, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)    # HWND_BOTTOM, no move/size/activate
        if prev and prev != h:
            self.u.keybd_event(0x12, 0, 0, 0)        # ALT: lets this process give the focus back
            self.u.keybd_event(0x12, 0, 2, 0)
            self.u.SetForegroundWindow(prev)


def _assoc_exe(ext: str) -> str:
    """The program Windows opens these files with ("" when not known)."""
    try:
        import ctypes
        from ctypes import wintypes
        n = wintypes.DWORD(520)
        b = ctypes.create_unicode_buffer(520)
        if ctypes.windll.shlwapi.AssocQueryStringW(0, 2, ext, None, b, ctypes.byref(n)) == 0:   # ASSOCSTR_EXECUTABLE
            return b.value
    except Exception:  # noqa: BLE001
        pass
    return ""


def grafis_home(exe: str | None = None, drives=None) -> str | None:
    """The folder GRAFIS is started in, on the drive that has \\grafis\\basis_d (its measurement charts): the
    folder of the program .mdl files open with, else the first drive with \\grafis."""
    exe = _assoc_exe(".mdl") if exe is None and sys.platform == "win32" else exe
    if exe and os.path.isfile(exe):
        return os.path.dirname(exe)
    for d in (drives if drives is not None else
              ([f"{x}:\\" for x in string.ascii_uppercase] if sys.platform == "win32" else [])):
        if os.path.isdir(os.path.join(d, "grafis", "basis_d")):
            return os.path.join(d, "grafis")
    return None


# ------------------------------------------------------------------ robot
_SHA_CACHE: dict = {}                             # path -> ((size, mtime), sha): hundreds of models, read once


def _sha(path):
    st = os.stat(path)
    key = (st.st_size, st.st_mtime_ns)
    hit = _SHA_CACHE.get(path)
    if hit and hit[0] == key:
        return hit[1]
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    if len(_SHA_CACHE) > 5000:
        _SHA_CACHE.clear()
    _SHA_CACHE[path] = (key, sha)
    return sha


def file_sha(path) -> str:
    """SHA-256 of a file, remembered while its size and time stay the same."""
    return _sha(path)


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


OK_TEXTS = ("ok", "o.k.", "weiter", "continue", "fortsetzen", "schließen", "schliessen", "close", "موافق")
CANCEL_TEXTS = ("cancel", "abbrechen", "إلغاء الأمر", "إلغاء")
NO_TEXTS = ("no", "nein", "لا")
_DONE_SINCE_START = {"n": 0}                       # models GRAFIS did since it was (re)started by the robot


def restart_grafis(drv, sleep=time.sleep, clock=time.monotonic) -> str:
    """Close GRAFIS (its models are saved already): asked to save again — No; still open after a while — ended.
    The next model starts a new GRAFIS with no scratch files."""
    def mains():
        return [w["hwnd"] for w in drv.windows() if w["cls"] != "#32770" and
                (w["title"] or "").lower().startswith("grafis")]
    found = mains()
    if not found:
        return ""
    for h in found:
        drv.close(h)
    t0 = clock()
    while clock() - t0 < 20:
        sleep(1)
        if hasattr(drv, "buttons_of"):
            for w in drv.windows():                    # "save changes?": no — the model is saved already
                if w["cls"] == "#32770" and "grafis" in (w["title"] or "").lower():
                    b = next((b for b in drv.buttons_of(w["hwnd"]) if b["text"].lower() in NO_TEXTS), None)
                    if b:
                        drv.press(b["hwnd"])
        if not mains():
            return "GRAFIS closed and started anew (scratch files)"
    if hasattr(drv, "terminate"):
        for h in mains():
            drv.terminate(h)
    return "GRAFIS ended and started anew (scratch files)"


def _grafis_says(drv, h) -> str:
    try:
        return " ".join(drv.texts_of(h)) if hasattr(drv, "texts_of") else ""
    except Exception:  # noqa: BLE001
        return ""


def _answer_dialogs(drv, st, notes: list) -> None:
    """GRAFIS start-up windows (System Info-Box, Start …) and the Windows crash box block the model from opening:
    answer them in the background — the button taught for that window, else its OK — and say so."""
    if not hasattr(drv, "buttons_of"):
        return
    taught = st.get("dialogs") or {}
    try:
        wins = drv.windows()
    except Exception:  # noqa: BLE001
        return
    for w in wins:
        title, low = w["title"] or "", (w["title"] or "").lower()
        if w["cls"] != "#32770":
            continue
        proc = ""
        if hasattr(drv, "process_of"):
            try:
                proc = drv.process_of(w["hwnd"]).lower()
            except Exception:  # noqa: BLE001
                proc = ""
        if proc == "werfault.exe" or "werfault" in low or "application error" in low or "anwendungsfehler" in low:
            drv.close(w["hwnd"])
            if "GRAFIS crashed (Windows error box closed)" not in notes:
                notes.append("GRAFIS crashed (Windows error box closed)")
            continue
        if "grafis" not in low and title not in taught:
            continue
        btns = drv.buttons_of(w["hwnd"])
        said = _grafis_says(drv, w["hwnd"])
        said_low = " ".join(said.lower().split())
        if "grave error" in said_low or "internal file organization" in said_low:
            # GRAFIS itself says to stop: Cancel ends it (the next model starts a new GRAFIS); OK would go on broken
            b = next((b for b in btns if b["text"].lower() in CANCEL_TEXTS), None) or (btns[-1] if btns else None)
            if b:
                drv.press(b["hwnd"])
            code = re.search(r"\bmu\w*-\d+|\b\w+_\w+: [^.!]+[.!]?", said)
            _DONE_SINCE_START["n"] = 0                   # a new GRAFIS starts with the next model
            raise RobotError(f"GRAFIS internal error {code.group(0).strip() if code else '?'} — GRAFIS was closed "
                             f"(Cancel)")
        if "measur" in said_low and "chart" in said_low:
            b = next((b for b in btns if b["text"].lower() in OK_TEXTS), None) or (btns[0] if btns else None)
            if b:
                drv.press(b["hwnd"])
            mas = re.search(r"\S+\.mas\b", said, re.I)
            raise RobotError(f"GRAFIS did not find the measurement chart {mas.group(0) if mas else ''} on the drive "
                             f"it was started on (GRAFIS folder: {grafis_home() or 'not found'})")
        want = next((t for k, t in taught.items() if title.startswith(k)), None)
        b = next((b for b in btns if want and b["text"].lower() == want.lower()), None) or \
            next((b for b in btns if b["text"].lower() in OK_TEXTS), None) or (btns[0] if len(btns) == 1 else None)
        if b:
            drv.press(b["hwnd"])
            note = f"answered \"{title[:40]}\" ({b['text']})"
            if note not in notes:
                notes.append(note)


def pending(ws, first: bool = False) -> list[dict]:
    """Order models written by this program and still not saved by GRAFIS (and not tried by the robot); ``first``:
    only the next one (the robot needs no more)."""
    out = []
    for r in ws.db.q("SELECT * FROM grafis_orders WHERE status='written' AND archived=0 AND id IN "
                     "(SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) ORDER BY id"):
        info = json.loads(r["info"])
        p = r["path"]
        try:
            if not p or not os.path.isfile(p) or info.get("sha256") != _sha(p):
                continue
        except OSError:                                # being written / locked: looked at next time
            continue
        # tried already for THIS writing of the model; a model written again with the same content (e.g. its order
        # read again) is a new job — an old try must not keep it waiting for ever
        if ws.db.one("SELECT id FROM robot_jobs WHERE sha256=? AND path=? AND at>=?",
                     (info["sha256"], p, r["created_at"])):
            continue
        out.append({"path": p, "sha256": info["sha256"], "line_number": info.get("line_number")})
        if first:
            break
    return out


_RUNNING = threading.Lock()                       # one robot at a time: GRAFIS gets one model after the other
_CURRENT: dict = {"path": None}                   # the model GRAFIS has open now (the MDL folder leaves it alone)


def current_model() -> str | None:
    return _CURRENT["path"]


def run_one(ws, job: dict, drv=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Open → Test Run All Parts → save → (close) for one model. Returns {status, message}; status "busy" when
    the robot is already working on a model (the timer and "Robot now" never drive GRAFIS together)."""
    from .activity import working
    if not _RUNNING.acquire(blocking=False):
        return {"status": "busy", "message": "the robot is working on another model", "path": job["path"]}
    try:
        rids = [r["revision_id"] for r in ws.db.q("SELECT revision_id FROM grafis_orders WHERE path=?",
                                                  (job["path"],))]
        _CURRENT["path"] = job["path"]
        with working(rids, "robot"):
            return _run_one(ws, job, drv, sleep, clock)
    finally:
        _CURRENT["path"] = None
        _RUNNING.release()


def _saved(path: str, old_sha: str, sleep=time.sleep) -> bool:
    """GRAFIS has saved the model: its content changed and it stays the same for a moment (not still written)."""
    try:
        if not os.path.isfile(path) or _sha(path) == old_sha:
            return False
        a = os.stat(path)
        sleep(1.5)
        b = os.stat(path)
        return (a.st_size, a.st_mtime) == (b.st_size, b.st_mtime)
    except OSError:                                   # locked while GRAFIS writes it
        return False


def _run_one(ws, job: dict, drv=None, sleep=time.sleep, clock=time.monotonic) -> dict:
    from . import errors_folder
    st = settings(ws)
    btn = st["buttons"]
    res = {"status": "failed", "message": "", "path": job["path"]}
    try:
        drv = drv or Win32()
        if "test_run" not in btn and st["mode"] == "mouse":
            raise RobotError("the 'Test Run All Parts' button was not taught yet (GRAFIS screen → Robot)")
        stem = os.path.splitext(os.path.basename(job["path"]))[0]
        notes: list[str] = []
        try:
            _answer_dialogs(drv, st, notes)           # a box left from before (crash, start window) first
        except RobotError as e:                       # left by the model before: GRAFIS was closed, start anew
            notes.append(f"before: {e}")
            sleep(3)
        behind = st["mode"] == "background" and st["behind"] and hasattr(drv, "send_back")
        prev = drv.active_window() if behind else None
        drv.open(job["path"])
        t0, main = clock(), None
        while clock() - t0 < st["open_timeout"]:
            main = next((w["hwnd"] for w in drv.windows() if shows_file(w["title"], job["path"])), None)
            if main and drv.responsive(main):
                break
            _answer_dialogs(drv, st, notes)
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not show {stem} within {st['open_timeout']:g} s — "
                             f"{_seen(drv, btn)}" + (f" — robot: {'; '.join(notes)}" if notes else ""))
        if behind:
            drv.send_back(main, prev)                 # the owner keeps working in front
        sleep(2)
        how = [_act(drv, st, main, "test_run", job["path"], True)]
        sleep(st["run_wait"])
        t0 = clock()
        while clock() - t0 < st["run_timeout"]:
            if drv.responsive(main):
                break
            _answer_dialogs(drv, st, notes)
            sleep(1)
        else:
            raise RobotError(f"GRAFIS not responding {st['run_timeout']:g} s after Test Run All Parts — "
                             f"{_seen(drv, btn)}" + (f" — robot: {'; '.join(notes)}" if notes else ""))
        _answer_dialogs(drv, st, notes)               # a box after the test run blocks the save
        if behind:
            drv.send_back(main, prev)
        how.append(_act(drv, st, main, "save", job["path"], True))
        t0 = clock()
        while clock() - t0 < st["save_timeout"]:
            if _saved(job["path"], job["sha256"], sleep):
                break
            _answer_dialogs(drv, st, notes)
            sleep(1)
        else:
            raise RobotError(f"GRAFIS did not save {stem} within {st['save_timeout']:g} s — {_seen(drv, btn)}"
                             + (f" — robot: {'; '.join(notes)}" if notes else ""))
        try:
            how.append(_act(drv, st, main, "close", job["path"], False))
        except RobotError:
            pass                                      # closing is a courtesy: the model is saved
        _DONE_SINCE_START["n"] += 1
        if st["restart_every"] and _DONE_SINCE_START["n"] >= st["restart_every"] and hasattr(drv, "close"):
            _DONE_SINCE_START["n"] = 0               # before GRAFIS runs out of scratch files
            try:
                notes.append(restart_grafis(drv, sleep, clock))
            except Exception as e:  # noqa: BLE001 - the model is saved: only said
                notes.append(f"GRAFIS not restarted: {e}")
        res.update(status="done", message="opened, Test Run All Parts, saved — " +
                   ", ".join(h for h in how + notes if h))
    except Exception as e:  # noqa: BLE001 - every failure is reported, never retried silently
        res["message"] = str(e) or type(e).__name__
        try:                                          # the report must never make the job run again
            errors_folder.report(ws, os.path.splitext(os.path.basename(job["path"]))[0], [res["message"]],
                                 "GRAFIS robot")
            from .done_folder import move_failed
            move_failed(ws, [r["revision_id"] for r in ws.db.q("SELECT revision_id FROM grafis_orders WHERE path=?",
                                                               (job["path"],))], [res["message"]], "GRAFIS robot")
        except Exception as e2:  # noqa: BLE001
            res["message"] += f" (error report not written: {e2 or type(e2).__name__})"
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
    jobs = pending(ws, first=True)
    return run_one(ws, jobs[0], drv) if jobs else None


def teach_dialog(ws, drv=None) -> dict:
    """The owner points at the button he presses in a GRAFIS window that appears at start (e.g. "GRAFIS - Start");
    the robot presses that button in the background whenever this window appears."""
    spec = (drv or Win32()).cursor_window()
    if not spec.get("text"):
        raise RobotError("point the mouse at a button of the GRAFIS window (it has no text there)")
    d = dict(settings(ws)["dialogs"])
    key = spec["title"].split("...")[0].split("…")[0].strip() or spec["title"]
    d[key] = spec["text"]
    ws.db.set_setting("robot.dialogs", d)
    return {"title": key, "button": spec["text"]}


def teach(ws, name: str, drv=None) -> dict:
    """Store the button under the mouse pointer as ``name`` (test_run / save / close)."""
    if name not in BUTTONS:
        raise ValueError(name)
    spec = (drv or Win32()).cursor_window()
    b = dict(settings(ws)["buttons"])
    b[name] = spec
    ws.db.set_setting("robot.buttons", b)
    return spec
