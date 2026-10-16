import json
import os

import pytest
from conftest import XML_52, XML_53, approved_setup, read, use_model

from level7mtm.services.workflow import WorkflowError
from level7mtm.storage.db import Database, restore_backup


def _rev(ws, number):
    return next(r for r in ws.list_revisions(True) if r["line_number"] == number)


def test_import_and_duplicate(ws):
    a = ws.import_file(XML_52)
    assert a.status == "imported" and [x["action"] for x in a.lines] == ["new", "new"]
    b = ws.import_file(XML_52)
    assert b.status == "duplicate" and not b.lines
    assert len(ws.list_revisions()) == 2                    # no duplicate production orders


def test_update_creates_new_revision(ws):
    ws.import_file(XML_53)                                   # revision 2
    data = read(XML_53).replace(b"9-53-1_2", b"9-53-1_3").replace(b"9-53-2_2", b"9-53-2_3")
    data = data.replace(b'Abbreviation="CH" Value="69.00"', b'Abbreviation="CH" Value="70.00"', 1)
    out = ws.import_data(data, "Order-9-53-1_3.xml")
    assert [x["action"] for x in out.lines] == ["new_revision", "new_revision"]
    assert _rev(ws, "9-53-1_2")["status"] == "superseded"
    assert _rev(ws, "9-53-1_3")["status"] == "ready"
    # an older revision arriving late never replaces the newer one
    old = read(XML_53).replace(b"9-53-1_2", b"9-53-1_1").replace(b"9-53-2_2", b"9-53-2_1")
    out = ws.import_data(old, "late.xml")
    assert out.lines[0]["action"] == "older_revision" and _rev(ws, "9-53-1_3")["status"] == "ready"


def test_same_revision_different_content_is_conflict(ws):
    ws.import_file(XML_53)
    data = read(XML_53).replace(b'Abbreviation="CH" Value="69.00"', b'Abbreviation="CH" Value="71.00"', 1)
    out = ws.import_data(data, "changed-without-revision.xml")
    assert out.lines[0]["action"] == "conflict"
    rev = ws.revision(_rev(ws, "9-53-1_2")["id"])
    assert rev["normalized"]["measurements"]["CH"]["value_mm"] == 690.0       # stored data not overwritten


def test_failed_file_recorded(ws):
    out = ws.import_data(b"<OrderMaster", "broken.xml")
    assert out.status == "failed"
    assert ws.db.one("SELECT status FROM import_files")["status"] == "failed"


def test_unknown_option_needs_review(ws):
    data = read(XML_52).replace(b'Variation Name="Jabzour Splender"', b'Variation Name="Jabzour X"', 1)
    ws.import_data(data, "x.xml")
    rid = _rev(ws, "9-52-1_0")["id"]
    assert ws.revision(rid)["status"] == "needs_review"


def test_generate_and_reproduce(ws):
    ws.import_file(XML_53)
    rid = _rev(ws, "9-53-1_2")["id"]
    g = ws.generate(rid)
    assert g["status"] == "ok" and ws.revision(rid)["status"] == "ready_for_review"
    same, fp, stored = ws.regenerate_and_compare(g["id"])
    assert same and fp == stored


def test_change_after_generation_invalidates(ws):
    ws.import_file(XML_53)
    rid = _rev(ws, "9-53-1_2")["id"]
    g = ws.generate(rid)
    ws.edit_measurement(rid, "CH", 700.0, "customer called")
    assert ws.generation(g["id"])["status"] == "invalidated"
    assert ws.revision(rid)["normalized"]["measurements"]["CH"]["manual"]["old_mm"] == 690.0
    assert any(a["action"] == "measurement.edited" for a in ws.audit_log())


