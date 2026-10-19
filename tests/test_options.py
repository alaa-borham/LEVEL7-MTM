"""Design option groups entered by the owner: group → values → name in the XML (Variation Name)."""
import os

import pytest
from conftest import XML_53, read

from level7mtm.services.options import option_table, save_options


def _xml_with(variation: str | None) -> bytes:
    txt = read(XML_53).decode("utf-8")
    if variation:
        txt = txt.replace('<Variation Name="Athlete1"/>', f'<Variation Name="Athlete1"/><Variation Name="{variation}"/>', 1)
    return txt.encode("utf-8")


def test_option_table_reads_groups_values_and_xml_names(ws):
    t = {g["group"]: g for g in option_table(ws)}
    assert set(t) >= {"collar", "sleeve", "body_shape", "jabzour", "pocket_chest", "pocket_side"}
    col = {v["value"]: v for v in t["collar"]["values"]}
    assert col["square"]["erp_names"] == ["Collar Square"] and col["classic"]["erp_names"] == ["gallab Classic"]


def test_add_group_and_value_then_import(ws):
    groups = option_table(ws)
    groups.append({"group": "neck_button", "label": {"ar": "زر الرقبة", "en": "Neck button"}, "required": False,
                   "default": "", "values": [
                       {"value": "hidden", "label": {"ar": "مخفي", "en": "Hidden"}, "erp_names": ["Neck Button Hidden"]},
                       {"value": "visible", "label": {"ar": "ظاهر", "en": "Visible"}, "erp_names": ["Neck Button Visible"]}]})
    col = next(g for g in groups if g["group"] == "collar")
    col["values"].append({"value": "royal", "label": {"ar": "رويال", "en": "Royal"}, "erp_names": ["Collar Royal"]})
    out = save_options(ws, groups)
    assert out["catalog_version"] and out["mapping_version"]
    o = ws.import_data(_xml_with("Neck Button Hidden"), "Order-9-53-1_2.xml")
    rev = ws.revision(o.lines[0]["revision_id"])
    assert rev["normalized"]["options"]["neck_button"] == "hidden" and rev["status"] == "ready"
    # not required, no default: an order without it is fine; with a default it is filled in (info, not silent)
    _, _, res = ws.resolve_order(o.lines[1]["revision_id"])
    assert "neck_button" not in res.options and not any(i.code == "option.missing" for i in res.issues)
    groups = option_table(ws)
    nb = next(g for g in groups if g["group"] == "neck_button")
    nb["default"] = "visible"
    save_options(ws, groups)
    _, _, res = ws.resolve_order(o.lines[1]["revision_id"])
    assert res.options["neck_button"] == "visible" and any(i.code == "option.default" for i in res.issues)
    # required without default: blocking
    groups = option_table(ws)
    nb = next(g for g in groups if g["group"] == "neck_button")
    nb["default"], nb["required"] = "", True
    save_options(ws, groups)
    _, _, res = ws.resolve_order(o.lines[1]["revision_id"])
    assert any(i.code == "option.missing" and i.severity == "blocking" for i in res.issues)


def test_invalid_or_used_options_are_refused(ws):
    groups = option_table(ws)
    dup = [dict(g) for g in groups]
    dup[0] = dict(dup[0], values=dup[0]["values"] + [{"value": "x", "label": {}, "erp_names": ["Collar Square"]}])
    with pytest.raises(ValueError, match="used twice"):
        save_options(ws, dup)
    pk, cat = ws.active("catalog")
    import json
    cat = json.loads(json.dumps(cat))
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    st["grafis_parts"] = [{"option": "collar=classic", "part": 80, "name": "", "material": "O", "copies": 1,
                           "mirrored": 1}]
    cat["version"] += ".9"
    ws.set_active("catalog", ws.save_document("catalog", cat))
    groups = option_table(ws)
    col = next(g for g in groups if g["group"] == "collar")
    col["values"] = [v for v in col["values"] if v["value"] != "classic"]
    with pytest.raises(ValueError, match="GRAFIS part 80"):
        save_options(ws, groups)


def test_options_page_round_trip(ws):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from level7mtm.ui.i18n import set_language
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    set_language("ar")
    win = MainWindow(ws)
    page = win.pages["Design options"]
    page.refresh()
    groups = page.collect()
    row = next(i for i, g in enumerate(groups) if g["group"] == "sleeve")
    page.g.setCurrentCell(row, 0)
    page.v.insertRow(page.v.rowCount())
    for j, x in enumerate(["kabak_round", "كبك دائري", "Round cuff", "Sleeve Kabak Round"]):
        page.v.setItem(page.v.rowCount() - 1, j, QtWidgets.QTableWidgetItem(x))
    g = next(g for g in page.collect() if g["group"] == "sleeve")
    assert g["values"][-1] == {"value": "kabak_round", "label": {"ar": "كبك دائري", "en": "Round cuff"},
                               "erp_names": ["Sleeve Kabak Round"]}
    save_options(ws, page.collect())
    t = {x["group"]: x for x in option_table(ws)}
    assert "kabak_round" in [v["value"] for v in t["sleeve"]["values"]]
    win.close()
    app.processEvents()


def test_group_opens_in_its_own_window_save_and_close_or_cancel(ws, monkeypatch):
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("level7mtm.ui.main_window.reread_held_question", lambda *a: None)
    win = MainWindow(ws)
    page = win.pages["Design options"]
    page.refresh()
    row = next(i for i, g in enumerate(page.groups) if g["group"] == "sleeve")
    page.g.setCurrentCell(row, 0)
    page.g.cellClicked.emit(row, 0)                                       # a click opens the group's window
    assert page.gd.isVisible() and page.gf["group"].text() == "sleeve"
    page.v.item(0, 3).setText("Sleeve Saada; Sleeve Long")
    page.gf["ar"].setText("الكم الجديد")
    page._group_save_close()
    assert not page.gd.isVisible()
    t = {x["group"]: x for x in option_table(ws)}
    assert t["sleeve"]["label"]["ar"] == "الكم الجديد" and "Sleeve Long" in t["sleeve"]["values"][0]["erp_names"]
    ver = ws.active("catalog")[1]["version"]
    assert page.save() and ws.active("catalog")[1]["version"] == ver     # nothing changed: no new version
    page.g.cellClicked.emit(row, 0)
    page.gf["ar"].setText("x")
    page.gd.reject()                                                      # Cancel: unchanged
    assert next(g for g in page.groups if g["group"] == "sleeve")["label"]["ar"] == "الكم الجديد"
    n = len(page.groups)
    page.add_group()
    assert len(page.groups) == n + 1 and page.gd.isVisible()
    page.gd.reject()                                                      # a new group cancelled is gone
    assert len(page.groups) == n
    win.close()
    app.processEvents()
