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
    err = tmp_path / "MDL-ERR" / os.path.basename(p1)                    # owner: the model to MDL-ERR …
    assert err.exists() and not os.path.exists(p1)
    assert (tmp_path / "MDL-ERR" / (os.path.splitext(os.path.basename(p1))[0] + ".txt")).exists()   # … with why
    os.replace(err, p1)                                                  # corrected in CAD, put back
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
    ws.db.set_setting("cutting.pattern_check", False)
    assert register_mdl(ws, p1, cut=False)["status"] == "handed_over"      # nested before the check existed
    ws.db.set_setting("cutting.pattern_check", True)
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


_FAKE_CLI = r'''#!/usr/bin/env python3
"""LEVEL7 Nesting stand-in that nests several models in one marker (nest … --with FILE SIZE=QTY)."""
import json, sys
a = sys.argv[1:]
open(sys.argv[0] + ".calls", "a").write(json.dumps(a) + "\n")
if a[:2] == ["nest", "--help"]:
    print("--width --sizes --time --spacing --with FILE SIZE=QTY")
    sys.exit(0)
if a[0] == "info":
    print(json.dumps({"size_names": ["S1"], "features": []}))
    sys.exit(0)
out = a[a.index("-o") + 1]
open(out, "w").write("marker")
if a[0] == "nest":
    print(json.dumps({"length_mm": 2500.0, "efficiency_total_width_pct": 81.5, "placed": 40}))
sys.exit(0)
'''


def _fake_nesting(ws, tmp_path):
    cli = tmp_path / "level7-cli"
    cli.write_text(_FAKE_CLI)
    cli.chmod(0o755)
    ws.db.set_setting("nesting.cli", str(cli))
    ws.db.set_setting("cutting.output", str(tmp_path / "CUT"))
    ws.db.set_setting("cutting.pattern_check", False)
    from level7mtm.services import auto_cut
    auto_cut._NEST_FLAGS.clear()
    return str(cli) + ".calls"


@needs_mdl
def test_orders_of_the_same_fabric_in_one_marker_when_the_customer_has_it_on(ws, tmp_path):
    """Owner: small orders of one customer on the same fabric nested together — on / off for each customer."""
    import json

    from level7mtm.services.auto_cut import combine_settings, set_combine
    from level7mtm.services.mdl_intake import fabric_key, queue
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    calls = _fake_nesting(ws, tmp_path)
    out = import_and_prepare(ws, XML_53)
    p1, p2 = (g["path"] for g in out.grafis)
    _saved_by_grafis(p1)
    _saved_by_grafis(p2, mark=2)
    os.utime(p1, (1, 1))
    os.utime(p2, (1, 1))
    assert fabric_key(ws, p1) == fabric_key(ws, p2) is not None          # same customer, fabric 100, colour, width
    assert combine_settings(ws, "level7")["on"] is False                  # off unless switched on
    set_combine(ws, "level7", True, 6)
    assert set(queue(ws)) == {p1, p2}
    r = register_mdl(ws, p1)
    assert r["status"] == "cutting_done" and r["combined"] == [p2], r
    nest = next(json.loads(ln) for ln in open(calls) if json.loads(ln)[0] == "nest" and "--width" in ln)
    assert nest[nest.index("--with") + 1] == p2 and nest[nest.index("--with") + 2] == "S1=1"
    assert nest[nest.index("--sizes") + 1] == "S1=1"
    cut = r["cutting"]["outputs"]
    assert cut and all(" +1." in os.path.basename(o) for o in cut)       # one marker, named for both
    assert queue(ws) == []                                                # both out of the queue
    done = {ws.revision(ln["revision_id"])["status"] for ln in out.lines}
    assert done == {"completed"}
    assert not os.path.exists(p2)                                         # the other model to MDL-done too


