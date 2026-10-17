"""CAD workspace: the marker LEVEL7 Nesting saved is read for the canvas, order lines show where they are."""
import json
import zipfile

import pytest
from conftest import XML_53


def _l7n(path):
    proj = {"pieces": [{"uid": "p1", "name": "front1", "sizes": {"base": {"cut": [[0, 0], [100, 0], [100, 50], [0, 50]],
                                                                         "grain": {"points": [[10, 25], [90, 25]]}}}}],
            "markers": [{"fabric": {"width": 1450}, "length": 300.0, "stats": {"placed": 2},
                         "placements": [{"piece_uid": "p1", "size": "base", "x": 0, "y": 0},
                                        {"piece_uid": "p1", "size": "base", "x": 250, "y": 100, "rotation": 90,
                                         "mirrored": True},
                                        {"piece_uid": "p1", "size": "base", "placed": False}]}]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("project.json", json.dumps({"format": "level7-nesting-project", "format_version": 1,
                                               "project": proj}))


def test_load_marker_places_pieces_like_level7_nesting(tmp_path):
    from level7mtm.ui.cad import load_marker
    p = tmp_path / "m.l7n"
    _l7n(p)
    m = load_marker(str(p))
    assert m["width"] == 1450 and m["length"] == 300 and len(m["pieces"]) == 2      # the unplaced one is left out
    a, b = m["pieces"]
    assert a["cut"][2] == (100, 50)
    # mirrored (x -> -x), turned 90° CCW, moved to (250, 100): (100, 0) -> (-100, 0) -> (0, -100) -> (250, 0)
    assert b["cut"][1] == pytest.approx((250, 0)) and b["mirrored"]
    assert load_marker(str(tmp_path / "missing.l7n")) is None


def test_order_rows_follow_the_chain(ws, tmp_path):
    from level7mtm.storage.db import now
    from level7mtm.ui.cad import order_rows
    ws.import_file(XML_53)
    rev = next(r for r in ws.list_revisions() if r["line_number"] == "9-53-1_2")
    rows = {r["rid"]: r for r in order_rows(ws)}
    assert rows[rev["id"]]["stage"] == "new" and rows[rev["id"]]["width"]
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, info) VALUES (?,?,?,?,?)",
                  (rev["id"], now(), "written", "x.mdl", json.dumps({"rows": [{"x": "X1", "value_mm": 1570}]})))
    assert {r["rid"]: r for r in order_rows(ws)}[rev["id"]]["stage"] == "grafis"
    l7n = tmp_path / "m.l7n"
    _l7n(l7n)
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status) "
                  "VALUES ('s','x.mdl',?,'{}','[]',?,'[]','handed_over')",
                  (now(), json.dumps([{"revision_id": rev["id"], "line_number": rev["line_number"]}])))
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) "
                  "VALUES ('s','x',?,'done',?,?,'')", (now(), json.dumps(["x.plt"]),
                                                      json.dumps({"l7n": str(l7n), "length_mm": 300.0,
                                                                  "efficiency_pct": 86.1, "placed": 2})))
    r = {r["rid"]: r for r in order_rows(ws)}[rev["id"]]
    assert r["stage"] == "cut" and r["l7n"] == str(l7n) and r["outputs"] == ["x.plt"]
    assert r["x_rows"] == [{"x": "X1", "value_mm": 1570}] and set(r["times"]) == {"new", "grafis", "nest", "cut"}
    with ws.db.tx() as c:
        c.execute("UPDATE grafis_orders SET archived=1")
    assert rev["id"] not in {r["rid"] for r in order_rows(ws)}               # cleaned from the list


def test_window_shows_the_selected_marker(ws, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.storage.db import now
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.import_file(XML_53)
    rev = ws.list_revisions()[0]
    l7n = tmp_path / "m.l7n"
    _l7n(l7n)
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status) "
                  "VALUES ('s','x.mdl',?,'{}','[]',?,'[]','handed_over')",
                  (now(), json.dumps([{"revision_id": rev["id"], "line_number": rev["line_number"]}])))
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) "
                  "VALUES ('s','x',?,'done','[]',?,'')", (now(), json.dumps({"l7n": str(l7n)})))
    win = MainWindow(ws)
    win.show()
    item = next(win.browser.tree.topLevelItem(i) for i in range(win.browser.tree.topLevelItemCount())
                if win.browser.tree.topLevelItem(i).data(0, 0x0100) == rev["id"])
    win.browser.tree.setCurrentItem(item)
    app.processEvents()
    assert win.sel_row["rid"] == rev["id"] and win.marker.view.marker["length"] == 300
    assert item.childCount() == 1 and item.child(0).text(1) == "2×"                 # front1 twice
    assert win.marker.hud_values["pieces"].text() == "2"
    win.goto("GRAFIS markers")
    assert win.current_page() is win.pages["GRAFIS markers"]
    win.tabs.setCurrentIndex(0)
    assert win.current_page() is None
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkey_exec = QMessageBox.exec
    QMessageBox.exec = lambda self: shown.append(self.text()) or 0
    try:
        win.model_result(rev["line_number"], rev["id"], {"status": "blocked", "path": None,
                                                         "problems": ["GRAFIS model (MDL) not set"]})
    finally:
        QMessageBox.exec = monkey_exec
    assert shown and "GRAFIS model (MDL) not set" in shown[0]                    # the result is said, not hidden
    win.toggle_theme()                                                             # rebuild keeps working
    win.close()
    app.processEvents()