def test_engine_test_model_cannot_be_approved(ws):
    ws.import_file(XML_53)
    rid = _rev(ws, "9-53-1_2")["id"]
    g = ws.generate(rid)
    blockers = ws.approval_blockers(g["id"])
    assert any("engine_test" in b for b in blockers)
    with pytest.raises(WorkflowError):
        ws.approve(g["id"])
    with pytest.raises(WorkflowError):
        ws.send_to_nesting([rid])


def test_approval_path_and_nesting(ws, tmp_path):
    approved_setup(ws)
    ws.import_file(XML_53)
    r1, r2 = _rev(ws, "9-53-1_2")["id"], _rev(ws, "9-53-2_2")["id"]
    g1 = ws.generate(r1)
    # the approved test copy still reports the undefined sleeve-cap rule -> blocked until reviewed
    assert any("cap" in b for b in ws.approval_blockers(g1["id"]))
    m = ws.document(ws.active("model")[0])
    m = json.loads(json.dumps(m))
    m["version"] = "0.1.0+test-approved-nocap"
    m["checks"] = [c for c in m["checks"] if c["id"] != "cap_vs_armhole"]
    use_model(ws, m)
    g1, g2 = ws.generate(r1), ws.generate(r2)
    assert ws.approval_blockers(g1["id"]) == []
    ws.approve(g1["id"], "test")
    ws.approve(g2["id"], "test")
    job = ws.send_to_nesting([r1, r2], str(tmp_path / "m.l7n"))
    assert os.path.exists(job["path"]) and ws.revision(r1)["status"] == "sent_to_nesting"
    again = ws.send_to_nesting([r1, r2])
    assert again["duplicate"] and again["job_id"] == job["job_id"]     # no duplicate production job
    c = job["contract"]
    assert {o["nesting_size"] for o in c["orders"]} == {"thobe-engine-test:9-53-1_2", "thobe-engine-test:9-53-2_2"}
    sleeve = next(p for p in c["pieces"] if p["piece_uid"] == "9-53-1_2:sleeve")
    assert (sleeve["copies_per_garment"], sleeve["mirrored_per_garment"], sleeve["total_copies"]) == (1, 1, 2)
    ws.set_job_status(job["job_id"], "done", "NEST-1")
    assert ws.revision(r1)["status"] == "completed"


def test_new_model_version_does_not_change_old_generation(ws):
    ws.import_file(XML_53)
    rid = _rev(ws, "9-53-1_2")["id"]
    g = ws.generate(rid)
    m = json.loads(json.dumps(ws.document(ws.active("model")[0])))
    m["version"] = "0.2.0"
    m["variables"]["ease_q"]["formula"] = "30"
    use_model(ws, m)
    assert ws.regenerate_and_compare(g["id"])[0]                    # old result reproduces with its own version
    g2 = ws.generate(rid)
    assert g2["fingerprint"] != g["fingerprint"]
    with pytest.raises(WorkflowError):                              # versions are immutable
        m["variables"]["ease_q"]["formula"] = "31"
        ws.save_document("model", m)


def test_cancelled_order_not_sent(ws):
    ws.import_file(XML_53)
    rid = _rev(ws, "9-53-1_2")["id"]
    ws.generate(rid)
    ws.cancel(rid, "customer cancelled")
    with pytest.raises(WorkflowError):
        ws.send_to_nesting([rid], test_only=True)
    with pytest.raises(WorkflowError):
        ws.generate(rid)


def test_backup_restore(ws, tmp_path):
    ws.import_file(XML_52)
    b = ws.db.backup(str(tmp_path / "bk"))
    ws.import_file(XML_53)
    path = ws.db.path
    ws.db.close()
    keep = restore_backup(b, path)
    assert os.path.exists(keep)
    db = Database(path)
    assert db.one("SELECT COUNT(*) n FROM revisions")["n"] == 2
    db.close()


def test_schema_newer_than_program(tmp_path):
    import sqlite3
    p = str(tmp_path / "x.db")
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version = 99")
    c.close()
    with pytest.raises(RuntimeError):
        Database(p)
