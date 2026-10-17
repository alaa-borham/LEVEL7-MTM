"""GRAFIS result intake: SCB named after the XML → checked against the order → handed to LEVEL7 Nesting."""
import hashlib
import os
import shutil
import sys

import pytest
from conftest import SCB, XML_53, needs_mdl, read

from level7mtm.services.grafis_intake import check_scb, register_scb
from level7mtm.services.watcher import FolderWatcher

pytestmark = pytest.mark.skipif(not os.path.exists(SCB), reason="customer SCB not in the checkout (not committed)")
NESTING = os.environ.get("LEVEL7_NESTING_SRC", os.path.join(os.path.dirname(__file__), "..", "..", "alaa-borham",
                                                             "level7-nesting"))


def _single_line_order(ws, tmp_path, width="1480"):
    """Test data derived from the real XML: only line 9-53-1_2, fabric width set to the sample marker's 1480 mm."""
    txt = read(XML_53).decode("utf-8")
    start = txt.index('<Order Number="9-53-2_2"')
    end = txt.index("</Order>", start) + len("</Order>")
    txt = (txt[:start] + txt[end:]).replace('Width="1450"', f'Width="{width}"')
    ws.import_data(txt.encode("utf-8"), "Order-9-53-1_2.xml")
    scb = tmp_path / "grafis" / "Order-9-53-1_2.scb"
    scb.parent.mkdir()
    shutil.copy(SCB, scb)
    return str(scb)


def test_real_order_mismatch_is_held_back(ws, tmp_path):
    ws.import_file(XML_53)
    scb = tmp_path / "Order-9-53-1_2.scb"
    shutil.copy(SCB, scb)
    r = register_scb(ws, str(scb), nesting_dir=str(tmp_path / "nest"), cut=False)
    msgs = [m for _, m in r["check"].issues]
    assert r["status"] == "needs_review" and r["nesting_path"] is None
    assert [ln["line_number"] for ln in r["check"].lines] == ["9-53-1_2", "9-53-2_2"]     # by XML file name
    assert any("1480" in m and "1450" in m for m in msgs)
    assert any("1 bundle" in m and "2 garment" in m for m in msgs)
    assert not os.path.exists(tmp_path / "nest")


def test_matching_marker_is_handed_over_unchanged(ws, tmp_path):
    scb = _single_line_order(ws, tmp_path)
    chk = check_scb(ws, scb, renest=False)
    assert chk.ok and chk.info["pieces_placed"] == 23 and len(chk.pieces) == 12
    r = register_scb(ws, scb, nesting_dir=str(tmp_path / "nest"), cut=False)
    assert r["status"] == "handed_over"
    assert hashlib.sha256(read(r["nesting_path"])).hexdigest() == hashlib.sha256(read(scb)).hexdigest()
    assert os.path.exists(scb)                                            # source never removed
    rows = open(tmp_path / "nest" / "nesting_orders.csv", encoding="utf-8-sig").read().splitlines()
    assert rows[0].startswith("marker,file") and rows[1].endswith("9-53-1_2")
    rid = ws.list_revisions()[0]["id"]
    assert ws.revision(rid)["status"] == "sent_to_nesting"
    again = register_scb(ws, scb, nesting_dir=str(tmp_path / "nest"), cut=False)
    assert again["status"] == "duplicate" and len(open(tmp_path / "nest" / "nesting_orders.csv").read().splitlines()) == 2
    if os.path.isdir(NESTING):
        pytest.importorskip("shapely")
        sys.path.insert(0, NESTING)
        from level7.service import open_any
        proj, issues = open_any(r["nesting_path"])
        assert proj.markers and len([p for p in proj.markers[0].placements if p.placed]) == 23


def test_review_findings_need_an_explicit_reason(ws, tmp_path):
    scb = _single_line_order(ws, tmp_path, width="1450")
    assert register_scb(ws, scb, nesting_dir=str(tmp_path / "n"), cut=False)["status"] == "needs_review"
    r = register_scb(ws, scb, nesting_dir=str(tmp_path / "n"), confirm_reason="marker cut on the 1480 roll", cut=False)
    assert r["status"] == "handed_over"
    assert any(a["action"] == "grafis.scb.review_accepted" for a in ws.audit_log())


