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
