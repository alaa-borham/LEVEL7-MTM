"""Automatic X values for GRAFIS: for each imported order line LEVEL7 MTM writes a copy of the GRAFIS model
(MDL) with the order's X values already filled in, named after the XML (``Order-9-53-1_2.mdl``), into the GRAFIS
input folder. Nobody types in the X-value window.

Values come from the confirmed catalog rules (XML value → X number of a part, owner decisions #13–#15). The
production parts come from the style's "GRAFIS parts" table (design option → GRAFIS part number, material,
normal / mirrored copies), entered by the owner in the Rules editor: exactly those parts are produced. Without that
table the model's own production parts are kept (and the report says so). Nothing is written when anything is
unclear — the line is held with the reason:
* the order line needs review, or the catalog step reports a blocking problem;
* a rule is not confirmed, or a measurement of a confirmed rule is missing (GRAFIS would silently keep its
  placeholder);
* the X number is not in the model's X table, or its name in the MDL differs from the rule's ``x_name``
  (another model version than the rules were written for);
* a configured GRAFIS part is not in the model or has another name there.
An X with no XML code is inactive (owner decision #21): it is not written, and listed as inactive in the report.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata

from ..grafis.container import read_container
from ..grafis.parts import PartSpec, production
from ..grafis.xvalues import read_xvalues
from ..grafis.xwriter import XWriteError, write_order_mdl
from .catalog import grafis_parts_for
from ..storage.db import now


def settings(ws) -> dict:
    return {"template": ws.grafis_mdl(),                 # the base model of the current customer
            "out": ws.db.get_setting("grafis.input") or os.path.join(ws.folder, "grafis", "input"),
            "enabled": bool(ws.db.get_setting("grafis.auto_mdl", True))}


def archive_folder(ws) -> str:
    """Where a copy of every order model is kept as a reference (owner: "for later, when there is an error"):
    chosen in the settings, else MDL-ARCHIVE next to the GRAFIS input folder."""
    chosen = ws.db.get_setting("grafis.mdl_archive")
    if chosen:
        return chosen
    inp = ws.db.get_setting("grafis.input")
    return os.path.join(os.path.dirname(inp.rstrip("/\\")), "MDL-ARCHIVE") if inp else \
        os.path.join(ws.folder, "grafis", "archive")


def archive_mdl(ws, path: str) -> str | None:
    """Copy the order model into the archive folder (the newer copy replaces the older: as written, then as
    GRAFIS saved it). Returns the copy, None when it could not be made (never stops the chain)."""
    import shutil
    if not path or not os.path.isfile(path):
        return None
    dest_dir = archive_folder(ws)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, os.path.basename(path))
        if os.path.normcase(os.path.abspath(dest)) != os.path.normcase(os.path.abspath(path)):
            shutil.copy2(path, dest)
        return dest
    except OSError:
        return None


def mdl_name(ws, rev) -> str:
    f = ws.db.one("SELECT file_name FROM import_files WHERE id=?", (rev["import_file"],))
    stem = os.path.splitext(f["file_name"])[0] if f else f"Order-{rev['line_number']}"
    # no spaces: "Order-9-50-1_0 - .xml" gave "Order-9-50-1_0 - __9-50-1_0.mdl", which GRAFIS did not show
    # under that name, so the robot waited in vain
    # only plain characters: Windows may add invisible right-to-left marks (U+200F) to a name typed in Arabic,
    # and GRAFIS (an older program) does not open such a file ("no GRAFIS window open")
    stem = "".join(ch for ch in stem if unicodedata.category(ch) != "Cf")
    stem = re.sub(r"[^A-Za-z0-9._\-\s]", "_", stem)
    stem = re.sub(r"\s+", "_", stem.strip(" -_.\t")) or f"Order-{rev['line_number']}"
    n = ws.db.one("SELECT count(*) AS n FROM revisions WHERE import_file=?", (rev["import_file"],))["n"]
    if n > 1 or not re.search(re.escape(rev["line_number"]) + r"$", stem):
        stem = f"{stem}__{rev['line_number']}"        # several lines in one XML: one model per line
    return re.sub(r'[<>:"/\\|?*]', "_", stem) + ".mdl"


def _in_xml(ws, rev, code: str) -> str:
    """Why a measurement is not in the order: the line's XML read again with today's settings of its customer."""
    from ..erp.importer import import_bytes
    try:
        f = ws.db.one("SELECT raw FROM import_files WHERE id=?", (rev["import_file"],))
        with ws.for_customer(rev["normalized"].get("customer")):
            _, prof = ws.active("mapping")
        ln = next((x for x in import_bytes(bytes(f["raw"]), prof).lines if x.number == rev["line_number"]), None)
    except Exception:  # noqa: BLE001 - only a hint
        return ""
    if ln is None:
        return ""
    if code in ln.measurements:
        return f"the XML has {code}: read the order again with the current settings"
    if any(p.code == "measure.unknown" and p.field == code for p in ln.problems):
        return f"the XML has {code} but it is not in the measurements of this customer"
    return f"the XML of this order line has no {code}"


def plan_values(ws, rid: int, template: str) -> dict:
    """{values: {(part, X): mm}, rows, inactive, problems} — nothing written."""
    rev = ws.revision(rid)
    _, cat, res = ws.resolve_order(rid)
    style = cat["products"][res.product]["styles"][res.style]
    rules = {r["id"]: r for r in style["rules"]}
    cont = read_container(template)
    tables: dict[int, dict] = {}
    problems, rows, values = [], [], {}
    if rev["status"] == "needs_review":
        problems.append("order line needs review (import problems) — resolve them first")
    for i in res.issues:
        if i.severity == "blocking" and not i.code.startswith("plan."):
            problems.append(i.message)
    applied_targets = {t.target for t in res.trace if t.status == "applied"}
    for t in res.trace:
        r = rules.get(t.rule_id, {})
        if t.status == "skipped" and r and t.target not in applied_targets and r.get("status") == "confirmed":
            why = _in_xml(ws, rev, t.source) if (t.message or "").startswith("measurement ") else ""
            problems.append(f"{t.rule_id}: {t.message} — {t.target.split('/')[-1]} would keep the GRAFIS placeholder"
                            + (f" ({why})" if why else ""))
        if t.status != "applied":
            continue
        piece, _, var = t.target.split("/")
        if not (piece.isdigit() and var.startswith("X") and var[1:].isdigit()):
            problems.append(f"{t.rule_id}: target {t.target} is not a GRAFIS X value")
            continue
        part, num = int(piece), int(var[1:])
        if part not in tables:
            tables[part] = read_xvalues(cont, part)
        g = tables[part].get(num)
        want = (r.get("target") or {}).get("x_name", "")
        row = {"part": f"{part:03d}", "x": var, "x_name": g.name if g else "", "value_mm": round(t.value, 3),
               "source": t.source, "rule": t.rule_id, "rule_status": r.get("status", "proposed"),
               "grafis_default_mm": g.default if g else None}
        rows.append(row)
        if g is None:
            problems.append(f"{t.rule_id}: {var} is not in the X table of part {part:03d} of {os.path.basename(template)}")
        elif want and want != g.name:
            problems.append(f"{t.rule_id}: {var} of part {part:03d} is '{g.name}' in the MDL, the rule expects "
                            f"'{want}' — other model version?")
        elif row["rule_status"] != "confirmed":
            problems.append(f"{t.rule_id} ({t.source} → {var}) is not confirmed")
        else:
            values[(part, num)] = t.value
    inactive = [f"{p:03d}/X{n} {g.name}".strip() for p, tab in sorted(tables.items())       # no XML code: not used
                for n, g in sorted(tab.items()) if (p, n) not in values]
    # production parts chosen by the design options
    parts, part_rows, parts_note = None, [], ""
    gp = grafis_parts_for(style, res.options)
    model_parts = production(cont)
    if gp is None:
        kept = [f"{n:03d} {nm}" for n, (nm, sp) in sorted(model_parts.items()) if sp]
        if not kept:                                    # base model with production off: parts must come from options
            problems.append("no GRAFIS part to produce: the base model has none and the GRAFIS parts table is "
                            "empty — fill it in (screen 'GRAFIS parts')")
        parts_note = "GRAFIS parts per option not configured — the model's own production parts are kept: " + \
            ", ".join(kept)
    else:
        parts = {}
        for num, row in sorted(gp.items()):
            have = model_parts.get(num)
            if have is None:
                problems.append(f"GRAFIS part {num:03d} ({row.get('name', '')}) is not in {os.path.basename(template)}")
                continue
            if row.get("name") and row["name"].strip() != have[0].strip():
                problems.append(f"GRAFIS part {num:03d} is '{have[0]}' in the MDL, the table says '{row['name']}' — "
                                f"other model version?")
                continue
            parts[num] = PartSpec(row["material"], row["copies"], row["mirrored"])
            part_rows.append({"part": f"{num:03d}", "name": have[0], "material": row["material"],
                              "copies": row["copies"], "mirrored": row["mirrored"],
                              "option": row.get("option") or "*"})
        if not parts:
            problems.append("no GRAFIS part selected for this order")
    return {"values": values, "rows": rows, "inactive": inactive, "problems": problems, "parts": parts,
            "part_rows": part_rows, "parts_note": parts_note}


def order_mdl(ws, rid: int, template: str | None = None, out_dir: str | None = None, force: bool = False) -> dict:
    """Write the order's MDL. Returns {status: written / duplicate / blocked / not_configured / skipped, ...}.
    ``force``: write again although the same model was written before (the owner asked for it)."""
    from .activity import working
    with working([rid], "model"), ws.for_customer(ws.customer_of(rid)):   # the customer's model and parts table
        return _order_mdl(ws, rid, template, out_dir, force)


def _order_mdl(ws, rid: int, template: str | None = None, out_dir: str | None = None, force: bool = False) -> dict:
    from .customers import pattern_for
    st = settings(ws)
    out_dir = out_dir or st["out"]
    rev = ws.revision(rid)
    res = {"revision_id": rid, "line_number": rev["line_number"], "status": "blocked", "path": None,
           "problems": [], "rows": [], "inactive": [], "part_rows": [], "parts_note": ""}
    if rev["status"] in ("superseded", "cancelled"):
        res.update(status="skipped", problems=[f"order line is {rev['status']}"])
        return res
    if not template:                              # the pattern (body shape) the order's Variation chooses
        template, why = pattern_for(ws, rid)
        if why:
            res["problems"] = [why]
            return _save(ws, res, None)
    res["template"] = template
    if not template or not os.path.isfile(template):
        res.update(status="not_configured", problems=["GRAFIS model (MDL) not set — choose it in the GRAFIS screen"])
        return _save(ws, res, None)
    with open(template, "rb") as fh:
        tsha = hashlib.sha256(fh.read()).hexdigest()
    tsha += f":catalog{ws.active('catalog')[0]}"           # a new rules / parts version (this customer's) writes again
    # owner: a model written again (read again, nested again, pattern changed) always comes from the base pattern
    # and has a new number: Order-…__line.mdl, then …_v2.mdl, _v3.mdl …; the one before goes to the archive
    stem, ext = os.path.splitext(mdl_name(ws, rev))
    key = f"mdl.version@{os.path.normcase(os.path.abspath(os.path.join(out_dir, stem)))}"
    n = int(ws.db.get_setting(key) or 0)
    if n == 0 and os.path.exists(os.path.join(out_dir, stem + ext)):
        n = 1                                          # written before the numbering
    numbered = lambda k: os.path.join(out_dir, stem + (f"_v{k}" if k > 1 else "") + ext)   # noqa: E731
    path = numbered(max(n, 1))
    dup = ws.db.one("SELECT * FROM grafis_orders WHERE revision_id=? AND status='written' AND template_sha256=? "
                    "AND archived=0 ORDER BY id DESC", (rid, tsha))
    if dup and not force and dup["path"] == path and os.path.exists(path):
        res.update(json.loads(dup["info"]), status="duplicate", path=path)
        return res
    try:
        plan = plan_values(ws, rid, template)
    except Exception as e:                         # catalog / template unreadable: hold the line, say why
        res["problems"] = [f"{type(e).__name__}: {e}"]
        return _save(ws, res, tsha)
    res.update(rows=plan["rows"], inactive=plan["inactive"], problems=plan["problems"],
               part_rows=plan["part_rows"], parts_note=plan["parts_note"])
    if plan["problems"]:
        return _save(ws, res, tsha)
    if not plan["values"]:
        res["problems"] = ["no X value to write"]
        return _save(ws, res, tsha)
    before = path if n and os.path.exists(path) else None
    if n:
        path = numbered(n + 1)
    try:
        write_order_mdl(template, plan["values"], path, plan["parts"])
    except (XWriteError, OSError) as e:
        res["problems"] = [str(e)]
        return _save(ws, res, tsha)
    ws.db.set_setting(key, max(n, 0) + 1)
    if before:
        _retire(ws, before)
    with open(path, "rb") as fh:                   # recognises the file as written (before GRAFIS saves it)
        res["sha256"] = hashlib.sha256(fh.read()).hexdigest()
    res.update(status="written", path=path)
    res["archive"] = archive_mdl(ws, path)          # kept as a reference
    return _save(ws, res, tsha)


def outdated(ws) -> list[int]:
    """Order lines not finished whose model was written from a base pattern, or with parts / rules of the customer,
    that have changed since (owner: "the base pattern is corrected", "I changed some parts") — the model written
    before must not be nested: their revision ids."""
    from .grafis_robot import file_sha
    out, shas, cats = [], {}, {}
    for g in ws.db.q("SELECT g.revision_id, g.template_sha256, g.info FROM grafis_orders g JOIN revisions r ON "
                     "r.id=g.revision_id WHERE g.status='written' AND g.archived=0 AND r.status IN ('ready', "
                     "'sent_to_nesting') AND g.id IN (SELECT MAX(id) FROM grafis_orders GROUP BY revision_id)"):
        template = json.loads(g["info"] or "{}").get("template")
        if not template or not g["template_sha256"]:
            continue
        if template not in shas:
            try:
                shas[template] = file_sha(template)
            except OSError:
                shas[template] = None                 # the pattern is not there now: nothing to compare
        now_sha = shas[template]
        if now_sha and not g["template_sha256"].startswith(now_sha):
            out.append(g["revision_id"])
            continue
        # owner: "I changed some parts (copies, mirrored, the parts of an option) but the orders already sent to CAD
        # and the nesting did not change": the customer's parts / rules (catalog) changed since it was written
        cid = ws.customer_of(g["revision_id"])
        if cid not in cats:
            try:
                with ws.for_customer(cid):
                    cats[cid] = f":catalog{ws.active('catalog')[0]}"
            except Exception:  # noqa: BLE001 - no catalog: nothing to compare
                cats[cid] = None
        if cats[cid] and ":catalog" in g["template_sha256"] and not g["template_sha256"].endswith(cats[cid]):
            out.append(g["revision_id"])
    return out


def redo_outdated(ws) -> list:
    """The models written from a base pattern changed since: written again from it (a new number), through CAD
    and the nesting again — not one with cutting files, nor one being nested now."""
    from .customers import reread_lines
    rids = outdated(ws)
    if not rids:
        return []
    outs, _ = reread_lines(ws, rids)
    return outs


def model_again(ws, rid: int) -> dict | None:
    """After the owner corrected an order (a measurement, an option, problems confirmed): its model is written
    again from the base pattern with a new number, goes through CAD and is nested again — also when it was nested
    or cut before. None: the order still waits for review (or models are not written by themselves)."""
    if not settings(ws)["enabled"] or ws.revision(rid)["status"] != "ready":
        return None
    return order_mdl(ws, rid, force=True)


def _retire(ws, path: str) -> None:
    """The model written before (another number): kept in the archive as it was, out of the MDL folder — not while
    CAD has it open."""
    from .grafis_robot import current_model
    cur = current_model()
    if cur and os.path.normcase(os.path.abspath(cur)) == os.path.normcase(os.path.abspath(path)):
        return
    try:
        archive_mdl(ws, path)
        os.remove(path)
    except OSError:
        pass
    with ws.db.tx() as c:                          # its older writings are over (the robot leaves them)
        c.execute("UPDATE grafis_orders SET archived=1 WHERE path=? AND status='written'", (path,))


def _save(ws, res, tsha) -> dict:
    info = {k: v for k, v in res.items() if k not in ("status", "path")}
    with ws.db.tx() as c:
        c.execute("INSERT INTO grafis_orders(revision_id, created_at, status, path, template_sha256, info) "
                  "VALUES (?,?,?,?,?,?)", (res["revision_id"], now(), res["status"], res["path"], tsha,
                                           json.dumps(info, ensure_ascii=False)))
        ws.db.audit(c, ws.actor, f"grafis.mdl.{res['status']}", "revision", res["revision_id"],
                    {"path": res["path"], "problems": res["problems"],
                     "values": {f"{r['part']}/{r['x']}": r["value_mm"] for r in res["rows"]}})
    return res


def import_and_prepare(ws, path: str):
    """Import an XML file and, when automatic mode is on, write the GRAFIS model of each new order line."""
    from . import errors_folder
    out = ws.import_file(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    if out.status == "failed":
        errors_folder.report(ws, stem, [out.message], "XML import failed", copy_file=path)
        from .done_folder import move_xml_to_er
        move_xml_to_er(ws, path, [out.message], "XML import failed")
    if settings(ws)["enabled"] and out.status == "imported":
        for ln in out.lines:
            if ln["action"] in ("new", "new_revision"):
                out.grafis.append(order_mdl(ws, ln["revision_id"]))
    elif out.status == "duplicate":
        redo = _moved_back(ws, path)                     # owner: from error back into process — done again
        again = [] if redo is not None or not settings(ws)["enabled"] else _prepare_cleaned(ws, path)
        if redo is not None:
            out = redo
        elif again:                                      # the same XML again after its lines were cleaned
            out.grafis += again
            out.message = "the same file was already imported; its order lines had been cleaned from the list — " \
                          "their GRAFIS models are written again"
    held, why = [], []                                   # owner: everything that needs attention goes to error
    if out.status == "imported":
        held = [ln["revision_id"] for ln in out.lines if ln.get("status") == "needs_review" and ln.get("revision_id")]
        why = [f"{ws.revision(r)['line_number']}: {p['message']}" for r in held
               for p in ws.revision(r)["normalized"].get("problems", []) if p.get("severity") in ("review", "error")]
    if settings(ws)["enabled"]:
        bad = [g for g in out.grafis if g["status"] in ("blocked", "not_configured")]
        for k, g in enumerate(bad):
            errors_folder.report(ws, f"{stem}__{g['line_number']}", g["problems"],
                                 f"order line {g['line_number']}: no GRAFIS model written",
                                 copy_file=path if k == 0 else None)
        held += [g["revision_id"] for g in bad]
        why += [f"{g['line_number']}: {p}" for g in bad for p in g["problems"]]
    if held:                                             # one move, every reason in its .txt
        from .done_folder import move_failed
        move_failed(ws, held, why, "order line needs attention")
    return out


def _moved_back(ws, path: str):
    """An XML read before and moved back into process by the owner (from error): its order lines that did not
    get their cutting files are read again with today's settings and their models written again from the base
    pattern (a new number) — through CAD and the nesting again. None: nothing to do again."""
    from .customers import reread_lines
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    f = ws.db.one("SELECT id, source_path FROM import_files WHERE sha256=?", (sha,))
    if not f or not f["source_path"] or os.path.normcase(os.path.dirname(os.path.abspath(f["source_path"]))) == \
            os.path.normcase(os.path.dirname(os.path.abspath(path))):
        return None                                      # not moved: the same file read again where it was
    rids = [r["id"] for r in ws.db.q("SELECT id FROM revisions WHERE import_file=? AND status NOT IN "
                                     "('superseded','cancelled','completed') ORDER BY id", (f["id"],))]
    if not rids:
        return None
    with ws.db.tx() as c:                                # the file is here now (moved by hand)
        c.execute("UPDATE import_files SET source_path=? WHERE id=?", (path, f["id"]))
    outs, _skipped = reread_lines(ws, rids)
    if not outs:
        return None
    outs[0].message = "moved back into process: its order lines read again and their models written again"
    return outs[0]


def _prepare_cleaned(ws, path: str) -> list[dict]:
    """Order lines of an XML imported before whose GRAFIS rows were all cleaned from the list (or never written):
    write their models again, so importing the file once more runs the whole chain again."""
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    f = ws.db.one("SELECT id FROM import_files WHERE sha256=? AND status='imported'", (sha,))
    if not f:
        return []
    out = []
    for r in ws.db.q("SELECT id FROM revisions WHERE import_file=? AND status NOT IN ('superseded','cancelled') "
                     "ORDER BY id", (f["id"],)):
        if not ws.db.one("SELECT 1 FROM grafis_orders WHERE revision_id=? AND archived=0", (r["id"],)):
            out.append(order_mdl(ws, r["id"]))
    return out
