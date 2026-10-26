"""Offscreen GUI smoke test: every screen opens, an order is imported, calculated and shown, nothing throws.
Screenshots are written when LEVEL7MTM_SHOTS is set (used for the documentation)."""
import os

import pytest
from conftest import XML_53

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def _wait(app, pool):
    while pool.activeThreadCount():
        app.processEvents()
        pool.waitForDone(50)
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("lang", ["ar", "en"])
def test_all_screens(ws, lang):
    from PySide6.QtCore import Qt
    from level7mtm.ui.i18n import set_language
    from level7mtm.ui.main_window import PAGES, MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    set_language(lang)
    app.setLayoutDirection(Qt.RightToLeft if lang == "ar" else Qt.LeftToRight)
    win = MainWindow(ws)
    win.show()
    errors = []
    win.guard = lambda fn: (fn() if True else None)            # surface exceptions to the test
    win.import_paths([XML_53])
    _wait(app, win.pool)
    rid = next(r["id"] for r in ws.list_revisions() if r["line_number"] == "9-53-1_2")
    win.current_rid = rid
    win.run(lambda: ws.generate(rid), lambda g: None)
    _wait(app, win.pool)
    win.set_role(True)
    shots = os.environ.get("LEVEL7MTM_SHOTS")
    for i, cls in enumerate(PAGES):
        win.goto(cls.title)
        app.processEvents()
        if cls.title in ("X values for GRAFIS", "Design options", "GRAFIS parts"):     # set up with the customer
            assert win.current_page() is win.pages["Customers"]
        else:
            assert win.current_page() is win.pages[cls.title]
        if shots:
            os.makedirs(shots, exist_ok=True)
            win.grab().save(os.path.join(shots, f"{lang}_{i:02d}_{cls.__name__}.png"))
    win.current_rid = rid                                      # the list may have chosen another line meanwhile
    win.goto("Pattern preview")
    app.processEvents()
    prev = win.pages["Pattern preview"]
    assert prev.result is not None and len(prev.view.scene().items()) > 10
    win.current_rid = rid
    win.goto("Validation")
    app.processEvents()
    val = win.pages["Validation"]
    assert not val.b_ok.isEnabled()                            # engine-test model: approval impossible
    assert val.block.count() > 0
    win.close()
    assert not errors


def test_grafis_parts_tab_round_trip(ws):
    """The owner enters option → GRAFIS part rows in the Rules editor; they are saved into the catalog."""
    from conftest import MDL
    from level7mtm.ui.i18n import set_language
    from level7mtm.ui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    set_language("en")
    if os.path.exists(MDL):
        ws.db.set_setting("grafis.mdl", MDL)
    win = MainWindow(ws)
    page = win.pages["Rules editor"]
    page.refresh()
    t = page.gpt
    for row in (["*", "7", "", "O", "1", "0"], ["jabzour=splender", "24", "", "o", "1", "0"]):
        t.insertRow(t.rowCount())
        for c, v in enumerate(row):
            t.setItem(t.rowCount() - 1, c, QtWidgets.QTableWidgetItem(v))
    cat = page._collect()
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    gp = st["grafis_parts"]
    assert [(g["option"], g["part"], g["material"], g["copies"], g["mirrored"]) for g in gp] == \
        [("", 7, "O", 1, 0), ("jabzour=splender", 24, "O", 1, 0)]
    if os.path.exists(MDL):
        assert [g["name"] for g in gp] == ["front1", "jabzour splender FB"]      # filled in from the model
        t.setItem(1, 1, QtWidgets.QTableWidgetItem("999"))
        with pytest.raises(ValueError, match="999"):
            page._collect()
    from level7mtm.services.catalog import load_catalog
    load_catalog(cat)                                      # the catalog check accepts the rows
    win.close()
    app.processEvents()


