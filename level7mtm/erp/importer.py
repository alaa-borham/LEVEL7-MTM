"""ERP XML → normalised order lines, using a versioned mapping profile.

Import success only means the data was read and checked; it says nothing about the pattern.
Every problem is reported with a severity:

* ``error``  — the file cannot be used (damaged XML, unknown format);
* ``review`` — the line is imported but needs a person (unknown option code, missing / out-of-range
  measurement, unparsable number ...). Such a line cannot be approved for production until fixed.
* ``info``   — noted for traceability.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .mapping import UNITS_TO_MM, measure_specs, profile_hash, select, select_one
from .xmlsafe import LoadedXml, XmlError, load_bytes, local, structure_report


@dataclass
class Problem:
    severity: str
    code: str
    message: str
    field: str | None = None


@dataclass
class Measured:
    code: str
    source_name: str
    source_value: str
    source_unit: str
    value_mm: float | None
    kind: str
    label: dict
    meaning: str
    status: str
    variable: str


@dataclass
class ImportedLine:
    number: str
    line_ref: str | None
    revision: str | None
    reseller: str | None
    order_no: str | None
    line_no: str | None
    date: str | None
    item: str | None
    quantity: int | None
    fabric_number: str | None
    fabric_width_mm: float | None
    fabric_color: str | None
    measurement_table: str | None
    basic_size: str | None
    measurements: dict[str, Measured] = field(default_factory=dict)
    options: dict[str, str] = field(default_factory=dict)
    option_codes: list[str] = field(default_factory=list)
    programs: list[dict] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return any(p.severity in ("review", "error") for p in self.problems)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ImportResult:
    sha256: str
    encoding: str
    root: str
    profile_id: str
    profile_version: str
    profile_sha256: str
    order_number: str | None
    reseller: str | None
    order_date: str | None
    lines: list[ImportedLine]
    problems: list[Problem]
    structure: dict
    raw: bytes

    @property
    def ok(self) -> bool:
        return not any(p.severity == "error" for p in self.problems)


# the order number: reseller-order-line_revision; the reseller may itself hold a dash (L-FR20-373-1_1,
# FR8-7776-X-2_2): the last three parts are order, line and revision
LINE_ANY = r"^(?P<reseller>[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*?)-(?P<order>[A-Za-z0-9]+)-(?P<line>[A-Za-z0-9]+)_(?P<revision>\d+)$"


def vkey(name: str) -> str:
    """A Variation name as it is matched: no case, spaces (also non-breaking), "_" or dashes of any kind —
    "Hidden With Zip Square", "hidden_with_zip square" and "Hidden With Zip\u00a0Square" are the same (the
    piece options screen compares them so too)."""
    return re.sub(r"[\s_\-\u2010-\u2015\u2212\u00a0\u200f\u200e]+", "", str(name)).lower()


def _num(text: str) -> float:
    t = text.strip()
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", t):
        raise ValueError(f"not a plain decimal number: {text!r}")
    return float(t)


def _date(text, fmt):
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), fmt).isoformat()
    except ValueError:
        return None


def import_bytes(data: bytes, profile: dict) -> ImportResult:
    ph = profile_hash(profile)
    try:
        lx: LoadedXml = load_bytes(data)
    except XmlError as e:
        import hashlib
        return ImportResult(hashlib.sha256(data).hexdigest(), "", "", profile["profile_id"], profile["version"], ph,
                            None, None, None, [], [Problem("error", "xml.invalid", str(e))], {}, data)
    root = lx.root
    ns = profile.get("namespaces", {})
    problems: list[Problem] = []
    det = profile["detect"]
    if local(root.tag) != det["root"]:
        problems.append(Problem("error", "profile.root", f"root element <{local(root.tag)}> does not match profile "
                                                        f"'{profile['profile_id']}' (expects <{det['root']}>)"))
    for k, v in det.get("attributes", {}).items():
        if root.attrib.get(k) != v:
            problems.append(Problem("error", "profile.detect", f"attribute {k}={root.attrib.get(k)!r}, profile expects {v!r}"))
    res = ImportResult(lx.sha256, lx.encoding, local(root.tag), profile["profile_id"], profile["version"], ph,
                       None, None, None, [], problems, structure_report(root), data)
    if problems:
        return res
    o = profile["order"]
    res.order_number = select_one(root, o["number"], ns)
    res.reseller = select_one(root, o.get("reseller", ""), ns) if o.get("reseller") else None
    res.order_date = _date(select_one(root, o["date"], ns), o.get("date_format", "%Y-%m-%d")) if o.get("date") else None
    line_els = select(root, profile["lines"]["path"], ns)
    if not line_els:
        problems.append(Problem("error", "lines.none", f"no order lines found at {profile['lines']['path']}"))
    seen = set()
    for el in line_els:
        ln = _line(el, profile, ns)
        if ln.number in seen:
            ln.problems.append(Problem("review", "line.duplicate", f"line {ln.number} appears twice in this file"))
        seen.add(ln.number)
        res.lines.append(ln)
    return res


def _line(el, profile, ns) -> ImportedLine:
    L = profile["lines"]
    num = select_one(el, L["number"], ns) or ""
    ln = ImportedLine(num, None, None, None, None, None, None, select_one(el, L.get("item", ""), ns) if L.get("item") else None,
                      None, None, None, None, None, None)
    p = ln.problems
    ln.date = _date(select_one(el, L.get("date", ""), ns) if L.get("date") else None,
                    profile["order"].get("date_format", "%Y-%m-%d"))
    rx = profile.get("line_number", {}).get("regex")
    if rx:
        m = re.match(rx, num) or re.match(LINE_ANY, num)      # e.g. L-FR20-373-1_1: a reseller with a dash
        if m:
            g = m.groupdict()
            ln.reseller, ln.order_no, ln.line_no, ln.revision = g.get("reseller"), g.get("order"), g.get("line"), g.get("revision")
            ln.line_ref = f"{ln.reseller}-{ln.order_no}-{ln.line_no}"
        else:
            p.append(Problem("review", "line.number", f"line number {num!r} does not match the expected pattern", "number"))
            ln.line_ref, ln.revision = num, "0"
    q = L["quantity"]
    qv = select_one(el, q["path"], ns)
    try:
        ln.quantity = int(_num(qv)) if qv is not None else None
        if ln.quantity is None:
            p.append(Problem("review", "quantity.missing", "quantity missing", "quantity"))
        elif not q.get("min", 1) <= ln.quantity <= q.get("max", 10 ** 6) or _num(qv) != ln.quantity:
            p.append(Problem("review", "quantity.range", f"quantity {qv} outside {q.get('min', 1)}–{q.get('max')}", "quantity"))
    except ValueError:
        p.append(Problem("review", "quantity.invalid", f"quantity {qv!r} is not a whole number", "quantity"))
    ln.fabric_number = select_one(el, L.get("fabric_number", ""), ns) if L.get("fabric_number") else None
    ln.fabric_color = select_one(el, L.get("fabric_color", ""), ns) if L.get("fabric_color") else None
    fw = L.get("fabric_width")
    if fw:
        v = select_one(el, fw["path"], ns)
        try:
            ln.fabric_width_mm = _num(v) * UNITS_TO_MM[fw.get("unit", "mm")] if v is not None else None
            if ln.fabric_width_mm is None:
                p.append(Problem("review", "fabric.width_missing", "fabric width missing", "fabric_width"))
        except ValueError:
            p.append(Problem("review", "fabric.width_invalid", f"fabric width {v!r} is not a number", "fabric_width"))
    ln.measurement_table = select_one(el, L.get("measurement_table", ""), ns) if L.get("measurement_table") else None
    ln.basic_size = select_one(el, L.get("basic_size", ""), ns) if L.get("basic_size") else None

    M = profile["measurements"]
    exp_table = M.get("measurement_table")
    if exp_table and ln.measurement_table != exp_table:
        p.append(Problem("review", "measure.table", f"measurement table {ln.measurement_table!r}, profile expects {exp_table!r}"))
    specs = measure_specs(profile)
    for t in select(el, M["path"], ns):
        code = select_one(t, M["code"], ns)
        name = select_one(t, M.get("name", ""), ns) if M.get("name") else ""
        raw = select_one(t, M["value"], ns)
        if code is None:
            p.append(Problem("review", "measure.no_code", f"measurement {name!r} without code"))
            continue
        if code in ln.measurements:
            p.append(Problem("review", "measure.duplicate", f"measurement {code} appears twice", code))
            continue
        sp = specs.get(code)
        if sp is None:
            p.append(Problem("review", "measure.unknown", f"measurement {code} ({name}) = {raw} is not defined in the "
                                                           f"mapping profile", code))
            continue
        try:
            v = _num(raw or "")
            mm = v * UNITS_TO_MM[sp.unit]
            if sp.range_src and not sp.range_src[0] <= v <= sp.range_src[1]:
                p.append(Problem("review", "measure.range", f"{code} = {raw} {sp.unit} outside {sp.range_src[0]:g}–"
                                                             f"{sp.range_src[1]:g} {sp.unit}", code))
        except ValueError:
            p.append(Problem("review", "measure.invalid", f"{code} = {raw!r} is not a number", code))
            mm = None
        ln.measurements[code] = Measured(code, name or "", raw or "", sp.unit, mm, sp.kind, sp.label, sp.meaning,
                                         sp.status, sp.variable)
    for code, sp in specs.items():
        if sp.required and code not in ln.measurements:
            p.append(Problem("review", "measure.missing", f"required measurement {code} ({sp.label.get('en', code)}) "
                                                           f"is missing", code))

    O = profile["options"]
    omap = O.get("map", {})
    loose = O.get("match") == "exact_ignore_case"
    norm = {vkey(k): k for k in omap} if loose else {k: k for k in omap}
    for code in select(el, O["path"], ns):
        ln.option_codes.append(code)
        key = norm.get(vkey(code) if loose else code)
        if key is None:
            p.append(Problem("review", "option.unknown", f"design code {code!r} is not mapped to a model option", code))
            continue
        g, v = omap[key]["group"], omap[key]["value"]
        if g in ln.options and ln.options[g] != v:
            p.append(Problem("review", "option.conflict", f"two values for {g}: {ln.options[g]} and {v}", g))
            continue
        ln.options[g] = v
    for rule in profile.get("conditional_required", []):
        if all(ln.options.get(g) == v for g, v in rule["when"].items()):
            for code in rule["require"]:
                if code not in ln.measurements:
                    p.append(Problem("review", "measure.missing_for_option",
                                     f"{code} is required for {rule['when']} but missing", code))
    P = profile.get("programs")
    if P:
        for pe in select(el, P["path"], ns):
            name = select_one(pe, P["name"], ns)
            entry = {"name": name, "value": select_one(pe, P["value"], ns), "type": select_one(pe, P["type"], ns)}
            spec = P.get("map", {}).get(name)
            if spec is None:
                p.append(Problem("review", "program.unknown", f"program value {name!r} is not defined in the profile"))
            else:
                entry.update({"variable": spec["variable"], "use_in_calculation": spec.get("use_in_calculation", False)})
            ln.programs.append(entry)
    return ln


def import_file(path: str, profile: dict) -> ImportResult:
    with open(path, "rb") as fh:
        data = fh.read()
    return import_bytes(data, profile)
