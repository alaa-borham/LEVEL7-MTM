"""ERP XML files whose every order line has its cutting files go to the "done" folder next to them (owner:
"when the files are finished successfully they move straight to the DONE folder"). A file with a line still
on its way or with an error stays where it is. Only files in the watched ERP folder are moved."""
from __future__ import annotations

import os
import shutil

OPEN = ("superseded", "cancelled")


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def move_finished(ws, revision_ids) -> list[str]:
    """Move the XML of these order lines to <ERP folder>/done when all lines of that XML are completed.
    Returns the new paths."""
    if not ws.db.get_setting("watch.move_done", True):
        return []
    erp = ws.db.get_setting("watch.folder")
    moved = []
    files = {r["import_file"] for r in (ws.db.one("SELECT import_file FROM revisions WHERE id=?", (rid,))
                                        for rid in revision_ids) if r and r["import_file"]}
    for fid in files:
        f = ws.db.one("SELECT id, source_path FROM import_files WHERE id=?", (fid,))
        src = f["source_path"] if f else ""
        if not src or not os.path.isfile(src) or not erp or not _same(os.path.dirname(src), erp):
            continue
        lines = ws.db.q("SELECT status FROM revisions WHERE import_file=? AND status NOT IN (?,?)", (fid,) + OPEN)
        if not lines or any(ln["status"] != "completed" for ln in lines):
            continue                                       # a line still on its way or with an error: stays
        done = next((os.path.join(erp, e) for e in os.listdir(erp)
                     if e.lower() == "done" and os.path.isdir(os.path.join(erp, e))), os.path.join(erp, "done"))
        os.makedirs(done, exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(src))
        target, k = os.path.join(done, stem + ext), 2
        while os.path.exists(target):
            target, k = os.path.join(done, f"{stem}_{k}{ext}"), k + 1
        try:
            shutil.move(src, target)
        except OSError:
            continue                                       # open in another program: tried again next time
        with ws.db.tx() as c:
            c.execute("UPDATE import_files SET source_path=? WHERE id=?", (target, fid))
            ws.db.audit(c, ws.actor, "xml.done", "file", os.path.basename(src), {"to": target})
        moved.append(target)
    return moved
