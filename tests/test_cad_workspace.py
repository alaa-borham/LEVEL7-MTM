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
    win.current_rid = rev["id"]                                                    # pattern preview: the
    win.goto("Pattern preview")                                                    # GRAFIS pieces, not the
    pv = win.pages["Pattern preview"]                                              # engine's test model
    app.processEvents()
    assert pv.sheet.isVisibleTo(pv) and not pv.engine.isVisibleTo(pv) and pv.sheet.view.marker["sheet"]
    win.tabs.setCurrentIndex(0)
    assert item.childCount() == 0                                              # no pieces listed under the order
    assert win.marker.hud_values["pieces"].text() == "2"
    from level7mtm.services.activity import working                               # a step running on the line
    with working([rev["id"]], "nesting", expected_s=100):
        win._tick()
        bar = win.browser._bars[rev["id"]]
        assert bar[0].parent() is item and bar[1].maximum() == 100 and "%" in bar[2].text()
    win._tick()
    assert rev["id"] not in win.browser._bars and item.childCount() == 0           # gone when it is done
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


def test_renest_reason_is_never_empty():
    from types import SimpleNamespace
    from level7mtm.ui.main_window import renest_why
    assert renest_why(SimpleNamespace(info={"waiting": True}, issues=[]))
    assert renest_why(SimpleNamespace(info={}, issues=[]))
    assert "x" in renest_why(SimpleNamespace(info={}, issues=[("info", "x")]))


def test_measurements_added_together(tmp_path):
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
    panel.tool_buttons["sum"].setChecked(True)                      # add up: the length tool comes on
    assert v.adding and v.tool == "line"
    v.zoom(6)
    v.measure_click((0, 0))
    v.measure_click((100, 0))                                       # 10 cm
    panel.use_tool("curve")                                         # a curve too: the total stays
    v.measure_click((30, 0))
    v.measure_click((80, 0))                                        # 5 cm along the edge
    assert len(v.parts) == 2 and abs(v.total() - 150) < 1e-6
    assert panel.hud_measure[0].text().strip("⁦⁩") == "Σ 15.0 cm"
    v.viewport().repaint()
    assert v.box.isVisible() and "15.0" in v.box.value.text() and "10.0 + 5.0" in v.box.extra.text()
    v.undo_part()                                                   # Backspace: the last part out
    assert abs(v.total() - 100) < 1e-6
    panel.tool_buttons["sum"].setChecked(False)
    assert not v.adding and v.parts == []


def test_pan_tool_moves_the_whole_marker_view(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
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
    panel.use_tool("pan")                                            # whole marker shown: it still moves
    before = v.mapToScene(v.viewport().rect().center())
    vp = v.viewport()
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, QPoint(400, 300))
    QTest.mouseMove(vp, QPoint(300, 250))
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, QPoint(300, 250))
    after = v.mapToScene(v.viewport().rect().center())
    assert after.x() > before.x() and after.y() > before.y()


