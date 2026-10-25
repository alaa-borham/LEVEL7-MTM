"""ERP XML files go to folders next to them (owner): read from "process", every order line has its cutting files
-> "done", an error -> "error" (process / done / error side by side; an older ERP folder keeps done and ER inside).
Every order line has its cutting files -> "done"
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


def _is_process(erp: str) -> bool:
    """The owner's layout: the folder read from is called "process" — also with a prefix ("XML-process")."""
    return os.path.basename(os.path.normpath(erp)).lower().endswith("process")


def _prefix(erp: str) -> str:
    base = os.path.basename(os.path.normpath(erp))
    return base[:len(base) - len("process")]


def _sibling(parent: str, names: list[str]) -> str:
    """The first of these folders beside process that exists (any case), else the first one (created)."""
    have = {e.lower(): e for e in os.listdir(parent) if os.path.isdir(os.path.join(parent, e))} \
        if os.path.isdir(parent) else {}
    for n in names:
        if n.lower() in have:
            return os.path.join(parent, have[n.lower()])
    d = os.path.join(parent, names[0])
    os.makedirs(d, exist_ok=True)
    return d


def _chosen(ws, key: str) -> str | None:
    """A folder the owner chose in the settings (owner: "read from — and move to — the folders I set")."""
    d = ws.db.get_setting(key) if ws is not None else None
    if d:
        os.makedirs(d, exist_ok=True)
        return d
    return None


def xml_dirs(erp: str, ws=None) -> tuple[str, str]:
    """(done, error) folders of the ERP XML files. The owner's layout: process (read from) / done / error side by
    side, with the same prefix (XML-process / XML-done / XML-error); else (older layout) done and ER inside the
    watched folder. A folder chosen in the settings (watch.done / watch.error) always comes first."""
    done, err = _chosen(ws, "watch.done"), _chosen(ws, "watch.error")
    if done and err:
        return done, err
    if _is_process(erp):
        parent, pre = os.path.dirname(os.path.normpath(erp)), _prefix(erp)
        return done or _sibling(parent, [pre + "done"]), err or _sibling(parent, [pre + "error", pre + "ER"])
    return done or _sub(erp, "done"), err or _sub(erp, "ER")


def deleted_dir(erp: str, ws=None) -> str:
    """Where the XML of deleted orders goes: the folder chosen (watch.deleted), else beside process with the same
    prefix (XML-deleted)."""
    chosen = _chosen(ws, "watch.deleted")
    if chosen:
        return chosen
    if _is_process(erp):
        parent, pre = os.path.dirname(os.path.normpath(erp)), _prefix(erp)
        return _sibling(parent, [pre + "deleted"])
    return _sub(erp, "deleted")


def _in_erp(src: str, erp: str, ws=None) -> bool:
    d = os.path.dirname(src)
    if _same(d, erp):
        return True
    return _same(d, xml_dirs(erp, ws)[1])


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
            or not _in_erp(src, erp, ws):
        return None
    er = xml_dirs(erp, ws)[1]
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


def _locate(ws, fid, erp: str | None) -> str | None:
    """Where the XML of this import is now inside the ERP folder (or its error folder): where it was read, else
    by its name — the owner may have moved the files (e.g. into process) after they were read."""
    import hashlib
    f = ws.db.one("SELECT source_path, file_name, sha256 FROM import_files WHERE id=?", (fid,))
    if not f or not erp or not os.path.isdir(erp):
        return None
    src = f["source_path"] or ""
    if src and os.path.isfile(src) and _in_erp(src, erp, ws):
        return src
    name = os.path.basename(src) or f["file_name"]
    for cand in (os.path.join(erp, name), os.path.join(xml_dirs(erp, ws)[1], name)):
        try:
            if os.path.isfile(cand):
                with open(cand, "rb") as fh:
                    if hashlib.sha256(fh.read()).hexdigest() == f["sha256"]:
                        with ws.db.tx() as c:
                            c.execute("UPDATE import_files SET source_path=? WHERE id=?", (cand, fid))
                        return cand
        except OSError:
            continue
    return None


def sweep_finished(ws) -> list[str]:
    """XML files still in the ERP folder (or error) whose order lines all have their cutting files: on to done
    (e.g. finished while the folder was elsewhere, or moved by hand). Run when the folder watch starts."""
    import hashlib
    erp = ws.db.get_setting("watch.folder")
    if not ws.db.get_setting("watch.move_done", True) or not erp or not os.path.isdir(erp):
        return []
    fids = []
    for d in (erp, xml_dirs(erp, ws)[1]):
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if not n.lower().endswith(".xml") or not os.path.isfile(p):
                continue
            try:
                with open(p, "rb") as fh:
                    sha = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                continue
            f = ws.db.one("SELECT id FROM import_files WHERE sha256=? AND status='imported'", (sha,))
            if f:
                fids.append(f["id"])
    rids = [r["id"] for fid in fids for r in ws.db.q("SELECT id FROM revisions WHERE import_file=?", (fid,))]
    return move_finished(ws, rids) if rids else []


