"""A distorted pattern (e.g. the back with a spike) is never cut: the model goes to MDL-ERR with the reason."""
import json
import math
import os
import zipfile

from level7mtm.services import pattern_check as pc


def _curve(n=200, w=600, h=900):
    """A smooth piece with many points (as GRAFIS outlines are)."""
    return [(w / 2 + w / 2 * math.cos(2 * math.pi * k / n), h / 2 + h / 2 * math.sin(2 * math.pi * k / n))
            for k in range(n)]


def test_sound_pieces_pass():
    assert pc.defects("rect", [(0, 0), (500, 0), (500, 800), (0, 800)]) == []
    assert pc.defects("round", _curve()) == []
    collar = [(0, 0), (420, 0), (420, 80), (40, 260)]                    # a collar point of about 55°
    assert pc.defects("collar", collar) == []
    notch = [(0, 0), (200, 0), (200, 400), (105, 400), (100, 392), (95, 400), (0, 400)]   # a nick: short
    assert pc.defects("notch", notch) == []


def test_spike_slit_and_folded_outline_are_found():
    spike = [(0, 0), (300, 0), (300, 400), (170, 400), (150, 900), (130, 400), (0, 400)]   # out of the piece
    assert any("needle" in d for d in pc.defects("back", spike))
    wedge = [(0, 0), (300, 0), (300, 600), (175, 600), (150, 120), (125, 600), (0, 600)]  # cut into it, opening
    assert any("needle" in d for d in pc.defects("back", wedge))
    slit = [(0, 0), (300, 0), (300, 600), (150.5, 600), (150.5, 290), (150, 289), (149.5, 290), (149.5, 600),
            (0, 600)]                                                    # the jabzour slit of a front: a cut line
    assert pc.defects("front", slit) == []
    # the deformed back of 10041-44499-1_0: the side line folds back into a hook of about 26°
    hook = [(0, 0), (800, 0), (800, 1500), (540, 1500), (600, 1000), (380, 1300), (240, 1500), (0, 1500)]
    assert any("needle" in d for d in pc.defects("back", hook))
    bow = [(0, 0), (400, 300), (400, 0), (0, 500)]                        # folded over itself
    assert any("crosses itself" in d for d in pc.defects("back", bow))
    assert any("no area" in d for d in pc.defects("tiny", [(0, 0), (5, 0), (5, 5)]))


def test_marker_checked_and_model_moved_to_mdl_err(ws, tmp_path):
    l7n = tmp_path / "m.l7n"
    doc = {"project": {"pieces": [{"uid": "a", "name": "back", "sizes": {"s": {"cut": [[0, 0], [300, 0], [300, 400],
                                                                                    [170, 400], [150, 900],
                                                                                    [130, 400], [0, 400]]}}},
                                  {"uid": "b", "name": "front", "sizes": {"s": {"cut": [[0, 0], [500, 0],
                                                                                     [500, 800], [0, 800]]}}}],
                       "markers": [{"placements": [{"piece_uid": "a", "size": "s"}, {"piece_uid": "b", "size": "s"},
                                                   {"piece_uid": "a", "size": "s", "mirrored": True}]}]}}
    with zipfile.ZipFile(l7n, "w") as z:
        z.writestr("project.json", json.dumps(doc))
    bad = pc.check_marker(str(l7n))
    assert len(bad) == 1 and bad[0].startswith("back:")                  # each piece once; front is sound
    ws.db.set_setting("grafis.input", str(tmp_path / "A" / "MDL"))
    m = tmp_path / "A" / "MDL" / "Order-1.mdl"
    m.parent.mkdir(parents=True)
    m.write_bytes(b"model")
    moved = pc.to_err(ws, str(m), bad)
    assert moved == str(tmp_path / "A" / "MDL-ERR" / "Order-1.mdl") and not m.exists()
    assert "back" in (tmp_path / "A" / "MDL-ERR" / "Order-1.txt").read_text(encoding="utf-8-sig")
    assert pc.check_marker(str(tmp_path / "missing.l7n")) == []


def test_many_points_checked_quickly():
    import time
    t = time.time()
    for _ in range(20):
        pc.defects("round", _curve(1500))
    assert time.time() - t < 10


def test_model_checked_as_soon_as_cad_saved_it(ws, tmp_path, monkeypatch):
    """The pattern as CAD saved it is checked before it waits for the nesting: the pieces from info --json when
    LEVEL7 Nesting gives them, else from a quick marker made for the check only."""
    from level7mtm.services import auto_cut
    m = tmp_path / "Order-2.mdl"
    m.write_bytes(b"model")
    spike = [[0, 0], [300, 0], [300, 400], [170, 400], [150, 900], [130, 400], [0, 400]]
    monkeypatch.setattr(auto_cut, "nesting_command", lambda ws_: (["cli"], {}))
    monkeypatch.setattr(auto_cut, "_info", lambda c, e, p: {"pieces": [{"name": "front", "sizes": {"s": {"cut": spike}}}]})
    assert pc.check_model(ws, str(m))[0].startswith("front:")
    ran = []

    def nest(cmd, env, log, timeout=0):
        ran.append(cmd)
        doc = {"project": {"pieces": [{"uid": "a", "name": "back", "sizes": {"s": {"cut": spike}}}],
                           "markers": [{"placements": [{"piece_uid": "a", "size": "s"}]}]}}
        with zipfile.ZipFile(cmd[cmd.index("-o") + 1], "w") as z:
            z.writestr("project.json", json.dumps(doc))
        return 0
    monkeypatch.setattr(auto_cut, "_info", lambda c, e, p: {"pieces": [{"name": "back"}]})   # no outlines
    monkeypatch.setattr(auto_cut, "_run", nest)
    assert pc.check_model(ws, str(m), 1450)[0].startswith("back:")
    assert ran and "--time" in ran[0] and "1450" in ran[0] and not os.path.exists(ran[0][ran[0].index("-o") + 1])
    ws.db.set_setting("cutting.pattern_check", False)
    assert pc.check_model(ws, str(m)) == []


def test_robot_sends_a_distorted_model_to_needs_attention(ws, tmp_path, monkeypatch):
    """CAD saved a distorted pattern: the robot records it as a CAD failure (Needs attention, out of the nesting
    queue) and the model goes to MDL-ERR."""
    import hashlib

    from level7mtm.services import grafis_robot as robot
    from level7mtm.services.mdl_intake import next_queued

    from test_grafis_robot import FakeGrafis
    ws.db.set_setting("grafis.input", str(tmp_path / "A" / "MDL"))
    ws.db.set_setting("robot.run_wait_s", 0)
    ws.db.set_setting("robot.buttons", {"test_run": {"title": "GRAFIS", "cls": "Toolbar", "dx": 470, "dy": 60}})
    m = tmp_path / "A" / "MDL" / "Order-3.mdl"
    m.parent.mkdir(parents=True)
    m.write_bytes(b"x")
    monkeypatch.setattr(pc, "check_model", lambda ws_, p, w=None: ["front: its outline crosses itself"])
    r = robot.run_one(ws, {"path": str(m), "sha256": hashlib.sha256(b"x").hexdigest()}, FakeGrafis(),
                      sleep=lambda s: None, clock=__import__("itertools").count().__next__)
    assert r["status"] == "failed" and "pattern distorted in CAD" in r["message"], r
    assert not m.exists() and (tmp_path / "A" / "MDL-ERR" / "Order-3.mdl").exists()
    assert next_queued(ws) is None
    from level7mtm.ui.i18n import set_language, tr_msg
    set_language("ar")
    assert "MDL-ERR" in tr_msg(r["message"])
    set_language("en")
