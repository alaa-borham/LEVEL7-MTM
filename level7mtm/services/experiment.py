"""Controlled experiment: the same ERP order calculated twice with one measurement changed.

Used for the "FL only" acceptance check. It compares two generations piece by piece and edge by edge, checks
seam relations and quantities, writes both results to LEVEL7 Nesting files and (when the LEVEL7 Nesting source is
available) re-opens them there to confirm that dimensions and order identity survive the hand-over.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys

from ..pattern.geometry import bbox


def _piece_map(result):
    return {p.id: p for p in result.pieces}


def compare(a, b) -> dict:
    """Edge lengths, bounding boxes and moved points of every piece of two PatternResults."""
    pa, pb = _piece_map(a), _piece_map(b)
    out = {}
    for pid in sorted(set(pa) | set(pb)):
        if pid not in pa or pid not in pb:
            out[pid] = {"present": [pid in pa, pid in pb]}
            continue
        x, y = pa[pid], pb[pid]
        ba, bb = bbox(x.seam), bbox(y.seam)
        edges = {e: {"a": x.edge_lengths[e], "b": y.edge_lengths[e], "delta": y.edge_lengths[e] - x.edge_lengths[e]}
                 for e in x.edge_lengths}
        out[pid] = {
            "edges": edges,
            "bbox_a": ba, "bbox_b": bb,
            "height_delta": (bb[3] - bb[1]) - (ba[3] - ba[1]),
            "width_delta": (bb[2] - bb[0]) - (ba[2] - ba[0]),
            "copies": [(x.copies, x.mirrored), (y.copies, y.mirrored)],
            "production": [x.production, y.production],
            "notches_moved": [max(abs(n1["position"][0] - n2["position"][0]), abs(n1["position"][1] - n2["position"][1]))
                              for n1, n2 in zip(x.notches, y.notches)],
            "identical": x.cut == y.cut and x.seam == y.seam,
        }
    return out


def changed_points(a, b, tol=1e-6) -> dict[str, tuple]:
    out = {}
    for k, p in a.points.items():
        q = b.points.get(k)
        if q is not None and (abs(p[0] - q[0]) > tol or abs(p[1] - q[1]) > tol):
            out[k] = (q[0] - p[0], q[1] - p[1])
    return out


def nesting_check(path: str, nesting_src: str | None, generations: dict[str, object]) -> dict:
    """Open the exchange file in LEVEL7 Nesting, compare geometry with the generation, nest once."""
    if not nesting_src or not os.path.isdir(nesting_src):
        return {"available": False}
    if nesting_src not in sys.path:
        sys.path.insert(0, nesting_src)
    from level7.model import FabricSettings, NestSettings  # noqa: PLC0415
    from level7.project_storage.store import load_project  # noqa: PLC0415
    from level7.service import run_nesting  # noqa: PLC0415
    proj = load_project(path)
    diffs, identity = [], []
    for pc in proj.pieces:
        for size, geom in pc.sizes.items():
            order = size.split(":", 1)[1]
            res = generations[order]
            src = next(p for p in res.pieces if f"{res.model_id}:{p.id}" == pc.uid)
            d = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(geom.cut, src.cut))
            diffs.append(d if len(geom.cut) == len(src.cut) else float("inf"))
            identity.append(size)
    marker, info = run_nesting(proj, FabricSettings(width=1450), NestSettings(time_limit=5, engine="builtin"),
                               workers=1)
    placed = [pl for pl in marker.placements if pl.placed]
    return {"available": True, "pieces": len(proj.pieces), "sizes": sorted(set(identity)),
            "max_geometry_difference_mm": max(diffs) if diffs else None,
            "placed": len(placed), "valid": info["validation"].ok,
            "placed_sizes": sorted({pl.size for pl in placed}),
            "quantities": proj.cut_order.size_quantities, "piece_counts": proj.cut_order.piece_counts,
            "length_mm": round(marker.length, 1)}


def fl_experiment(ws, xml_path: str, line: str, new_fl_cm: str, out_dir: str, nesting_src: str | None = None) -> dict:
    """Import the real order, calculate; import the same order as next revision with only FL changed, calculate;
    compare; write both to nesting (TEST files: the base pattern is not approved) and check them."""
    from ..pattern.serialize import result_from_json  # noqa: PLC0415
    os.makedirs(out_dir, exist_ok=True)
    data = open(xml_path, "rb").read()
    ws.import_data(data, os.path.basename(xml_path), xml_path)
    rid_a = next(r["id"] for r in ws.list_revisions() if r["line_number"] == line)
    ga = ws.generate(rid_a)
    rev = ws.revision(rid_a)
    old_fl = rev["raw_line"]["measurements"]["FL"]["value"]
    base_line, revno = line.rsplit("_", 1)
    new_line = f"{base_line}_{int(revno) + 1}"
    # only FL of this line changes; the revision suffix of the whole file moves on (as the ERP does)
    text = data.decode("utf-8").replace(f"_{revno}\"", f"_{int(revno) + 1}\"")
    pat = re.compile(r'(<Order Number="' + re.escape(new_line) + r'".*?Abbreviation="FL" Value=")([^"]+)(")', re.S)
    text, n = pat.subn(lambda m: m.group(1) + new_fl_cm + m.group(3), text, count=1)
    if n != 1:
        raise ValueError("FL of the order line not found")
    changed = text.encode("utf-8")
    changed_name = os.path.basename(xml_path).replace(f"_{revno}", f"_{int(revno) + 1}")
    with open(os.path.join(out_dir, changed_name), "wb") as fh:
        fh.write(changed)
    ws.import_data(changed, changed_name)
    rid_b = next(r["id"] for r in ws.list_revisions() if r["line_number"] == new_line)
    gb = ws.generate(rid_b)
    ra, rb = ws.pattern(ga["id"]), ws.pattern(gb["id"])
    # points are not stored with the generation: re-evaluate the model for the moved-points report
    from ..pattern.model import evaluate_model, load_model  # noqa: PLC0415
    model = load_model(ws.document(ga["model_doc"]))
    pts_a = evaluate_model(model, ga["inputs"], ga["options"])
    pts_b = evaluate_model(model, gb["inputs"], gb["options"])
    input_diff = {k: (ga["inputs"].get(k), gb["inputs"].get(k)) for k in set(ga["inputs"]) | set(gb["inputs"])
                  if ga["inputs"].get(k) != gb["inputs"].get(k)}
    meas_diff = {}
    ma, mb = rev["normalized"]["measurements"], ws.revision(rid_b)["normalized"]["measurements"]
    for k in set(ma) | set(mb):
        if (ma.get(k) or {}).get("value_mm") != (mb.get(k) or {}).get("value_mm"):
            meas_diff[k] = ((ma.get(k) or {}).get("value_mm"), (mb.get(k) or {}).get("value_mm"))
    report = {
        "order": line, "changed_order": new_line, "fl_cm": [old_fl, new_fl_cm],
        "measurement_differences": meas_diff, "variable_differences": input_diff,
        "moved_points": changed_points(pts_a, pts_b),
        "pieces": compare(ra, rb),
        "relations": {"a": ga["result"]["seam_relations"], "b": gb["result"]["seam_relations"]},
        "checks": {"a": ga["result"]["checks"], "b": gb["result"]["checks"]},
        "quantity": [ws.revision(rid_a)["quantity"], ws.revision(rid_b)["quantity"]],
        "generation_status": [ga["status"], gb["status"]],
        "model": [ra.model_id, ra.model_version, ra.model_status],
        "rules_used_for_X1": [t for t in gb["result"]["rules_trace"] if t["target"].endswith("/X1")],
    }
    # nesting: rev A is superseded once rev B arrived, so each result goes into its own TEST file
    ja = _exchange_one(ws, ga, rid_a, os.path.join(out_dir, f"TEST-{line}.l7n"))
    jb = ws.send_to_nesting([rid_b], os.path.join(out_dir, f"TEST-{new_line}.l7n"), test_only=True)
    report["nesting"] = {
        "a": nesting_check(ja, nesting_src, {f"{line}": ra}),
        "b": nesting_check(jb["path"], nesting_src, {f"{new_line}": rb}),
        "files": [ja, jb["path"]],
    }
    with open(os.path.join(out_dir, "fl_experiment.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    return report


def _exchange_one(ws, g, rid, path):
    """Write the (now superseded) first calculation directly — the workflow refuses superseded revisions."""
    from ..nesting.exchange import ExchangeLine, write_exchange  # noqa: PLC0415
    from ..pattern.serialize import result_from_json  # noqa: PLC0415
    rev = ws.revision(rid)
    model = ws.document(g["model_doc"])
    ln = ExchangeLine(rev["line_number"].rsplit("-", 1)[0], rev["line_ref"], rev["revision"], int(rev["quantity"]),
                      model["model_id"], str(model["version"]), model["status"], g["id"],
                      result_from_json(g["result"]), rev["normalized"].get("fabric_width_mm"),
                      rev["raw_line"].get("fabric_number"), g["result"].get("shrink_factors", {}))
    write_exchange([ln], path, test_only=True)
    return path
