"""Clean the GRAFIS screen: hide its rows (the database history and the audit log are kept) and, if asked,
delete the files those rows produced — order models in the MDL folder and cutting files (PLT / ISO / DXF / L7N)."""
from __future__ import annotations

import json
import os


def files_of_rows(ws) -> list[str]:
    out = []
    for r in ws.db.q("SELECT path FROM grafis_orders WHERE archived=0 AND status='written'"):
        if r["path"]:
            out.append(r["path"])
    for r in ws.db.q("SELECT sha256 FROM grafis_results WHERE archived=0"):
        for cj in ws.db.q("SELECT outputs, info FROM cutting_jobs WHERE sha256=?", (r["sha256"],)):
            out += json.loads(cj["outputs"])
            l7n = json.loads(cj["info"]).get("l7n")
            if l7n:
                out.append(l7n)
    return sorted({p for p in out if p and os.path.isfile(p)})


def clean(ws, delete_files: bool = False) -> dict:
    files = files_of_rows(ws) if delete_files else []
    deleted, failed = [], []
    for p in files:
        try:
            os.remove(p)
            deleted.append(p)
        except OSError as e:                         # e.g. open in GRAFIS
            failed.append(f"{os.path.basename(p)}: {e}")
    with ws.db.tx() as c:
        n1 = c.execute("UPDATE grafis_orders SET archived=1 WHERE archived=0").rowcount
        n2 = c.execute("UPDATE grafis_results SET archived=1 WHERE archived=0").rowcount
        c.execute("DELETE FROM robot_jobs")            # a cleaned order can be run again by the robot
        ws.db.audit(c, ws.actor, "grafis.list_cleaned", "screen", "grafis",
                    {"rows": n1 + n2, "deleted": deleted, "failed": failed})
    return {"rows": n1 + n2, "deleted": deleted, "failed": failed}


def remove_lines(ws, rids: list[int], delete_files: bool = False, reason: str = "removed from the list") -> dict:
    """Remove order lines from the order list (owner: "how do I delete orders"): the order is cancelled (kept in
    the history and the log, never processed again) and its rows are cleaned; with ``delete_files`` its order
    model and cutting files (PLT / ISO / DXF / L7N) are deleted too. Returns {lines, deleted, failed}."""
    files, deleted, failed = [], [], []
    for rid in rids:
        for g in ws.db.q("SELECT path FROM grafis_orders WHERE revision_id=?", (rid,)):
            if g["path"]:
                files.append(g["path"])
        for r in ws.db.q("SELECT sha256, lines FROM grafis_results"):
            if any(ln.get("revision_id") == rid for ln in json.loads(r["lines"])):
                for cj in ws.db.q("SELECT outputs, info FROM cutting_jobs WHERE sha256=?", (r["sha256"],)):
                    files += json.loads(cj["outputs"])
                    l7n = json.loads(cj["info"]).get("l7n")
                    if l7n:
                        files.append(l7n)
    if delete_files:
        for p in sorted({p for p in files if p and os.path.isfile(p)}):
            try:
                os.remove(p)
                deleted.append(p)
            except OSError as e:                         # e.g. open in GRAFIS
                failed.append(f"{os.path.basename(p)}: {e}")
    for rid in rids:
        ws.cancel(rid, reason)
    with ws.db.tx() as c:
        for rid in rids:
            c.execute("UPDATE grafis_orders SET archived=1 WHERE revision_id=?", (rid,))
        ws.db.audit(c, ws.actor, "orders.removed", "revision", ",".join(map(str, rids)),
                    {"deleted": deleted, "failed": failed, "reason": reason})
    return {"lines": len(rids), "deleted": deleted, "failed": failed}