def test_x_value_row_turns_active_when_a_code_is_typed(ws):
    from conftest import MDL
    if not os.path.exists(MDL):
        pytest.skip("customer MDL not in the checkout")
    from PySide6.QtCore import Qt
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.db.set_setting("grafis.mdl", MDL)
    win = MainWindow(ws)
    page = win.pages["X values for GRAFIS"]
    page.refresh()
    page.part.setCurrentIndex(page.part.findData(2))                       # one part (not "all main parts")
    row = next(i for i in range(page.t.rowCount()) if page.t.item(i, 0).text() == "X10")
    assert page.t.item(row, 5).data(Qt.CheckStateRole) is None              # inactive
    page.t.item(row, 3).setText("FW")
    assert page.t.item(row, 5).checkState() == Qt.Checked                   # active, confirmed
    assert next(r for r in page.rows() if r["x"] == 10) == {"part": 2, "x": 10, "codes": ["FW"],
                                                           "formula": "{M}", "confirmed": True}
    page.t.item(row, 3).setText("")
    assert page.t.item(row, 5).data(Qt.CheckStateRole) is None              # inactive again
    win.close()
    app.processEvents()


def test_size_table_offers_to_move_codes_of_a_part_the_model_lacks(ws, monkeypatch):
    from conftest import MDL
    if not os.path.exists(MDL):
        pytest.skip("customer MDL not in the checkout")
    import copy

    from PySide6.QtWidgets import QMessageBox
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.db.set_setting("grafis.mdl", MDL)
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    for r in st["rules"]:
        if r["target"]["piece"] == "002":
            r["target"]["piece"] = "007"
    cat["version"] = "9.0.0"
    ws.set_active("catalog", ws.save_document("catalog", cat, "test"))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    win = MainWindow(ws)
    page = win.pages["X values for GRAFIS"]
    page.refresh()
    assert not page.carry.isHidden() and page.carry_from.currentData() == "007"
    page.carry_to.setCurrentIndex(page.carry_to.findData(2))
    page.carry_codes()
    assert page.carry.isHidden()
    assert any(page.t.item(i, 3).text() for i in range(page.t.rowCount()))
    win.close()
    app.processEvents()


def test_chosen_cutting_folder_and_moving_existing_files(ws, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    page = win.pages["GRAFIS markers"]
    old, new = tmp_path / "old", tmp_path / "mine"
    old.mkdir()
    (old / "Order-1.plt").write_text("x")
    (old / "Order-1.l7n").write_text("x")
    ws.db.set_setting("cutting.output", str(old))
    import json
    with ws.db.tx() as c:
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) VALUES "
                  "('s','p','t','done',?,?,'')", (json.dumps([str(old / "Order-1.plt")]),
                                                  json.dumps({"l7n": str(old / "Order-1.l7n")})))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(new))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    page.choose_out()
    assert ws.db.get_setting("cutting.output") == str(new) and ws.db.get_setting("cutting.output_chosen")
    assert (new / "Order-1.plt").exists() and not (old / "Order-1.plt").exists()
    job = ws.db.one("SELECT outputs, info FROM cutting_jobs")
    assert json.loads(job["info"])["l7n"] == str(new / "Order-1.l7n")
    assert json.loads(job["outputs"]) == [str(new / "Order-1.plt")]
    # the folders button keeps the chosen cutting folder
    base = tmp_path / "base"
    base.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(base))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    page.choose_folders()
    assert ws.db.get_setting("cutting.output") == str(new) and not (base / "CUT").exists()
    page.timer.stop()
    win.close()
    app.processEvents()


def test_names_in_a_message_for_copying():
    from level7mtm.ui.main_window import quoted_names
    assert quoted_names("design code 'Small Size Fit' is not mapped to a model option") == ["Small Size Fit"]
    assert quoted_names("'A' and 'B ' and 'A'") == ["A", "B"] and quoted_names("nothing") == []


def test_a_message_row_opens_its_text_with_a_click(ws, monkeypatch):
    from PySide6.QtWidgets import QTableWidgetItem
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    t = win.pages["Order details"].probs
    shown = []
    t.show_text = lambda it: shown.append(it.text())
    t.setRowCount(1)
    t.setItem(0, 0, QTableWidgetItem("review"))
    t.setItem(0, 1, QTableWidgetItem("design code 'Small Size Fit' is not mapped to a model option"))
    t.itemClicked.emit(t.item(0, 0))                                    # a click on the row, any cell
    assert shown == ["design code 'Small Size Fit' is not mapped to a model option"]
    win.close()
    app.processEvents()


