"""Order model computed by GRAFIS → nesting → cutting files, without a marker (SCB) from GRAFIS.

LEVEL7 MTM writes ``<XML>__<line>.mdl`` (X values of the order) into the MDL folder. In GRAFIS the model is
opened, recalculated ("Test Run All Parts") and saved under the same name. When the saved file appears in the
watched MDL folder it is checked and handed to LEVEL7 Nesting, which reads the pieces from the MDL directly:

* the file must belong to a model this program wrote for an order line that is still current;
* the X values in the file must still be the order's values (nothing changed by hand in GRAFIS);
* the pieces must have been recalculated: the geometry of the production parts must differ from the base model's
  (otherwise "Test Run All Parts" was not done — the pattern would still be the base size);
then the pieces are nested at the order's fabric width and quantity and PLT / ISO are written (auto_cut).
The file as written by this program (not yet saved by GRAFIS) is recognised and left alone.
"""
from __future__ import annotations

import hashlib
import json
import os

from ..grafis.container import block_bytes, read_container
from ..grafis.parts import production
from ..grafis.textsections import read_text
from ..grafis.xvalues import read_xvalues
from ..storage.db import now
from .grafis_intake import ScbCheck


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def identity(path: str, content: str | None = None) -> str:
    """The nesting job of a saved model: its content + the file — two order lines with identical models are still
    two jobs (two garments)."""
    content = content or _sha(path)
    return hashlib.sha256(f"{content}|{os.path.abspath(path).lower()}".encode()).hexdigest()


def check_mdl(ws, path: str) -> tuple[ScbCheck, dict | None]:
    content = _sha(path)
    chk = ScbCheck(path, identity(path, content))
    row = ws.db.one("SELECT * FROM grafis_orders WHERE status='written' AND path=? ORDER BY id DESC", (path,))
    if row is None:
        by_name = [r for r in ws.db.q("SELECT * FROM grafis_orders WHERE status='written' ORDER BY id DESC")
                   if os.path.basename(r["path"] or "").lower() == os.path.basename(path).lower()]
        row = by_name[0] if by_name else None
    if row is None:
        chk.issues.append(("blocking", "this model was not written by LEVEL7 MTM for an order line"))
        return chk, None
    info = json.loads(row["info"])
    if info.get("sha256") == content:
        chk.info = {"waiting": True}
        return chk, row                                  # still as written: GRAFIS has not saved it yet
    failed = ws.db.one("SELECT status, message FROM robot_jobs WHERE path=? AND at>=? ORDER BY id DESC LIMIT 1",
                       (row["path"], row["created_at"]))
    if failed and failed["status"] == "failed":
        # CAD stopped on an error with this model (owner: "it must go to Needs attention, never to the nesting —
        # nested, the error goes on and it comes back to CAD again and again"): what GRAFIS saved is not nested.
        # The order waits under Needs attention until the owner fixes it: Robot: try again (the copy saved in CAD
        # is then nested), Read again, or its XML moved back to process
        chk.info = {"waiting": True, "cad_error": failed["message"]}
        return chk, row
    rev = ws.revision(row["revision_id"])
    chk.lines = [{"revision_id": rev["id"], "line_number": rev["line_number"], "status": rev["status"],
                  "quantity": rev["quantity"]}]
    if rev["status"] in ("superseded", "cancelled"):
        chk.issues.append(("blocking", f"order line {rev['line_number']} is {rev['status']}"))
    try:
        cont = read_container(path)
    except Exception as e:  # noqa: BLE001
        chk.issues.append(("blocking", f"damaged GRAFIS file: {e}"))
        return chk, row
    if cont.errors:
        chk.issues.append(("blocking", "damaged GRAFIS file: " + "; ".join(cont.errors)))
        return chk, row
    for r in info.get("rows", []):                      # the order's values must still be there
        xv = read_xvalues(cont, int(r["part"])).get(int(r["x"][1:]))
        if xv is None or abs((xv.default or 0) - r["value_mm"]) > 0.05:
            chk.issues.append(("blocking", f"{r['part']}/{r['x']} is {xv.default if xv else '—'} in the saved model, "
                                           f"the order says {r['value_mm']:g} — changed in GRAFIS?"))
    prod = {n: s for n, (_, s) in production(cont).items() if s}
    if not prod:
        chk.issues.append(("blocking", "the saved model has no production part"))
    template = info.get("template")                      # the pattern the model was written from
    if not template:
        with ws.for_customer(ws.customer_of(row["revision_id"])):
            template = ws.grafis_mdl()                   # (models written before patterns existed)
    if template and os.path.isfile(template):
        base = read_container(template)
        with open(template, "rb") as fh:
            tdata = fh.read()
        with open(path, "rb") as fh:
            data = fh.read()
        changed = False
        for n in prod:
            for b in cont.find(3, 7):
                if b.bid[1] != n:
                    continue
                o = base.get(*b.bid)
                if o is None or block_bytes(tdata, o)[4:] != block_bytes(data, b)[4:]:
                    changed = True
                    break
            if changed:
                break
        if not changed:
            chk.issues.append(("blocking", "the pieces are still those of the base model: run 'Test Run All Parts' "
                                           "in GRAFIS, then save"))
    sizes = read_text(cont).sizes
    chk.info = {"sizes": [f"base:{sizes[0].name}"] if sizes else [], "parts": sorted(prod), "source": "mdl",
                "width_mm": rev["normalized"].get("fabric_width_mm"), "bundles": rev["quantity"]}
    chk.pieces = [{"part": n, "name": production(cont)[n][0], "copies": s.copies, "mirrored": s.mirrored}
                  for n, s in sorted(prod.items())]
    return chk, row


