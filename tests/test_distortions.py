"""The distortion log: which piece / option / pattern is distorted most (fix the model once in GRAFIS)."""
import json

from conftest import XML_53, read

from level7mtm.services.distortions import defects, distortion_log
from level7mtm.storage.db import now


def test_defects_in_a_message():
    msg = ("pattern distorted in CAD, not nested: back: its outline crosses itself; front1: 2 needle tip(s) — a "
           "spike or a slit in the outline; sleeve: its outline is far from the base pattern (solidity 0.70, base "
           "0.94; roundness 0.40, base 0.54)")
    assert defects(msg) == [("back", "crosses"), ("front1", "needle"), ("sleeve", "shape")]
    assert defects("pattern distorted, no cutting file: collar: the piece has (almost) no area") == \
        [("collar", "no_area")]
    assert defects("GRAFIS did not answer") == []


def _model(ws, rid, name, template="C:/P/Slim.mdl"):
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, info) VALUES (?,?,?,?,?)",
                  (rid, now(), "written", f"C:/MDL/{name}", json.dumps({"template": template})))


def test_the_log_counts_each_distorted_piece_of_an_order_once(ws):
    o = ws.import_data(read(XML_53), "Order-9-53-1_2.xml")
    r1, r2 = (ln["revision_id"] for ln in o.lines)
    _model(ws, r1, "A.mdl")
    _model(ws, r2, "B.mdl")
    with ws.db.tx() as c:                     # A: found by AI TOOL and again by the marker check — counted once
        c.execute("INSERT INTO robot_jobs(sha256, path, at, status, message) VALUES ('x', 'C:/MDL/A.mdl', ?, "
                  "'failed', 'pattern distorted in CAD, not nested: back: its outline crosses itself')", (now(),))
        c.execute("INSERT INTO cutting_jobs(sha256, scb_path, created_at, status, outputs, info, log) VALUES "
                  "('y', 'C:/MDL-ERR/A.mdl', ?, 'failed', '[]', ?, '')",
                  (now(), json.dumps({"pattern_errors": ["back: its outline crosses itself"]})))
        c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status) VALUES "
                  "('z', 'C:/MDL/B.mdl', ?, '{}', '[]', '[]', ?, 'needs_review')",
                  (now(), json.dumps([["blocking", "pattern distorted in CAD, not nested: back: 1 needle tip(s) — a "
                                                   "spike; front1: its outline crosses itself"]])))
    log = distortion_log(ws, 30)
    assert log["orders"] == 2 and log["checked"] == 2
    back = next(p for p in log["pieces"] if p["piece"] == "back")
    assert back["orders"] == 2 and back["kinds"] == {"crosses": 1, "needle": 1}
    assert log["pieces"][0]["piece"] == "back"                          # the most distorted first
    assert log["patterns"] == [{"pattern": "Slim.mdl", "orders": 2}]
    assert all(o["rate"] == 1.0 for o in log["options"])                # both orders made: both distorted
    assert distortion_log(ws, 30, cid="someone else")["orders"] == 0
