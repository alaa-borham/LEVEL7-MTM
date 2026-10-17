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
    assert page.t.rowCount() == 2 and page.t.item(1, 1).text() == "From Richy"
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
    assert win.current_page() is cp and cp.inner.currentIndex() == 2
    assert "Richy" in cp.head.text()                                  # whose settings these are
    page = cp.inner_pages["Design options"]
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
    page = win.pages["Customers"].inner_pages["X values for GRAFIS"]
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
