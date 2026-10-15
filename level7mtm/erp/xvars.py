"""X-value table: GRAFIS X numbers (X-Werte) fed from order measurements.

Each entry: ``x`` (e.g. "X7"), ``source`` (measurement codes, first one present wins — e.g. COS or COG),
optional ``formula`` over measurement codes (mm), the GRAFIS evidence and a status (``proposed`` /
``confirmed``). Values are millimetres. Editable from the application; every save is a new version.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from ..formula.expr import FormulaError, compute

FORMAT = "level7mtm-xvars"


class XVarError(ValueError):
    pass


def load_xvars(path_or_dict) -> dict:
    doc = copy.deepcopy(path_or_dict) if isinstance(path_or_dict, dict) else json.load(open(path_or_dict, encoding="utf-8"))
    if doc.get("format") != FORMAT:
        raise XVarError("not an X-value table")
    seen = set()
    for e in doc["entries"]:
        if not re.fullmatch(r"X(G)?\d+", e["x"]):
            raise XVarError(f"invalid X name {e['x']!r} (expected X1, X23, XG1 ...)")
        if e["x"] in seen:
            raise XVarError(f"{e['x']} defined twice")
        seen.add(e["x"])
        if e.get("status", "proposed") not in ("proposed", "confirmed"):
            raise XVarError(f"{e['x']}: unknown status {e.get('status')!r}")
    return doc


def xvars_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compute_x(doc: dict, measurements_mm: dict[str, float | None], needed: list[str] | None = None):
    """Return ({X: value_mm}, problems[(severity, x, message)])."""
    values, problems = {}, []
    entries = {e["x"]: e for e in doc["entries"]}
    for name in (needed if needed is not None else list(entries)):
        e = entries.get(name)
        if e is None:
            problems.append(("review", name, f"{name} is used by the model but not defined in the X table"))
            continue
        if e.get("status", "proposed") != "confirmed":
            problems.append(("review", name, f"{name} mapping is not confirmed yet ({e.get('evidence', '')})"))
        if e.get("formula"):
            try:
                values[name] = compute(e["formula"], {k: v for k, v in measurements_mm.items() if v is not None})
            except FormulaError as err:
                problems.append(("review", name, f"{name} = {e['formula']}: {err}"))
            continue
        src = next((s for s in e.get("source", []) if measurements_mm.get(s) is not None), None)
        if src is None:
            problems.append(("review", name, f"{name}: none of {e.get('source')} is present in the order"))
            continue
        values[name] = float(measurements_mm[src])
    return values, problems