@needs_mdl
def test_no_combining_when_off_or_no_fabric_number(ws, tmp_path):
    import json

    from level7mtm.services.auto_cut import set_combine
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    calls = _fake_nesting(ws, tmp_path)
    out = import_and_prepare(ws, XML_53)
    p1, p2 = (g["path"] for g in out.grafis)
    _saved_by_grafis(p1)
    _saved_by_grafis(p2, mark=2)
    os.utime(p1, (1, 1))
    os.utime(p2, (1, 1))
    set_combine(ws, "level7", False)
    r = register_mdl(ws, p1)
    assert r["status"] == "cutting_done" and "combined" not in r
    from level7mtm.services.mdl_intake import queue
    assert queue(ws) == [p2], queue(ws)                                   # the one nested is out of the queue
    assert not any("--with" in json.loads(ln) for ln in open(calls) if json.loads(ln)[:2] != ["nest", "--help"])
    set_combine(ws, "level7", True)
    rid = next(ln["revision_id"] for ln in out.lines if ln["line_number"] == "9-53-2_2")
    raw = json.loads(ws.db.one("SELECT raw_line FROM revisions WHERE id=?", (rid,))["raw_line"])
    raw["fabric_number"] = ""
    with ws.db.tx() as c:
        c.execute("UPDATE revisions SET raw_line=? WHERE id=?", (json.dumps(raw), rid))
    from level7mtm.services.mdl_intake import fabric_key
    assert fabric_key(ws, p2) is None                                     # never guessed


@needs_mdl
def test_an_older_level7_nesting_nests_each_order_alone(ws, tmp_path, monkeypatch):
    from level7mtm.services import auto_cut
    from level7mtm.services.auto_cut import set_combine
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    _fake_nesting(ws, tmp_path)
    monkeypatch.setattr(auto_cut, "_nest_flags", lambda cmd, env: "--width --sizes --time")   # no --with
    out = import_and_prepare(ws, XML_53)
    p1, p2 = (g["path"] for g in out.grafis)
    _saved_by_grafis(p1)
    _saved_by_grafis(p2, mark=2)
    os.utime(p1, (1, 1))
    os.utime(p2, (1, 1))
    set_combine(ws, "level7", True)
    r = register_mdl(ws, p1)
    assert r["status"] == "cutting_done" and "combined" not in r and os.path.exists(p2)


@needs_mdl
def test_two_orders_in_one_marker_with_the_real_level7_nesting(ws, tmp_path, monkeypatch):
    """End to end with LEVEL7 Nesting itself (when it knows --with): one marker, the garments of both orders."""
    import subprocess
    import sys

    import pytest

    from level7mtm.services import auto_cut
    from level7mtm.services.auto_cut import set_combine
    src = os.environ.get("LEVEL7_NESTING_SRC") or os.path.join(os.path.dirname(__file__), "..", "..",
                                                               "level7-nesting")
    if not os.path.isdir(os.path.join(src, "level7")):
        pytest.skip("LEVEL7 Nesting source not here")
    env = dict(os.environ, PYTHONPATH=os.path.abspath(src))
    helptext = subprocess.run([sys.executable, "-m", "level7.cli", "nest", "--help"], env=env, capture_output=True,
                              text=True).stdout
    if "--with" not in helptext:
        pytest.skip("this LEVEL7 Nesting does not nest several models in one marker yet")
    monkeypatch.setenv("LEVEL7_NESTING_SRC", os.path.abspath(src))
    auto_cut._NEST_FLAGS.clear()
    ws.db.set_setting("grafis.mdl", MDL)
    ws.db.set_setting("grafis.input", str(tmp_path / "MDL"))
    ws.db.set_setting("cutting.output", str(tmp_path / "CUT"))
    ws.db.set_setting("cutting.time_s", 5)
    ws.db.set_setting("cutting.pattern_check", False)
    out = import_and_prepare(ws, XML_53)
    p1, p2 = (g["path"] for g in out.grafis)
    _saved_by_grafis(p1)
    _saved_by_grafis(p2, mark=2)
    os.utime(p1, (1, 1))
    os.utime(p2, (1, 1))
    set_combine(ws, "level7", True)
    r = register_mdl(ws, p1)
    first = ws.db.one("SELECT info, log FROM cutting_jobs ORDER BY id LIMIT 1")
    assert r["status"] == "cutting_done" and r.get("combined") == [p2], first["info"] + first["log"][-1500:]
    cut = r["cutting"]
    assert cut["garments"] == 2 and cut["placed"] and cut["placed"] % 2 == 0      # both orders on one marker
    plan = cut.get("plan")                                # the printed plan: which piece is whose (ISO: no text)
    assert plan and plan.endswith(" - PLAN.pdf") and open(plan, "rb").read(5) == b"%PDF-", cut.get("plan_error")
    from level7mtm.export.marker_plan import read_plan
    assert {p["order"] for p in read_plan(cut["l7n"])["pieces"]} == {"9-53-1_2", "9-53-2_2"}
    assert {ws.revision(ln["revision_id"])["status"] for ln in out.lines} == {"completed"}