def sweep_held(ws) -> list[str]:
    """XML files still in process whose order lines wait for review or got no model: into error with the reason
    (owner: everything that needs attention in one folder). Run when the folder watch starts."""
    rows = ws.db.q("SELECT id, line_number, normalized FROM revisions WHERE status='needs_review' OR id IN (SELECT "
                   "r.id FROM revisions r WHERE r.status='ready' AND (SELECT g.status FROM grafis_orders g WHERE "
                   "g.revision_id=r.id ORDER BY g.id DESC LIMIT 1) IN ('blocked','not_configured'))")
    out = []
    import json as _json
    for r in rows:
        probs = [p["message"] for p in _json.loads(r["normalized"]).get("problems", [])
                 if p.get("severity") in ("review", "error")] or ["no model written"]
        out += move_failed(ws, [r["id"]], [f"{r['line_number']}: {m}" for m in probs], "order line needs attention")
    return out


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
        src = _locate(ws, fid, erp)
        if not src:
            continue
        lines = ws.db.q("SELECT status FROM revisions WHERE import_file=? AND status NOT IN (?,?)", (fid,) + OPEN)
        if not lines or any(ln["status"] != "completed" for ln in lines):
            continue                                       # a line still on its way or with an error: stays
        target = _move(ws, fid, src, xml_dirs(erp, ws)[0], "xml.done")
        if target:
            reason = os.path.join(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0] + ".txt")
            if os.path.basename(os.path.dirname(src)).lower() in ("er", "error") and os.path.isfile(reason):
                try:
                    os.remove(reason)                      # the error is over
                except OSError:
                    pass
            moved.append(target)
    return moved


def where_is_xml(ws, rid: int) -> tuple[str | None, str]:
    """Where the XML of an order line is now (owner: "I do not see the XML files, I do not know where they went"):
    (path or None, what happened to it last, from the log)."""
    r = ws.db.one("SELECT import_file FROM revisions WHERE id=?", (rid,))
    f = ws.db.one("SELECT source_path, file_name FROM import_files WHERE id=?", (r["import_file"],)) if r else None
    if not f:
        return None, "no XML file recorded for this order"
    path = f["source_path"] or ""
    if path and os.path.isfile(path):
        return path, ""
    name = os.path.basename(path) or f["file_name"]
    last = ws.db.one("SELECT at, action, details FROM audit WHERE entity_id=? AND action IN ('xml.done', 'xml.error', "
                     "'orders.removed') ORDER BY id DESC LIMIT 1", (name,)) if name else None
    erp = ws.db.get_setting("watch.folder") or ""
    dirs = [erp] + (list(xml_dirs(erp, ws)) + [deleted_dir(erp, ws)] if erp and os.path.isdir(erp) else [])
    for d in dirs:
        for cand in (os.path.join(d, name), os.path.join(d, "deleted", name)):
            if d and os.path.isfile(cand):
                return cand, ""
    said = f"{last['action']} {last['at']}" if last else ""
    return None, f"{name} is not at {path or '—'} any more (moved or deleted outside the program)" + \
        (f" — last: {said}" if said else "")


def moved_from_error(ws, old: str, new: str) -> bool:
    """The owner moved this XML back into the folder read from (from error, done or deleted) — read again. Its
    folder renamed (process → XML-process) or moved between read folders is the same file: only its place
    follows (owner: the files were read again after the folder was renamed)."""
    if not old or _same(os.path.dirname(old), os.path.dirname(new)):
        return False
    old_dir = os.path.dirname(old)
    if not os.path.isdir(old_dir):
        return False                                    # its folder is gone (renamed): the same file
    erp = ws.db.get_setting("watch.folder") or ""
    if not erp or not os.path.isdir(erp):
        return False
    done, err = xml_dirs(erp, ws)
    return any(_same(old_dir, d) for d in (err, done, deleted_dir(erp, ws))) or \
        os.path.basename(old_dir).lower() in ("er", "error", "done", "deleted") or \
        os.path.basename(old_dir).lower().endswith(("-error", "-done", "-deleted", "_error", "_done", "_deleted"))
