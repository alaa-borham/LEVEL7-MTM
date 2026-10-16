"""Automatic X values: each imported order line gets a copy of the GRAFIS model with its X values filled in."""
import json
import os
import struct

import pytest
from conftest import MDL, SCB, XML_53, needs_mdl, read

from level7mtm.grafis.container import computed_check, parse_container, stored_check
from level7mtm.grafis.xvalues import read_xvalues
from level7mtm.grafis.xwriter import XWriteError, check_all, write_xvalues
from level7mtm.services.grafis_orders import import_and_prepare, order_mdl


@needs_mdl
def test_xtable_reads_all_records():
    """Records have 3…6 units; the old reader stopped at the first 4-unit record (X13) and saw only X1…X12."""
    x = read_xvalues(MDL, 2)
    assert sorted(x) == list(range(1, 51))
    assert (x[4].name, x[5].name, x[13].name, x[14].name, x[15].name) == \
        ("down FRONT R", "down FRONT L", "hip", "buttom girth--F1", "bottom width--F2")
    assert (x[23].name, x[31].name, x[37].name, x[38].name) == \
        ("jabzour--G1", "sleeve length sadaa--CC1", "sadaa round--CC4", "sadaa width--CC5")
    assert x[13].default == 600 and x[13].sizes["__HN40_0"] > 0
    assert read_xvalues(MDL, 5)[1].name == "collar length--E1"
    assert [v.name for v in read_xvalues(MDL, 6).values()] == ["sleeve length", "elbow", "elbow2", "kabak length",
                                                               "kabak width"]


@needs_mdl
def test_grafis_check_values_reproduced():
    for f in (MDL, SCB):
        if os.path.exists(f):
            ok, n = check_all(read(f))
            assert ok == n > 100, f


@needs_mdl
def test_write_xvalues_changes_only_the_values():
    data = read(MDL)
    out = write_xvalues(data, {(2, 1): 1570.0, (2, 13): 700.0, (6, 1): 675.0})
    assert len(out) == len(data) and out[:80] == data[:80]
    a, b = parse_container(data), parse_container(out)
    changed = [y.bid for x, y in zip(a.blocks, b.blocks) if data[x.offset:x.offset + 32 + len(x.payload)] !=
               out[y.offset:y.offset + 32 + len(y.payload)]]
    assert changed == [(3, 2, 3, 1), (3, 6, 3, 1)]
    assert all(computed_check(out, blk) == stored_check(out, blk) for blk in b.blocks)
    x = read_xvalues(b, 2)
    assert x[1].default == 1570 and set(x[1].sizes.values()) == {1570.0}      # every size entry too
    assert x[13].default == 700 and x[3].default == 440                        # X3 untouched
    assert read_xvalues(b, 6)[1].default == 675
    with pytest.raises(XWriteError, match="X99"):
        write_xvalues(data, {(2, 99): 1.0})
    bad = bytearray(data)
    blk = a.get(3, 2, 3, 1)
    struct.pack_into("<I", bad, blk.offset, stored_check(data, blk) ^ 1)        # template with a stale check
    with pytest.raises(XWriteError, match="check value"):
        write_xvalues(bytes(bad), {(2, 1): 1570.0})


@needs_mdl
def test_order_import_writes_grafis_models(ws, tmp_path):
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = import_and_prepare(ws, XML_53)
    assert [g["status"] for g in o.grafis] == ["written", "written"]
    names = sorted(os.path.basename(g["path"]) for g in o.grafis)
    assert names == ["Order-9-53-1_2__9-53-1_2.mdl", "Order-9-53-1_2__9-53-2_2.mdl"]
    g = o.grafis[0]
    x = read_xvalues(g["path"], 2)
    xml = {"X1": 1570, "X3": 495, "X7": 420, "X9": 690, "X12": 640, "X13": 700, "X14": 830, "X23": 450, "X31": 675}
    assert {k: x[int(k[1:])].default for k in xml} == xml
    assert x[8].default == 360 and any(k.startswith("002/X8 front width 1") for k in g["kept_default"])
    assert check_all(read(g["path"]))[0] == check_all(read(g["path"]))[1]
    assert order_mdl(ws, g["revision_id"])["status"] == "duplicate"
    row = ws.db.one("SELECT * FROM grafis_orders WHERE status='written' ORDER BY id LIMIT 1")
    assert json.loads(row["info"])["rows"][0]["source"] == "FL"


@needs_mdl
def test_order_model_is_held_when_unclear(ws, tmp_path):
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    txt = read(XML_53).decode("utf-8")
    o = import_and_prepare(ws, XML_53)
    assert {g["status"] for g in o.grafis} == {"not_configured"}               # no model chosen: said, not silent
    ws.db.set_setting("grafis.mdl", MDL)
    # a confirmed measurement missing: GRAFIS would keep its placeholder → nothing written
    no_ch = txt.replace('Abbreviation="CH"', 'Abbreviation="CHX"').replace("9-53-1_2", "9-53-1_3") \
        .replace("9-53-2_2", "9-53-2_3")
    o = ws.import_data(no_ch.encode(), "Order-9-53-1_3.xml")
    g = order_mdl(ws, o.lines[0]["revision_id"])
    assert g["status"] == "blocked" and g["path"] is None
    assert any("CH" in p for p in g["problems"])
    assert not list(tmp_path.glob("in/*9-53-1_3*"))


@needs_mdl
def test_scb_of_a_line_model_matches_only_that_line(ws, tmp_path):
    import shutil
    from level7mtm.services.grafis_intake import check_scb
    ws.import_file(XML_53)
    scb = tmp_path / "Order-9-53-1_2__9-53-2_2-S-52.scb"
    shutil.copy(SCB, scb)
    chk = check_scb(ws, str(scb))
    assert [ln["line_number"] for ln in chk.lines] == ["9-53-2_2"]
