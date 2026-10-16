"""Order model saved back by GRAFIS (after 'Test Run All Parts') → checked → nesting / cutting."""
import os

from conftest import MDL, XML_53, needs_mdl, read

from level7mtm.grafis.container import parse_container
from level7mtm.grafis.xwriter import build_order_model, rebuild
from level7mtm.services.grafis_orders import import_and_prepare
from level7mtm.services.mdl_intake import register_mdl


def _saved_by_grafis(path, recalculated=True, x1=None):
    """Simulates GRAFIS saving the model: changed piece geometry of front1 (part 7) when recalculated."""
    data = read(path)
    cont = parse_container(data)
    if x1 is not None:
        data = build_order_model(data, {(2, 1): x1})[0]
        cont = parse_container(data)
    if recalculated:
        b = cont.get(3, 7, 7, 1)
        p = bytearray(b.payload)
        p[-1] ^= 0x01
        data = rebuild(data, cont, {b.bid: bytes(p)})
    else:
        b = cont.get(3, 1, 1, 0)                       # GRAFIS rewrote something, but not the pieces
        data = rebuild(data, cont, {b.bid: b.payload + b"\x00" * 8})
    with open(path, "wb") as fh:
        fh.write(data)


@needs_mdl
def test_saved_model_states(ws, tmp_path):
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    out = import_and_prepare(ws, XML_53)
    p1, p2 = (g["path"] for g in out.grafis)
    assert register_mdl(ws, p1, cut=False)["status"] == "waiting_for_grafis"      # still as written
    _saved_by_grafis(p1, recalculated=False)
    r = register_mdl(ws, p1, cut=False)
    assert r["status"] == "needs_review" and any("Test Run All Parts" in m for _, m in r["check"].issues)
    assert (tmp_path / "ER" / "Order-9-53-1_2__9-53-1_2.txt").exists()
    _saved_by_grafis(p1, recalculated=True)
    r = register_mdl(ws, p1, cut=False)
    assert r["status"] == "handed_over", r["check"].issues
    assert r["check"].info["sizes"] == ["base:__HN49_0"] and r["check"].lines[0]["line_number"] == "9-53-1_2"
    rev = ws.revision(r["check"].lines[0]["revision_id"])
    assert rev["status"] == "sent_to_nesting"
    assert register_mdl(ws, p1, cut=False)["status"] == "duplicate"
    # X value changed by hand in GRAFIS: held
    _saved_by_grafis(p2, recalculated=True, x1=1600.0)
    r = register_mdl(ws, p2, cut=False)
    assert r["status"] == "needs_review" and any("002/X1" in m for _, m in r["check"].issues)


@needs_mdl
def test_unknown_model_is_unmatched(ws, tmp_path):
    p = tmp_path / "Other.mdl"
    p.write_bytes(read(MDL))
    r = register_mdl(ws, str(p), cut=False)
    assert r["status"] == "unmatched"
    assert os.path.exists(p)                         # a model is never moved away from GRAFIS
