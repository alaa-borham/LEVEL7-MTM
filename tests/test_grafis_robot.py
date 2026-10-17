"""GRAFIS robot logic with a simulated GRAFIS (the real one runs only on the factory PC)."""
import itertools

from conftest import MDL, XML_53, needs_mdl

from level7mtm.services import grafis_robot as robot
from level7mtm.services.grafis_orders import import_and_prepare


class FakeGrafis:
    """Opens a file into a window; clicking Save (or Ctrl+S) after Test Run rewrites the file."""

    def __init__(self, saves=True):
        self.saves, self.opened, self.clicks, self.ran = saves, None, [], False

    def open(self, path):
        self.opened = path

    def windows(self):
        out = [{"hwnd": 2, "title": "GRAFIS", "cls": "Toolbar"}]
        if self.opened:
            out.append({"hwnd": 1, "title": f"Grafis - [A: ..{self.opened[-30:]}]", "cls": "GrafisMain"})
        return out

    def responsive(self, h):
        return True

    def click(self, h, dx, dy):
        self.clicks.append((h, dx, dy))
        if (h, dx, dy) == (2, 470, 60):
            self.ran = True
        if (h, dx, dy) == (2, 20, 60):
            self._save()

    def ctrl_s(self, h):
        self.clicks.append(("ctrl_s", h))
        self._save()

    def _save(self):
        if self.saves and self.ran:
            with open(self.opened, "ab") as fh:
                fh.write(b"\0" * 8)


def _setup(ws, tmp_path):
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    ws.db.set_setting("robot.enabled", True)
    ws.db.set_setting("robot.run_wait_s", 0)
    ws.db.set_setting("robot.save_timeout_s", 3)
    ws.db.set_setting("robot.open_timeout_s", 3)
    import_and_prepare(ws, XML_53)


def _clock():
    c = itertools.count(0, 1)
    return lambda: next(c)


@needs_mdl
def test_robot_opens_runs_and_saves_each_model_once(ws, tmp_path):
    _setup(ws, tmp_path)
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60}})
    jobs = robot.pending(ws)
    assert [j["line_number"] for j in jobs] == ["9-53-1_2", "9-53-2_2"]
    g = FakeGrafis()
    r = robot.run_one(ws, jobs[0], g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "done" and g.opened == jobs[0]["path"]
    assert g.clicks == [(2, 470, 60), ("ctrl_s", 1)]                # Test Run, then Ctrl+S (no Save taught)
    assert [j["line_number"] for j in robot.pending(ws)] == ["9-53-2_2"]   # saved by GRAFIS: done
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60},
                                        "save": {"title": "GRAFIS", "cls": "Toolbar", "dx": 20, "dy": 60}})
    g = FakeGrafis()
    r = robot.run_one(ws, robot.pending(ws)[0], g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "done" and g.clicks == [(2, 470, 60), (2, 20, 60)]
    assert robot.pending(ws) == []


@needs_mdl
def test_robot_failures_go_to_er_and_are_not_retried(ws, tmp_path):
    _setup(ws, tmp_path)
    job = robot.pending(ws)[0]
    r = robot.run_one(ws, job, FakeGrafis(), sleep=lambda s: None, clock=_clock())
    assert r["status"] == "failed" and "not taught" in r["message"]
    assert (tmp_path / "ER" / "Order-9-53-1_2__9-53-1_2.txt").exists()
    assert job["path"] not in [j["path"] for j in robot.pending(ws)]  # not retried by itself
    ws.db.q("DELETE FROM robot_jobs")
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60}})
    r = robot.run_one(ws, robot.pending(ws)[0], FakeGrafis(saves=False), sleep=lambda s: None, clock=_clock())
    assert r["status"] == "failed" and "did not save" in r["message"]


def test_robot_off_does_nothing(ws):
    assert robot.step(ws, FakeGrafis()) is None


def test_shortened_grafis_title_is_recognised():
    p = r"A:\LEVEL7GRAFIS-IN\MDL\Order-9-52-1_0__9-52-1_0.mdl"
    assert robot.shows_file("Grafis - [A: ..9-52-1_0__9-52-1_0.mdl] - 50-Werte-OPTIMASS", p)
    assert robot.shows_file("Grafis - [A:\\LEVEL7GRAFIS-IN\\MDL\\Order-9-52-1_0__9-52-1_0.mdl]", p)
    assert not robot.shows_file("Grafis - [A: ..9-52-2_0__9-52-2_0.mdl] - 50-Werte-OPTIMASS", p)
    assert not robot.shows_file("GRAFIS", p)


def test_open_timeout_says_what_grafis_shows_instead(ws, tmp_path):
    """The reason names the GRAFIS windows / dialogs on the screen, so the owner sees what blocks."""
    class Blocked(FakeGrafis):
        def windows(self):
            return [{"hwnd": 1, "title": "Grafis - [A: ..9-49-1_0.mdl]", "cls": "GrafisMain"},
                    {"hwnd": 3, "title": "Grafis", "cls": "#32770"}]
    ws.db.set_setting("robot.open_timeout_s", 3)
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60}})
    p = tmp_path / "Order-9-50-1_0.mdl"
    p.write_bytes(b"x")
    r = robot.run_one(ws, {"path": str(p), "sha256": "s"}, Blocked(), sleep=lambda s: None, clock=_clock())
    assert r["status"] == "failed" and "did not show Order-9-50-1_0" in r["message"]
    assert '"Grafis - [A: ..9-49-1_0.mdl]"' in r["message"] and '"Grafis" (dialog)' in r["message"]


