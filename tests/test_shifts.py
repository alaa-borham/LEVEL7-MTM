"""Shifts (owner): a shift's end puts the "ready to cut" list back to zero (nothing deleted); the dashboard keeps
every shift with its dates and what was done in it."""
import json
import os
from datetime import datetime

import pytest
from conftest import XML_53

from level7mtm.services import shifts


def _cut(ws, rid, at):
    """An order nested and cut at ``at`` (as the chain leaves it)."""
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (rid,))
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) VALUES "
                  "(?, ?, 'written', 'x.mdl', 't', '{}')", (rid, at))
        c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status, "
                  "handed_over) VALUES (?, 'x.mdl', ?, '{}', '[]', ?, '[]', 'handed_over', 1)",
                  (f"r{rid}", at, json.dumps([{"revision_id": rid}])))
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) VALUES "
                  "(?, 'x.mdl', ?, 'done', ?, ?, '')", (f"r{rid}", at, json.dumps(["a.plt", "a.iso"]),
                                                      json.dumps({"length_mm": 2500})))


def test_ending_a_shift_empties_ready_to_cut_and_keeps_the_history(ws):
    from level7mtm.ui.cad import order_rows
    rid = ws.import_file(XML_53).lines[0]["revision_id"]
    _cut(ws, rid, "2026-10-23T09:00:00")
    assert shifts.current(ws)["started_at"] == shifts.FIRST              # just updated: nothing leaves the list
    assert [r["stage"] for r in order_rows(ws) if r["rid"] == rid] == ["cut"]
    rec = shifts.end_shift(ws)
    assert rec["counts"]["nested"] == 1 and rec["counts"]["cut_files"] == 2 and rec["counts"]["metres"] == 2.5
    assert not [r for r in order_rows(ws) if r["rid"] == rid]           # off "ready to cut" …
    assert ws.revision(rid)["status"] == "completed"                      # … still in the program
    h = shifts.history(ws)
    assert h[0]["ended_at"] is None and h[1]["counts"]["nested"] == 1     # the running one, then the ended


def test_a_shift_of_the_schedule_ends_the_one_before_by_itself(ws):
    shifts.set_schedule(ws, [{"name": "Morning", "start": "8:00"}, {"name": "Evening", "start": "20:00"}])
    assert [r["start"] for r in shifts.schedule(ws)] == ["08:00", "20:00"]
    ws.db.set_setting("shifts.started_at", "2026-10-23T08:00:00")
    ws.db.set_setting("shifts.current", "Morning")
    cur = shifts.current(ws, datetime(2026, 10, 23, 21, 0))
    assert cur == {"name": "Evening", "started_at": "2026-10-23T20:00:00"}
    ended = ws.db.one("SELECT * FROM shifts")
    assert ended["name"] == "Morning" and ended["ended_at"] == "2026-10-23T20:00:00"
    assert shifts.current(ws, datetime(2026, 10, 24, 7, 0))["name"] == "Evening"    # over midnight: still evening
    with pytest.raises(ValueError):
        shifts.set_schedule(ws, [{"name": "x", "start": "25:99"}])


def test_dashboard_shows_the_shifts(ws, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    win = MainWindow(ws)
    win.goto("Home")
    home = win.pages["Home"]
    home.refresh()
    assert home.shifts.rowCount() == 1
    home.end_shift()
    assert home.shifts.rowCount() == 2
    win.close()
    app.processEvents()
