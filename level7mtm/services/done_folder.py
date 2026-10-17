"""ERP XML files go to folders next to them (owner): every order line has its cutting files -> "done"
("when the files are finished successfully they move straight to the DONE folder"); a line with an error ->
"ER" ("if error it goes to ER"), with the reason in a .txt beside it. A file in ER whose lines are later all
finished (after "try again") goes on to done. Only files of the watched ERP folder (or its ER) are moved."""
from __future__ import annotations

import os
import shutil

OPEN = ("superseded", "cancelled")


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _sub(erp: str, name: str) -> str:
    """The subfolder of the ERP folder, whatever its case on disk (done / DONE, ER / er); created if missing."""
    d = next((os.path.join(erp, e) for e in os.listdir(erp)
              if e.lower() == name.lower() and os.path.isdir(os.path.join(erp, e))), os.path.join(erp, name))
    os.makedirs(d, exist_ok=True)
    return d


def _in_erp(src: str, erp: str) -> bool:
    d = os.path.dirname(src)
    return _same(d, erp) or (os.path.basename(d).lower() == "er" and _same(os.path.dirname(d), erp))


def _move(ws, fid, src: str, folder: str, action: str) -> str | None:
    stem, ext = os.path.splitext(os.path.basename(src))
    target, k = os.path.join(folder, stem + ext), 2
    while os.path.exists(target):
        target, k = os.path.join(folder, f"{stem}_{k}{ext}"), k + 1
    try:
        shutil.move(src, target)
    except OSError:
        return None                                        # open in another program: tried again next time
    with ws.db.tx() as c:
        if fid is not None:
            c.execute("UPDATE import_files SET source_path=? WHERE id=?", (target, fid))
        ws.db.audit(c, ws.actor, action, "file", os.path.basename(src), {"to": target})
    return target


def move_xml_to_er(ws, src: str, problems: list[str], kind: str, fid=None) -> str | None:
    """An XML of the ERP folder that failed: into <ERP>/ER with <name>.txt saying why."""
    from ..storage.db import now
    erp = ws.db.get_setting("watch.folder")
    if not ws.db.get_setting("watch.move_done", True) or not erp or not src or not os.path.isfile(src) \
            or not _in_erp(src, erp):
        return None
    er = _sub(erp, "ER")
    stem = os.path.splitext(os.path.basename(src))[0]
    try:
        with open(os.path.join(er, stem + ".txt"), "w", encoding="utf-8-sig") as fh:
            fh.write(f"LEVEL7 MTM — {kind}\n{now()}\n\nالسبب / reason:\n")
            for pr in problems or ["(no detail)"]:
                fh.write(f"- {pr}\n")
    except OSError:
        pass
    if _same(os.path.dirname(src), er):
        return src                                         # already there (the reason is updated)
    return _move(ws, fid, src, er, "xml.error")


def move_failed(ws, revision_ids, problems: list[str], kind: str) -> list[str]:
    """The XML of these order lines into <ERP>/ER (one of its lines has an error)."""
    out = []
    files = {r["import_file"] for r in (ws.db.one("SELECT import_file FROM revisions WHERE id=?", (rid,))
                                        for rid in revision_ids) if r and r["import_file"]}
    for fid in files:
        f = ws.db.one("SELECT id, source_path FROM import_files WHERE id=?", (fid,))
        t = move_xml_to_er(ws, f["source_path"] if f else "", problems, kind, fid)
        if t:
            out.append(t)
    return out


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
        if not src or not os.path.isfile(src) or not erp or not _in_erp(src, erp):
            continue
        lines = ws.db.q("SELECT status FROM revisions WHERE import_file=? AND status NOT IN (?,?)", (fid,) + OPEN)
        if not lines or any(ln["status"] != "completed" for ln in lines):
            continue                                       # a line still on its way or with an error: stays
        target = _move(ws, fid, src, _sub(erp, "done"), "xml.done")
        if target:
            reason = os.path.join(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0] + ".txt")
            if os.path.basename(os.path.dirname(src)).lower() == "er" and os.path.isfile(reason):
                try:
                    os.remove(reason)                      # the error is over
                except OSError:
                    pass
            moved.append(target)
    return moved
