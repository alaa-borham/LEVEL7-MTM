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


def check_mdl(ws, path: str) -> tuple[ScbCheck, dict | None]:
    content = _sha(path)
    # identity = content + file: two order lines with identical models are still two jobs (two garments)
    chk = ScbCheck(path, hashlib.sha256(f"{content}|{os.path.abspath(path).lower()}".encode()).hexdigest())
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


def register_mdl(ws, path: str, cut: bool | None = None) -> dict:
    """Watched-folder handler for the MDL folder. Returns {status, check, cutting, ...}."""
    from . import errors_folder
    from .auto_cut import auto_cut, settings
    chk, row = check_mdl(ws, path)
    if chk.info.get("waiting"):
        return {"status": "waiting_for_grafis", "check": chk, "cutting": None}
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


def next_queued(ws) -> str | None:
    """The next model waiting for its nesting that no folder look will bring any more (e.g. saved by GRAFIS while
    the nesting was stopped, or handed over and never nested): the oldest first."""
    from .auto_cut import settings
    from .grafis_robot import file_sha
    if not settings(ws)["enabled"]:
        return None                                   # cutting files are not made by themselves
    for r in ws.db.q("SELECT path, sha256 FROM grafis_results WHERE archived=0 AND handed_over=1 AND path IS NOT NULL "
                     "AND sha256 NOT IN (SELECT sha256 FROM cutting_jobs) ORDER BY id"):
        if r["path"] and os.path.isfile(r["path"]):
            return r["path"]
    known = {r["path"] for r in ws.db.q("SELECT path FROM grafis_results WHERE path IS NOT NULL")}
    for g in ws.db.q("SELECT path, info FROM grafis_orders WHERE status='written' AND archived=0 AND id IN "
                     "(SELECT MAX(id) FROM grafis_orders GROUP BY revision_id) ORDER BY id"):
        p = g["path"]
        if not p or p in known or not os.path.isfile(p):
            continue
        try:
            if file_sha(p) != json.loads(g["info"]).get("sha256"):      # saved by GRAFIS, never looked at
                return p
        except OSError:
            continue
    return None


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
