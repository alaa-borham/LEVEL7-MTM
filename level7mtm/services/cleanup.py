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
