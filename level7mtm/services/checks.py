"""Measurement checks of a customer / product, for all his patterns or for one pattern (owner's settings).

For each measurement of the XML (in the XML's unit, e.g. cm):
    range    from … to …: a value outside holds the order for review (an empty side is open)
    none     no check: any value is accepted
    formula  the value is computed from the other measurements and the order's Programs, e.g. ``FL - 10``,
             ``CH / 2 + 3`` or ``Cuff_Individuel_Hight / 10`` (``M`` is the measurement's own XML value) — when the XML has none, or it is outside from … to … (both given), or
             always (from … to … empty)
A check replaces the range of the ERP mapping for that measurement. The checks of one pattern (the Variation that
chooses it) come over the ones set for all the patterns.
"""
from __future__ import annotations

import re

from ..erp.importer import Measured, Problem
from ..erp.mapping import UNITS_TO_MM
from ..formula.expr import FormulaError, compute
from .workflow import WorkflowError

ALL = "*"                                         # the checks of every pattern of the customer / product
MODES = ("range", "none", "formula")


def _key(cid: str) -> str:
    return f"measure.checks@{cid}"


def get_checks(ws, cid: str) -> dict[str, dict[str, dict]]:
    """{pattern (ALL or its Variation): {measurement code: {mode, min, max, formula}}}."""
    return {p: dict(rows) for p, rows in (ws.db.get_setting(_key(cid)) or {}).items()}


def _expr(text: str) -> str:
    return re.sub(r"\{([A-Za-z][A-Za-z0-9_]*)\}", r"\1", text.strip())


def _clean(code: str, r: dict) -> dict | None:
    mode = r.get("mode") or "range"
    if mode not in MODES:
        raise WorkflowError(f"measurement {code}: unknown check '{mode}'")
    lo, hi = r.get("min"), r.get("max")
    lo = None if lo in (None, "") else float(lo)
    hi = None if hi in (None, "") else float(hi)
    if lo is not None and hi is not None and lo > hi:
        raise WorkflowError(f"measurement {code}: the lowest value is above the highest")
    out = {"mode": mode, "min": lo, "max": hi}
    if mode == "formula":
        f = (r.get("formula") or "").strip()
        if not f:
            raise WorkflowError(f"measurement {code}: write the formula (e.g. FL - 10)")
        try:
            compute(_expr(f), lambda name: 1.0)          # every name known: only read it
        except FormulaError as e:
            raise WorkflowError(f"measurement {code}: the formula '{f}' is not valid: {e}") from None
        out["formula"] = f
    elif mode == "range" and lo is None and hi is None:
        return None                                    # nothing set: as the ERP mapping says
    return out


def set_checks(ws, cid: str, pattern: str, rows: dict[str, dict]) -> bool:
    """The checks of one pattern (or ALL) of the customer / product; other customers keep theirs. Returns True
    when changed."""
    pattern = (pattern or ALL).strip() or ALL
    clean = {}
    for code, r in rows.items():
        c = _clean(code, r)
        if c is not None:
            clean[code] = c
    every = get_checks(ws, cid)
    if every.get(pattern, {}) == clean:
        return False
    if clean:
        every[pattern] = clean
    else:
        every.pop(pattern, None)
    ws.db.set_setting(_key(cid), every)
    return True


def rules_for(ws, cid: str, option_codes) -> dict[str, dict]:
    """The checks of an order line: the customer's for all patterns, then the ones of its pattern."""
    from .customers import _plain
    every = get_checks(ws, cid)
    if not every:
        return {}
    out = dict(every.get(ALL, {}))
    have = {_plain(c) for c in option_codes or []}
    for p, rows in every.items():
        if p != ALL and _plain(p) in have:
            out.update(rows)
    return out


def _value(m: Measured | None) -> float | None:
    try:
        return float(str(m.source_value).replace(",", ".")) if m is not None else None
    except ValueError:
        return None


def program_name(name: str) -> str:
    """A Program of the XML as a name in a formula: "Cuff Individuel Hight" → Cuff_Individuel_Hight."""
    n = re.sub(r"\W+", "_", str(name or "").strip()).strip("_")
    return ("P_" + n) if n[:1].isdigit() else n