def test_select_tool_picks_a_piece(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
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
    pc = v.marker["pieces"][0]
    xs, ys = [x for x, _ in pc["cut"]], [y for _, y in pc["cut"]]
    at = v.mapFromScene((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    v.viewport().repaint()
    assert v.selected is not None and v.box.isVisible() and "cm" in v.box.extra.text()
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, v.mapFromScene(-120, -120))   # off the pieces
    assert v.selected is None and not v.box.isVisible()


def test_measurement_dialog_in_cm_with_western_digits():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QTimer
    from level7mtm.ui.main_window import measurement_dialog
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    seen = {}

    def answer():
        d = app.activeModalWidget()
        box = d.findChild(QtWidgets.QDoubleSpinBox)
        seen["text"] = box.text()
        box.setValue(61.5)
        [e for e in d.findChildren(QtWidgets.QLineEdit) if e.parent() is d][0].setText("owner")   # the reason
        d.accept()
    QTimer.singleShot(50, answer)
    assert measurement_dialog(None, "CH", "Chest", 600.0) == (615.0, "owner")
    assert seen["text"] == "60.0 cm"


def test_tool_shortcuts_over_the_marker(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from level7mtm.ui.cad import MarkerPanel, MarkerView, load_marker
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    p = tmp_path / "m.l7n"
    _l7n(p)
    panel = MarkerPanel()
    panel.resize(900, 600)
    panel.show()
    panel.set_marker(load_marker(str(p)))
    app.processEvents()
    v = panel.view
    monkeypatch.setattr(MarkerView, "_mouse_over", lambda self: True)

    def press(key, vk=0):
        e = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, 0, vk, 0, "")
        return v.eventFilter(v, e)
    assert press(Qt.Key_L) and v.tool == "line"
    assert press(0x0634, 0x43) and v.tool == "curve"            # Arabic layout: the C key gives «ش»
    assert press(Qt.Key_S) and v.adding
    k = v.transform().m11()
    assert press(Qt.Key_Plus) and v.transform().m11() > k
    assert press(Qt.Key_H) and v.tool is None and v.dragMode() == v.DragMode.ScrollHandDrag
    assert press(Qt.Key_V) and v.dragMode() == v.DragMode.NoDrag
    assert not press(Qt.Key_Q)                                   # not a tool key: left alone
    assert "[L]" in panel.tool_buttons["line"].toolTip()


def test_problem_messages_follow_the_language():
    from level7mtm.ui.i18n import set_language, tr_msg
    msgs = ["order line needs review (import problems) — resolve them first",
            "design option 'pocket_chest' not set",
            "R003: measurement DSR not in the order — X4 would keep the GRAFIS placeholder",
            "nesting did not produce a valid marker (see log)"]
    set_language("ar")
    try:
        ar = [tr_msg(m) for m in msgs]
        assert ar[0] == "سطر الطلب يحتاج مراجعة (مشاكل في الاستيراد) — عالجها أولاً"
        assert ar[1] == "خيار التصميم 'pocket_chest' غير محدد"
        assert ar[2] == "R003: القياس DSR غير موجود في الطلب — X4 سيبقى على قيمة موديل GRAFIS الأصلية"
        assert ar[3].startswith("التعشيق")
        assert tr_msg("something new nobody translated") == "something new nobody translated"
    finally:
        set_language("en")
    assert [tr_msg(m) for m in msgs] == msgs                       # English: as it is


def test_pattern_sheet_one_of_each_piece_side_by_side(tmp_path):
    from level7mtm.ui.cad import load_marker, pattern_sheet
    p = tmp_path / "m.l7n"
    _l7n(p)
    m = load_marker(str(p))
    sh = pattern_sheet(m)
    names = [pc["name"] for pc in m["pieces"]]
    assert sh["sheet"] and [pc["name"] for pc in sh["pieces"]] == list(dict.fromkeys(names))
    import math
    from PySide6.QtCore import QRectF
    boxes = [QRectF(min(q[0] for q in pc["cut"]), min(q[1] for q in pc["cut"]),
                    max(q[0] for q in pc["cut"]) - min(q[0] for q in pc["cut"]),
                    max(q[1] for q in pc["cut"]) - min(q[1] for q in pc["cut"])) for pc in sh["pieces"]]
    assert not any(a.intersects(b) for i, a in enumerate(boxes) for b in boxes[i + 1:])     # never on each other
    for pc in sh["pieces"]:
        if len(pc["grain"]) >= 2:                                         # standing: grain line vertical
            (x1, y1), (x2, y2) = pc["grain"][0], pc["grain"][-1]
            assert abs(x2 - x1) < 1e-6 * max(1, abs(y2 - y1)) + 1e-6
    assert pattern_sheet(None) is None


def test_language_switch_keeps_folder_state_and_menus(ws):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QMenu
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    win.show()
    app.processEvents()
    g = win.pages["GRAFIS markers"]
    state = g.mdl_watcher.state if g.mdl_watcher else None
    menus = len(win.menuBar().findChildren(QMenu))
    for lang in ("en", "ar", "en"):
        win.set_lang(lang)
        app.processEvents()
    from PySide6.QtCore import QEvent
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)   # deleteLater of the old menus
    app.processEvents()
    g2 = win.pages["GRAFIS markers"]
    assert g2 is not g
    if state is not None:
        assert g2.mdl_watcher.state is state                       # files already handled stay handled
    assert len(win.menuBar().findChildren(QMenu)) <= menus         # the old menus are gone


def test_pieces_button_is_a_drop_down_of_both_pages(ws):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    win.show()
    app.processEvents()
    b = win.ribbon.buttons["options"]
    acts = b.menu().actions()
    assert len(acts) == 3 and "parts" not in win.ribbon.buttons and "xvalues" not in win.ribbon.buttons
    acts[2].trigger()                                             # Fixed pieces: with the customer
    assert win.current_page().title == "Customers" and win.pages["Customers"].inner.currentIndex() == 3


def test_nest_again_button_with_level7_nesting_in_its_arrow(ws):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    win.show()
    app.processEvents()
    b = win.ribbon.buttons["renest"]
    assert b.popupMode() == QtWidgets.QToolButton.InstantPopup and "nesting" not in win.ribbon.buttons
    assert len(b.menu().actions()) == 2
    f = win.ribbon.buttons["cut"]                                 # cutting files and ER under "Files"
    assert len(f.menu().actions()) == 2 and "er" not in win.ribbon.buttons


def test_owner_arranges_the_ribbon(ws):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow, ordered_ribbon
    groups = [("a", "A", [("x", "", "X", None), ("y", "", "Y", None)]), ("b", "B", [("z", "", "Z", None)])]
    out = ordered_ribbon(groups, {"groups": ["b", "a"], "items": {"a": ["y", "x"]}, "hidden": ["z"]})
    assert [(g, [i[0] for i in its]) for g, _t, its in out] == [("a", ["y", "x"])]   # b only had z: left out
    assert [g for g, _t, _i in ordered_ribbon(groups, {"groups": ["b"]})] == ["b", "a"]   # unknown after known
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.db.set_setting("ui.ribbon", {"groups": ["system"], "hidden": ["robot"]})
    win = MainWindow(ws)
    win.show()
    app.processEvents()
    assert "robot" not in win.ribbon.buttons and "setup" in win.ribbon.buttons
    assert "home" not in win.ribbon.buttons                       # "Today" opens from the L7 mark
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    QTest.mouseClick(win.ribbon.brand_mark, Qt.LeftButton)
    assert win.current_page().title == "Home"
