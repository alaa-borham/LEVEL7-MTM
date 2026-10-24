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
    assert order_mdl(ws, g["revision_id"], force=True)["status"] == "written"     # "write again" button
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


def test_order_model_names_have_no_spaces(ws, tmp_path):
    """'Order-9-50-1_0 - .xml' (as received) must give a model name GRAFIS shows unchanged: no spaces."""
    import shutil
    from conftest import XML_53
    from level7mtm.services.grafis_orders import mdl_name
    odd = tmp_path / "Order-9-53-1_2 - .xml"
    shutil.copy(XML_53, odd)
    ws.import_file(str(odd))
    names = sorted(mdl_name(ws, ws.revision(r["id"])) for r in ws.list_revisions())
    assert names == ["Order-9-53-1_2__9-53-1_2.mdl", "Order-9-53-1_2__9-53-2_2.mdl"]
    assert not any(" " in n for n in names)



def test_order_model_names_drop_invisible_marks(ws, tmp_path):
    """A name typed in Arabic Windows can carry U+200F marks; GRAFIS does not open such a file."""
    import shutil
    from conftest import XML_53
    from level7mtm.services.grafis_orders import mdl_name
    odd = tmp_path / "\u200f\u200fOrder-9-53-1_2\u200e.xml"
    shutil.copy(XML_53, odd)
    ws.import_file(str(odd))
    names = sorted(mdl_name(ws, ws.revision(r["id"])) for r in ws.list_revisions())
    assert names == ["Order-9-53-1_2__9-53-1_2.mdl", "Order-9-53-1_2__9-53-2_2.mdl"]
    assert all(n.isascii() for n in names)


@needs_mdl
def test_codes_on_a_part_the_model_lacks_are_carried_over(ws):
    """A customer copied from another whose main part had another number: his model shows no XML codes; they are
    found on the other part and moved by X name (else X number), an X with codes keeping its own."""
    import copy

    from level7mtm.services.xtable import carry_codes, codes_elsewhere, x_table
    before = {r["x"]: r["codes"] for r in x_table(ws, MDL, 2)}
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    for r in st["rules"]:
        if r["source"]["type"] == "measurement" and r["target"]["piece"] == "002":
            r["target"]["piece"] = "007"
    cat["version"] = "9.0.0"
    ws.set_active("catalog", ws.save_document("catalog", cat, "test"))
    assert all(not r["codes"] for r in x_table(ws, MDL, 2))
    assert codes_elsewhere(ws, MDL) == {"007": sum(1 for c in before.values() if c)}
    out = carry_codes(ws, MDL, "007", 2)
    assert out["catalog_version"] and not out["left"]
    assert {r["x"]: r["codes"] for r in x_table(ws, MDL, 2)} == before
    assert codes_elsewhere(ws, MDL) == {}
    assert carry_codes(ws, MDL, "007", 2)["moved"] == []


@needs_mdl
def test_codes_left_on_a_part_the_model_lacks_are_deleted(ws):
    import copy

    from level7mtm.services.xtable import codes_elsewhere, drop_codes, x_table
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = next(iter(next(iter(cat["products"].values()))["styles"].values()))
    st["rules"] += [dict(copy.deepcopy(r), id=r["id"] + "_old", target=dict(r["target"], piece="009"))
                    for r in st["rules"] if r["source"]["type"] == "measurement"][:3]
    cat["version"] = "9.0.0"
    ws.set_active("catalog", ws.save_document("catalog", cat, "test"))
    before = x_table(ws, MDL, 2)
    assert codes_elsewhere(ws, MDL) == {"009": 3}
    assert drop_codes(ws, "009")["removed"] == 3
    assert codes_elsewhere(ws, MDL) == {} and x_table(ws, MDL, 2) == before     # this model's codes untouched
    assert drop_codes(ws, "009") == {"removed": 0, "catalog_version": None}


@needs_mdl
def test_model_written_again_has_a_new_number_from_the_base_pattern(ws, tmp_path):
    """Owner: a model written again (read again, nested again) comes from the base pattern, never from the copy CAD
    saved, and has the next number; the one before goes to the archive, out of the MDL folder."""
    from level7mtm.services.customers import reread_lines
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = import_and_prepare(ws, XML_53)
    first = o.grafis[0]["path"]
    with open(first, "ab") as fh:
        fh.write(b"saved by CAD")                                        # CAD saved it (another content)
    rid = o.grafis[0]["revision_id"]
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (rid,))   # nested, cutting files made
    outs, skipped = reread_lines(ws, [rid])
    assert not skipped
    again = outs[0].grafis[0]["path"]
    assert os.path.basename(again) == "Order-9-53-1_2__9-53-1_2_v2.mdl"
    assert not os.path.exists(first)                                     # out of the MDL folder …
    from level7mtm.services.grafis_orders import archive_folder
    assert read(os.path.join(archive_folder(ws), os.path.basename(first))).endswith(b"saved by CAD")   # … archived
    assert b"saved by CAD" not in read(again)                            # from the base pattern
    outs, _ = reread_lines(ws, [outs[0].lines[0]["revision_id"]])
    assert os.path.basename(outs[0].grafis[0]["path"]) == "Order-9-53-1_2__9-53-1_2_v3.mdl"


