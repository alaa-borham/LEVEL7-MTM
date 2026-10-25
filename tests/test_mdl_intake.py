"""Order model saved back by GRAFIS (after 'Test Run All Parts') → checked → nesting / cutting."""
import os

from conftest import MDL, XML_53, needs_mdl, read

from level7mtm.grafis.container import parse_container
from level7mtm.grafis.xwriter import build_order_model, rebuild
from level7mtm.services.grafis_orders import import_and_prepare
from level7mtm.services.mdl_intake import register_mdl


def _saved_by_grafis(path, recalculated=True, x1=None, only=None, mark=1):
    """Simulates GRAFIS saving the model: changed piece geometry of front1 (part 7) when recalculated."""
    data = read(path)
    cont = parse_container(data)
    if x1 is not None:
        data = build_order_model(data, {(2, 1): x1})[0]
        cont = parse_container(data)
    if recalculated:                                   # Test Run All Parts: every production part recalculated
        from level7mtm.grafis.parts import production
        prod = {n for n, (_, sp) in production(cont).items() if sp}
        changes = {}
        for b in cont.find(3, 7):
            if b.bid[1] in prod and (only is None or b.bid[1] == only):
                p = bytearray(b.payload)
                p[-1] ^= mark
                changes[b.bid] = bytes(p)
        data = rebuild(data, cont, changes)
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


@needs_mdl
def test_distorted_models_leave_the_nesting_queue_ahead_of_their_turn(ws, tmp_path, monkeypatch):
    """Models waiting in the nesting queue are checked ahead of their turn: a distorted one leaves the queue at
    once (also one handed over before the check existed); a sound one is checked once only."""
    from level7mtm.services import pattern_check
    from level7mtm.services.mdl_intake import precheck_queue, queue
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "A" / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    ws.db.set_setting("cutting.auto", True)
    p1, p2 = (g["path"] for g in import_and_prepare(ws, XML_53).grafis)
    _saved_by_grafis(p1, recalculated=True)
    assert register_mdl(ws, p1, cut=False)["status"] == "handed_over"      # nested before the check existed
    _saved_by_grafis(p2, recalculated=True)
    for p in (p1, p2):
        os.utime(p, (os.path.getatime(p), os.path.getmtime(p) - 60))
    monkeypatch.setattr(pattern_check, "_check_model", lambda ws_, p, w=None: (
        pattern_check.LAST.update(text="pattern checked: 1 pieces (info)") or ["back: its outline crosses itself"]))
    if not queue(ws):
        import pytest
        pytest.skip("nesting not enabled in this workspace")
    assert sorted(queue(ws)) == sorted([p1, p2])
    held = precheck_queue(ws, 5)
    assert sorted(held) == sorted([p1, p2]) and queue(ws) == []
    for p in (p1, p2):
        assert (tmp_path / "A" / "MDL-ERR" / os.path.basename(p)).exists()
    rev = ws.db.one("SELECT status FROM revisions WHERE id=(SELECT revision_id FROM grafis_orders WHERE path=?)",
                    (p1,))
    assert rev["status"] != "sent_to_nesting"


def test_several_nestings_at_once_never_take_the_same_model(ws, tmp_path, monkeypatch):
    """nesting.parallel = 2: two models of the queue are claimed for two nestings at the same time, never the
    same one twice; a place is free again when its model is done."""
    from level7mtm.services import mdl_intake
    from level7mtm.services.mdl_intake import claim, intake_now, nesting_now, next_queued, parallel
    paths = [str(tmp_path / f"M{i}.mdl") for i in range(3)]
    monkeypatch.setattr(mdl_intake, "queue", lambda ws_: list(paths))
    assert parallel(ws) == 1 and claim(ws) == [paths[0]]
    assert claim(ws) == [] and next_queued(ws) == paths[1]               # its one place is taken
    mdl_intake._NOW.clear()
    ws.db.set_setting("nesting.parallel", 2)
    a = claim(ws)
    assert a == paths[:2] and claim(ws) == [] and nesting_now() == set(paths[:2])
    assert intake_now()[0] == "M0.mdl"
    monkeypatch.setattr(mdl_intake, "_register_mdl", lambda ws_, p, cut=None: {"status": "cutting_done"})
    mdl_intake.register_mdl(ws, paths[0])                               # done: its place is free
    done = paths.pop(0)                                                 # (and it left the queue)
    assert done not in nesting_now() and claim(ws) == [paths[1]] and nesting_now() == set(paths)
    mdl_intake._NOW.clear()
    ws.db.set_setting("nesting.parallel", 9)
    assert parallel(ws) == 4


