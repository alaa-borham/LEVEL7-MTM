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
        self.menu, self.commands, self.posted, self.backs = menu, [], [], []

    def active_window(self):
        return 42                                                # the owner's window in front

    def send_back(self, h, prev=None):
        self.backs.append((h, prev))

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
    assert g.backs == [(1, 42), (1, 42)]          # GRAFIS behind, the owner's window in front again (twice)
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
    assert g.closed[0] == 7 and set(g.pressed) == {80, 92}                # crash box closed, OK, taught button
    assert 1 in g.closed and "started anew" in r["message"]               # the model left open: GRAFIS closed
    assert r["status"] == "done", r["message"]
    assert "GRAFIS crashed" in r["message"] and 'answered "GRAFIS System Info-Box" (OK)' in r["message"]


def test_nesting_failure_says_why():
    from level7mtm.services.auto_cut import _why
    assert _why("x\nUNPLACED: front1 base:__HN40_0 wider than the fabric\n", 0).startswith(
        "1 piece(s) not placed — front1 base:__HN40_0 wider than the fabric")
    assert _why("error: ValueError: no pieces", 2) == "error: ValueError: no pieces"
    assert "exit code 3" in _why("", 3)


def test_one_robot_at_a_time_and_a_broken_report_never_retries(ws, monkeypatch, tmp_path):
    from level7mtm.services import errors_folder, grafis_robot
    job = {"path": str(tmp_path / "a.mdl"), "sha256": "x", "line_number": "1"}
    grafis_robot._RUNNING.acquire()
    try:
        assert grafis_robot.run_one(ws, job)["status"] == "busy"          # the timer and "Robot now" never both
    finally:
        grafis_robot._RUNNING.release()

    def broken(*a, **k):
        raise PermissionError()
    monkeypatch.setattr(errors_folder, "report", broken)

    class NoGrafis:
        def windows(self):
            return []

        def open(self, p):
            raise TimeoutError()
    r = grafis_robot.run_one(ws, job, drv=NoGrafis(), sleep=lambda s: None)
    assert r["status"] == "failed" and r["message"].startswith("TimeoutError")   # a reason, never empty
    assert ws.db.one("SELECT id FROM robot_jobs WHERE path=?", (job["path"],))     # recorded: not run again


def test_a_model_written_again_with_the_same_content_is_tried_again(ws, tmp_path):
    """Orders read again write the same model again: an old robot try (before this writing) must not keep it
    waiting for ever (owner: "it says waiting for the robot and the robot is on")."""
    import hashlib
    import json

    from conftest import XML_53

    from level7mtm.services import grafis_robot as robot
    rid = ws.import_file(XML_53).lines[0]["revision_id"]
    m = tmp_path / "Order-1.mdl"
    m.write_bytes(b"model")
    sha = hashlib.sha256(b"model").hexdigest()
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) "
                  "VALUES (?, '2026-10-19T09:00:00', 'written', ?, 't', ?)", (rid, str(m), json.dumps({"sha256": sha})))
        c.execute("INSERT INTO robot_jobs(sha256, path, at, status, message) VALUES (?,?,?,?,?)",
                  (sha, str(m), "2026-10-19T09:05:00", "failed", "GRAFIS was closed"))
    assert robot.pending(ws) == []                                        # tried for this writing
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) "
                  "VALUES (?, '2026-10-20T09:00:00', 'written', ?, 't', ?)", (rid, str(m), json.dumps({"sha256": sha})))
    assert [j["path"] for j in robot.pending(ws)] == [str(m)]             # written again: a new job


