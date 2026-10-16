"""ER folder: files that stop the automatic chain are put there with the reason."""
import os
import shutil

from conftest import MDL, SCB, XML_53, needs_mdl, read

from level7mtm.services.grafis_intake import register_scb
from level7mtm.services.grafis_orders import import_and_prepare


def test_broken_xml_and_unconfigured_model_go_to_er(ws, tmp_path):
    er = tmp_path / "ER"
    ws.db.set_setting("errors.folder", str(er))
    bad = tmp_path / "Order-broken.xml"
    bad.write_bytes(b"<OrderMaster><Orders><Order")
    out = import_and_prepare(ws, str(bad))
    assert out.status == "failed"
    assert (er / "Order-broken.xml").exists() and bad.exists()            # copied, the ERP original stays
    assert "السبب" in (er / "Order-broken.txt").read_text(encoding="utf-8-sig")
    out = import_and_prepare(ws, XML_53)                                   # no GRAFIS model chosen yet
    assert {g["status"] for g in out.grafis} == {"not_configured"}
    assert (er / "Order-9-53-1_2.xml").exists()
    assert (er / "Order-9-53-1_2__9-53-1_2.txt").exists() and (er / "Order-9-53-1_2__9-53-2_2.txt").exists()


def test_no_er_folder_set_moves_nothing(ws, tmp_path):
    d = tmp_path / "erp"
    d.mkdir()
    bad = d / "Order-broken.xml"
    bad.write_bytes(b"<x")
    assert import_and_prepare(ws, str(bad)).status == "failed"
    assert sorted(os.listdir(d)) == ["Order-broken.xml"]
    assert not any(n.endswith(".txt") for _, _, fs in os.walk(tmp_path) for n in fs)


@needs_mdl
def test_unmatched_scb_is_moved_to_er(ws, tmp_path):
    er, scb_dir = tmp_path / "ER", tmp_path / "SCB"
    scb_dir.mkdir()
    ws.db.set_setting("errors.folder", str(er))
    scb = scb_dir / "Order-unknown.scb"
    shutil.copy(SCB, scb)
    r = register_scb(ws, str(scb), cut=False)
    assert r["status"] == "unmatched" and not scb.exists()
    assert (er / "Order-unknown.scb").read_bytes() == read(SCB)
    assert "does not match" in (er / "Order-unknown.txt").read_text(encoding="utf-8-sig")
    row = ws.db.one("SELECT path FROM grafis_results")
    assert row["path"] == r["moved_to"] == str(er / "Order-unknown.scb")


@needs_mdl
def test_good_order_writes_mdl_and_nothing_in_er(ws, tmp_path):
    er = tmp_path / "ER"
    ws.db.set_setting("errors.folder", str(er))
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    out = import_and_prepare(ws, XML_53)
    assert [g["status"] for g in out.grafis] == ["written", "written"]
    assert not er.exists() or not os.listdir(er)
    assert len(os.listdir(tmp_path / "MDL")) == 2
