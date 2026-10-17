"""Product catalog and the unified rule engine.

Catalog document (``level7mtm-catalog``, versioned like every other document):

    products → styles → base pattern (model id + version) + measurement table + option groups
    measurement definitions (independent of customer values): code, name, unit, method, order, visibility, editing
    rules: measurement or design option → target (piece, construction, variable) = value expression,
           with execution order, factory scope and size scope; several rules may use the same measurement
    option rules: which pieces an option needs, which variables it sets, which properties it changes
    pieces: production properties (copies, mirrored copies, material, rotations, mirror policy) and the separate
           flags "preview" and "production"

Separation (by design):
    1. this module only computes *pattern variables* (X1, X3 …) from measurements and options;
    2. the base pattern (model file) contains the geometry that turns those variables into shapes.
A variable value alone proves nothing about the pattern: geometry validation happens in the model evaluation.

Value expressions use the formula language; ``{M}`` is the source measurement in mm, ``{CODE}`` another
measurement of the order in mm, and variables set by earlier rules can be used by name.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field

from ..formula.expr import FormulaError, compute
from ..pattern.allowance import Issue

FORMAT = "level7mtm-catalog"


class CatalogError(ValueError):
    pass


@dataclass
class RuleTrace:
    rule_id: str
    source: str
    target: str
    expression: str
    source_value: float | None
    value: float | None
    status: str          # applied / skipped / failed / overridden / out_of_scope / disabled
    message: str = ""


@dataclass
class Resolution:
    product: str
    style: str
    inputs: dict[str, float]
    trace: list[RuleTrace]
    issues: list[Issue]
    piece_plan: dict[str, dict]            # piece id -> properties (+ "required_by")
    base_pattern: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)            # the order's options + configured defaults


def load_catalog(path_or_dict) -> dict:
    doc = copy.deepcopy(path_or_dict) if isinstance(path_or_dict, dict) else json.load(open(path_or_dict, encoding="utf-8"))
    check_catalog(doc)
    return doc


def catalog_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_catalog(doc: dict) -> None:
    if doc.get("format") != FORMAT:
        raise CatalogError("not a LEVEL7 MTM catalog")
    for k in ("catalog_id", "version", "measurements", "products"):
        if k not in doc:
            raise CatalogError(f"catalog misses '{k}'")
    meas = doc["measurements"]
    for code, m in meas.items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", code):
            raise CatalogError(f"invalid measurement code {code!r}")
        for k in ("name", "unit", "kind", "order"):
            if k not in m:
                raise CatalogError(f"measurement {code}: missing '{k}'")
    for pid, prod in doc["products"].items():
        for sid, st in prod.get("styles", {}).items():
            where = f"{pid}/{sid}"
            bp = st.get("base_pattern") or {}
            if not bp.get("model_id") or not bp.get("version"):
                raise CatalogError(f"{where}: base pattern (model id + version) is required")
            for code in st.get("measurement_table", []):
                if code not in meas:
                    raise CatalogError(f"{where}: measurement table uses undefined code {code}")
            ids = set()
            for r in st.get("rules", []):
                if r["id"] in ids:
                    raise CatalogError(f"{where}: duplicate rule id {r['id']}")
                ids.add(r["id"])
                s = r["source"]
                if s["type"] == "measurement" and s["code"] not in meas:
                    raise CatalogError(f"{where} {r['id']}: undefined measurement {s['code']}")
                if s["type"] == "option" and s.get("value") not in st.get("option_groups", {}).get(s.get("group"), []):
                    raise CatalogError(f"{where} {r['id']}: unknown option {s.get('group')}={s.get('value')}")
                t = r["target"]
                if not t.get("piece") or not t.get("variable"):
                    raise CatalogError(f"{where} {r['id']}: target needs piece and variable")
            for orule in st.get("option_rules", []):
                if orule["value"] not in st.get("option_groups", {}).get(orule["group"], []):
                    raise CatalogError(f"{where}: option rule for unknown option {orule['group']}={orule['value']}")
            gp = st.get("grafis_parts")
            if gp is not None:
                if not isinstance(gp, list):
                    raise CatalogError(f"{where}: grafis_parts must be a list of rows")
                for row in gp:
                    opt = row.get("option") or ""
                    if opt not in ("", "*"):
                        g, _, v = opt.partition("=")
                        if v not in st.get("option_groups", {}).get(g, []):
                            raise CatalogError(f"{where}: GRAFIS part {row.get('part')} for unknown option {opt}")
                    n, c, m = row.get("part"), row.get("copies"), row.get("mirrored")
                    if not (isinstance(n, int) and 1 <= n <= 999):
                        raise CatalogError(f"{where}: GRAFIS part number {n!r} invalid")
                    if not (isinstance(c, int) and isinstance(m, int) and 0 <= c <= 99 and 0 <= m <= 99 and c + m):
                        raise CatalogError(f"{where}: GRAFIS part {n}: copies / mirrored must be 0…99, not both 0")
                    if not re.fullmatch(r"[A-Z]", str(row.get("material", ""))):
                        raise CatalogError(f"{where}: GRAFIS part {n}: material is one letter (O, F, …)")


def effective_options(style: dict, options: dict[str, str]) -> tuple[dict[str, str], list[Issue]]:
    """Options of the order plus the owner's default for a group the order does not set (reported as info);
    a required group without value and without default is blocking."""
    out, issues = dict(options), []
    for g, s in ((g, style.get("option_group_settings", {}).get(g, {})) for g in style.get("option_groups", {})):
        if g in out:
            continue
        if s.get("default"):
            out[g] = s["default"]
            issues.append(Issue("info", "option.default", f"design option '{g}' not in the order: default "
                                                         f"'{s['default']}' used"))
        elif s.get("required", True):
            issues.append(Issue("blocking", "option.missing", f"design option '{g}' not set"))
    return out, issues


def grafis_parts_for(style: dict, options: dict[str, str]) -> dict[int, dict] | None:
    """GRAFIS production parts for the chosen options ({part number: row}); None when not configured.
    Rows with option "" or "*" are always produced; an option row overrides a base row of the same part."""
    rows = style.get("grafis_parts")
    if not rows:
        return None
    out: dict[int, dict] = {}
    for row in sorted(rows, key=lambda r: (r.get("option") or "") not in ("", "*")):
        opt = row.get("option") or ""
        if opt in ("", "*") or options.get(opt.partition("=")[0]) == opt.partition("=")[2]:
            out[row["part"]] = row
    return out


def find_style(doc: dict, item: str | None, measurement_table: str | None) -> tuple[str, str]:
    for pid, prod in doc["products"].items():
        if (item and item in prod.get("erp_items", [])) or (measurement_table and measurement_table in
                                                           prod.get("erp_measurement_tables", [])):
            return pid, prod.get("default_style") or next(iter(prod["styles"]))
    if len(doc["products"]) == 1:                     # one product (one garment) for the customer: always it
        pid, prod = next(iter(doc["products"].items()))
        return pid, prod.get("default_style") or next(iter(prod["styles"]))
    raise CatalogError(f"no product for ERP item {item!r} / measurement table {measurement_table!r}")


def target_name(style: dict, target: dict) -> str:
    """Variables of the base piece keep their GRAFIS name (X1); other pieces are prefixed (P005.X1)."""
    base = style["base_pattern"].get("base_piece")
    return target["variable"] if target["piece"] == base else f"P{target['piece']}.{target['variable']}"


def _expr(text: str) -> str:
    return re.sub(r"\{([A-Za-z][A-Za-z0-9_]*)\}", lambda m: "SRC_M" if m.group(1) == "M" else f"MEAS_{m.group(1)}", text)


def _in_scope(lst, value) -> bool:
    return not lst or "*" in lst or (value is not None and value in lst)


def resolve(doc: dict, product: str, style_id: str, measurements_mm: dict[str, float | None],
            options: dict[str, str], factory: str | None = None, size: str | None = None) -> Resolution:
    style = doc["products"][product]["styles"][style_id]
    options, issues = effective_options(style, options)
    trace: list[RuleTrace] = []
    values: dict[str, float] = {}
    writer: dict[str, str] = {}
    env_meas = {f"MEAS_{k}": v for k, v in measurements_mm.items() if v is not None}

    # measurement table completeness (no silent zeros, no reference values)
    for code in style.get("measurement_table", []):
        if measurements_mm.get(code) is None and doc["measurements"][code].get("required"):
            issues.append(Issue("review", "measure.missing", f"measurement {code} of the style table is missing"))

    rules = [dict(r, _kind="measurement") for r in style.get("rules", [])]
    for orule in style.get("option_rules", []):
        for i, v in enumerate(orule.get("variables", [])):
            rules.append({"id": f"{orule['group']}={orule['value']}#{i + 1}", "source": {"type": "option",
                          "group": orule["group"], "value": orule["value"]}, "target": v["target"],
                          "value": v["value"], "order": v.get("order", 1000), "factories": v.get("factories", ["*"]),
                          "sizes": v.get("sizes", ["*"]), "enabled": True, "status": v.get("status", "proposed")})
    for r in sorted(rules, key=lambda r: (r.get("order", 0), r["id"])):
        tname = target_name(style, r["target"])
        src = r["source"]
        srcdesc = src["code"] if src["type"] == "measurement" else f"{src['group']}={src['value']}"
        tr = RuleTrace(r["id"], srcdesc, f"{r['target']['piece']}/{r['target'].get('construction', '')}/"
                                          f"{r['target']['variable']}", r["value"], None, None, "applied")
        trace.append(tr)
        if not r.get("enabled", True):
            tr.status = "disabled"
            continue
        if not (_in_scope(r.get("factories"), factory) and _in_scope(r.get("sizes"), size)):
            tr.status = "out_of_scope"
            continue
        env = dict(env_meas)
        env.update(values)
        if src["type"] == "measurement":
            mv = measurements_mm.get(src["code"])
            if mv is None:
                tr.status = "skipped"
                tr.message = f"measurement {src['code']} not in the order"
                continue
            tr.source_value = mv
            env["SRC_M"] = mv
        elif options.get(src["group"]) != src["value"]:
            tr.status = "skipped"
            tr.message = "option not selected"
            continue
        try:
            val = compute(_expr(str(r["value"])), env)
        except FormulaError as e:
            tr.status = "failed"
            tr.message = str(e)
            issues.append(Issue("blocking", "rule.error", f"rule {r['id']} ({srcdesc} → {tname}): {e}"))
            continue
        if tname in values:
            prev = writer[tname]
            for t in trace:
                if t.rule_id == prev and t.status == "applied":
                    t.status = "overridden"
            issues.append(Issue("review", "rule.override", f"rule {r['id']} overrides {prev} on {tname} "
                                                           f"({values[tname]:.1f} → {val:.1f} mm)"))
        values[tname] = val
        writer[tname] = r["id"]
        tr.value = val
        if r.get("status") != "confirmed":
            issues.append(Issue("review", "rule.unconfirmed", f"rule {r['id']} {srcdesc} → {tname} is not "
                                                              f"confirmed ({r.get('note') or 'proposed'})"))
    # piece plan: rebuilt from scratch on every call (no pieces left over from earlier options)
    plan: dict[str, dict] = {}
    for pid in style.get("base_pieces", []):
        plan[pid] = {"required_by": ["style"]}
    for orule in style.get("option_rules", []):
        if options.get(orule["group"]) == orule["value"]:
            for pid in orule.get("pieces", []):
                plan.setdefault(pid, {"required_by": []})["required_by"].append(f"{orule['group']}={orule['value']}")
    for pid, ent in plan.items():
        props = style.get("pieces", {}).get(pid)
        if props is None:
            issues.append(Issue("blocking", "plan.piece_undefined", f"piece '{pid}' (needed by "
                                f"{', '.join(ent['required_by'])}) has no production definition", piece=pid))
            continue
        ent.update(copy.deepcopy(props))
        for orule in style.get("option_rules", []):
            if options.get(orule["group"]) == orule["value"]:
                ent.update(copy.deepcopy(orule.get("properties", {}).get(pid, {})))
    return Resolution(product, style_id, values, trace, issues, plan, dict(style["base_pattern"]), options)


def apply_plan(result, plan: dict[str, dict]) -> list[Issue]:
    """Set production properties on the generated pieces. Pieces the pattern produced but the plan does not
    need are kept for review but neither previewed nor produced; planned pieces the pattern cannot produce are
    blocking (the order cannot be approved), without making the geometry itself invalid."""
    issues: list[Issue] = []
    produced = {p.id: p for p in result.pieces}
    for pid, props in plan.items():
        if "material" not in props:
            continue
        p = produced.get(pid)
        if p is None:
            issues.append(Issue("blocking", "plan.piece_missing",
                                f"piece '{pid}' needed by {', '.join(props['required_by'])} is not produced by the "
                                f"base pattern", piece=pid))
            continue
        p.copies, p.mirrored, p.material = int(props["copies"]), int(props["mirrored"]), props["material"]
        p.allow_rotation = [float(r) for r in props.get("rotations", [0, 180])]
        p.allow_mirror_substitute = bool(props.get("mirror_substitute", False))
        p.preview = bool(props.get("preview", True))
        p.production = bool(props.get("production", True))
        if p.mirrored and not props.get("mirror_allowed", False):
            issues.append(Issue("blocking", "plan.mirror", f"piece '{pid}' has mirrored copies but mirroring is not "
                                                           f"allowed for it", piece=pid))
    for pid, p in produced.items():
        if pid not in plan:
            p.preview = False
            p.production = False
    return issues