def program_values(programs) -> dict[str, float]:
    """The number of each Program of an order line (<Program VariationName=… Value=…/>), by its formula name and
    by its variable in the ERP mapping."""
    out = {}
    for pr in programs or []:
        try:
            v = float(str(pr.get("value")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        if pr.get("name"):
            out[program_name(pr["name"])] = v
        if pr.get("variable"):
            out.setdefault(pr["variable"], v)
    return out


def apply_checks(ln, rules: dict[str, dict], specs: dict) -> None:
    """The customer's checks on one order line (in place): its range problems replaced, values computed."""
    def drop(code, kinds):
        ln.problems = [p for p in ln.problems if not (p.field == code and p.code in kinds)]

    order = sorted(rules.items(), key=lambda kv: kv[1]["mode"] == "formula")   # the formulas last: they read the rest
    for code, r in order:
        m = ln.measurements.get(code)
        v = _value(m)
        lo, hi = r.get("min"), r.get("max")
        unit = m.source_unit if m is not None else getattr(specs.get(code), "unit", "cm")
        outside = v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi))
        drop(code, ("measure.range",))
        if r["mode"] == "none":
            continue
        if r["mode"] == "range":
            if outside:
                ln.problems.append(Problem("review", "measure.range", f"{code} = {m.source_value} {unit} outside "
                                           f"{_g(lo)}–{_g(hi)} {unit}", code))
            continue
        if not (v is None or outside or (lo is None and hi is None)):
            continue                                   # the XML value is fine: kept
        env = program_values(ln.programs)              # e.g. Cuff_Individuel_Hight = 76.2 (as in the XML)
        env.update({c: x for c, x in ((c, _value(mm)) for c, mm in ln.measurements.items()) if x is not None})
        if v is not None:
            env["M"] = v
        try:
            new = round(float(compute(_expr(r["formula"]), env)), 2)
        except FormulaError as e:
            ln.problems.append(Problem("review", "measure.formula", f"{code}: the formula {r['formula']} could not "
                                       f"be computed: {e}", code))
            continue
        sp = specs.get(code)
        ln.measurements[code] = Measured(code, m.source_name if m else code, f"{new:g}", unit,
                                         new * UNITS_TO_MM.get(unit, 10.0),
                                         sp.kind if sp else (m.kind if m else "length"),
                                         sp.label if sp else (m.label if m else {"en": code}),
                                         sp.meaning if sp else (m.meaning if m else ""),
                                         sp.status if sp else (m.status if m else "proposed"),
                                         sp.variable if sp else (m.variable if m else code))
        drop(code, ("measure.range", "measure.missing", "measure.invalid", "measure.missing_for_option"))
        was = f"{m.source_value} {unit}" if m is not None and m.source_value not in (None, "") else "none"
        ln.problems.append(Problem("info", "measure.formula", f"{code} = {new:g} {unit} from the formula "
                                   f"{r['formula']} (XML: {was})", code))


def _g(x) -> str:
    return "" if x is None else f"{x:g}"


# ------------------------------------------------------------------ limits learned from the customer's orders
# owner: most distorted patterns came from a wrong value in the XML (e.g. DSB ten times too large): held before
# CAD when a value is far from what this customer's orders usually have — "DSB = 165 cm, usually 14–20 cm"
LEARNED_MIN = 20                                   # orders with the measurement before limits are learned
LEARNED_MAX = 3000                                 # the newest orders looked at
LEARNED_FENCE = 3.0                                # outside the middle half widened 3 times on each side
_LEARNED: dict = {}                                # cid -> (time, {(code, unit): (lo, hi, low, high, n)})
_LEARNED_S = 600.0


def _quantile(xs: list[float], q: float) -> float:
    k = (len(xs) - 1) * q
    i = int(k)
    return xs[i] if i + 1 >= len(xs) else xs[i] + (xs[i + 1] - xs[i]) * (k - i)


def learned_limits(ws, cid: str, fresh: bool = False) -> dict:
    """{(code, unit): (lo, hi, usual_low, usual_high, n)} from this customer's orders that were nested and cut."""
    import json
    import time
    hit = _LEARNED.get(cid)
    if hit and not fresh and time.monotonic() - hit[0] < _LEARNED_S:
        return hit[1]
    values: dict = {}
    for r in ws.db.q("SELECT r.normalized FROM revisions r JOIN orders o ON o.id=r.order_id WHERE o.customer_id=? "
                     "AND r.status IN ('completed', 'approved') ORDER BY r.id DESC LIMIT ?", (cid, LEARNED_MAX)):
        try:
            ms = json.loads(r["normalized"]).get("measurements", {})
        except (TypeError, ValueError):
            continue
        for code, m in ms.items():
            try:
                v = float(str(m.get("source_value")).replace(",", "."))
            except (TypeError, ValueError):
                continue
            values.setdefault((code, m.get("source_unit") or "cm"), []).append(v)
    out = {}
    for key, xs in values.items():
        if len(xs) < LEARNED_MIN:
            continue
        xs.sort()
        q1, q3, med = _quantile(xs, 0.25), _quantile(xs, 0.75), _quantile(xs, 0.5)
        iqr = max(q3 - q1, abs(med) * 0.05, 0.5)      # all alike: a small spread still allowed
        out[key] = (q1 - LEARNED_FENCE * iqr, q3 + LEARNED_FENCE * iqr, _quantile(xs, 0.02), _quantile(xs, 0.98),
                    len(xs))
    _LEARNED[cid] = (time.monotonic(), out)
    return out


def apply_learned(ln, limits: dict, rules: dict) -> None:
    """A value far from the customer's usual ones holds the line for review (in place). A measurement with the
    owner's own check (range / none / formula) follows that check only."""
    for code, m in ln.measurements.items():
        if code in rules:
            continue
        v = _value(m)
        lim = limits.get((code, m.source_unit or "cm"))
        if v is None or lim is None:
            continue
        lo, hi, a, b, n = lim
        if lo <= v <= hi:
            continue
        ln.problems.append(Problem("review", "measure.unusual", f"{code} = {m.source_value} {m.source_unit} is "
                                   f"unusual for this customer (usually {_g(round(a, 1))}–{_g(round(b, 1))} "
                                   f"{m.source_unit}, from {n} orders)", code))
