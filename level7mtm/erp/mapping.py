"""Versioned ERP mapping profiles.

A profile tells the importer where each value is inside the ERP XML (element/attribute paths, namespaces),
what it means, its unit, validation limits and how ERP design codes map to model options. Nothing is guessed:
fields, units and codes that the profile does not define are reported and make the order "needs review".

Path syntax (relative to the context element):  ``Item/@Quantity`` · ``@Number`` · ``ns:Elem/@attr`` with
prefixes declared in ``namespaces``.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass

FORMAT = "level7mtm-erp-mapping"
FORMAT_VERSION = 1
UNITS_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
KINDS = ("length", "girth", "half_girth", "flat_width", "angle", "count", "other")


class MappingError(ValueError):
    pass


@dataclass
class MeasureSpec:
    code: str
    variable: str
    label: dict
    unit: str
    kind: str
    meaning: str
    required: bool
    range_src: tuple[float, float] | None
    status: str


def load_profile(path_or_dict) -> dict:
    if isinstance(path_or_dict, dict):
        doc = copy.deepcopy(path_or_dict)
    else:
        with open(path_or_dict, encoding="utf-8") as fh:
            doc = json.load(fh)
    check_profile(doc)
    return doc


def profile_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def check_profile(doc: dict) -> None:
    if doc.get("format") != FORMAT:
        raise MappingError("not a LEVEL7 MTM mapping profile")
    if int(doc.get("format_version", 0)) > FORMAT_VERSION:
        raise MappingError("mapping profile format is newer than this program")
    for k in ("profile_id", "version", "detect", "order", "lines", "measurements", "options"):
        if k not in doc:
            raise MappingError(f"mapping profile misses '{k}'")
    for code, f in doc["measurements"]["fields"].items():
        if f.get("unit") not in UNITS_TO_MM:
            raise MappingError(f"measurement {code}: unit {f.get('unit')!r} unknown (allowed: {sorted(UNITS_TO_MM)})")
        if f.get("kind") not in KINDS:
            raise MappingError(f"measurement {code}: kind {f.get('kind')!r} unknown (allowed: {KINDS})")
        r = f.get("range")
        if r is not None and (len(r) != 2 or r[0] > r[1]):
            raise MappingError(f"measurement {code}: invalid range {r}")
    for code, m in doc["options"].get("map", {}).items():
        if "group" not in m or "value" not in m:
            raise MappingError(f"option code {code!r} needs 'group' and 'value'")


def measure_specs(doc: dict) -> dict[str, MeasureSpec]:
    out = {}
    for code, f in doc["measurements"]["fields"].items():
        r = f.get("range")
        out[code] = MeasureSpec(code, f.get("variable", code), f.get("label", {"en": code}), f["unit"], f["kind"],
                                f.get("meaning", ""), bool(f.get("required", False)), tuple(r) if r else None,
                                f.get("status", "proposed"))
    return out


# ---------------------------------------------------------------- path evaluation

def select(el, path: str, ns: dict[str, str]):
    """Return list of values (strings) or elements for a simple path."""
    parts = [p for p in path.split("/") if p]
    nodes = [el]
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part.startswith("@"):
            name = _qname(part[1:], ns)
            return [n.attrib[name] for n in nodes if name in n.attrib]
        if part == "text()":
            return [(n.text or "").strip() for n in nodes]
        q = _qname(part, ns)
        nxt = []
        for n in nodes:
            nxt.extend(c for c in n if c.tag == q)
        nodes = nxt
        if last:
            return nodes
    return nodes


def select_one(el, path: str, ns: dict[str, str]):
    v = select(el, path, ns)
    return v[0] if v else None


def _qname(name: str, ns: dict[str, str]) -> str:
    if ":" in name:
        prefix, local = name.split(":", 1)
        if prefix not in ns:
            raise MappingError(f"namespace prefix {prefix!r} not declared in the profile")
        return f"{{{ns[prefix]}}}{local}"
    return name