def test_grafis_grave_error_and_missing_chart_stop_the_model(ws, tmp_path):
    """GRAFIS says "grave error … mu5Tset-3": Cancel (it is closed, the next model starts a new one), never OK;
    "measurement chart … .mas not available": the model fails with the chart named."""
    import hashlib

    class Broken(FakeGrafis):
        def __init__(self, text):
            super().__init__()
            self.text, self.pressed = text, []

        def windows(self):
            return [{"hwnd": 5, "title": "GRAFIS", "cls": "#32770"}] if not self.pressed else []

        def buttons_of(self, h):
            return [{"hwnd": 51, "text": "موافق"}, {"hwnd": 52, "text": "إلغاء الأمر"}]

        def texts_of(self, h):
            return self.text

        def press(self, b):
            self.pressed.append(b)

    ws.db.set_setting("robot.mode", "background")
    p = tmp_path / "Order-9-50-1_0.mdl"
    p.write_bytes(b"x")
    job = {"path": str(p), "sha256": hashlib.sha256(b"x").hexdigest()}
    g = Broken(["ATTENTION:", "A grave error has been discovered in\nthe internal file organization. The", "mu5Tset-3"])
    g.windows = lambda: [{"hwnd": 5, "title": "GRAFIS", "cls": "#32770"}] if g.opened else []
    r = robot.run_one(ws, job, g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "failed" and "mu5Tset-3" in r["message"] and g.pressed == [52]     # Cancel
    g = Broken(["You have called measurment chart", "\\grafis\\basis_d\\mas\\3537.mas",
                "It is not available on the current drive or is faulty."])
    g.windows = lambda: [{"hwnd": 5, "title": "GRAFIS - Info (5)", "cls": "#32770"}] if g.opened else []
    r = robot.run_one(ws, job, g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "failed" and "3537.mas" in r["message"] and g.pressed == [51]      # OK
    from level7mtm.ui.i18n import set_language, tr_msg
    set_language("ar")
    assert "3537.mas" in tr_msg(r["message"]) and "جدول القياسات" in tr_msg(r["message"])
    set_language("en")


def test_grafis_is_started_on_its_own_drive(tmp_path):
    exe = tmp_path / "prog" / "grafis.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    assert robot.grafis_home(exe=str(exe)) == str(exe.parent)
    (tmp_path / "D" / "grafis" / "basis_d").mkdir(parents=True)
    assert robot.grafis_home(exe="", drives=[str(tmp_path / "C"), str(tmp_path / "D")]) == \
        str(tmp_path / "D" / "grafis")
    assert robot.grafis_home(exe="", drives=[]) is None


def test_grafis_restarted_before_it_runs_out_of_scratch_files():
    """GRAFIS keeps scratch files of every model it opened ("lm4modl_z: Zu viele Scratch-Files!"): the robot
    closes it after so many models — asked to save again: No — and ends it if it stays open."""
    class Open:
        def __init__(self, stubborn=False):
            self.closed, self.pressed, self.ended, self.stubborn, self.asked = [], [], [], stubborn, True

        def windows(self):
            out = [] if (self.closed and not self.stubborn) else [{"hwnd": 1, "title": "Grafis - [A: x.mdl]",
                                                                 "cls": "GrafisMain"}]
            if self.closed and self.asked:
                out.append({"hwnd": 9, "title": "GRAFIS", "cls": "#32770"})
            return out

        def buttons_of(self, h):
            return [{"hwnd": 91, "text": "Ja"}, {"hwnd": 92, "text": "Nein"}]

        def press(self, b):
            self.pressed.append(b)
            self.asked = False

        def close(self, h):
            self.closed.append(h)

        def terminate(self, h):
            self.ended.append(h)
            self.stubborn = False
            return True
    g = Open()
    assert "started anew" in robot.restart_grafis(g, sleep=lambda s: None, clock=_clock())
    assert g.closed == [1] and g.pressed == [92] and not g.ended          # No: the model is saved already
    g = Open(stubborn=True)
    assert "ended" in robot.restart_grafis(g, sleep=lambda s: None, clock=_clock()) and g.ended == [1]

    class NoGrafis:
        def windows(self):
            return []
    assert robot.restart_grafis(NoGrafis()) == ""                       # nothing open: nothing to do


def test_scratch_files_grave_error_named(ws, tmp_path):
    import hashlib

    class Box(FakeGrafis):
        def __init__(self):
            super().__init__()
            self.pressed = []

        def windows(self):
            return [{"hwnd": 5, "title": "GRAFIS", "cls": "#32770"}] if self.opened and not self.pressed else []

        def buttons_of(self, h):
            return [{"hwnd": 51, "text": "موافق"}, {"hwnd": 52, "text": "إلغاء الأمر"}]

        def texts_of(self, h):
            return ["ATTENTION:", "A grave error has been discovered in the internal file organization. The",
                    "error is defined as", "lm4modl_z: Zu viele Scratch-Files!"]

        def press(self, b):
            self.pressed.append(b)
    ws.db.set_setting("robot.mode", "background")
    p = tmp_path / "Order-1.mdl"
    p.write_bytes(b"x")
    g = Box()
    r = robot.run_one(ws, {"path": str(p), "sha256": hashlib.sha256(b"x").hexdigest()}, g, sleep=lambda s: None,
                      clock=_clock())
    assert r["status"] == "failed" and "Zu viele Scratch-Files" in r["message"] and g.pressed == [52]


def test_one_model_at_a_time_closed_before_the_next(ws, tmp_path):
    """Owner: the robot works on one model, closes it, then takes the next. A model GRAFIS still shows from before
    is closed first; the model done is closed before the next one (here GRAFIS is closed: no close taught)."""
    import hashlib

    class Grafis(FakeGrafis):
        def __init__(self):
            super().__init__()
            self.closed, self.left = [], "Grafis - [A: ..old-model__1.mdl]"

        def windows(self):
            if self.left:
                return [{"hwnd": 3, "title": self.left, "cls": "GrafisMain"}]
            return super().windows()

        def close(self, h):
            self.closed.append(h)
            if h == 3:
                self.left = None
            if h == 1:
                self.opened = None                       # GRAFIS closed: the model with it
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60},
                                        "save": {"title": "GRAFIS", "cls": "Toolbar", "dx": 20, "dy": 60}})
    ws.db.set_setting("robot.mode", "mouse")
    ws.db.set_setting("robot.run_wait_s", 0)
    p = tmp_path / "Order-9-50-1_0.mdl"
    p.write_bytes(b"x")
    g = Grafis()
    r = robot.run_one(ws, {"path": str(p), "sha256": hashlib.sha256(b"x").hexdigest()}, g, sleep=lambda s: None,
                      clock=_clock())
    assert r["status"] == "done", r["message"]
    assert g.closed[0] == 3 and 1 in g.closed and g.opened is None        # the old one first, then this one


