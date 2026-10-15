import os
import shutil

from conftest import MDL, SCB, XML_52, needs_mdl, read

from level7mtm.grafis.container import GrafisFormatError, parse_container
from level7mtm.grafis.formulas import canonical, read_formulas
from level7mtm.grafis.textsections import read_text
from level7mtm.services.watcher import FolderWatcher


@needs_mdl
def test_mdl_formulas():
    parts = read_formulas(MDL)
    assert len(parts) == 30 and sum(len(p.records) for p in parts.values()) == 335
    assert not any(p.errors for p in parts.values())
    z = {r.name: r for r in parts[2].records}
    assert z["Z6"].formula == "X7/6.7+Z1" and z["Z6"].description == "فتحه الرقبه عرض"
    assert z["Z6"].history[0].formula == "X7/6.8+Z1"
    assert z["Z23"].measured == "AB" and z["Z28"].measured == "GL"
    assert z["Z76"].parse_error                                  # "X35/" as stored in the file
    assert canonical("X01") == "X1" and canonical("XG04") == "XG4"


@needs_mdl
def test_mdl_text_sections():
    t = read_text(MDL)
    assert [s.name for s in t.sizes][1:] == ["52-S", "53-S", "54-S", "55-S", "56-S", "57-S", "58-S", "59-S", "60-S", "62-S"]
    assert len(t.parts) == 102 and t.parts[6].name == "front1" and t.parts[6].production
    assert t.measure_tables[2][0].code == "T2M1"
    s = read_text(SCB)
    assert s.sbinfo["WIDTH"] == "1480" and s.sbinfo["PIECES"] == "12"


def test_not_grafis():
    try:
        parse_container(b"x" * 200)
    except GrafisFormatError:
        return
    raise AssertionError("expected GrafisFormatError")


@needs_mdl
def test_truncated_mdl():
    c = parse_container(read(MDL)[:100000])
    assert not c.ok


def test_watcher_waits_for_stable_file(ws, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    t = [0.0]
    w = FolderWatcher(ws, str(inbox), settle_seconds=2, clock=lambda: t[0])
    data = read(XML_52)
    target = inbox / "Order-9-52-1_0.xml"
    target.write_bytes(data[:500])                    # ERP still copying
    assert w.poll() == []
    t[0] = 1
    target.write_bytes(data)                          # grows -> wait again
    os.utime(target, (100, 100))
    assert w.poll() == []
    t[0] = 2.5
    assert w.poll() == []                             # stable for 1.5 s only
    t[0] = 3.5
    out = w.poll()
    assert [o.status for o in out] == ["imported"]
    assert target.exists()                            # source never deleted
    t[0] = 10
    assert w.poll() == []                             # processed once
    shutil.copy(target, inbox / "copy.xml")
    t[0] = 11
    w.poll()
    t[0] = 20
    assert [o.status for o in w.poll()] == ["duplicate"]
    (inbox / "partial.xml.part").write_bytes(b"<x")
    (inbox / "bad.xml").write_bytes(b"<OrderMaster")
    t[0] = 21
    w.poll()
    t[0] = 30
    w.poll()
    assert str(inbox / "bad.xml") in w.state.failed
    assert w.retry(str(inbox / "bad.xml")).status == "failed"
