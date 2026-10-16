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