_NOW: dict = {}                                       # the model the nesting worker is on, and since when


def intake_now() -> tuple[str, int] | None:
    """(model, minutes) the nesting worker has been on it — a long time says where the queue is held up."""
    if not _NOW:
        return None
    import time as _t
    return os.path.basename(_NOW["path"]), int((_t.monotonic() - _NOW["since"]) // 60)


def register_mdl(ws, path: str, cut: bool | None = None) -> dict:
    """Watched-folder handler for the MDL folder. Returns {status, check, cutting, ...}."""
    import time as _t
    _NOW.update(path=path, since=_t.monotonic())
    try:
        return _register_mdl(ws, path, cut)
    finally:
        _NOW.clear()


def _register_mdl(ws, path: str, cut: bool | None = None) -> dict:
    from . import errors_folder
    from .auto_cut import auto_cut, settings
    from .workflow import WorkflowError
    try:
        chk, row = check_mdl(ws, path)
    except WorkflowError:                                 # its order line was read again meanwhile: the new
        chk = ScbCheck(path, identity(path))              # model comes on its own; this file waits
        chk.info = {"waiting": True}
        return {"status": "waiting_for_grafis", "check": chk, "cutting": None}
    except Exception as e:  # noqa: BLE001 - recorded once: taken again and again it held up the whole queue
        chk, row = ScbCheck(path, identity(path)), None
        chk.issues.append(("blocking", f"the saved model could not be read: {type(e).__name__}: {e}"))
    if chk.info.get("waiting"):
        return {"status": "cad_error" if chk.info.get("cad_error") else "waiting_for_grafis", "check": chk,
                "cutting": None}
    status = "checked" if chk.ok else ("unmatched" if row is None else "needs_review")
    dup = ws.db.one("SELECT id, handed_over, archived FROM grafis_results WHERE sha256=?", (chk.sha256,))
    if dup and dup["handed_over"] and not dup["archived"]:
        tried = ws.db.one("SELECT id FROM cutting_jobs WHERE sha256=?", (chk.sha256,))
        if tried or not (settings(ws)["enabled"] if cut is None else cut):
            return {"status": "duplicate", "check": chk, "cutting": None}
        # handed over but nesting never finished (the program stopped in between): nest it now
    ok = chk.ok
    with ws.db.tx() as c:
        values = (now(), json.dumps(chk.info, ensure_ascii=False), json.dumps(chk.pieces, ensure_ascii=False),
                  json.dumps(chk.lines), json.dumps(chk.issues, ensure_ascii=False),
                  "handed_over" if ok else status, int(ok), path if ok else None)
        if dup:
            c.execute("UPDATE grafis_results SET checked_at=?, info=?, pieces=?, lines=?, issues=?, status=?, "
                      "handed_over=?, nesting_path=?, archived=0 WHERE id=?", values + (dup["id"],))
        else:
            c.execute("INSERT INTO grafis_results(checked_at, info, pieces, lines, issues, status, handed_over, "
                      "nesting_path, sha256, path) VALUES (?,?,?,?,?,?,?,?,?,?)", values + (chk.sha256, path))
        if ok:
            for ln in chk.lines:
                c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=? AND status NOT IN "
                          "('superseded','cancelled')", (ln["revision_id"],))
        ws.db.audit(c, ws.actor, f"grafis.mdl_saved.{'handed_over' if ok else status}", "file",
                    os.path.basename(path), {"issues": chk.issues})
    out = {"status": "handed_over" if ok else status, "check": chk, "cutting": None}
    if row is not None:                                  # the model as GRAFIS saved it: the reference copy
        from .grafis_orders import archive_mdl
        archive_mdl(ws, path)
    if cut is None:
        cut = settings(ws)["enabled"]
    if ok and cut:
        out["cutting"] = auto_cut(ws, chk, path)
        out["status"] = "cutting_done" if out["cutting"]["status"] in ("done", "duplicate") else \
            "cutting_" + out["cutting"]["status"]
    if out["status"] not in ("handed_over", "cutting_done"):
        problems = [m for s, m in chk.issues if s != "info"] + \
            ([out["cutting"].get("message", "")] if out.get("cutting") else [])
        errors_folder.report(ws, os.path.splitext(os.path.basename(path))[0], problems,
                             f"order model saved by GRAFIS not processed ({out['status']})")
        from .done_folder import move_failed
        move_failed(ws, [ln["revision_id"] for ln in chk.lines], problems,
                    f"order model saved by GRAFIS not processed ({out['status']})")
    return out


