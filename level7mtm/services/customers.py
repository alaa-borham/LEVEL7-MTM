"""Customers (owner: "each customer has his own name in the XML, his own size table and a different parts table").

A customer is {id, name, reference, mdl}:
* ``reference``: the value of the XML root attribute ``Reference`` (e.g. "From level7", "From Richy"); an XML is
  read with the settings of the customer it names;
* his own ERP mapping profile and catalog (size table / X values, piece options, fixed pieces), kept as their own
  documents and versions (``active.mapping@<id>``, ``active.catalog@<id>``);
* his own GRAFIS base model (``grafis.mdl@<id>``; none chosen: the first customer's).

The first customer ("level7") is the one the settings were made for before customers existed; its documents and
settings keep their old keys. A new customer starts as a copy of an existing one; changing one never changes the
other.
"""
from __future__ import annotations

import copy
import re

from ..erp.xmlsafe import XmlError, load_bytes
from .workflow import DEFAULT_CUSTOMER, WorkflowError

ID_RE = re.compile(r"[^a-z0-9_]+")


def _ref_of(profile: dict) -> str:
    return (profile.get("detect", {}).get("attributes") or {}).get("Reference", "")


def list_customers(ws) -> list[dict]:
    """Every customer, the first one first (made from the settings that exist when there is no list yet)."""
    rows = ws.db.get_setting("customers") or []
    if not any(r["id"] == DEFAULT_CUSTOMER for r in rows):
        with ws.for_customer(DEFAULT_CUSTOMER):
            ref = _ref_of(ws.active("mapping")[1])
        rows = [{"id": DEFAULT_CUSTOMER, "name": "LEVEL7", "reference": ref}] + rows
    out = []
    for r in rows:
        with ws.for_customer(r["id"]):
            out.append({**r, "mdl": ws.grafis_mdl(), "own_mdl": bool(ws.db.get_setting(ws._key("grafis.mdl")))
                        or r["id"] == DEFAULT_CUSTOMER})
    return out


def names(ws) -> dict[str, str]:
    """{id: name} — cheap (no documents read), for lists shown every second."""
    out = {DEFAULT_CUSTOMER: "LEVEL7"}
    out.update({r["id"]: r["name"] for r in ws.db.get_setting("customers") or []})
    return out


def get(ws, cid: str) -> dict | None:
    return next((c for c in list_customers(ws) if c["id"] == cid), None)


def name_of(ws, cid: str | None) -> str:
    c = get(ws, cid or DEFAULT_CUSTOMER)
    return c["name"] if c else (cid or "")


def _save_list(ws, rows):
    ws.db.set_setting("customers", [{"id": r["id"], "name": r["name"], "reference": r["reference"]} for r in rows])


def customer_for_xml(ws, data: bytes) -> tuple[str | None, str]:
    """The customer an XML belongs to, by its ``Reference``: (id, "") or (None, why). An XML that cannot be read
    goes to the first customer (the import then says what is wrong with it)."""
    try:
        root = load_bytes(data).root
    except XmlError:
        return DEFAULT_CUSTOMER, ""
    ref = root.attrib.get("Reference")
    custs = list_customers(ws)
    for c in custs:
        if c["reference"] == ref:
            return c["id"], ""
    if ref is None:
        return DEFAULT_CUSTOMER, ""
    return None, (f"no customer has the XML name Reference='{ref}' — add the customer in Customers "
                  f"(known: {', '.join(repr(c['reference']) for c in custs)})")


def add_customer(ws, name: str, reference: str, copy_from: str = DEFAULT_CUSTOMER, mdl: str | None = None) -> str:
    """A new customer, his settings copied from ``copy_from`` (then changed on their own). Returns his id."""
    name, reference = name.strip(), reference.strip()
    if not name or not reference:
        raise WorkflowError("a customer needs a name and his XML name (Reference)")
    custs = list_customers(ws)
    if any(c["reference"] == reference for c in custs):
        raise WorkflowError(f"the XML name '{reference}' belongs to a customer already")
    base = ID_RE.sub("_", name.lower()).strip("_") or "customer"
    cid, n = base, 2
    while any(c["id"] == cid for c in custs) or cid == DEFAULT_CUSTOMER:
        cid, n = f"{base}_{n}", n + 1
    with ws.for_customer(copy_from):
        _, prof = ws.active("mapping")
        _, cat = ws.active("catalog")
        src_mdl = ws.grafis_mdl()
    prof, cat = copy.deepcopy(prof), copy.deepcopy(cat)
    root_prof = prof["profile_id"].split("@")[0]
    root_cat = cat["catalog_id"].split("@")[0]
    prof.update(profile_id=f"{root_prof}@{cid}", version="1.0.0")
    prof.setdefault("detect", {}).setdefault("attributes", {})["Reference"] = reference
    cat.update(catalog_id=f"{root_cat}@{cid}", version="1.0.0")
    note = f"customer {name}: copy of {copy_from}"
    with ws.for_customer(cid):
        ws.set_active("mapping", ws.save_document("mapping", prof, note))
        ws.set_active("catalog", ws.save_document("catalog", cat, note))
        if mdl or src_mdl:
            ws.db.set_setting(ws._key("grafis.mdl"), mdl or src_mdl)
    _save_list(ws, [c for c in custs] + [{"id": cid, "name": name, "reference": reference}])
    with ws.db.tx() as c:
        ws.db.audit(c, ws.actor, "customer.added", "customer", cid, {"name": name, "reference": reference,
                                                                      "copy_from": copy_from})
    return cid


