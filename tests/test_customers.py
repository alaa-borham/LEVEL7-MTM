"""Customers: each with his XML name (Reference), his own mapping / rules (size table, options, parts) and model."""
import os

import pytest

from level7mtm.services import customers
from level7mtm.services.workflow import DEFAULT_CUSTOMER, WorkflowError

HERE = os.path.dirname(__file__)


def _xml(path, ref):
    data = open(path, "rb").read()
    return data.replace(b'Reference="From level7"', f'Reference="{ref}"'.encode())


@pytest.fixture
def xml53():
    from tests.test_cad_workspace import XML_53
    return XML_53


def test_first_customer_from_the_existing_settings(ws):
    cs = customers.list_customers(ws)
    assert [c["id"] for c in cs] == [DEFAULT_CUSTOMER] and cs[0]["reference"] == "From level7"


def test_xml_of_an_unknown_customer_is_refused_with_the_reason(ws, xml53):
    out = ws.import_data(_xml(xml53, "From Richy"), "Order-richy.xml")
    assert out.status == "failed" and "From Richy" in out.message and "Customers" in out.message


def test_new_customer_copy_reads_his_xml_and_has_his_own_rules(ws, xml53, tmp_path):
    cid = customers.add_customer(ws, "Richy", "From Richy", copy_from=DEFAULT_CUSTOMER, mdl=str(tmp_path / "r.mdl"))
    assert customers.get(ws, cid)["reference"] == "From Richy"
    out = ws.import_data(_xml(xml53, "From Richy"), "Order-richy.xml")
    assert out.status == "imported", out.message
    rid = out.lines[0]["revision_id"]
    assert ws.customer_of(rid) == cid
    with ws.for_customer(cid):
        pk_r, cat_r = ws.active("catalog")
        assert ws.grafis_mdl() == str(tmp_path / "r.mdl")
    with ws.for_customer(DEFAULT_CUSTOMER):
        pk_l, cat_l = ws.active("catalog")
    assert pk_r != pk_l and cat_r["catalog_id"].endswith("@" + cid)
    assert ws.resolve_order(rid)[0] == pk_r                       # the order is read with Richy's rules
    with ws.for_customer(cid):                                    # a change for Richy …
        import copy
        cat = copy.deepcopy(cat_r)
        cat["version"] = "1.0.1"
        ws.set_active("catalog", ws.save_document("catalog", cat, "test"))
    with ws.for_customer(DEFAULT_CUSTOMER):                       # … leaves LEVEL7 as it is
        assert ws.active("catalog")[0] == pk_l
    ok = ws.import_data(open(xml53, "rb").read(), "Order-l7.xml")  # LEVEL7's XML, same line numbers:
    assert ok.status == "imported"                                # his own order lines, not Richy's
    l7 = ok.lines[0]["revision_id"]
    assert l7 != rid and ws.customer_of(l7) == DEFAULT_CUSTOMER
    with pytest.raises(WorkflowError):
        customers.add_customer(ws, "Other", "From Richy")
    customers.update_customer(ws, cid, name="Richy Co", reference="From Richy2")
    c = customers.get(ws, cid)
    assert c["name"] == "Richy Co" and c["reference"] == "From Richy2"