def queue(ws) -> list[str]:
    """The models waiting for their nesting, in the order they are nested: the owner's order (moved to the top /
    bottom / a step up or down), then the oldest first. Those that no folder look will bring any more count too
    (saved by GRAFIS while the nesting was stopped, or handed over and never nested)."""
    from .auto_cut import settings
    from .grafis_robot import file_sha
    if not settings(ws)["enabled"]:
        return []                                     # cutting files are not made by themselves
    stale, found = [], []
    try:
        for r in ws.db.q("SELECT id, path, sha256 FROM grafis_results WHERE archived=0 AND handed_over=1 AND path IS "
                         "NOT NULL AND sha256 NOT IN (SELECT sha256 FROM cutting_jobs) ORDER BY id"):
            if not (r["path"] and os.path.isfile(r["path"])):
                continue
            try:
                same = identity(r["path"], file_sha(r["path"])) == r["sha256"]
            except OSError:
                continue                              # being written: looked at next time
            if same:
                found.append(r["path"])
                continue
            # the file changed since it was handed over (saved again by CAD, or written again after the order was
            # read again): this old job is over — left in the queue it was taken again and again, and every model
            # after it waited (owner: "the nesting does not work most of the time"); the file itself is looked at
            # as it is now (below / by the folder watch)
            stale.append(r["id"])
    finally:
        if stale:
            with ws.db.tx() as c:
                c.executemany("UPDATE grafis_results SET handed_over=0, archived=1, status='replaced' WHERE id=?",
                              [(i,) for i in stale])
    known = {r["sha256"] for r in ws.db.q("SELECT sha256 FROM grafis_results")}   # by content, not by name: a
    latest: dict[str, str] = {}                       # model saved again is new; per file its newest writing
    last_try = {j["path"]: j for j in ws.db.q("SELECT path, status, at FROM robot_jobs ORDER BY id")}
    for g in ws.db.q("SELECT path, info, created_at FROM grafis_orders WHERE status='written' AND archived=0 AND id "
                     "IN (SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) ORDER BY id"):
        if g["path"]:
            latest.pop(g["path"], None)
            j = last_try.get(g["path"])
            if j and j["status"] == "failed" and j["at"] >= g["created_at"]:
                continue                              # CAD stopped on an error: Needs attention, never nested
            latest[g["path"]] = json.loads(g["info"]).get("sha256")
    from .grafis_robot import current_model
    import time as _t
    busy = current_model()
    for p, written in latest.items():                # oldest first
        if p in found or not os.path.isfile(p) or p == busy:
            continue                                  # (open in CAD now: when it is saved)
        try:
            if _t.time() - os.path.getmtime(p) < 5:
                continue                              # still being saved: looked at in a moment
            sha = file_sha(p)
            if sha != written and identity(p, sha) not in known:
                found.append(p)                       # saved by GRAFIS, never looked at
        except OSError:
            continue
    ranks = ws.db.get_setting("nesting.order") or {}
    first = {p: i for i, p in enumerate(found)}
    return sorted(found, key=lambda p: (ranks[p], 0) if p in ranks else (1e9, first[p]))


def next_queued(ws) -> str | None:
    """The next model to nest (the first of the queue)."""
    q = queue(ws)
    return q[0] if q else None


def move_in_queue(ws, paths: list[str], how: str) -> list[str]:
    """The owner moves these models in the nesting queue: "top", "up" (one step), "down" (one step) or "bottom".
    Returns the new order."""
    q = queue(ws)
    chosen = [p for p in q if p in set(paths)]
    if not chosen:
        return q
    if how == "top":
        q = chosen + [p for p in q if p not in chosen]
    elif how == "bottom":
        q = [p for p in q if p not in chosen] + chosen
    elif how in ("up", "down"):
        step = -1 if how == "up" else 1
        idx = [q.index(p) for p in chosen]
        for i in (sorted(idx) if step < 0 else sorted(idx, reverse=True)):
            j = i + step
            if 0 <= j < len(q) and q[j] not in chosen:
                q[i], q[j] = q[j], q[i]
    ws.db.set_setting("nesting.order", {p: i for i, p in enumerate(q)})   # the order kept for the whole queue
    return q


def handled_before(ws, path: str) -> bool:
    """An MDL of the folder that needs no look when the watch starts: still as this program wrote it (CAD has not
    saved it), or saved and read before with the same content (checked / nested). A model saved meanwhile is read."""
    from .grafis_robot import file_sha
    try:
        sha = file_sha(path)
    except OSError:
        return False
    if ws.db.one("SELECT 1 FROM grafis_results WHERE path=? AND sha256=?", (path, sha)):
        return True
    for g in ws.db.q("SELECT info FROM grafis_orders WHERE path=? AND status='written' ORDER BY id DESC LIMIT 1",
                     (path,)):
        return json.loads(g["info"]).get("sha256") == sha
    return False