def test_nesting_places_follow_the_setting(ws):
    """LEVEL7 Nesting runs as many times at once as set: 2 → the second waits for nobody, the third does."""
    import threading
    import time

    from level7mtm.services.auto_cut import _NESTING
    ws.db.set_setting("nesting.parallel", 2)
    inside, most, lock = [0], [0], threading.Lock()

    def job():
        with _NESTING(ws):
            with lock:
                inside[0] += 1
                most[0] = max(most[0], inside[0])
            time.sleep(0.3)
            with lock:
                inside[0] -= 1
    ts = [threading.Thread(target=job) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert most[0] == 2



@needs_mdl
def test_a_model_test_run_on_some_parts_only_is_held(ws, tmp_path):
    """Owner: "the orders were not test-run on all parts" — a part still exactly as in the base pattern was not run:
    the model is held (Needs attention) with the parts named, never nested."""
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    p1 = import_and_prepare(ws, XML_53).grafis[0]["path"]
    _saved_by_grafis(p1, recalculated=True, only=7)                     # only the active part (front1) was run
    r = register_mdl(ws, p1, cut=False)
    assert r["status"] == "needs_review"
    msg = next(m for _, m in r["check"].issues if "did not run on" in m)
    assert "007" not in msg and "008" in msg                             # front1 run, back1 … not


@needs_mdl
def test_orders_saved_without_test_run_on_all_parts_are_taken_back(ws, tmp_path):
    """Once: an order already nested / ready to cut whose saved model has parts as in the base pattern goes back to
    Needs attention; its cutting files are moved into NOT TEST-RUN (never deleted)."""
    import json

    from level7mtm.services.grafis_orders import in_error
    from level7mtm.services.mdl_intake import hold_untested
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    ws.db.set_setting("errors.folder", str(tmp_path / "ER"))
    g1, g2 = import_and_prepare(ws, XML_53).grafis
    _saved_by_grafis(g1["path"], recalculated=True)                     # all parts run: stays
    _saved_by_grafis(g2["path"], recalculated=True, only=7)              # the active part only
    ok = []
    for g in (g1, g2):
        from level7mtm.services import mdl_intake
        chk, _ = mdl_intake.check_mdl(ws, g["path"])
        chk.issues = []                                                  # as nested before the strict check
        with ws.db.tx() as c:
            c.execute("INSERT INTO grafis_results(checked_at, info, pieces, lines, issues, status, handed_over, "
                      "nesting_path, sha256, path) VALUES ('2999-01-01', '{}', '[]', ?, '[]', 'handed_over', 1, ?, ?, ?)",
                      (json.dumps(chk.lines), g["path"], chk.sha256, g["path"]))
        cut = tmp_path / "CUT" / (os.path.basename(g["path"]) + ".plt")
        cut.parent.mkdir(exist_ok=True)
        cut.write_text("plt")
        with ws.db.tx() as c:
            c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) VALUES "
                      "(?, ?, '2999-01-01', 'done', ?, '{}', '')", (chk.sha256, g["path"], json.dumps([str(cut)])))
            c.execute("UPDATE revisions SET status='completed' WHERE id=?", (g["revision_id"],))
        ok.append(cut)
    held = hold_untested(ws)
    assert held == ["9-53-2_2"]
    assert ok[0].exists() and not ok[1].exists() and (ok[1].parent / "NOT TEST-RUN" / ok[1].name).exists()
    assert ws.revision(g2["revision_id"])["status"] == "ready" and g2["revision_id"] in in_error(ws)
    assert ws.revision(g1["revision_id"])["status"] == "completed"