def test_size_table_and_options_go_out_to_excel_and_back(ws, tmp_path, monkeypatch):
    """Export to Excel, change the sheet, import it: the tables are filled (saved on Save and close)."""
    from conftest import MDL
    if not os.path.exists(MDL):
        pytest.skip("customer MDL not in the checkout")
    from PySide6.QtCore import Qt
    from level7mtm.services.sheets import read_rows, write_xlsx
    from level7mtm.ui import main_window as mw
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: None)
    ws.db.set_setting("grafis.mdl", MDL)
    win = mw.MainWindow(ws)
    page = win.pages["X values for GRAFIS"]
    page.refresh()
    page.part.setCurrentIndex(page.part.findData(2))
    out = str(tmp_path / "x.xlsx")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a, **k: (out, ""))
    page.export_excel()
    rows = read_rows(out)
    i = next(n for n, r in enumerate(rows) if r[1] == "10")
    rows[i][4] = "FW"
    write_xlsx(out, rows[0], rows[1:])
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", lambda *a, **k: (out, ""))
    page.import_excel()
    row = next(n for n in range(page.t.rowCount()) if page.t.item(n, 0).text() == "X10")
    assert page.t.item(row, 3).text() == "FW" and page.t.item(row, 5).checkState() == Qt.Checked
    opts = win.pages["Design options"]
    opts.refresh()
    o = str(tmp_path / "o.xlsx")
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName", lambda *a, **k: (o, ""))
    opts.export_excel()
    rows = read_rows(o)
    rows.append(["neck_button", "زر الرقبة", "Neck button", "0", "", "hidden", "مخفي", "Hidden",
                 "Neck Button Hidden", "", "", ""])
    write_xlsx(o, rows[0], rows[1:])
    monkeypatch.setattr(mw.QFileDialog, "getOpenFileName", lambda *a, **k: (o, ""))
    n = len(opts.groups)
    opts.import_excel()
    assert len(opts.groups) == n + 1 and opts.groups[-1]["values"][0]["erp_names"] == ["Neck Button Hidden"]
    monkeypatch.setattr(mw, "reread_held_question", lambda *a, **k: None)
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: pytest.fail(str(a[2])))
    assert opts.save() is True
    from level7mtm.services.options import option_table
    assert "neck_button" in {g["group"] for g in option_table(ws)}
    win.close()
    app.processEvents()


def test_a_stop_of_the_line_shows_in_red_and_the_report_opens(ws, monkeypatch):
    from datetime import datetime, timedelta

    from level7mtm.ui import main_window as mw
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = mw.MainWindow(ws)
    win.show()
    ws.db.set_setting("robot.enabled", True)
    win.chain_on = True
    win._chain_since = datetime.now() - timedelta(hours=1)
    monkeypatch.setattr(mw.QApplication, "beep", lambda: None)
    win._watch_line([{"stage": "grafis"}])
    app.processEvents()
    assert win.alert_bar.isVisible() and "AI TOOL" in win.alert_bar.text()
    assert ws.db.one("SELECT COUNT(*) AS n FROM audit WHERE action='alert.raised'")["n"] == 1
    win._watch_line([{"stage": "grafis"}])                        # the same stop: kept once
    assert ws.db.one("SELECT COUNT(*) AS n FROM audit WHERE action='alert.raised'")["n"] == 1
    win._snooze_alerts()
    win._watch_line([{"stage": "grafis"}])
    assert not win.alert_bar.isVisible()                          # hidden by the owner until something else
    win._watch_line([])
    assert not win.alert_bar.isVisible()
    monkeypatch.setattr(mw.QDialog, "exec", lambda self: 0)
    win.daily_report()
    win.close()
    app.processEvents()