def test_customers_page_and_selector(ws, xml53, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cid = customers.add_customer(ws, "Richy", "From Richy")
    out = ws.import_data(_xml(xml53, "From Richy"), "Order-richy.xml")
    win = MainWindow(ws)
    win.show()
    app.processEvents()
    assert [win.customer_box.itemText(i) for i in range(win.customer_box.count())] == ["LEVEL7", "Richy"]
    win.goto("Customers")
    page = win.pages["Customers"]
    assert page.t.rowCount() == 2 and page.t.item(1, 2).text() == "From Richy" and page.t.item(1, 3).text() == "—"
    win.customer_box.setCurrentIndex(1)
    win.customer_box.activated.emit(1)
    assert ws.customer() == cid                                   # the settings pages now edit Richy's
    from level7mtm.ui import cad
    row = next(r for r in cad.order_rows(ws) if r["rid"] == out.lines[0]["revision_id"])
    assert row["customer"] == "Richy"
    ws.db.set_setting("ui.customer", None)


def test_pattern_chosen_by_the_variation_of_the_order(ws, xml53, tmp_path):
    from level7mtm.services.customers import pattern_for, patterns, set_patterns
    out = ws.import_data(open(xml53, "rb").read(), "Order-9-53.xml")   # its Variations include Athlete1
    rid = out.lines[0]["revision_id"]
    ws.set_grafis_mdl(str(tmp_path / "base.mdl"))
    assert pattern_for(ws, rid) == (str(tmp_path / "base.mdl"), "")       # no patterns: the customer's model
    set_patterns(ws, [{"variation": "Slim2", "mdl": str(tmp_path / "slim.mdl")},
                      {"variation": "Athlete1", "mdl": str(tmp_path / "athlete.mdl")}])
    assert [p["variation"] for p in patterns(ws)] == ["Slim2", "Athlete1"]
    assert pattern_for(ws, rid) == (str(tmp_path / "athlete.mdl"), "")    # chosen by the order's Variation
    set_patterns(ws, [{"variation": "Slim2", "mdl": str(tmp_path / "slim.mdl")}])
    mdl, why = pattern_for(ws, rid)
    assert mdl is None and "no pattern" in why and "Slim2" in why and "the order has: " in why and "Athlete1" in why
    set_patterns(ws, [{"variation": "athlete_1", "mdl": str(tmp_path / "a.mdl")}])      # spaces / _ / case
    assert pattern_for(ws, rid)[0] == str(tmp_path / "a.mdl")                            # = "Athlete1"
    from level7mtm.services.customers import variations_seen
    assert "Athlete1" in variations_seen(ws)
    set_patterns(ws, [{"variation": "New Shape", "mdl": str(tmp_path / "n.mdl")}])     # read by the import
    assert "New Shape" in ws.active("mapping")[1]["options"]["map"]
    set_patterns(ws, [{"variation": "Slim2", "mdl": str(tmp_path / "slim.mdl")}])
    from level7mtm.services.grafis_orders import order_mdl
    r = order_mdl(ws, rid)
    assert r["status"] == "blocked" and "no pattern" in r["problems"][0]   # held, with the reason
    with pytest.raises(WorkflowError):
        set_patterns(ws, [{"variation": "Slim2", "mdl": ""}])


def test_learn_a_customer_xml_and_read_it_again(ws, xml53, tmp_path):
    from level7mtm.services.customers import learn_from_xml, reread_file
    cid = customers.add_customer(ws, "Richy", "From Richy")
    txt = _xml(xml53, "From Richy").decode("utf-8")
    txt = txt.replace('Number="9-53-1_2"', 'Number="GR-1963-1_2"').replace('Number="9-53-2_2"', 'Number="GR-1963-2_2"')
    txt = txt.replace("Saudi Thobe_TableMeasurement", "Thobe_Measurments_Full").replace('Abbreviation="SH"',
                                                                                          'Abbreviation="SHX"')
    p = tmp_path / "GR-1963.xml"
    p.write_text(txt, encoding="utf-8")
    first = ws.import_file(str(p))
    assert first.lines[0]["status"] == "needs_review"
    res = learn_from_xml(ws, cid, p.read_bytes())
    assert res["table"] == "Thobe_Measurments_Full" and res["line_pattern"] and "SHX" in res["added"]
    assert "SH" in res["not_required"]
    again = reread_file(ws, str(p))
    rev = ws.revision(again.lines[0]["revision_id"])
    msgs = [x["message"] for x in rev["normalized"]["problems"]]
    assert not any("expected pattern" in m or "profile expects" in m or "SHX" in m for m in msgs), msgs
    assert ws.customer_of(rev["id"]) == cid


def test_options_page_shows_the_customer_and_his_unlinked_variations(ws, xml53):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.customers import held_files, reread_held
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    cid = customers.add_customer(ws, "Richy", "From Richy")
    data = _xml(xml53, "From Richy").replace(b'Name="Sleeve Saada"', b'Name="Sada"')
    out = ws.import_data(data, "Order-richy.xml")
    assert out.lines[0]["status"] == "needs_review" and held_files(ws, cid)
    ws.db.set_setting("ui.customer", cid)
    win = MainWindow(ws)
    win.show()
    win.goto("Design options")                                        # opens with the customer, in one place
    app.processEvents()
    cp = win.pages["Customers"]
    assert win.current_page() is cp and cp.editor_key == "Design options" and cp.editor_dialog.isVisible()
    assert "Richy" in cp.head.text() and "Richy" in cp.editor_dialog.windowTitle()   # whose settings these are
    page = cp.editor_page
    names = [page.unlinked.item(i).text() for i in range(page.unlinked.count())]
    assert "Sada" in names and "Collar Square" not in names            # only the ones without a meaning
    # give "Sada" a meaning (sleeve = saada), then the held file is read again and passes that problem
    from level7mtm.services.options import option_table, save_options
    groups = option_table(ws)
    sleeve = next(g for g in groups if g["group"] == "sleeve")
    next(v for v in sleeve["values"] if v["value"] == "saada")["erp_names"].append("Sada")
    save_options(ws, groups)
    outs = reread_held(ws, cid)
    rev = ws.revision(outs[0].lines[0]["revision_id"])
    assert not any("'Sada'" in p["message"] for p in rev["normalized"]["problems"])
    ws.db.set_setting("ui.customer", None)


def test_x_values_of_all_main_parts_in_one_table(ws, tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from conftest import MDL
    if not os.path.isfile(MDL):
        pytest.skip("customer MDL not in the checkout")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.set_grafis_mdl(MDL)
    win = MainWindow(ws)
    win.show()
    win.goto("X values for GRAFIS")
    app.processEvents()
    page = win.pages["Customers"].editor_page
    assert page.part.currentData() == page.ALL                      # all main parts first
    parts = {page.t.item(i, 0).data(0x0100)[0] for i in range(page.t.rowCount())}
    assert len(parts) > 1 and page.t.item(0, 0).text().startswith(f"{min(parts):03d}/X")
    k = next(i for i in range(page.t.rowCount()) if page.t.item(i, 0).data(0x0100)[0] == max(parts))
    page.t.item(k, 3).setText("FL")                                 # an X of another part, saved with the rest
    monkeypatch.setattr("level7mtm.ui.main_window.reread_held_question", lambda *a: None)
    page.save()
    rules = ws.active("catalog")[1]["products"]
    st = next(iter(next(iter(rules.values()))["styles"].values()))
    assert any(r["target"]["piece"] == f"{max(parts):03d}" and r["source"].get("code") == "FL" for r in st["rules"])


def test_each_setting_opens_in_its_own_window_save_and_close(ws, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.customers import patterns
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("level7mtm.ui.main_window.reread_held_question", lambda *a: None)
    win = MainWindow(ws)
    win.show()
    win.goto("Customers")
    cp = win.pages["Customers"]
    cp.tile_buttons["patterns"].click()
    app.processEvents()
    d, page = cp.editor_dialog, cp.editor_page
    assert d.isVisible()
    page._add("Big Size", "C:/x/big.mdl")
    save = next(b for b in d.findChildren(QtWidgets.QPushButton) if b.text() in ("Save and close", "حفظ وإغلاق"))
    save.click()
    app.processEvents()
    assert not d.isVisible() and patterns(ws) == [{"variation": "Big Size", "mdl": "C:/x/big.mdl"}]
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    cp.tile_buttons["patterns"].click()                              # an error keeps the window open
    cp.editor_page._add("", "C:/x/only-model.mdl")
    next(b for b in cp.editor_dialog.findChildren(QtWidgets.QPushButton)
         if b.text() in ("Save and close", "حفظ وإغلاق")).click()
    app.processEvents()
    assert cp.editor_dialog.isVisible()
    cp.editor_dialog.reject()


def test_part_numbers_typed_next_to_an_option_value(ws, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from conftest import MDL
    if not os.path.isfile(MDL):
        pytest.skip("customer MDL not in the checkout")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.gparts import parts_table
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr("level7mtm.ui.main_window.reread_held_question", lambda *a: None)
    ws.set_grafis_mdl(MDL)
    win = MainWindow(ws)
    win.show()
    win.goto("Design options")
    page = win.pages["Customers"].editor_page
    gi = next(i for i, g in enumerate(page.groups) if g["group"] == "sleeve")
    page.g.setCurrentCell(gi, 0)
    app.processEvents()
    vi = next(i for i in range(page.v.rowCount()) if page.v.item(i, 0).text() == "saada")
    page.v.item(vi, 4).setText("36, 37")                              # the parts this Variation chooses
    page.v.item(vi, 5).setText("2")                                   # two of each, one of them mirrored
    page.v.item(vi, 6).setText("1")
    assert page.save()
    got = sorted((int(r["part"]), r["copies"], r["mirrored"]) for r in parts_table(ws) if r["option"] == "sleeve=saada")
    assert got == [(36, 2, 1), (37, 2, 1)]
    gi = next(i for i, g in enumerate(page.groups) if g["group"] == "sleeve")
    page.g.setCurrentCell(gi, 0)
    app.processEvents()
    vi = next(i for i in range(page.v.rowCount()) if page.v.item(i, 0).text() == "saada")
    assert page.v.item(vi, 4).text() == "36, 37"                        # the numbers only, as typed
    assert (page.v.item(vi, 5).text(), page.v.item(vi, 6).text()) == ("2", "1")


def test_customer_matched_by_reference_and_reseller(ws, xml53):
    data = _xml(xml53, "From Richy")                                  # Reseller="9" in the sample
    a = customers.add_customer(ws, "Richy A", "From Richy", reseller="10040")
    assert ws.import_data(data, "a.xml").status == "failed"             # same name, other reseller: refused
    b = customers.add_customer(ws, "Richy B", "From Richy", reseller="9")
    out = ws.import_data(data, "b.xml")
    assert out.status == "imported" and ws.customer_of(out.lines[0]["revision_id"]) == b
    with pytest.raises(WorkflowError):                                 # the same name and reseller twice
        customers.add_customer(ws, "Richy C", "From Richy", reseller="9")
    customers.update_customer(ws, a, reseller="")                       # empty: any reseller of that name
    assert customers.get(ws, a)["reseller"] == ""


def test_learn_takes_the_reseller_of_the_xml(ws, xml53):
    from level7mtm.services.customers import learn_from_xml
    cid = customers.add_customer(ws, "Richy", "From Richy")
    res = learn_from_xml(ws, cid, _xml(xml53, "From Richy"))
    assert res["reseller"] == "9" and customers.get(ws, cid)["reseller"] == "9"


def test_products_by_item_number_each_with_its_own_settings(ws, xml53, tmp_path):
    cid = customers.add_customer(ws, "Richy", "From Richy", reseller="9")
    men = customers.add_product(ws, cid, "Thobe men", "91001")             # the sample's Item Number
    kids = customers.add_product(ws, cid, "Thobe kids", "92002")
    assert [p["id"] for p in customers.products(ws, cid)] == [men, kids]
    assert customers.names(ws)[men] == "Richy / Thobe men"
    out = ws.import_data(_xml(xml53, "From Richy"), "Order-richy.xml")
    rid = out.lines[0]["revision_id"]
    assert out.status == "imported" and ws.customer_of(rid) == men       # customer, then product by its Item
    customers.set_patterns(ws, [{"variation": "Athlete1", "mdl": str(tmp_path / "men.mdl")}], men)
    customers.set_patterns(ws, [{"variation": "Athlete1", "mdl": str(tmp_path / "kids.mdl")}], kids)
    assert customers.pattern_for(ws, rid) == (str(tmp_path / "men.mdl"), "")   # the product's own patterns
    with ws.for_customer(men):
        pk_men = ws.active("catalog")[0]
    with ws.for_customer(kids):
        pk_kids = ws.active("catalog")[0]
    assert pk_men != pk_kids                                             # own tables
    other = _xml(xml53, "From Richy").replace(b'Item Number="91001"', b'Item Number="55555"')
    bad = ws.import_data(other, "Order-other.xml")
    assert bad.status == "failed" and "55555" in bad.message            # an unknown item: never guessed
    with pytest.raises(WorkflowError):
        customers.add_product(ws, cid, "Again", "91001")
    customers.update_customer(ws, cid, reference="From Richy 2")        # the products follow the customer
    assert customers.get(ws, men)["reference"] == "From Richy 2"
    with ws.for_customer(men):
        assert ws.active("mapping")[1]["detect"]["attributes"]["Reference"] == "From Richy 2"


def test_customers_page_lists_products_under_their_customer(ws):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a = customers.add_customer(ws, "Richy", "From Richy")
    customers.add_customer(ws, "Other", "From Other")
    customers.add_product(ws, a, "Thobe men", "1001001001")
    win = MainWindow(ws)
    win.show()
    win.goto("Customers")
    app.processEvents()
    t = win.pages["Customers"].t
    col0 = [t.item(i, 0).text().strip() for i in range(t.rowCount())]
    assert col0[1].endswith("Richy") and "Thobe men" in col0[2] and col0[2].startswith("↳")   # under Richy
    assert t.item(2, 4).text() == "1001001001" and col0[3].endswith("Other")
    assert "Richy / Thobe men" in [win.customer_box.itemText(i) for i in range(win.customer_box.count())]
    cp = win.pages["Customers"]
    from level7mtm.ui.i18n import tr
    assert t.item(1, 8).text() == tr("for each product")               # the patterns are the products'
    customers.set_patterns(ws, [{"variation": "Big Size", "mdl": "a.mdl"}, {"variation": "Slim", "mdl": "b.mdl"}],
                           customers.products(ws, a)[0]["id"])
    cp.refresh()
    assert t.item(2, 8).text() == tr("{n} patterns").format(n=2)         # the names only in Patterns
    assert cp.tile_buttons["patterns"].isEnabled()                       # LEVEL7: no products
    t.selectRow(1)
    app.processEvents()
    assert ws.customer() == a and not cp.tile_buttons["patterns"].isEnabled()
    t.selectRow(2)
    app.processEvents()
    assert cp.tile_buttons["patterns"].isEnabled()                       # the product has its own


def test_reread_says_why_a_line_is_still_held(ws, xml53):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.customers import reread_held
    from level7mtm.ui.main_window import reread_report
    data = open(xml53, "rb").read().replace(b"Collar Square", b"Collar Round")
    out = ws.import_data(data, "Order-r.xml")
    assert out.lines[0]["status"] == "needs_review"
    text = reread_report(ws, reread_held(ws))
    assert "Collar Round" in text and "•" in text                       # the Variation still without a meaning


def test_tables_reviewed_on_one_of_the_patterns(ws, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    a, b = str(tmp_path / "athlete.mdl"), str(tmp_path / "big.mdl")
    customers.set_patterns(ws, [{"variation": "Style Athlete", "mdl": a}, {"variation": "Big Size", "mdl": b}])
    assert ws.grafis_mdl() == a                                          # the first pattern, by itself
    win = MainWindow(ws)
    page = win.pages["GRAFIS parts"]
    page.refresh()
    assert [page.pattern_box.itemData(i) for i in range(page.pattern_box.count())] == [a, b]
    assert page.pattern_pick.isHidden()                                  # no file chooser with patterns
    page.pattern_box.setCurrentIndex(1)
    page.pattern_box.activated.emit(1)
    assert ws.grafis_mdl() == b
    customers.set_patterns(ws, [])
    page.refresh()
    assert not page.pattern_pick.isHidden()                             # no patterns: choose the model file


def test_product_measurement_table_and_basic_size(ws, xml53):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.customers import basic_sizes_seen, measurement_table, set_measurement_table
    from level7mtm.ui.main_window import MainWindow
    import re
    cid = customers.add_customer(ws, "Richy", "From Richy")
    data = _xml(xml53, "From Richy")
    item = re.search(rb'Item Number="(\d+)"', data).group(1).decode()
    table = re.search(rb'Measurement-Table-Name="([^"]+)"', data).group(1).decode()
    pid = customers.add_product(ws, cid, "Thobe", item)
    assert set_measurement_table(ws, pid, "Other_Table") and measurement_table(ws, pid) == "Other_Table"
    out = ws.import_data(data, "Order-r.xml")
    assert out.lines[0]["status"] == "needs_review"                     # another table name: waits for review
    rev = ws.revision(out.lines[0]["revision_id"])
    assert any(p["code"] == "measure.table" for p in rev["normalized"]["problems"])
    assert basic_sizes_seen(ws, pid) == [rev["raw_line"]["basic_size"]]
    set_measurement_table(ws, pid, table)
    assert not set_measurement_table(ws, pid, table)                     # unchanged: no new version
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    win.goto("Customers")
    app.processEvents()
    t = win.pages["Customers"].t
    row = next(i for i in range(t.rowCount()) if "Thobe" in t.item(i, 0).text())
    assert t.item(row, 5).text() == table and t.item(row, 6).text() == rev["raw_line"]["basic_size"]


def test_orders_read_before_the_product_are_read_again_with_it(ws, xml53):
    """Orders read with the customer's settings (before his product was added) are read again with the
    product's: after saving its settings (held files of the family) or by right click → Read again."""
    import re

    from level7mtm.services.customers import held_files, reread_lines
    cid = customers.add_customer(ws, "Richy", "From Richy")
    data = _xml(xml53, "From Richy").replace(b"Collar Square", b"Collar Round")
    out = ws.import_data(data, "Order-r.xml")
    rid = out.lines[0]["revision_id"]
    assert ws.customer_of(rid) == cid and out.lines[0]["status"] == "needs_review"
    item = re.search(rb'Item Number="(\d+)"', data).group(1).decode()
    pid = customers.add_product(ws, cid, "Thobe", item)
    assert held_files(ws, pid)                                            # the customer's held file: his product's too
    outs, skipped = reread_lines(ws, [rid])
    assert not skipped and outs[0].status == "imported"
    assert ws.customer_of(outs[0].lines[0]["revision_id"]) == pid         # now read with the product


def test_rereading_after_saving_reads_only_that_customers_orders(ws, xml53):
    """The re-read runs in the background, where no customer is chosen on the screen: it must still read the held
    files of the customer whose settings were saved, never another customer's."""
    import threading

    from level7mtm.services.customers import reread_held
    cid = customers.add_customer(ws, "Richy", "From Richy")
    mine = ws.import_data(_xml(xml53, "From Richy").replace(b"Collar Square", b"Collar Round"), "r.xml")
    other = ws.import_data(open(xml53, "rb").read().replace(b"Collar Square", b"Collar Round"), "l.xml")
    assert mine.lines[0]["status"] == other.lines[0]["status"] == "needs_review"
    got = []
    th = threading.Thread(target=lambda: got.extend(reread_held(ws, cid)))
    th.start()
    th.join()
    assert [o.file_name for o in got] == ["r.xml"]                     # LEVEL7's order is not touched


def test_cutting_format_per_customer_and_product(ws):
    from level7mtm.services.auto_cut import KNOWN_FORMATS, customer_formats, export_formats, settings
    got = export_formats(ws)            # read from LEVEL7 Nesting when it is there (e.g. plt, dxf, iso, scb)
    assert {"plt", "iso"} <= set(got) and (got == KNOWN_FORMATS or ws.db.get_setting("nesting.formats") == got)
    cid = customers.add_customer(ws, "Richy", "From Richy")
    pid = customers.add_product(ws, cid, "Thobe", "91001")
    assert customer_formats(ws, pid) is None and settings(ws, pid)["formats"] == ["plt", "iso"]   # markers page
    ws.db.set_setting(f"cutting.formats@{cid}", ["iso"])
    assert settings(ws, pid)["formats"] == ["iso"]                       # the product follows its customer
    ws.db.set_setting(f"cutting.formats@{pid}", ["dxf"])
    assert settings(ws, pid)["formats"] == ["dxf"] and settings(ws, cid)["formats"] == ["iso"]
    assert settings(ws)["formats"] == ["plt", "iso"]                     # other customers: as before


def test_every_order_model_is_kept_in_the_archive(ws, tmp_path):
    from level7mtm.services.grafis_orders import archive_folder, archive_mdl
    ws.db.set_setting("grafis.input", str(tmp_path / "A" / "MDL"))
    assert archive_folder(ws) == str(tmp_path / "A" / "MDL-ARCHIVE")      # next to the GRAFIS input folder
    p0 = tmp_path / "A" / "MDL" / "X.mdl"
    p0.parent.mkdir(parents=True, exist_ok=True)
    p0.write_bytes(b"m")
    assert archive_mdl(ws, str(p0)) is None                             # owner: no copies unless asked
    ws.db.set_setting("grafis.keep_archive", True)
    p0.unlink()
    m = tmp_path / "A" / "MDL" / "Order-1.mdl"
    m.write_bytes(b"written")
    assert archive_mdl(ws, str(m)) == str(tmp_path / "A" / "MDL-ARCHIVE" / "Order-1.mdl")
    m.write_bytes(b"saved by GRAFIS")
    archive_mdl(ws, str(m))
    assert (tmp_path / "A" / "MDL-ARCHIVE" / "Order-1.mdl").read_bytes() == b"saved by GRAFIS"   # newest copy
    ws.db.set_setting("grafis.mdl_archive", str(tmp_path / "mine"))
    assert archive_mdl(ws, str(m)) == str(tmp_path / "mine" / "Order-1.mdl")
    assert archive_mdl(ws, str(tmp_path / "missing.mdl")) is None


def test_many_held_files_counted_fast_and_reported_short(ws, xml53):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.customers import held_files, held_lines, reread_held
    from level7mtm.ui.main_window import reread_report
    data = open(xml53, "rb").read().replace(b"Collar Square", b"Collar Round")
    for i in range(25):
        ws.import_data(data.replace(b"9-53-", f"9-{100 + i}-".encode()), f"o{i}.xml")
    fids = held_files(ws, "level7")
    assert len(fids) == 25 and held_lines(ws, fids) == 25                # the held line of each (not the ready)
    text = reread_report(ws, reread_held(ws, "level7", fids))
    assert text.count("\n") < 25 and "Collar Round" in text                # totals and reasons, not 25 files


def test_saving_unchanged_options_still_offers_the_held_orders(ws, xml53, monkeypatch):
    """The Variation was saved before (the window then froze): saving again changes nothing, the orders it holds
    are still offered for reading again — once, not again when the outer window closes."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.options import option_table, save_options
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ws.import_data(open(xml53, "rb").read().replace(b"Collar Square", b"Collar Weird"), "w.xml")
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("Collar Weird")
    save_options(ws, g)                                                   # saved, the orders not read again
    asked = []
    monkeypatch.setattr("level7mtm.ui.main_window.reread_held_question", lambda page, msg: asked.append(msg))
    win = MainWindow(ws)
    page = win.pages["Design options"]
    page.refresh()
    row = next(i for i, x in enumerate(page.groups) if x["group"] == "collar")
    page.g.setCurrentCell(row, 0)
    page.g.cellClicked.emit(row, 0)
    page._group_save_close()
    assert len(asked) == 1
    page.save()                                                           # the outer Save and close
    assert len(asked) == 1
    win.close()
    app.processEvents()


def test_one_file_failing_does_not_stop_the_others(ws, xml53, monkeypatch):
    from level7mtm.services import customers as cs
    data = open(xml53, "rb").read().replace(b"Collar Square", b"Collar Weird")
    for i in range(3):
        ws.import_data(data.replace(b"9-53-", f"9-{100 + i}-".encode()), f"o{i}.xml")
    fids = cs.held_files(ws, "level7")
    real = cs._reread
    monkeypatch.setattr(cs, "_reread", lambda w, fid: (_ for _ in ()).throw(OSError("disk")) if fid == fids[1]
                        else real(w, fid))
    outs = cs.reread_held(ws, "level7", fids)
    assert [o.status for o in outs] == ["imported", "failed", "imported"] and outs[1].file_name == "o1.xml"


@pytest.mark.skipif(not os.path.exists(__import__("conftest").MDL), reason="customer MDL not in the checkout")
def test_one_xml_with_lines_of_two_products_each_line_its_own_settings(ws, xml53, tmp_path):
    """One XML for one customer with lines of two products (Item Numbers): each line is read with its own
    product's settings and its model written from its own product's pattern."""
    import shutil

    from conftest import MDL
    from level7mtm.services.grafis_orders import import_and_prepare
    a, b = tmp_path / "thobe.mdl", tmp_path / "shirt.mdl"
    shutil.copy(MDL, a)
    shutil.copy(MDL, b)
    cid = customers.add_customer(ws, "Dubai", "From Dubai")
    p1 = customers.add_product(ws, cid, "Thobe", "91001", mdl=str(a))
    p2 = customers.add_product(ws, cid, "Shirt", "91002", mdl=str(b))
    data = _xml(xml53, "From Dubai")
    i = data.rfind(b'Item Number="91001"')
    data = data[:i] + b'Item Number="91002"' + data[i + len(b'Item Number="91001"'):]
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    x = tmp_path / "Order-mixed.xml"
    x.write_bytes(data)
    out = import_and_prepare(ws, str(x))
    assert out.status == "imported" and len(out.lines) == 2
    assert [ws.customer_of(ln["revision_id"]) for ln in out.lines] == [p1, p2]
    assert sorted(g["template"] for g in out.grafis) == sorted([str(a), str(b)])     # each its own pattern
    data2 = data.replace(b'Item Number="91002"', b'Item Number="99999"')
    o2 = ws.import_data(data2.replace(b"9-53-", b"9-54-"), "unknown.xml")
    assert o2.status == "failed" and "99999" in o2.message                          # a product not set up: said


def test_measurement_limits_set_for_one_customer_only(ws, xml53):
    """"FL = 83 cm outside 100–200 cm": the owner sets the limits (or none) for this customer / product; the
    held order then passes when read again, another customer keeps his limits."""
    from level7mtm.services.customers import held_files, measure_range, reread_held, set_measure_range
    cid = customers.add_customer(ws, "Richy", "From Richy")
    code = "FL"
    assert measure_range(ws, cid, code)[0]                               # copied from LEVEL7 (100–200 cm)
    data = _xml(xml53, "From Richy").replace(b'Abbreviation="FL" Value="157.00"', b'Abbreviation="FL" Value="83.00"')
    out = ws.import_data(data, "r.xml")
    rev = ws.revision(out.lines[0]["revision_id"])
    assert any(p["code"] == "measure.range" for p in rev["normalized"]["problems"])
    level7 = measure_range(ws, "level7", code)
    assert set_measure_range(ws, cid, code, None) and measure_range(ws, cid, code)[0] is None
    assert not set_measure_range(ws, cid, code, None)                    # unchanged: no new version
    assert measure_range(ws, "level7", code) == level7                   # the other customer keeps his
    reread_held(ws, cid, held_files(ws, cid))
    rid = ws.db.one("SELECT id FROM revisions WHERE line_number=? AND status NOT IN ('cancelled','superseded') "
                    "ORDER BY id DESC", (rev["line_number"],))["id"]
    assert not any(p["code"] == "measure.range" for p in ws.revision(rid)["normalized"]["problems"])
    set_measure_range(ws, cid, code, (0.5, 300))
    assert measure_range(ws, cid, code)[0] == [0.5, 300.0]
    with pytest.raises(Exception):
        set_measure_range(ws, cid, code, (5, 1))


def test_order_page_limits_button_finds_the_measurement_out_of_range(ws, xml53):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    data = open(xml53, "rb").read().replace(b'Abbreviation="FL" Value="157.00"', b'Abbreviation="FL" Value="83.00"')
    rid = ws.import_data(data, "r.xml").lines[0]["revision_id"]
    win = MainWindow(ws)
    win.current_rid = rid
    page = win.pages["Order details"]
    page.refresh()
    assert page._range_code(rid) == "FL"                                  # from "FL = 83 cm outside …"
    assert any(b.text() == "حدود القياس…" or b.text() == "Measurement limits…"
               for b in page.findChildren(QtWidgets.QPushButton))
    win.close()
    app.processEvents()


def test_held_line_read_again_although_another_line_of_its_xml_went_further(ws, xml53):
    """One XML, two order lines: one went on (sent to nesting), the other waits for a Variation that the owner
    then gave a meaning. Saving reads the held line again — the file's other line is left as it is."""
    from level7mtm.services.customers import held_files, held_lines, reread_held, reread_lines
    from level7mtm.services.options import option_table, save_options
    data = open(xml53, "rb").read()
    i = data.rfind(b"Collar Square")
    data = data[:i] + b"Collar Weird" + data[i + len(b"Collar Square"):]   # the first line only
    out = ws.import_data(data, "two.xml")
    st = {ln["line_number"]: ln["status"] for ln in out.lines}
    held = next(ln for ln in out.lines if ln["status"] == "needs_review")
    other = next(ln for ln in out.lines if ln["status"] == "ready")
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=?", (other["revision_id"],))
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("Collar Weird")
    save_options(ws, g)
    fids = held_files(ws, "level7")
    assert len(fids) == 1 and held_lines(ws, fids) == 1, st
    outs = reread_held(ws, "level7", fids)
    assert [ln["line_number"] for ln in outs[0].lines] == [held["line_number"]]
    assert outs[0].lines[0]["status"] == "ready"
    assert ws.revision(other["revision_id"])["status"] == "sent_to_nesting"      # untouched
    from level7mtm.services.activity import working
    with working([other["revision_id"]], "nesting"):
        _, skipped = reread_lines(ws, [other["revision_id"]])
    assert skipped and "being nested now" in skipped[0]                  # never while it is being nested


def test_line_blocked_at_the_model_is_read_again_after_saving(ws, xml53):
    """A line read well whose model could not be written ("measurement SW not in the order") is held too: after
    the size table is saved it is read again with it."""
    from level7mtm.services.customers import held_files, held_lines, reread_held
    from level7mtm.services.grafis_orders import _in_xml
    out = ws.import_data(open(xml53, "rb").read(), "a.xml")
    rid = out.lines[0]["revision_id"]
    assert out.lines[0]["status"] == "ready" and held_files(ws, "level7") == []
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) "
                  "VALUES (?, '2026-10-23T10:00:00', 'blocked', NULL, 't', '{}')", (rid,))
    fids = held_files(ws, "level7")
    assert len(fids) == 1 and held_lines(ws, fids) == 1
    outs = reread_held(ws, "level7", fids)
    assert [ln["line_number"] for ln in outs[0].lines] == [out.lines[0]["line_number"]]
    rev = ws.revision(outs[0].lines[0]["revision_id"])
    assert _in_xml(ws, rev, "SW") == "the XML of this order line has no SW"
    assert _in_xml(ws, rev, "FL").startswith("the XML has FL")


def test_line_whose_nesting_failed_gets_its_model_again_from_the_pattern(ws, xml53):
    """The owner adds a grain line to a piece of the base pattern: a line saved by CAD whose nesting failed is read
    again by right click and its model written again (from the corrected pattern)."""
    from level7mtm.services.customers import reread_lines
    out = ws.import_data(open(xml53, "rb").read(), "n.xml")
    rid = out.lines[0]["revision_id"]
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=?", (rid,))
    outs, skipped = reread_lines(ws, [rid])
    assert not skipped and [ln["line_number"] for ln in outs[0].lines] == [out.lines[0]["line_number"]]
    assert outs[0].lines[0]["action"] == "new" and outs[0].lines[0]["status"] == "ready"
    assert ws.revision(out.lines[1]["revision_id"])["status"] == "ready"   # the file's other line untouched


def test_each_customer_chooses_his_cutting_files_folder(ws, tmp_path):
    """Beside each customer: the folder his cutting files are sent to (a product follows its customer unless it has
    its own); none chosen: his folder inside the cutting folder."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    from level7mtm.services.auto_cut import cut_folder
    from level7mtm.ui.main_window import MainWindow
    ws.db.set_setting("cutting.output", str(tmp_path / "CUT"))
    cid = customers.add_customer(ws, "Dubai", "From Dubai")
    pid = customers.add_product(ws, cid, "Thobe", "1001")
    assert cut_folder(ws, [pid]) == os.path.join(str(tmp_path / "CUT"), "Dubai")
    ws.db.set_setting(f"cutting.folder@{cid}", str(tmp_path / "Dubai cutter"))
    assert cut_folder(ws, [pid]) == str(tmp_path / "Dubai cutter")           # the product follows its customer
    ws.db.set_setting(f"cutting.folder@{pid}", str(tmp_path / "Thobe"))
    assert cut_folder(ws, [pid]) == str(tmp_path / "Thobe") and cut_folder(ws, [cid]) == str(tmp_path / "Dubai cutter")
    assert cut_folder(ws, ["level7"]) == os.path.join(str(tmp_path / "CUT"), "LEVEL7")
    assert cut_folder(ws, [cid, "level7"]).endswith("mixed")             # one marker of two customers
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow(ws)
    win.goto("Customers")
    app.processEvents()
    t = win.pages["Customers"].t
    row = next(i for i in range(t.rowCount()) if t.item(i, 0).text().strip() == "Dubai")
    assert "Dubai cutter" in t.cellWidget(row, 1).findChildren(QtWidgets.QToolButton)[0].text()
    win.close()
    app.processEvents()


def test_held_orders_read_again_at_once_after_saving(ws, xml53, monkeypatch):
    """Owner: an order kept its old messages ("Variation … has no meaning") although the Variation was given one
    — the question after saving was missed. Saving now reads the held orders again at once, without asking."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    import time

    from level7mtm.services.options import option_table, save_options
    from level7mtm.ui.main_window import MainWindow, reread_held_question
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    asked, told = [], []
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a)))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", staticmethod(lambda *a, **k: told.append(a[2])))
    rid = ws.import_data(open(xml53, "rb").read().replace(b"Collar Square", b"Collar Weird"), "w.xml") \
        .lines[0]["revision_id"]
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("Collar Weird")
    save_options(ws, g)
    line = ws.revision(rid)["line_number"]
    win = MainWindow(ws)
    reread_held_question(win.pages["Design options"], "Saved")
    t0 = time.time()
    while not told and time.time() - t0 < 20:
        app.processEvents()
        time.sleep(0.05)
    assert not asked and told                                             # no question; the result at the end
    now_ = ws.db.one("SELECT status FROM revisions WHERE line_number=? ORDER BY id DESC", (line,))
    assert now_["status"] == "ready"
    win.close()
    app.processEvents()


