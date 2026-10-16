"""Hand-over to GRAFIS (phase 1: GRAFIS computes the pattern).

For one order line LEVEL7 MTM produces the X values GRAFIS needs, from the catalog rules, next to what the GRAFIS
model currently holds (default "_xxxxx_x" value and the range of its standard sizes). The size entry is named
after the order line (e.g. ``9-53-1_2``) so the GRAFIS result stays traceable.

Output: ``<workspace>/grafis/<line>_X.csv`` (UTF-8 with BOM, opens in Excel) and ``<line>_X.json``.
The exact file format of GRAFIS' X-value import is not known yet; until it is, the sheet is for entry in the
GRAFIS X-value window. Nothing is written into the MDL.
"""
from __future__ import annotations

import csv
import json
import os

from ..grafis.xvalues import read_xvalues


def x_sheet(ws, rid: int, mdl_path: str | None = None, part: int = 2, out_dir: str | None = None) -> dict:
    rev = ws.revision(rid)
    _, cat, res = ws.resolve_order(rid)
    style = cat["products"][res.product]["styles"][res.style]
    table = read_xvalues(mdl_path, part) if mdl_path and os.path.exists(mdl_path) else {}
    rules = {r["id"]: r for r in style["rules"]}
    rows, warnings = [], []
    for t in res.trace:
        if t.status != "applied" and t.status != "overridden":
            if t.status == "skipped" and rules.get(t.rule_id):
                warnings.append(f"{t.rule_id}: {t.message}")
            continue
        r = rules.get(t.rule_id, {})
        piece, _, var = t.target.split("/")
        num = int(var[1:]) if var[1:].isdigit() and var.startswith("X") else None
        g = table.get(num) if num is not None and piece == f"{part:03d}" else None
        rng = (min(g.sizes.values()), max(g.sizes.values())) if g and g.sizes else None
        row = {"piece": piece, "x": var, "x_name": (g.name if g else r.get("target", {}).get("x_name", "")),
               "order_value_mm": round(t.value, 2) if t.value is not None else None, "source": t.source,
               "source_value_mm": t.source_value, "rule": t.rule_id, "rule_status": r.get("status", ""),
               "superseded": t.status == "overridden",
               "grafis_default_mm": g.default if g else None,
               "grafis_size_range_mm": f"{rng[0]:g}–{rng[1]:g}" if rng else "",
               "in_grafis_table": bool(g)}
        if g is None and table:
            warnings.append(f"{var} (rule {t.rule_id}) is not in the GRAFIS X table of part {part:03d}")
        if rng and t.value is not None and not rng[0] <= t.value <= rng[1]:
            warnings.append(f"{var} {row['x_name']}: order value {t.value:g} mm is outside the GRAFIS standard sizes "
                            f"{rng[0]:g}–{rng[1]:g} mm (information, not an error)")
        rows.append(row)
    supplied = {int(r["x"][1:]) for r in rows if r["piece"] == f"{part:03d}" and r["x"][1:].isdigit()
                and not r["superseded"]}
    for num, g in sorted(table.items()):
        if num not in supplied:
            warnings.append(f"X{num} {g.name or ''}: not supplied by the order — GRAFIS would use its default "
                            f"{g.default:g} mm")
    unconfirmed = [r["rule"] for r in rows if r["rule_status"] != "confirmed"]
    doc = {"order_line": rev["line_number"], "grafis_size_name": rev["line_number"], "product": res.product,
           "style": res.style, "part": f"{part:03d}", "mdl": os.path.basename(mdl_path) if mdl_path else None,
           "options": rev["normalized"]["options"], "quantity": rev["quantity"],
           "fabric_width_mm": rev["normalized"].get("fabric_width_mm"), "rows": rows, "warnings": warnings,
           "unconfirmed_rules": unconfirmed, "pieces_required": list(res.piece_plan),
           "issues": [i.message for i in res.issues if i.severity in ("blocking", "review")]}
    out_dir = out_dir or os.path.join(ws.folder, "grafis")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, f"{rev['line_number']}_X")
    with open(base + ".json", "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
    with open(base + ".csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["order line / GRAFIS size", rev["line_number"]])
        w.writerow(["options"] + [f"{k}={v}" for k, v in doc["options"].items()])
        w.writerow([])
        cols = ["piece", "x", "x_name", "order_value_mm", "source", "source_value_mm", "rule", "rule_status",
                "grafis_default_mm", "grafis_size_range_mm"]
        w.writerow(cols)
        for r in rows:
            if not r["superseded"]:
                w.writerow([r[c] for c in cols])
        w.writerow([])
        for m in warnings:
            w.writerow(["warning", m])
    doc["files"] = [base + ".csv", base + ".json"]
    with ws.db.tx() as c:
        ws.db.audit(c, ws.actor, "grafis.x_sheet", "revision", rid, {"files": doc["files"]})
    return doc