def test_grafis_short_of_memory_is_started_anew_and_the_model_waits(ws, tmp_path):
    """"GRAFIS - Memory Status: nur noch 12,676 MB freier Speicher für die Auslagerungsdatei": OK, the model goes on,
    GRAFIS starts anew before the next one; still short after a new GRAFIS: the model waits (not failed)."""
    import hashlib

    class Low(FakeGrafis):
        def __init__(self, free):
            super().__init__()
            self.free, self.pressed, self.closed, self.box = free, [], [], False

        def memory_free_mb(self):
            return self.free

        def windows(self):
            out = [] if self.closed else [{"hwnd": 2, "title": "GRAFIS", "cls": "Toolbar"}]
            if self.box:
                out.append({"hwnd": 5, "title": "GRAFIS - Memory Status", "cls": "#32770"})
            return out

        def buttons_of(self, h):
            return [{"hwnd": 51, "text": "موافق"}]

        def texts_of(self, h):
            return ["Es stehen nur noch 12,676 MB freier Speicher f�r die Auslagerungsdatei zur Verf�gung."]

        def press(self, b):
            self.pressed.append(b)
            self.box = False

        def close(self, h):
            self.closed.append(h)

    robot._LOW["memory"] = ""
    ws.db.set_setting("robot.mode", "background")
    p = tmp_path / "Order-1.mdl"
    p.write_bytes(b"x")
    job = {"path": str(p), "sha256": hashlib.sha256(b"x").hexdigest()}
    g = Low(free=200)                                   # short even after GRAFIS was started anew
    r = robot.run_one(ws, job, g, sleep=lambda s: None, clock=_clock())
    assert r["status"] == "waiting" and "free memory 200 MB" in r["message"] and g.closed == [2]
    assert not ws.db.one("SELECT id FROM robot_jobs WHERE path=?", (str(p),))   # not tried: taken again later
    from level7mtm.ui.i18n import set_language, tr_msg
    set_language("ar")
    assert "الذاكرة" in tr_msg(r["message"])
    set_language("en")
    g = Low(free=5000)
    g.box = True                                        # the box is on the screen: OK, and noted for after the save
    notes = []
    robot._answer_dialogs(g, robot.settings(ws), notes)
    assert g.pressed == [51] and robot._LOW["memory"] and "short of memory" in notes[0]
    r = robot.run_one(ws, job, g, sleep=lambda s: None, clock=_clock())
    assert g.closed == [2] and not robot._LOW["memory"]  # started anew first, then the model went on
    assert robot.shortage(g, {"min_memory_mb": 1024, "min_disk_mb": 10 ** 12}, str(p)).startswith("free disk")
    assert robot.shortage(g, {"min_memory_mb": 1024, "min_disk_mb": 1}, str(p)) == ""


