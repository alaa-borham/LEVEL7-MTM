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
        win.nav.setCurrentRow(i)
        app.processEvents()
        assert win.stack.currentWidget() is win.pages[cls.title]
        if shots:
            os.makedirs(shots, exist_ok=True)
            win.grab().save(os.path.join(shots, f"{lang}_{i:02d}_{cls.__name__}.png"))
    prev = win.pages["Pattern preview"]
    assert prev.result is not None and len(prev.view.scene().items()) > 10
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
