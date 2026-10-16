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
    assert x[8].default == 360 and "002/X8 front width 1" in g["inactive"]
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


# GRAFIS parts as the owner enters them in the Rules editor (test data: the reference model's own 13 parts for the
# base, and options that switch jabzour / chest pocket parts — the real table comes from the owner)
GPARTS = [{"option": "", "part": n, "name": nm, "material": mt, "copies": c, "mirrored": m} for n, nm, mt, c, m in (
    (7, "front1", "O", 1, 0), (8, "back1", "O", 1, 0), (9, "shoulder1", "O", 1, 1), (10, "side", "O", 1, 1),
    (34, "ashtack", "O", 2, 2), (36, "sleeve SH-101", "O", 1, 1), (74, "down-001+14+004+Royal", "F", 1, 0))] + [
    {"option": "jabzour=splender", "part": 24, "name": "jabzour splender FB", "material": "O", "copies": 1,
     "mirrored": 0},
    {"option": "pocket_chest=cut", "part": 13, "name": "pocket chest cut", "material": "O", "copies": 1, "mirrored": 0},
    {"option": "collar=classic", "part": 80, "name": "Gallab Royal-Full fab", "material": "O", "copies": 1,
     "mirrored": 1}]


def _with_parts(ws, rows):
    pk, cat = ws.active("catalog")
    cat = json.loads(json.dumps(cat))
    for prod in cat["products"].values():
        for st in prod["styles"].values():
            st["grafis_parts"] = rows
    cat["version"] += "+parts" + str(len(rows))
    ws.set_active("catalog", ws.save_document("catalog", cat))


@needs_mdl
def test_part_selection_written_into_model():
    from level7mtm.grafis.parts import PartSpec, production
    from level7mtm.grafis.xwriter import XWriteError, build_order_model
    data = read(MDL)
    own = {n: s for n, (_, s) in production(parse_container(data)).items() if s}
    assert len(own) == 13
    same, changed = build_order_model(data, {}, own)
    assert changed == [] and same == data                                   # nothing to do: identical file
    want = {7: PartSpec("O", 1, 0), 13: PartSpec("O", 1, 0), 36: PartSpec("O", 1, 1), 17: PartSpec("F", 1, 0)}
    out, changed = build_order_model(data, {(2, 1): 1570.0}, want)
    assert {n: s for n, (_, s) in production(parse_container(out)).items() if s} == want
    assert 13 in changed and 22 in changed and 7 not in changed
    ok, n = check_all(out)
    assert ok == n
    from level7mtm.grafis.textsections import read_text
    tl = {p.number: p for p in read_text(parse_container(out)).parts}
    assert (tl[13].production, tl[13].material, tl[13].count, tl[13].mirrored) == (True, "O", 1, 0)
    assert not tl[22].production and tl[17].material == "F"
    with pytest.raises(XWriteError, match="999"):
        build_order_model(data, {}, {999: PartSpec()})
    with pytest.raises(XWriteError, match="material 'Z'"):
        build_order_model(data, {}, {13: PartSpec("Z", 1, 0)})


@needs_mdl
def test_order_model_gets_the_parts_of_its_options(ws, tmp_path):
    from level7mtm.grafis.parts import production
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    _with_parts(ws, GPARTS)
    o = import_and_prepare(ws, XML_53)
    assert [g["status"] for g in o.grafis] == ["written", "written"]
    by_line = {g["line_number"]: g for g in o.grafis}
    p1 = {n for n, (_, s) in production(read_container_path(by_line["9-53-1_2"]["path"])).items() if s}
    p2 = {n for n, (_, s) in production(read_container_path(by_line["9-53-2_2"]["path"])).items() if s}
    assert p1 == {7, 8, 9, 10, 34, 36, 74, 24, 13}                       # collar square: no part 80
    assert p2 == p1 | {80}                                                # collar classic
    assert read_xvalues(by_line["9-53-1_2"]["path"], 2)[1].default == 1570
    # a part name that differs from the model: held, nothing written
    bad = [dict(GPARTS[0], name="front2")] + GPARTS[1:]
    _with_parts(ws, bad)
    g = order_mdl(ws, by_line["9-53-1_2"]["revision_id"])
    assert g["status"] == "blocked" and any("front1" in p for p in g["problems"])


@needs_mdl
def test_scb_check_compares_parts_of_its_material(ws, tmp_path):
    import shutil
    from level7mtm.services.grafis_intake import check_scb
    _with_parts(ws, [r for r in GPARTS if r["option"] != "collar=classic"] + [
        {"option": "", "part": 14, "name": "NN", "material": "O", "copies": 1, "mirrored": 0},
        {"option": "", "part": 22, "name": "second jabzour", "material": "O", "copies": 1, "mirrored": 0}])
    ws.import_file(XML_53)
    scb = tmp_path / "Order-9-53-1_2__9-53-1_2.scb"
    shutil.copy(SCB, scb)
    chk = check_scb(ws, str(scb))
    msgs = [m for _, m in chk.issues]
    assert chk.info["material_code"] == "O"
    assert not any("down-001" in m for m in msgs)                     # interlining part: other marker
    assert any(s == "blocking" and "pocket chest cut" in m for s, m in chk.issues)   # expected, missing in SCB
    assert any("Gallab Royal-Full fab" in m and "not expected" in m for m in msgs)


def read_container_path(p):
    from level7mtm.grafis.container import read_container
    return read_container(p)


@needs_mdl
def test_x_value_table_editor(ws, tmp_path):
    """The owner links XML codes to X values in a table of the model's X list; a new code is added everywhere."""
    from level7mtm.services.xtable import save_x_table, x_parts, x_table
    assert x_parts(MDL) == {2: 50, 5: 44, 6: 5}
    rows = {r["x"]: r for r in x_table(ws, MDL, 2)}
    assert rows[1]["codes"] == ["FL"] and rows[7]["codes"] == ["COS", "COG"] and rows[8]["codes"] == []
    assert rows[13]["name"] == "hip" and rows[13]["confirmed"]
    rows[8]["codes"], rows[8]["confirmed"] = ["FW1"], True          # new XML code → front width 1
    rows[12]["codes"] = []                                          # WA no longer fills X12
    out = save_x_table(ws, MDL, 2, list(rows.values()))
    assert out["new_codes"] == ["FW1"] and out["catalog_version"] == "1.3.1" and out["mapping_version"]
    again = {r["x"]: r for r in x_table(ws, MDL, 2)}
    assert again[8]["codes"] == ["FW1"] and again[12]["codes"] == [] and again[1]["codes"] == ["FL"]
    _, cat = ws.active("catalog")
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    r8 = next(r for r in st["rules"] if r["target"]["variable"] == "X8")
    assert r8["target"]["x_name"] == "front width 1" and r8["status"] == "confirmed"
    _, prof = ws.active("mapping")
    assert "FW1" in prof["measurements"]["fields"]
    # an order with the new code: the value goes into X8 of the order's model
    sh = 'Abbreviation="SH" Value="49.50"/>'
    txt = read(XML_53).decode("utf-8").replace(sh, sh + '<Type Name="Front w1" Abbreviation="FW1" Value="38.50"/>')
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = ws.import_data(txt.encode(), "Order-9-53-1_2.xml")
    g = order_mdl(ws, o.lines[0]["revision_id"])
    assert g["status"] == "written", g["problems"]
    assert read_xvalues(g["path"], 2)[8].default == 385
    with pytest.raises(ValueError, match="valid XML code"):
        save_x_table(ws, MDL, 2, [{"x": 8, "codes": ["9bad"], "formula": "{M}", "confirmed": True}])