def test_grafis_restore_backup_copy_is_closed_never_opened():
    """"GRAFIS - Restore backup copy" (after GRAFIS was ended): Close — Open would bring back an old copy."""
    class Box:
        def __init__(self):
            self.pressed, self.closed = [], []

        def windows(self):
            return [{"hwnd": 7, "title": "GRAFIS - Restore backup copy", "cls": "GrafisDlg"}]

        def buttons_of(self, h):
            return [{"hwnd": 71, "text": "Open"}, {"hwnd": 72, "text": "Delete"}, {"hwnd": 73, "text": "Close"}]

        def press(self, b):
            self.pressed.append(b)

        def close(self, h):
            self.closed.append(h)
    g, notes = Box(), []
    robot._answer_dialogs(g, {}, notes)
    assert g.pressed == [73] and "backup copies not restored" in notes[0]
    assert robot.restart_grafis(g, sleep=lambda s: None, clock=_clock()) == ""   # not a GRAFIS to close


def test_grafis_temp_cleaned_when_grafis_is_closed(tmp_path):
    """A:\\GRAFIS\\temp (scratch files): old files removed once GRAFIS is closed; new ones (in use) stay."""
    import os
    import time
    t = tmp_path / "temp"
    (t / "sub").mkdir(parents=True)
    old, new = t / "a.scr", t / "b.scr"
    old.write_bytes(b"x" * 2048)
    (t / "sub" / "c.scr").write_bytes(b"y")
    new.write_bytes(b"z")
    for f in (old, t / "sub" / "c.scr"):
        os.utime(f, (time.time() - 3600, time.time() - 3600))
    n, _ = robot.clean_temp(str(tmp_path))
    assert n == 2 and not old.exists() and new.exists() and t.is_dir()
    assert robot.clean_temp(str(tmp_path / "none")) == (0, 0.0)
    from level7mtm.ui.i18n import set_language, tr_msg
    set_language("ar")
    assert "temp" in tr_msg("GRAFIS closed and started anew (scratch files); GRAFIS temp cleaned: 3 files, 12 MB")
    set_language("en")


def test_busy_says_what_the_robot_is_doing_and_since_when(tmp_path):
    """"Busy" with no model opened said nothing: it names the model, the step and for how long."""
    import time
    robot._CURRENT.update(path=str(tmp_path / "Order-7.mdl"))
    robot._step("cleaning the GRAFIS temp folder")
    robot._CURRENT["since"] = time.monotonic() - 42
    try:
        t = robot.busy_text()
        assert t.startswith("the robot is on Order-7.mdl: cleaning the GRAFIS temp folder for 4")
        from level7mtm.ui.i18n import set_language, tr_msg
        set_language("ar")
        assert "ينظّف مجلد temp" in tr_msg(t) and "Order-7.mdl" in tr_msg(t)
        set_language("en")
    finally:
        robot._CURRENT.update(path=None, step="", since=None)
    assert robot.busy_text() == "the robot is working on another model"
    t = tmp_path / "g" / "temp"
    t.mkdir(parents=True)
    for i in range(5):
        (t / f"{i}.scr").write_bytes(b"x")
    assert robot.clean_temp(str(tmp_path / "g"), now_=lambda: time.time() + 3600, budget_s=0)[0] == 0   # no time
    assert robot.clean_temp(str(tmp_path / "g"), now_=lambda: time.time() + 3600)[0] == 5
