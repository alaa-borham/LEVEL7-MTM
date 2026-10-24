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
    # the second line's model, identical content once saved, is its own job (second garment)
    with open(p2, "wb") as fh:
        fh.write(read(p1))
    r2 = register_mdl(ws, p2, cut=False)
    assert r2["status"] == "handed_over" and r2["check"].lines[0]["line_number"] == "9-53-2_2"
    # X value changed by hand in GRAFIS: held
    _saved_by_grafis(p2, recalculated=True, x1=1600.0)            # saved again with X1 changed by hand
    r = register_mdl(ws, p2, cut=False)
    assert r["status"] == "needs_review" and any("002/X1" in m for _, m in r["check"].issues)


@needs_mdl
def test_unknown_model_is_unmatched(ws, tmp_path):
    p = tmp_path / "Other.mdl"
    p.write_bytes(read(MDL))
    r = register_mdl(ws, str(p), cut=False)
    assert r["status"] == "unmatched"
    assert os.path.exists(p)                         # a model is never moved away from GRAFIS


def test_a_model_that_cannot_be_read_never_holds_up_the_queue(ws, tmp_path, monkeypatch):
    """A saved model whose check or nesting fails in an unforeseen way is recorded once and leaves the queue (taken
    again and again it held up every model after it); one whose order line was read again meanwhile waits."""
    import json

    from level7mtm.services import mdl_intake
    from level7mtm.services.mdl_intake import next_queued, register_mdl
    from level7mtm.services.workflow import WorkflowError
    p = tmp_path / "A.mdl"
    p.write_text("saved")
    rid = ws.import_file(__import__("conftest").XML_53).lines[0]["revision_id"]
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) VALUES "
                  "(?, ?, 'written', ?, 't', ?)", (rid, "2026-10-24T09:00:00", str(p), json.dumps({"sha256": "x"})))
    import os
    os.utime(p, (os.path.getatime(p), os.path.getmtime(p) - 60))
    assert next_queued(ws) == str(p)                                      # saved by CAD, never looked at

    def boom(ws_, path):
        raise ValueError("strange file")
    monkeypatch.setattr(mdl_intake, "check_mdl", boom)
    out = register_mdl(ws, str(p))
    assert out["status"] == "unmatched" and "strange file" in out["check"].issues[0][1]
    assert next_queued(ws) is None                                        # recorded: out of the queue

    def gone(ws_, path):
        raise WorkflowError("revision 9 not found")
    monkeypatch.setattr(mdl_intake, "check_mdl", gone)
    assert register_mdl(ws, str(p))["status"] == "waiting_for_grafis"


def test_a_model_cad_stopped_on_is_never_nested(ws, tmp_path):
    """CAD (the robot) stopped on an error with a model: what GRAFIS saved is not nested — the order needs
    attention (nested, the error went on and it came back to CAD again and again); once the robot is told to
    try again, the saved copy is taken."""
    import json

    from level7mtm.services.mdl_intake import next_queued
    p = tmp_path / "B.mdl"
    p.write_text("saved after an error")
    rid = ws.import_file(XML_53).lines[0]["revision_id"]
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) VALUES "
                  "(?, ?, 'written', ?, 't', ?)", (rid, "2026-10-24T09:00:00", str(p), json.dumps({"sha256": "x"})))
        c.execute("INSERT INTO robot_jobs(sha256, path, at, status, message) VALUES ('x', ?, '2026-10-24T09:01:00', "
                  "'failed', 'GRAFIS internal error mu5Tset-3 — GRAFIS was closed (Cancel)')", (str(p),))
    os.utime(p, (os.path.getatime(p), os.path.getmtime(p) - 60))
    assert next_queued(ws) is None                                        # not in the nesting queue
    out = register_mdl(ws, str(p))
    assert out["status"] == "cad_error" and out["cutting"] is None
    assert not ws.db.one("SELECT id FROM grafis_results WHERE path=?", (str(p),))
    assert ws.revision(rid)["status"] != "sent_to_nesting"
    ws.db.q("DELETE FROM robot_jobs")                                    # Robot: try again
    assert next_queued(ws) == str(p)


@needs_mdl
def test_a_distorted_model_is_held_before_the_nesting(ws, tmp_path, monkeypatch):
    """Also a model CAD saved without the robot (or before the robot checked): its pattern is checked when it is
    read for the nesting — distorted: Needs attention, into MDL-ERR, never handed over."""
    from level7mtm.services import pattern_check
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "A" / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    p1 = import_and_prepare(ws, XML_53).grafis[0]["path"]
    _saved_by_grafis(p1, recalculated=True)
    monkeypatch.setattr(pattern_check, "check_model", lambda ws_, p, w=None: ["front: its outline crosses itself"])
    r = register_mdl(ws, p1, cut=True)
    assert r["status"] == "needs_review" and r["cutting"] is None
    assert any("pattern distorted in CAD" in m for _, m in r["check"].issues)
    assert not os.path.exists(p1) and (tmp_path / "A" / "MDL-ERR" / os.path.basename(p1)).exists()
    assert ws.revision(r["check"].lines[0]["revision_id"])["status"] != "sent_to_nesting"
