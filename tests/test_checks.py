"""The owner's measurement checks per customer / product and pattern: from … to, no check, or a formula."""
import os

import pytest
from conftest import XML_53, read

from level7mtm.services import customers
from level7mtm.services.checks import ALL, get_checks, set_checks


def _xml(fl="157.00", ref="From level7"):
    return read(XML_53).replace(b'Abbreviation="FL" Value="157.00"', f'Abbreviation="FL" Value="{fl}"'.encode()) \
        .replace(b'Reference="From level7"', f'Reference="{ref}"'.encode())


def _first(ws, out):
    return ws.revision(out.lines[0]["revision_id"])


def test_range_none_and_formula(ws):
    assert _first(ws, ws.import_data(_xml("83.00"), "a.xml"))["status"] == "needs_review"   # 100–200 (mapping)
    assert set_checks(ws, "level7", ALL, {"FL": {"mode": "range", "min": "80", "max": ""}})
    assert get_checks(ws, "level7")[ALL]["FL"] == {"mode": "range", "min": 80.0, "max": None}
    r = _first(ws, ws.import_data(_xml("83.00").replace(b"9-53-", b"9-60-"), "b.xml"))
    assert not any(p["code"] == "measure.range" for p in r["normalized"]["problems"])        # 80–(open)
    r = _first(ws, ws.import_data(_xml("70.00").replace(b"9-53-", b"9-61-"), "c.xml"))
    assert any(p["code"] == "measure.range" and "80–" in p["message"] for p in r["normalized"]["problems"])
    set_checks(ws, "level7", ALL, {"FL": {"mode": "none"}})
    r = _first(ws, ws.import_data(_xml("10.00").replace(b"9-53-", b"9-62-"), "d.xml"))
    assert not any(p["code"] == "measure.range" for p in r["normalized"]["problems"])
    # outside 100–200: computed from another measurement (SH = 49.5 cm)
    set_checks(ws, "level7", ALL, {"FL": {"mode": "formula", "min": 100, "max": 200, "formula": "SH * 3"}})
    r = _first(ws, ws.import_data(_xml("83.00").replace(b"9-53-", b"9-63-"), "e.xml"))
    assert r["normalized"]["measurements"]["FL"]["value_mm"] == pytest.approx(1485.0)
    assert any(p["code"] == "measure.formula" and p["severity"] == "info" for p in r["normalized"]["problems"])
    r = _first(ws, ws.import_data(_xml("157.00").replace(b"9-53-", b"9-64-"), "f.xml"))
    assert r["normalized"]["measurements"]["FL"]["value_mm"] == pytest.approx(1570.0)       # inside: kept
    with pytest.raises(Exception):
        set_checks(ws, "level7", ALL, {"FL": {"mode": "formula", "formula": "SH *"}})
    with pytest.raises(Exception):
        set_checks(ws, "level7", ALL, {"FL": {"mode": "range", "min": 5, "max": 1}})


def test_a_missing_measurement_from_a_formula_and_per_pattern(ws):
    """SW not in the XML: computed (e.g. from WB); a pattern's check comes over the one of all patterns."""
    cid = customers.add_customer(ws, "Richy", "From Richy")
    customers.set_patterns(ws, [{"variation": "Athlete1", "mdl": "a.mdl"}, {"variation": "Big", "mdl": "b.mdl"}],
                           cid)
    set_checks(ws, cid, ALL, {"SW": {"mode": "formula", "formula": "WB * 2"}})
    set_checks(ws, cid, "Athlete1", {"SW": {"mode": "formula", "formula": "WB + 1"}})
    r = _first(ws, ws.import_data(_xml(ref="From Richy"), "g.xml"))      # the order has Variation Athlete1
    assert r["normalized"]["measurements"]["SW"]["value_mm"] == pytest.approx(60.0)          # WB 5 cm + 1
    assert get_checks(ws, "level7") == {}                                 # LEVEL7 keeps his own


def test_measurement_checks_window(ws):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    asked = []
    import level7mtm.ui.main_window as mw
    real, mw.reread_held_question = mw.reread_held_question, lambda *a: asked.append(a)
    try:
        win = MainWindow(ws)
        win.goto("Customers")
        cp = win.pages["Customers"]
        assert "Measurement checks" in cp.tile_buttons
        cp.open_editor("Measurement checks")
        app.processEvents()
        page = cp.editor_page
        row = next(i for i in range(page.t.rowCount()) if page.t.item(i, 0).text() == "FL")
        page.t.cellWidget(row, 2).setCurrentIndex(2)                      # Formula
        page.t.item(row, 5).setText("SH * 3")
        assert page.save() is True and asked
        assert get_checks(ws, "level7")[ALL]["FL"]["formula"] == "SH * 3"
        cp.editor_dialog.close()
        win.close()
        app.processEvents()
    finally:
        mw.reread_held_question = real


def test_a_measurement_from_a_program_of_the_xml(ws):
    """From Dubai: a sleeve type sends no SW but <Program VariationName="Cuff Individuel Hight" Value="76.2"/>:
    SW is computed from it when the XML has no SW."""
    from level7mtm.services.checks import program_name
    from level7mtm.services.customers import programs_seen
    assert program_name("Cuff Individuel Hight ") == "Cuff_Individuel_Hight"
    set_checks(ws, "level7", ALL, {"SW": {"mode": "formula", "formula": "Cuff_Individuel_Hight / 100"}})
    r = _first(ws, ws.import_data(_xml(), "p.xml"))                       # Program Value="675"
    assert r["normalized"]["measurements"]["SW"]["value_mm"] == pytest.approx(67.5)
    assert programs_seen(ws, "level7") == ["Cuff_Individuel_Hight"]


def test_old_markers_leave_the_cutting_folder_and_are_found(ws, tmp_path):
    """A nesting from before the cutting folder held cutting files only: its .l7n there is moved to the program's
    markers folder (the order still shows its marker); a marker moved by hand to a customer folder is found."""
    import json

    from level7mtm.services.auto_cut import find_marker, tidy_markers
    out = tmp_path / "cut"
    (out / "Richy").mkdir(parents=True)
    ws.db.set_setting("cutting.output", str(out))
    old = out / "FR20-841.l7n"
    old.write_bytes(b"zip")
    (out / "FR20-841.plt").write_bytes(b"plt")
    with ws.db.tx() as c:
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) VALUES "
                  "('s', 'x.scb', '2026-10-21T09:45:00', 'done', ?, ?, '')",
                  (json.dumps([str(out / "FR20-841.plt")]), json.dumps({"l7n": str(old)})))
    assert tidy_markers(ws) == 1
    info = json.loads(ws.db.one("SELECT info FROM cutting_jobs")["info"])
    assert info["l7n"] == os.path.join(ws.folder, "markers", "FR20-841.l7n") and os.path.isfile(info["l7n"])
    assert not old.exists() and (out / "FR20-841.plt").exists()            # the cutting file stays
    moved = out / "Richy" / "J-1.l7n"
    moved.write_bytes(b"zip")
    assert find_marker(ws, str(tmp_path / "gone" / "J-1.l7n")) == str(moved)
    assert find_marker(ws, str(tmp_path / "gone" / "none.l7n")) is None