@needs_mdl
def test_a_finished_order_corrected_goes_through_cad_again(ws, tmp_path):
    """Owner: an order nested and cut, then a measurement corrected: its model is written again from the base
    pattern with a new number and the list shows it in CAD again (not the old nesting)."""
    from level7mtm.services.grafis_orders import model_again
    from level7mtm.ui.cad import order_rows
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = import_and_prepare(ws, XML_53)
    rid = o.grafis[0]["revision_id"]
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (rid,))
        c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status, "
                  "handed_over) VALUES ('r1', ?, '2000-01-01T00:00:00', '{}', '[]', ?, '[]', 'handed_over', 1)",
                  (o.grafis[0]["path"], json.dumps([{"revision_id": rid}])))
    ws.edit_measurement(rid, "FL", 1600.0, "customer called")             # allowed although it was cut
    g = model_again(ws, rid)
    assert g["status"] == "written" and g["path"].endswith("_v2.mdl")
    row = next(r for r in order_rows(ws) if r["rid"] == rid)
    assert row["stage"] == "grafis" and row["mdl"] == g["path"]          # in CAD again, not the old result


@needs_mdl
def test_models_written_again_when_the_base_pattern_is_corrected(ws, tmp_path):
    """Owner: the base pattern was corrected (a grain line added) — an order whose model was written before it
    (nesting failed / waiting) gets its model again from the corrected pattern; one cut already is left."""
    import shutil

    from level7mtm.services.grafis_orders import outdated, redo_outdated
    base = tmp_path / "base.mdl"
    shutil.copy(MDL, base)
    ws.db.set_setting("grafis.mdl", str(base))
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = import_and_prepare(ws, XML_53)
    r1, r2 = (g["revision_id"] for g in o.grafis)
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=?", (r1,))   # its nesting failed
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (r2,))         # cut: stays as it is
    assert outdated(ws) == []
    from level7mtm.grafis.xwriter import write_order_mdl
    write_order_mdl(MDL, {(2, 8): 361.0}, str(base))                       # the owner corrects the pattern
    assert outdated(ws) == [r1]
    outs = redo_outdated(ws)
    assert [ln["line_number"] for o_ in outs for ln in o_.lines] == ["9-53-1_2"]
    assert outs[0].grafis[0]["path"].endswith("_v2.mdl")
    assert outdated(ws) == []


@needs_mdl
def test_models_written_again_when_the_parts_change(ws, tmp_path):
    """Owner: "I changed some parts but the orders already sent to CAD and the nesting did not change" — an order
    not cut yet gets its model again with the customer's new parts / rules; one cut already is left."""
    import copy

    from level7mtm.services.grafis_orders import outdated, redo_outdated
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "in"))
    o = import_and_prepare(ws, XML_53)
    r1, r2 = (g["revision_id"] for g in o.grafis)
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=?", (r1,))   # waits for the nesting
        c.execute("UPDATE revisions SET status='completed' WHERE id=?", (r2,))         # cut: stays as it is
    assert outdated(ws) == []
    pk, body = ws.active("catalog")
    body = copy.deepcopy(body)
    body["note"] = "parts changed"                                              # e.g. blend front: 2 copies
    body["version"] = body.get("version", "1.0.0") + ".1"                      # (saved as a new version)
    ws.set_active("catalog", ws.save_document("catalog", body, "parts changed"))
    assert outdated(ws) == [r1]
    outs = redo_outdated(ws)
    assert [ln["line_number"] for o_ in outs for ln in o_.lines] == ["9-53-1_2"]
    assert outdated(ws) == []


def test_models_written_again_a_few_at_a_time_and_each_once(ws, monkeypatch):
    """735 orders at once made the program heavy and the list flicker: 40 per round, the rest in the next rounds;
    an order that could not be written again (its XML gone) is not tried every 3 minutes for the same change."""
    from level7mtm.services import customers, grafis_orders as go
    go._TRIED.clear()
    go._WHY.clear()
    rids = list(range(1, 101))

    def fake_outdated(ws_):
        for r in rids:
            go._WHY.setdefault(r, "patternA:catalog7")
        return list(rids)
    monkeypatch.setattr(go, "outdated", fake_outdated)
    seen = []
    monkeypatch.setattr(customers, "reread_lines", lambda ws_, rs: (seen.append(list(rs)) or [], []))
    for _ in range(4):
        go.redo_outdated(ws)
    assert [len(x) for x in seen] == [40, 40, 20]                 # then nothing: each once
    assert sorted(r for x in seen for r in x) == rids
    go._WHY[5] = "patternA:catalog8"                              # a newer change: that one again
    go.redo_outdated(ws)
    assert seen[-1] == [5]
    go._TRIED.clear()
    go._WHY.clear()