class BackgroundGrafis(FakeGrafis):
    """GRAFIS with a menu: commands and posted clicks, never the real mouse."""
    MENU = [{"path": "Datei > Speichern", "text": "Speichern", "id": 57603},
            {"path": "Datei > Speichern unter", "text": "Speichern unter...", "id": 57604},
            {"path": "Teile > Test Run All Parts", "text": "Test Run All Parts", "id": 4711}]

    def __init__(self, menu=True):
        super().__init__()
        self.menu, self.commands, self.posted = menu, [], []

    def menu_items(self, h):
        return list(self.MENU) if self.menu else []

    def command(self, h, cmd_id):
        self.commands.append((h, cmd_id))
        if cmd_id == 4711:
            self.ran = True
        if cmd_id == 57603:
            self._save()

    def find_child(self, top, path):
        return 99 if path == [["ToolbarWindow32", 0]] else None

    def post_click(self, h, x, y):
        self.posted.append((h, x, y))
        if (x, y) == (470, 30):
            self.ran = True

    def click(self, h, dx, dy):
        raise AssertionError("the real mouse must not move")

    def ctrl_s(self, h):
        raise AssertionError("no keyboard in the background")


@needs_mdl
def test_robot_works_in_the_background_without_the_mouse(ws, tmp_path):
    _setup(ws, tmp_path)
    ws.db.set_setting("robot.mode", "background")
    g = BackgroundGrafis()                                       # menu commands: nothing taught at all
    r = robot.run_one(ws, robot.pending(ws)[0], g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "done" and g.commands == [(1, 4711), (1, 57603)]    # "Save as" never chosen
    assert "menu Teile > Test Run All Parts" in r["message"]
    # no menu entry for Test Run: the taught button is clicked through its own window (pointer stays)
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60,
                                                     "child": {"path": [["ToolbarWindow32", 0]], "x": 470, "y": 30}}})
    BackgroundGrafis.MENU = BackgroundGrafis.MENU[:2]
    try:
        g = BackgroundGrafis()
        r = robot.run_one(ws, robot.pending(ws)[0], g, sleep=lambda s: None, clock=_clock())
        assert r["status"] == "done" and g.posted == [(99, 470, 30)] and g.commands == [(1, 57603)]
        assert "background click" in r["message"]
        ws.db.set_setting("robot.buttons", {})
        import pytest
        with pytest.raises(robot.RobotError, match="without the mouse"):    # nothing usable: said, not guessed
            robot._act(BackgroundGrafis(), robot.settings(ws), 1, "test_run", "x.mdl", True)
    finally:
        BackgroundGrafis.MENU = BackgroundGrafis.MENU + [{"path": "Teile > Test Run All Parts",
                                                          "text": "Test Run All Parts", "id": 4711}]
    ws.db.set_setting("robot.menu", {"test_run": "Teile > Test Run All Parts", "save": "Datei > Speichern"})
    assert robot._menu_item(BackgroundGrafis(), 1, "save", {"save": "Datei > Speichern"})["id"] == 57603


def test_robot_answers_grafis_start_windows_and_the_crash_box(ws, tmp_path):
    """At the owner's: a crashed GRAFIS (WerFault box), then "GRAFIS System Info-Box" and "GRAFIS - Start ..."
    held the model back. The robot answers them in the background and goes on."""
    class Starting(BackgroundGrafis):
        def __init__(self):
            super().__init__()
            self.boxes = {7: ("WerFault.exe - Application Error", [("Close", 70)]),
                          8: ("GRAFIS System Info-Box", [("OK", 80)]),
                          9: ("GRAFIS - Start ...", [("Neu", 90), ("Letzte Datei", 91), ("Abbrechen", 92)])}
            self.pressed, self.closed = [], []

        def windows(self):
            out = [{"hwnd": h, "title": t, "cls": "#32770"} for h, (t, _) in self.boxes.items()]
            if not self.boxes and self.opened:
                out.append({"hwnd": 1, "title": f"Grafis - [A: ..{self.opened[-30:]}]", "cls": "GrafisMain"})
            return out

        def buttons_of(self, h):
            return [{"hwnd": b, "text": t} for t, b in self.boxes[h][1]]

        def press(self, b):
            self.pressed.append(b)
            self.boxes = {h: v for h, v in self.boxes.items() if b not in [x for _, x in v[1]]}

        def close(self, h):
            self.closed.append(h)
            self.boxes.pop(h, None)
    ws.db.set_setting("robot.open_timeout_s", 5)
    ws.db.set_setting("robot.mode", "background")
    ws.db.set_setting("robot.dialogs", {"GRAFIS - Start": "Abbrechen"})     # taught by the owner
    p = tmp_path / "Order-9-50-1_0.mdl"
    p.write_bytes(b"x")
    import hashlib
    g = Starting()
    r = robot.run_one(ws, {"path": str(p), "sha256": hashlib.sha256(b"x").hexdigest()}, g, sleep=lambda s: None,
                      clock=_clock())
    assert g.closed == [7] and set(g.pressed) == {80, 92}                   # crash box closed, OK, taught button
    assert r["status"] == "done", r["message"]
    assert "GRAFIS crashed" in r["message"] and 'answered "GRAFIS System Info-Box" (OK)' in r["message"]


def test_nesting_failure_says_why():
    from level7mtm.services.auto_cut import _why
    assert _why("x\nUNPLACED: front1 base:__HN40_0 wider than the fabric\n", 0).startswith(
        "1 piece(s) not placed — front1 base:__HN40_0 wider than the fabric")
    assert _why("error: ValueError: no pieces", 2) == "error: ValueError: no pieces"
    assert "exit code 3" in _why("", 3)
