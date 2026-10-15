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