def test_unknown_and_broken_files(ws, tmp_path):
    p = tmp_path / "Order-1-2-3_4.scb"
    shutil.copy(SCB, p)
    assert register_scb(ws, str(p), cut=False)["status"] == "unmatched"
    bad = tmp_path / "Order-9-53-1_2.scb"
    bad.write_bytes(read(SCB)[:5000])
    ws.import_file(XML_53)
    r = register_scb(ws, str(bad), cut=False)
    assert r["status"] != "handed_over" and any(s == "blocking" for s, _ in r["check"].issues)


def test_watch_grafis_output_folder(ws, tmp_path):
    scb = _single_line_order(ws, tmp_path)
    t = [0.0]
    w = FolderWatcher(ws, os.path.dirname(scb), settle_seconds=1, clock=lambda: t[0], extensions=(".scb",),
                      handler=lambda p: register_scb(ws, p, nesting_dir=str(tmp_path / "nest"), cut=False))
    assert w.poll() == []
    t[0] = 2
    out = w.poll()
    assert [o["status"] for o in out] == ["handed_over"]


@pytest.mark.skipif(not os.path.isdir(NESTING), reason="LEVEL7 Nesting source not available")
def test_fully_automatic_chain_to_plt_and_iso(ws, tmp_path, monkeypatch):
    """Real XML (2 thobes, fabric 1450 mm) + a GRAFIS SCB named after it → nesting at 1450 mm for 2 garments →
    PLT + ISO, without anybody opening a file. (The SCB is the reference 52-S marker: this proves the chain,
    not the pattern of this customer.)"""
    pytest.importorskip("shapely")
    pytest.importorskip("pyclipper")
    monkeypatch.setenv("LEVEL7_NESTING_SRC", os.path.abspath(NESTING))
    ws.db.set_setting("cutting.time_s", 5)
    ws.db.set_setting("cutting.output", str(tmp_path / "cut"))
    ws.import_file(XML_53)
    scb = tmp_path / "grafis" / "Order-9-53-1_2.scb"
    scb.parent.mkdir()
    shutil.copy(SCB, scb)
    t = [0.0]
    w = FolderWatcher(ws, str(scb.parent), settle_seconds=1, clock=lambda: t[0], extensions=(".scb",),
                      handler=lambda p: register_scb(ws, p, nesting_dir=str(tmp_path / "nest")))
    w.poll()
    t[0] = 2
    out = w.poll()
    assert [o["status"] for o in out] == ["cutting_done"], out[0]["cutting"]
    cut = out[0]["cutting"]
    assert cut["width_mm"] == 1450 and cut["garments"] == 2 and cut["placed"] == 46       # 23 pieces x 2
    assert sorted(os.path.basename(p) for p in cut["outputs"]) == ["Order-9-53-1_2.iso", "Order-9-53-1_2.plt"]
    assert open(cut["outputs"][0], "rb").read(2) in (b"IN", b"H\r", b"H\n") or os.path.getsize(cut["outputs"][0]) > 1000
    assert all(ws.revision(r["id"])["status"] == "completed" for r in ws.list_revisions())
    assert "remnant" in cut and "done" in cut["remnant"]                     # "small pieces together" was run
    again = register_scb(ws, str(scb), nesting_dir=str(tmp_path / "nest"))
    assert again["status"] == "duplicate"                                               # no second cut job


def test_nesting_not_configured_is_reported(ws, tmp_path, monkeypatch):
    monkeypatch.delenv("LEVEL7_NESTING_SRC", raising=False)
    import level7mtm.services.auto_cut as ac
    monkeypatch.setattr(ac, "DEFAULT_CLI", [])
    scb = _single_line_order(ws, tmp_path)
    r = register_scb(ws, scb, nesting_dir=str(tmp_path / "nest"))
    assert r["status"] == "cutting_not_configured" and "level7-cli" in r["cutting"]["message"]
