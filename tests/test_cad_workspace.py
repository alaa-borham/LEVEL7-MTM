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
    with ws.db.tx() as c:                                   # the robot failed: said on the order, not hidden
        c.execute("INSERT INTO robot_jobs(sha256, path, at, status, message) VALUES ('h','x.mdl','9999','failed',"
                  "'GRAFIS did not show x within 60 s')")
    r = {r["rid"]: r for r in order_rows(ws)}[rev["id"]]
    assert r["stage"] == "err" and r["robot_failed"] and "within 60 s" in r["problems"][-1]
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
    assert not r["robot_failed"] and not r["problems"]                         # GRAFIS saved it after all
    assert r["stage"] == "cut" and r["l7n"] == str(l7n) and r["outputs"] == ["x.plt"]
    assert r["x_rows"] == [{"x": "X1", "value_mm": 1570}] and set(r["times"]) == {"new", "grafis", "nest", "cut"}
    with ws.db.tx() as c:
        c.execute("UPDATE grafis_orders SET archived=1")
    assert rev["id"] not in {r["rid"] for r in order_rows(ws)}               # cleaned from the list


def test_window_shows_the_selected_marker(ws, tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
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
    groups = {win.browser.tree.topLevelItem(i).data(0, 0x0101): win.browser.tree.topLevelItem(i)
              for i in range(win.browser.tree.topLevelItemCount())}
    assert list(groups) == ["work", "err", "cut"]                                 # three separate lists
    item = next(it for it in win.browser._order_items() if it.data(0, 0x0100) == rev["id"])
    assert item.parent() is groups["cut"] and groups["cut"].text(1) == "1"
    win.browser.tree.setCurrentItem(item)
    app.processEvents()
    assert win.sel_row["rid"] == rev["id"] and win.marker.view.marker["length"] == 300
    assert item.childCount() == 1 and item.child(0).text(1) == "2×"                 # front1 twice
    assert win.marker.hud_values["pieces"].text() == "2"
    win.resize(700, 600)                                                           # narrow window: icons only
    app.processEvents()
    assert win.ribbon.compact and all(b.toolButtonStyle() == Qt.ToolButtonIconOnly for b in win.ribbon.buttons.values())
    win.resize(1900, 900)
    app.processEvents()
    assert not win.ribbon.compact or win.ribbon.width() < win.ribbon.full_width
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
    scb = tmp_path / "SCB"
    scb.mkdir()
    ws.db.set_setting("grafis.watch.folder", str(scb))
    gp = win.pages["GRAFIS markers"]
    assert gp.running()                                  # automatic from the start: robot + MDL folder, no click
    gp.timer.stop()
    gp.mdl_watcher = None
    assert not gp.running()
    win._light_clicked("watch")                                                    # red light clicked: on again
    assert gp.running() and gp.watcher is not None and ws.db.get_setting("grafis.watch.enabled")
    gp.timer.stop()
    erp = tmp_path / "ERP"                                                          # owner's timer for ERP orders
    erp.mkdir()
    assert win.erp_clock.text() == "متوقف" or win.erp_clock.text() == "off"       # countdown: off
    win.set_watch_interval(45)
    win.start_watch(str(erp))
    assert win.timer.interval() == 45 * 1000 and win.watch_interval_s() == 45
    assert win.erp_spin.value() == 45 and win.pages["GRAFIS markers"].erp_every.value() == 45   # both in step
    win._tick()
    assert win.erp_clock.text() in ("00:45", "00:44")
    win._status_lights()
    assert "45" in win.sb_items["erp"].property("label")
    win.timer.stop()
    win.watcher = None
    win.toggle_theme()                                                             # rebuild keeps working
    win.close()
    app.processEvents()


def test_snap_to_corners_first_then_edges():
    from level7mtm.ui.cad import sharp_corners
    curve = [(0, 0), (50, 1), (100, 3), (150, 6), (200, 10), (200, 100), (0, 100)]   # gentle curve on top
    assert sharp_corners(curve) == [(0, 0), (200, 10), (200, 100), (0, 100)]          # steps of the curve are not


def test_curve_never_goes_round_a_corner():
    from level7mtm.ui.cad import contour_length, curve_between, nearest_on_contour, sharp_corners
    # a piece with a curved neck: the curve between two of its points is measured, never round a corner
    neck = [(0, 100)] + [(50 - 50 * __import__("math").cos(t / 10 * 3.14159 / 2) * 0 + t * 5, 100 - (t * 5) ** 2 / 50)
                         for t in range(1, 10)] + [(50, 50), (300, 50), (300, 200), (0, 200)]
    corners = sharp_corners(neck)
    sa = nearest_on_contour(neck, neck[2])[1]
    sb = nearest_on_contour(neck, neck[7])[1]
    length, way = curve_between(neck, corners, sa, sb)
    assert abs(length - (sb - sa)) < 1e-6                            # along the neck curve
    far = nearest_on_contour(neck, (300, 120))[1]                    # on the other side of a corner
    assert curve_between(neck, corners, sa, far) is None
    assert contour_length(neck) > 0


def test_measuring_geometry():
    from level7mtm.ui.cad import arc_between, contour_length, nearest_on_contour
    sq = [(0, 0), (100, 0), (100, 50), (0, 50)]
    assert contour_length(sq) == 300
    d, s, c = nearest_on_contour(sq, (40, -3))
    assert d == 3 and s == 40 and c == (40, 0)
    assert arc_between(sq, 0, 100)[0] == 100                       # along the long edge
    length, way = arc_between(sq, 0, 250)                           # the shorter way round: 50 mm
    assert length == 50 and way[0] == (0, 50) and way[-1] == (0, 0)


def test_measure_tools_on_the_marker(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.cad import MarkerPanel, load_marker
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    p = tmp_path / "m.l7n"
    _l7n(p)
    panel = MarkerPanel()
    panel.resize(900, 600)
    panel.show()
    panel.set_marker(load_marker(str(p)))
    app.processEvents()
    v = panel.view
    panel.use_tool("line")
    v.zoom(6)                                                        # about 2 px per mm, as on a real marker
    px = 1 / v.transform().m11()
    assert v.snap((5 * px, 4 * px)) == ((0.0, 0.0), "corner")            # near the corner: on it
    assert v.snap((50, 3 * px))[1] == "edge" and v.snap((50, 3 * px))[0][1] == 0   # near a line: on the line
    assert v.snap((50, 25))[1] is None                                  # in the middle: where it is
    v.measure_click((0.3, 0.2))                                      # snaps to the corner (0, 0)
    v.measure_click((100, 50))
    assert v.meas["text"] == "11.2 cm" and panel.hud_measure[0].isVisible()      # in centimetres
    assert v.meas["extra"] == ""                                     # no ΔX / ΔY
    panel.use_tool("curve")
    v.measure_click((30, 0))                                         # on the edge of front1 (first copy)
    v.measure_click((100, 20))                                       # past the corner (100, 0): not one curve
    assert v.meas["text"] in ("Not one curve", "ليست منحنى واحدًا") and "value" not in v.meas
    v.measure_click((30, 0))
    v.measure_click((80, 0))                                         # same edge: 5 cm along it
    assert v.meas["text"] == "5.0 cm" and v.meas["extra"].startswith("front1")
    app.processEvents()
    v.viewport().repaint()
    assert v.box.isVisible() and "5.0 cm" in v.box.value.text()
    v.box.move(5, 300)                                               # the owner drags the box away …
    v.box.moved = True
    v.measure_click((30, 0))
    v.measure_click((80, 0))
    v.viewport().repaint()
    assert v.box.pos().y() == 300                                    # … and it stays where he put it
    k = v.transform().m11()
    v.zoom_at(1.35, v.viewport().rect().center())                    # space + right click: zoom in
    assert abs(v.transform().m11() - k * 1.35) < 1e-9
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    at = QPointF(v.mapFromScene(50, 1))                              # near front1's edge: the magnifier
    app.sendEvent(v.viewport(), QMouseEvent(QEvent.MouseMove, at, v.viewport().mapToGlobal(at), Qt.NoButton,
                                            Qt.NoButton, Qt.NoModifier))
    assert v._near is not None and v._near[0] < 2
    v.viewport().repaint()
    k = v.transform().m11()
    QTest.keyPress(v, Qt.Key_Space)                                  # space held: zoom mode
    assert v.zooming
    QTest.mouseClick(v.viewport(), Qt.LeftButton, pos=v.viewport().rect().center())   # left = zoom out
    assert v.transform().m11() < k and not v.meas.get("pts") == [(0, 0)]
    QTest.mouseClick(v.viewport(), Qt.RightButton, pos=v.viewport().rect().center())  # right = zoom in
    assert abs(v.transform().m11() - k) < 1e-6
    QTest.keyRelease(v, Qt.Key_Space)
    assert not v.zooming
    # the space bar while a button has the keyboard focus (the owner: "zoom with the space bar still does
    # not work"): with the mouse over the marker it zooms and never presses that button
    btn = QtWidgets.QPushButton("x", panel)
    pressed = []
    btn.clicked.connect(lambda: pressed.append(1))
    btn.show()
    btn.setFocus()
    v._mouse_over = lambda: True
    QTest.keyPress(btn, Qt.Key_Space)
    assert v.zooming
    QTest.keyRelease(btn, Qt.Key_Space)
    assert not v.zooming and not pressed
    panel.use_tool("select")
    assert v.tool is None and not v.meas
    panel.close()
