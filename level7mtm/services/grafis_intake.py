"""GRAFIS result intake (phase 1): GRAFIS writes one SCB per order (file named after the XML); LEVEL7 MTM checks
it against the order and hands it to LEVEL7 Nesting unchanged.

Checks (nothing is repaired, every finding is shown):
* the file is a readable GRAFIS marker (container, #SBINFO, #SBTLIST);
* it belongs to known order lines (by file name: the XML / order number in it);
* fabric width of the marker = fabric width of the order (XML);
* bundles = total quantity of the matched order lines;
* no unplaced pieces (PCSUNPLC), placed pieces = Σ (copies + mirrored) × bundles;
* piece list against the expected GRAFIS parts of the style (when configured in the catalog).

Hand-over: the SCB is copied byte for byte into the nesting folder (never modified, never deleted at the source)
and a row is added to ``nesting_orders.csv`` there. The same file content is never handed over twice.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field

from ..grafis.container import GrafisFormatError, read_container
from ..grafis.textsections import read_text
from ..storage.db import now

LINE_RE = re.compile(r"(\d+-\d+-\d+_\d+)")


@dataclass
class ScbCheck:
    path: str
    sha256: str
    lines: list[dict] = field(default_factory=list)          # matched revisions
    info: dict = field(default_factory=dict)
    pieces: list[dict] = field(default_factory=list)
    issues: list[tuple[str, str]] = field(default_factory=list)   # (severity, message)

    @property
    def ok(self) -> bool:
        return not any(s in ("blocking", "review") for s, _ in self.issues)


def _scb_pieces(text_raw: str) -> list[dict]:
    out, sec = [], None
    for line in text_raw.split("\r\n"):
        if line.startswith("#"):
            sec = line.strip()
            continue
        if sec == "#SBTLIST":
            m = re.match(r"^\s*(\d{3})\s+(.*?)\s+(\d+)\s+(\d+)\s+\S*x\S*", line)
            if m:
                out.append({"part": int(m.group(1)), "name": m.group(2).strip(), "copies": int(m.group(3)),
                            "mirrored": int(m.group(4))})
    return out


def _sizes(text_raw: str) -> list[str]:
    out, sec = [], None
    for line in text_raw.split("\r\n"):
        if line.startswith("#"):
            sec = line.strip()
            continue
        if sec == "#SBGRLST" and line.strip():
            parts = line.split()
            if len(parts) >= 2:
                out.append(" ".join(parts[1:]))
    return out


def check_scb(ws, path: str, renest: bool | None = None) -> ScbCheck:
    """``renest``: LEVEL7 Nesting nests again at the order's width and quantity (automatic chain), so a different
    width / bundle count of the GRAFIS marker is information, not a finding. Default: the cutting.auto setting."""
    if renest is None:
        renest = bool(ws.db.get_setting("cutting.auto", True))
    data = open(path, "rb").read()
    chk = ScbCheck(path, hashlib.sha256(data).hexdigest())
    try:
        cont = read_container(path)
    except (GrafisFormatError, OSError) as e:
        chk.issues.append(("blocking", f"not a readable GRAFIS file: {e}"))
        return chk
    if cont.errors:
        chk.issues.append(("blocking", "damaged GRAFIS file: " + "; ".join(cont.errors)))
        return chk
    blk = cont.get(2, 1, 0, 0)
    raw = blk.payload.split(b"\0", 1)[0].decode("cp1256", "replace") if blk else ""
    text = read_text(cont)
    sb = text.sbinfo
    if not sb:
        chk.issues.append(("blocking", "no marker information (#SBINFO): this is not a GRAFIS marker (SCB)"))
        return chk
    chk.pieces = _scb_pieces(raw)
    sizes = _sizes(raw)
    for k in ("PCSTOTAL", "PCSUNPLC"):                    # written without a space: "PCSTOTAL23"
        m = re.search(rf"^{k}\s*(\d+)", raw, re.M)
        if m:
            sb[k] = m.group(1)
    mat = next(((k[8:], v) for k, v in sb.items() if k.startswith("MATERIAL") and len(k) == 9), (None, None))
    num = lambda k: float(sb[k]) if sb.get(k, "").replace(".", "", 1).isdigit() else None   # noqa: E731
    chk.info = {"width_mm": num("WIDTH"), "length_mm": num("LENGTH"), "efficiency_pct": num("EFFCNCY"),
                "bundles": int(num("BUNDLES") or 0), "pieces_placed": int(num("PCSTOTAL") or 0) if
                sb.get("PCSTOTAL") else None, "unplaced": int(num("PCSUNPLC") or 0), "sizes": sizes,
                "user": sb.get("USER"), "created": sb.get("CRTIME"), "material": mat[1],
                "material_code": mat[0]}
    # --- which order lines
    stem = os.path.splitext(os.path.basename(path))[0]
    by_file = ws.db.q("SELECT r.id FROM revisions r JOIN import_files f ON f.id=r.import_file "
                      "WHERE lower(f.file_name) IN (?, ?)", (f"{stem}.xml".lower(), f"{stem}.XML".lower()))
    rids = [r["id"] for r in by_file]
    own = re.search(r"__(\d+-\d+-\d+_\d+)", stem)     # model written per line: "<XML>__<line>" (grafis_orders)
    if own:
        rids = []
    if not rids:
        refs = {own.group(1)} if own else set(LINE_RE.findall(stem)) | {s for s in sizes if LINE_RE.fullmatch(s)}
        for ref in refs:
            r = ws.db.one("SELECT id FROM revisions WHERE line_number=?", (ref,))
            if r:
                rids.append(r["id"])
    if not rids:
        chk.issues.append(("blocking", f"file name '{stem}' does not match any imported XML / order line"))
        return chk
    total_qty = 0
    widths = set()
    for rid in rids:
        rev = ws.revision(rid)
        chk.lines.append({"revision_id": rid, "line_number": rev["line_number"], "status": rev["status"],
                          "quantity": rev["quantity"]})
        total_qty += int(rev["quantity"] or 0)
        widths.add(rev["normalized"].get("fabric_width_mm"))
        if rev["status"] in ("superseded", "cancelled"):
            chk.issues.append(("blocking", f"{rev['line_number']} is {rev['status']} — this marker must not be cut"))
        if rev["normalized"].get("problems"):
            chk.issues.append(("review", f"{rev['line_number']}: import problems not resolved"))
    # --- checks
    w = chk.info["width_mm"]
    for ow in widths:
        if ow is not None and w is not None and abs(ow - w) > 0.5:
            chk.issues.append(("info" if renest else "review",
                               f"marker width {w:g} mm ≠ fabric width of the order {ow:g} mm"
                               + (" — LEVEL7 Nesting nests at the order width" if renest else "")))
    if chk.info["bundles"] != total_qty:
        single = len(sizes) == 1
        chk.issues.append(("info" if renest and single else "review",
                           f"marker has {chk.info['bundles']} bundle(s), the order lines total {total_qty} "
                           f"garment(s)" + (" — LEVEL7 Nesting uses the order quantity" if renest and single else "")))
    if chk.info["unplaced"]:
        chk.issues.append(("blocking", f"{chk.info['unplaced']} piece(s) not placed in the GRAFIS marker"))
    expected_total = sum(p["copies"] + p["mirrored"] for p in chk.pieces) * max(chk.info["bundles"], 1)
    if chk.info["pieces_placed"] is not None and chk.info["pieces_placed"] != expected_total:
        chk.issues.append(("review", f"placed pieces {chk.info['pieces_placed']} ≠ piece list × bundles "
                                     f"({expected_total})"))
    if not any(any(ln["line_number"] in s for ln in chk.lines) for s in sizes):
        chk.issues.append(("info", f"size names in the marker ({', '.join(sizes)}) do not contain the order number; "
                                   f"identity comes from the file name"))
    _expected_parts(ws, chk)
    return chk


def _expected_parts(ws, chk: ScbCheck) -> None:
    """Compare the marker's parts with the GRAFIS parts the catalog expects for the chosen options (if set up)."""
    try:
        _, cat, res = ws.resolve_order(chk.lines[0]["revision_id"])
    except Exception:  # noqa: BLE001 - catalog problems are reported elsewhere
        return
    from .catalog import grafis_parts_for
    style = cat["products"][res.product]["styles"][res.style]
    gp = grafis_parts_for(style, res.options)
    if gp is None:
        chk.issues.append(("info", "GRAFIS parts per option are not configured (Rules editor → GRAFIS parts); "
                                   "piece list shown for review only"))
        return
    code = chk.info.get("material_code")          # one marker per material: "MATERIALO = upper fabric"
    expected = {row["name"]: (row["copies"], row["mirrored"]) for row in gp.values()
                if code is None or row["material"] == code}
    have = {p["name"]: (p["copies"], p["mirrored"]) for p in chk.pieces}
    for name, cm in expected.items():
        if name not in have:
            chk.issues.append(("blocking", f"part '{name}' expected for this order is missing in the marker"))
        elif have[name] != cm:
            chk.issues.append(("review", f"part '{name}': {have[name][0]}+{have[name][1]} mirrored in the marker, "
                                         f"expected {cm[0]}+{cm[1]}"))
    for name in have:
        if name not in expected:
            chk.issues.append(("review", f"part '{name}' in the marker is not expected for the chosen options"))


