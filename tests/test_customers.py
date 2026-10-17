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
    ok = ws.import_data(open(xml53, "rb").read(), "Order-l7.xml")  # LEVEL7's XML: still LEVEL7
    assert ok.status in ("imported", "duplicate")
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