def test_held_orders_read_again_when_the_program_opens(ws, xml53):
    """A Variation given a meaning before (the question then missed): the order is read again when the program
    opens, without anybody saving again."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    import time

    from level7mtm.services.options import option_table, save_options
    from level7mtm.ui.main_window import MainWindow
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    rid = ws.import_data(open(xml53, "rb").read().replace(b"Collar Square", b"Collar Weird"), "w.xml") \
        .lines[0]["revision_id"]
    line = ws.revision(rid)["line_number"]
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("Collar Weird")
    save_options(ws, g)                                                   # saved; never read again
    ws.db.set_setting("reread.at_start", True)
    win = MainWindow(ws)
    win.show()
    win._reread_held_at_start()
    t0 = time.time()
    st = lambda: ws.db.one("SELECT status FROM revisions WHERE line_number=? ORDER BY id DESC", (line,))["status"]  # noqa
    while st() != "ready" and time.time() - t0 < 20:
        app.processEvents()
        time.sleep(0.05)
    assert st() == "ready"
    win.close()
    app.processEvents()


def test_order_numbers_with_a_dash_in_the_reseller(ws, xml53):
    """Richy: L-FR20-373-1_1, FR8-7776-X-2_2 — no "line number in an unknown form" any more."""
    data = open(xml53, "rb").read().replace(b'Number="9-53-1_2"', b'Number="L-FR20-373-1_1"')
    out = ws.import_data(data, "r.xml")
    probs = [p["code"] for ln in out.lines for p in ws.revision(ln["revision_id"])["normalized"]["problems"]]
    assert "line.number" not in probs


def test_held_order_whose_variation_has_a_meaning_now_is_found(ws, xml53):
    """Whatever missed it before: a held order whose Variation "without meaning" has one now is found (and read
    again by the program every few minutes)."""
    from level7mtm.services.customers import reread_lines, stale_held
    from level7mtm.services.options import option_table, save_options
    rid = ws.import_data(open(xml53, "rb").read().replace(b"Collar Square", b"Collar Weird"), "w.xml") \
        .lines[0]["revision_id"]
    assert stale_held(ws) == []                                           # no meaning yet: nothing to do
    g = option_table(ws)
    next(v for v in next(x for x in g if x["group"] == "collar")["values"]
         if v["value"] == "square")["erp_names"].append("collar weird")   # other case: the same name
    save_options(ws, g)
    assert stale_held(ws) == [rid]
    outs, skipped = reread_lines(ws, stale_held(ws))
    assert not skipped and outs[0].lines[0]["status"] == "ready" and stale_held(ws) == []


def test_nesting_gaps_per_customer(ws, monkeypatch):
    """Owner: the room between the pieces is each customer's own; sent to LEVEL7 Nesting only when set and only the
    options it knows (an older one: said, nested without them)."""
    import pytest

    from level7mtm.services import auto_cut, customers
    a = customers.add_customer(ws, "Richy", "From Richy")
    b = customers.add_customer(ws, "Dubai", "From Dubai")
    assert auto_cut.customer_gaps(ws, a) == {"spacing": 0.0, "buffer_x": 0.0, "buffer_y": 0.0, "margin": 0.0}
    auto_cut.set_customer_gaps(ws, a, {"spacing": 3, "margin": 10})
    auto_cut.set_customer_gaps(ws, b, {"spacing": 1.5})
    assert auto_cut.customer_gaps(ws, a)["spacing"] == 3.0 and auto_cut.customer_gaps(ws, b)["spacing"] == 1.5
    with pytest.raises(ValueError):
        auto_cut.set_customer_gaps(ws, a, {"spacing": -1})
    monkeypatch.setattr(auto_cut, "_nest_flags", lambda cmd, env: "--spacing --buffer-x --buffer-y --margin")
    assert auto_cut.gap_args(ws, a, ["cli"], {}) == ["--spacing", "3", "--margin", "10"]
    assert auto_cut.gap_args(ws, None, ["cli"], {}) == []
    monkeypatch.setattr(auto_cut, "_nest_flags", lambda cmd, env: "--spacing")
    log = []
    assert auto_cut.gap_args(ws, a, ["cli"], {}, log) == ["--spacing", "3"] and "--margin" in log[0]


def test_orders_read_again_from_their_xml_file_not_a_copy(ws, tmp_path, xml53):
    """Owner: "no file is read except from process; done: passed CAD, nesting and cutting; error: an XML with data
    missing" — an order is read again only from its XML in process (as it is now: an edited file as edited); in
    error / done it is not read, and it is said where it is (move it to process); never from a copy."""
    from level7mtm.services.customers import reread_lines, xml_bytes
    ws.db.set_setting("import.keep_xml", False)
    customers.add_customer(ws, "Richy", "From Richy")
    erp = tmp_path / "ERP XML" / "process"
    erp.mkdir(parents=True)
    ws.db.set_setting("watch.folder", str(erp))
    src = erp / "Order-1.xml"
    src.write_bytes(_xml(xml53, "From Richy"))
    out = ws.import_file(str(src))
    rid = out.lines[0]["revision_id"]
    fid = ws.db.one("SELECT import_file FROM revisions WHERE id=?", (rid,))["import_file"]
    assert bytes(ws.db.one("SELECT raw FROM import_files WHERE id=?", (fid,))["raw"]) == b""   # no copy kept
    assert xml_bytes(ws, fid)[1] == str(src)
    outs, _ = reread_lines(ws, [rid])
    assert outs[0].status == "imported"
    rid = outs[0].lines[0]["revision_id"]
    err = tmp_path / "ERP XML" / "error"
    err.mkdir()
    os.replace(src, err / "Order-1.xml")                                   # in error: not read from there
    data, why = xml_bytes(ws, fid)
    assert data is None and "is in error" in why
    outs, _ = reread_lines(ws, [rid])
    assert outs[0].status == "failed" and "move it to process" in outs[0].message
    os.replace(err / "Order-1.xml", src)                                   # the owner moved it back: read
    assert xml_bytes(ws, fid)[0]