def register_scb(ws, path: str, handover: bool = True, nesting_dir: str | None = None,
                 confirm_reason: str | None = None, cut: bool | None = None) -> dict:
    """Check an SCB from GRAFIS, record the result, and (when it passes) hand it to the nesting folder.

    ``confirm_reason``: an operator accepts the *review* findings (never blocking ones); the reason is recorded."""
    from .auto_cut import auto_cut, settings
    if cut is None:
        cut = settings(ws)["enabled"]
    chk = check_scb(ws, path, renest=cut)
    if confirm_reason and chk.lines and not any(s == "blocking" for s, _ in chk.issues):
        accepted = [m for s, m in chk.issues if s == "review"]
        chk.issues = [(s if s != "review" else "info", m if s != "review" else f"accepted by {ws.actor}: {m} "
                       f"(reason: {confirm_reason})") for s, m in chk.issues]
        with ws.db.tx() as c:
            ws.db.audit(c, ws.actor, "grafis.scb.review_accepted", "file", os.path.basename(path),
                        {"accepted": accepted, "reason": confirm_reason})
    nesting_dir = nesting_dir or ws.db.get_setting("nesting.inbox") or os.path.join(ws.folder, "nesting")
    dup = ws.db.one("SELECT id, handed_over FROM grafis_results WHERE sha256=?", (chk.sha256,))
    target = None
    status = "checked" if chk.ok else "needs_review"
    if not chk.lines:
        status = "unmatched"
    if handover and chk.ok and not (dup and dup["handed_over"]):
        os.makedirs(nesting_dir, exist_ok=True)
        target = os.path.join(nesting_dir, os.path.basename(path))
        if os.path.exists(target) and hashlib.sha256(open(target, "rb").read()).hexdigest() != chk.sha256:
            base, ext = os.path.splitext(target)
            target = f"{base}_{chk.sha256[:8]}{ext}"
        shutil.copy2(path, target + ".part")
        os.replace(target + ".part", target)
        _append_batch_row(nesting_dir, target, chk)
        status = "handed_over"
    elif dup and dup["handed_over"]:
        status = "duplicate"
    with ws.db.tx() as c:
        if dup:
            c.execute("UPDATE grafis_results SET checked_at=?, issues=?, status=? WHERE id=?",
                      (now(), json.dumps(chk.issues, ensure_ascii=False), status if status != "duplicate" else
                       "handed_over", dup["id"]))
        else:
            c.execute("INSERT INTO grafis_results(sha256, path, checked_at, info, pieces, lines, issues, status, "
                      "handed_over, nesting_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (chk.sha256, path, now(), json.dumps(chk.info, ensure_ascii=False),
                       json.dumps(chk.pieces, ensure_ascii=False), json.dumps(chk.lines),
                       json.dumps(chk.issues, ensure_ascii=False), status, int(target is not None), target))
        if target:
            for ln in chk.lines:
                c.execute("UPDATE revisions SET status='sent_to_nesting' WHERE id=? AND status NOT IN "
                          "('superseded','cancelled')", (ln["revision_id"],))
        ws.db.audit(c, ws.actor, f"grafis.scb.{status}", "file", os.path.basename(path),
                    {"lines": [ln["line_number"] for ln in chk.lines], "issues": chk.issues, "nesting": target})
    out = {"status": status, "check": chk, "nesting_path": target, "cutting": None}
    if cut and status == "handed_over":
        out["cutting"] = auto_cut(ws, chk, target)
        if out["cutting"]["status"] != "done":
            out["status"] = "cutting_" + out["cutting"]["status"]
        else:
            out["status"] = "cutting_done"
    return out


BATCH_COLUMNS = ["marker", "file", "size", "qty", "width", "material", "orders"]


def _append_batch_row(folder: str, scb_path: str, chk: ScbCheck) -> None:
    """``nesting_orders.csv``: one row per SCB received from GRAFIS (UTF-8 with BOM, opens in Excel)."""
    path = os.path.join(folder, "nesting_orders.csv")
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(BATCH_COLUMNS)
        w.writerow([os.path.splitext(os.path.basename(scb_path))[0], os.path.basename(scb_path),
                    ";".join(chk.info.get("sizes", [])), chk.info.get("bundles"), chk.info.get("width_mm"),
                    chk.info.get("material") or "", ";".join(ln["line_number"] for ln in chk.lines)])