def update_customer(ws, cid: str, name: str | None = None, reference: str | None = None,
                    mdl: str | None = None) -> None:
    """Rename, change the XML name (a new version of his mapping profile) or choose his GRAFIS model."""
    custs = list_customers(ws)
    cur = next((c for c in custs if c["id"] == cid), None)
    if cur is None:
        raise WorkflowError(f"customer {cid} not found")
    if reference is not None and reference.strip() and reference.strip() != cur["reference"]:
        reference = reference.strip()
        if any(c["reference"] == reference and c["id"] != cid for c in custs):
            raise WorkflowError(f"the XML name '{reference}' belongs to a customer already")
        from .xtable import _next_version
        with ws.for_customer(cid):
            _, prof = ws.active("mapping")
            prof = copy.deepcopy(prof)
            prof.setdefault("detect", {}).setdefault("attributes", {})["Reference"] = reference
            prof["version"] = _next_version(ws, "mapping", prof["profile_id"], prof["version"])
            ws.set_active("mapping", ws.save_document("mapping", prof, f"XML name of the customer: {reference}"))
        cur["reference"] = reference
    if name is not None and name.strip():
        cur["name"] = name.strip()
    if mdl is not None:
        with ws.for_customer(cid):
            ws.db.set_setting(ws._key("grafis.mdl") if cid != DEFAULT_CUSTOMER else "grafis.mdl", mdl)
    _save_list(ws, [cur if c["id"] == cid else c for c in custs])
    with ws.db.tx() as c:
        ws.db.audit(c, ws.actor, "customer.changed", "customer", cid, {"name": name, "reference": reference,
                                                                        "mdl": mdl})


# ------------------------------------------------------------------ patterns (owner: "each customer has about 7
# pattern files"; "chosen by a Variation"): the body shapes Slim2, Athlete1 … each with its own GRAFIS model;
# the size table, options and fixed pieces stay the customer's.
def patterns(ws, cid: str | None = None) -> list[dict]:
    """[{variation, mdl}] of the customer (the current one when not given)."""
    with ws.for_customer(cid or ws.customer()):
        return [dict(p) for p in ws.db.get_setting(ws._key("grafis.patterns")) or []]


def set_patterns(ws, rows: list[dict], cid: str | None = None) -> None:
    clean, seen = [], set()
    for r in rows:
        v, m = (r.get("variation") or "").strip(), (r.get("mdl") or "").strip()
        if not v and not m:
            continue
        if not v or not m:
            raise WorkflowError("each pattern needs the Variation name of the XML and its GRAFIS model (MDL)")
        if v.lower() in seen:
            raise WorkflowError(f"the Variation '{v}' is given twice")
        seen.add(v.lower())
        clean.append({"variation": v, "mdl": m})
    with ws.for_customer(cid or ws.customer()):
        ws.db.set_setting(ws._key("grafis.patterns"), clean)
        with ws.db.tx() as c:
            ws.db.audit(c, ws.actor, "customer.patterns", "customer", ws.customer(), {"patterns": clean})


def pattern_for(ws, rid: int) -> tuple[str | None, str]:
    """The GRAFIS model of an order line: the pattern whose Variation the order has, else (no patterns set) the
    customer's model. Returns (mdl, "") or (None, why)."""
    rev = ws.revision(rid)
    cid = rev["normalized"].get("customer") or DEFAULT_CUSTOMER
    pats = patterns(ws, cid)
    if not pats:
        with ws.for_customer(cid):
            return ws.grafis_mdl(), ""
    codes = {str(c).strip().lower() for c in rev["raw_line"].get("option_codes") or []}
    hit = [p for p in pats if p["variation"].lower() in codes]
    if len(hit) == 1:
        return hit[0]["mdl"], ""
    if len(hit) > 1:
        return None, (f"the order has several pattern Variations ({', '.join(p['variation'] for p in hit)}) — "
                      f"only one may choose the pattern")
    return None, (f"no pattern for this order: none of its Variations is a pattern of the customer "
                  f"({', '.join(p['variation'] for p in pats)})")

