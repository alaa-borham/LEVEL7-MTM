"""Base model with production switched off (owner): the parts come only from the GRAFIS parts table."""
import os

import pytest
from conftest import MDL, XML_53, needs_mdl, read

from level7mtm.grafis.container import read_container
from level7mtm.grafis.parts import production
from level7mtm.grafis.xwriter import build_order_model
from level7mtm.services.gparts import model_parts, option_choices, parts_table, save_parts
from level7mtm.services.grafis_orders import import_and_prepare


@pytest.fixture
def base_off(tmp_path):
    p = tmp_path / "ALAA2026.mdl"
    p.write_bytes(build_order_model(read(MDL), {}, {})[0])          # every part: not produced
    assert not any(s for _, s in production(read_container(str(p))).values())
    return str(p)


@needs_mdl
def test_no_parts_table_and_no_production_blocks_before_grafis(ws, tmp_path, base_off):
    ws.db.set_setting("grafis.mdl", base_off)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    out = import_and_prepare(ws, XML_53)
    assert {g["status"] for g in out.grafis} == {"blocked"}
    assert any("GRAFIS parts table is empty" in p for p in out.grafis[0]["problems"])


@needs_mdl
def test_parts_table_switches_exactly_those_parts_on(ws, tmp_path, base_off):
    ws.db.set_setting("grafis.mdl", base_off)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    assert model_parts(base_off)[7] == ("front1", "O") and model_parts(base_off)[17][1] == "F"
    keys = [k for k, _ in option_choices(ws)]
    assert "*" in keys and "collar=classic" in keys
    ver = save_parts(ws, [{"option": "*", "part": 7, "material": "O", "copies": 1, "mirrored": 0},
                          {"option": "*", "part": 8, "material": "O", "copies": 1, "mirrored": 0},
                          {"option": "*", "part": 36, "material": "O", "copies": 1, "mirrored": 1},
                          {"option": "collar=classic", "part": 80, "material": "O", "copies": 1, "mirrored": 1},
                          {"option": "collar=square", "part": 78, "material": "O", "copies": 1, "mirrored": 0}],
                     base_off)
    assert ver and [r["name"] for r in parts_table(ws)][:3] == ["front1", "back1", "sleeve SH-101"]
    out = import_and_prepare(ws, XML_53)
    assert [g["status"] for g in out.grafis] == ["written", "written"], out.grafis[0]["problems"]
    sq, cl = (read_container(g["path"]) for g in out.grafis)          # 9-53-1: square, 9-53-2: classic
    on = lambda c: {n for n, (_, s) in production(c).items() if s}   # noqa: E731
    assert on(sq) == {7, 8, 36, 78} and on(cl) == {7, 8, 36, 80}
    with pytest.raises(ValueError, match="999"):
        save_parts(ws, [{"option": "*", "part": 999, "material": "O", "copies": 1, "mirrored": 0}], base_off)


@needs_mdl
def test_parts_page_round_trip(ws, base_off):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.db.set_setting("grafis.mdl", base_off)
    win = MainWindow(ws)
    page = win.pages["GRAFIS parts"]
    page.refresh()
    page.add_row({})
    w = [page.t.cellWidget(0, j) for j in range(5)]
    w[0].setCurrentIndex(w[0].findData("jabzour=splender"))
    w[1].setCurrentIndex(w[1].findData(17))
    assert w[2].currentText() == "F"                                  # the part's own material
    w[4].setValue(0)
    assert page.rows() == [{"option": "jabzour=splender", "part": 17, "material": "F", "copies": 1, "mirrored": 0}]
    save_parts(ws, page.rows(), base_off)
    page.refresh()
    assert page.rows()[0]["part"] == 17
    win.close()
    app.processEvents()
