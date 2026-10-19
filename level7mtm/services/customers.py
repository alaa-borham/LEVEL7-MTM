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
            out.append({"reseller": "", **r, "mdl": ws.grafis_mdl(), "own_mdl": bool(ws.db.get_setting(ws._key("grafis.mdl")))
                        or r["id"] == DEFAULT_CUSTOMER})
    return out


def names(ws) -> dict[str, str]:
    """{id: name} — cheap (no documents read), for lists shown every second. A product: "Customer / Product"."""
    rows = ws.db.get_setting("customers") or []
    top = {DEFAULT_CUSTOMER: "LEVEL7"}
    top.update({r["id"]: r["name"] for r in rows if not r.get("parent")})
    out = {}
    for cid, n in top.items():                       # each customer, then his products under him
        out[cid] = n
        for r in rows:
            if r.get("parent") == cid:
                out[r["id"]] = f"{n} / {r['name']}"
    return out


def get(ws, cid: str) -> dict | None:
    return next((c for c in list_customers(ws) if c["id"] == cid), None)


def name_of(ws, cid: str | None) -> str:
    c = get(ws, cid or DEFAULT_CUSTOMER)
    return c["name"] if c else (cid or "")


def _save_list(ws, rows):
    ws.db.set_setting("customers", [{"id": r["id"], "name": r["name"], "reference": r["reference"],
                                     "reseller": r.get("reseller", ""),
                                     **({"parent": r["parent"], "item": r.get("item", "")} if r.get("parent") else {})}
                                    for r in rows])


def products(ws, cid: str) -> list[dict]:
    """The customer's products (each with its Item Number and its own patterns and tables)."""
    return [c for c in list_customers(ws) if c.get("parent") == cid]


def _items_of(ws, cid: str, root) -> list[str]:
    """The Item Numbers of the order lines of an XML, read with the customer's mapping."""
    from ..erp.mapping import select, select_one
    with ws.for_customer(cid):
        _, prof = ws.active("mapping")
    L, ns = prof["lines"], prof.get("namespaces", {})
    return [(select_one(el, L.get("item", "Item/@Number"), ns) or "").strip() for el in select(root, L["path"], ns)]


def customer_for_xml(ws, data: bytes) -> tuple[str | None, str]:
    """The customer an XML belongs to, by its ``Reference``: (id, "") or (None, why). An XML that cannot be read
    goes to the first customer (the import then says what is wrong with it)."""
    try:
        root = load_bytes(data).root
    except XmlError:
        return DEFAULT_CUSTOMER, ""
    ref, res = root.attrib.get("Reference"), (root.attrib.get("Reseller") or "").strip()
    everyone = list_customers(ws)
    custs = [c for c in everyone if not c.get("parent")]
    same = [c for c in custs if c["reference"] == ref]
    exact = [c for c in same if c.get("reseller") and c["reseller"] == res]       # name and reseller both
    open_ = [c for c in same if not c.get("reseller")]                            # no reseller set: by name
    hit = (exact or open_ or [None])[0]
    if hit is not None:
        prods = [p for p in everyone if p.get("parent") == hit["id"]]
        if not prods:
            return hit["id"], ""
        items = sorted(set(_items_of(ws, hit["id"], root)))       # the product: by the Item Number of the lines
        if len(items) > 1:
            return None, f"the XML has order lines of several products (Item {', '.join(items)}) — one per file"
        prod = next((p for p in prods if items and p.get("item") == items[0]), None)
        if prod is None:
            return None, (f"no product of {hit['name']} has the Item Number '{items[0] if items else ''}' — add it "
                          f"in Customers (known: {', '.join(p.get('item', '') for p in prods)})")
        return prod["id"], ""
    if ref is None:
        return DEFAULT_CUSTOMER, ""
    known = ", ".join(f"{c['reference']!r}" + (f"/{c['reseller']}" if c.get("reseller") else "") for c in custs)
    return None, (f"no customer has the XML name Reference='{ref}' with Reseller='{res}' — add the customer "
                  f"in Customers (known: {known})")


def add_product(ws, parent: str, name: str, item: str, copy_from: str | None = None, mdl: str | None = None) -> str:
    """A product of a customer: his XML lines with this Item Number use the product's own patterns and tables
    (copied from ``copy_from``, default the customer, then changed on their own). Returns its id."""
    name, item = name.strip(), (item or "").strip()
    p = get(ws, parent)
    if p is None or p.get("parent"):
        raise WorkflowError("choose a customer for the product")
    if not name or not item:
        raise WorkflowError("a product needs a name and its Item Number in the XML")
    if any(c.get("parent") == parent and c.get("item") == item for c in list_customers(ws)):
        raise WorkflowError(f"the Item Number '{item}' is a product of {p['name']} already")
    return _create(ws, name, p["reference"], copy_from or parent, mdl, p.get("reseller", ""), parent=parent,
                   item=item, base=f"{parent}_{item}")


def add_customer(ws, name: str, reference: str, copy_from: str = DEFAULT_CUSTOMER, mdl: str | None = None,
                 reseller: str = "") -> str:
    """A new customer, his settings copied from ``copy_from`` (then changed on their own). Returns his id.
    ``reseller``: the Reseller number of his XML files (empty: any)."""
    name, reference, reseller = name.strip(), reference.strip(), (reseller or "").strip()
    if not name or not reference:
        raise WorkflowError("a customer needs a name and his XML name (Reference)")
    custs = [c for c in list_customers(ws) if not c.get("parent")]
    if any(c["reference"] == reference and c.get("reseller", "") == reseller for c in custs):
        raise WorkflowError(f"the XML name '{reference}' belongs to a customer already")
    return _create(ws, name, reference, copy_from, mdl, reseller)


def _create(ws, name, reference, copy_from, mdl, reseller, parent=None, item="", base=None) -> str:
    custs = list_customers(ws)
    base = ID_RE.sub("_", (base or name).lower()).strip("_") or "customer"
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
    entry = {"id": cid, "name": name, "reference": reference, "reseller": reseller}
    if parent:
        entry.update(parent=parent, item=item)
    _save_list(ws, [c for c in custs] + [entry])
    with ws.db.tx() as c:
        ws.db.audit(c, ws.actor, "product.added" if parent else "customer.added", "customer", cid,
                    {"name": name, "reference": reference, "copy_from": copy_from, "item": item})
    return cid


def update_customer(ws, cid: str, name: str | None = None, reference: str | None = None,
                    mdl: str | None = None, reseller: str | None = None, item: str | None = None) -> None:
    """Rename, change the XML name (a new version of his mapping profile) or choose his GRAFIS model; for a
    product also its Item Number."""
    custs = list_customers(ws)
    cur = next((c for c in custs if c["id"] == cid), None)
    if cur is None:
        raise WorkflowError(f"customer {cid} not found")
    if cur.get("parent"):                            # a product: name, Item Number, model (the rest is the customer's)
        reference = reseller = None
        if item is not None and item.strip() and item.strip() != cur.get("item"):
            if any(c.get("parent") == cur["parent"] and c.get("item") == item.strip() and c["id"] != cid
                   for c in custs):
                raise WorkflowError(f"the Item Number '{item.strip()}' is a product of this customer already")
            cur["item"] = item.strip()
    if reference is not None and reference.strip() and reference.strip() != cur["reference"]:
        reference = reference.strip()
        if any(c["reference"] == reference and c["id"] != cid and not c.get("parent") for c in custs):
            raise WorkflowError(f"the XML name '{reference}' belongs to a customer already")
        from .xtable import _next_version
        for one in [cid] + [c["id"] for c in custs if c.get("parent") == cid]:   # his products read it too
            with ws.for_customer(one):
                _, prof = ws.active("mapping")
                prof = copy.deepcopy(prof)
                prof.setdefault("detect", {}).setdefault("attributes", {})["Reference"] = reference
                prof["version"] = _next_version(ws, "mapping", prof["profile_id"], prof["version"])
                ws.set_active("mapping", ws.save_document("mapping", prof, f"XML name of the customer: {reference}"))
        for c in custs:
            if c.get("parent") == cid:
                c["reference"] = reference
        cur["reference"] = reference
    if name is not None and name.strip():
        cur["name"] = name.strip()
    if reseller is not None:
        reseller = reseller.strip()
        if any(c["reference"] == cur["reference"] and c.get("reseller", "") == reseller and c["id"] != cid
               and not c.get("parent") for c in custs):
            raise WorkflowError(f"the XML name '{cur['reference']}' with Reseller '{reseller}' belongs to a "
                                f"customer already")
        cur["reseller"] = reseller
        for c in custs:
            if c.get("parent") == cid:
                c["reseller"] = reseller
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
        if clean and ws.grafis_mdl() not in [p["mdl"] for p in clean]:
            ws.db.set_setting(ws._key("grafis.mdl"), clean[0]["mdl"])     # the tables are reviewed on a pattern
        _, prof = ws.active("mapping")
        omap = prof.get("options", {}).get("map", {})
        known = {_plain(k) for k in omap}
        new = [p["variation"] for p in clean if _plain(p["variation"]) not in known]
        if new:                                        # read by the import as the order's pattern
            from .xtable import _next_version
            prof = copy.deepcopy(prof)
            for v in new:
                prof["options"]["map"][v] = {"group": "pattern", "value": ID_RE.sub("_", v.lower()).strip("_"),
                                             "label": {"ar": f"بترون {v}", "en": f"Pattern {v}"}}
            prof["version"] = _next_version(ws, "mapping", prof["profile_id"], prof["version"])
            ws.set_active("mapping", ws.save_document("mapping", prof, "pattern Variations"))
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
    have = [str(c) for c in rev["raw_line"].get("option_codes") or []]
    codes = {_plain(c) for c in have}
    hit = [p for p in pats if _plain(p["variation"]) in codes]
    if len(hit) == 1:
        return hit[0]["mdl"], ""
    if len(hit) > 1:
        return None, (f"the order has several pattern Variations ({', '.join(p['variation'] for p in hit)}) — "
                      f"only one may choose the pattern")
    return None, (f"no pattern for this order: none of its Variations is a pattern of the customer "
                  f"({', '.join(p['variation'] for p in pats)}) — the order has: {', '.join(have) or '—'}")


def _plain(name: str) -> str:
    """Variation names compared without case, spaces, "_" or "-" ("Richy Big" = "richy_big")."""
    return re.sub(r"[\s_\-]+", "", str(name)).lower()


def variations_seen(ws, cid: str | None = None) -> list[str]:
    """Every Variation name that came in the XML of the customer's orders (for choosing a pattern's name)."""
    import json
    cid = cid or ws.customer()
    seen: dict[str, str] = {}
    for r in ws.db.q("SELECT raw_line, normalized FROM revisions ORDER BY id DESC LIMIT 2000"):
        if (json.loads(r["normalized"]).get("customer") or DEFAULT_CUSTOMER) != cid:
            continue
        for c in json.loads(r["raw_line"]).get("option_codes") or []:
            seen.setdefault(_plain(c), str(c))
    return sorted(seen.values(), key=str.lower)



# ------------------------------------------------------------------ learning a customer's XML (owner: Richy's files
# have other measurement codes, full girths, another table name and order numbers like GR-1963-2_2)
LINE_ANY = r"^(?P<reseller>[A-Za-z0-9]+)-(?P<order>[A-Za-z0-9]+)-(?P<line>[A-Za-z0-9]+)_(?P<revision>\d+)$"


def learn_from_xml(ws, cid: str, data: bytes) -> dict:
    """Make the customer's XML mapping read his files as they are, from one of them: his measurement table
    name and order numbers, every measurement code in it (new ones defined in cm), no range taken over from
    another customer that his values do not keep, no required measurement his files do not have. Saved as a
    new version of his mapping. Returns what changed and the Variations still to be given a meaning."""
    from ..erp.mapping import select, select_one
    from .xtable import _next_version
    root = load_bytes(data).root
    with ws.for_customer(cid):
        _, prof = ws.active("mapping")
    prof = copy.deepcopy(prof)
    ns = prof.get("namespaces", {})
    L = prof["lines"]
    lines = select(root, L["path"], ns) if isinstance(L.get("path"), str) else []
    if not lines:
        raise WorkflowError("no order line found in this XML")
    el = lines[0]
    M = prof["measurements"]
    fields = M["fields"]
    out = {"added": [], "ranges_dropped": [], "not_required": [], "table": None, "line_pattern": False,
           "unknown_variations": []}
    num = select_one(el, L.get("number", "@Number"), ns) or ""
    rx = prof.get("line_number", {}).get("regex")
    if rx and not re.match(rx, num) and re.match(LINE_ANY, num):
        prof["line_number"]["regex"] = LINE_ANY
        out["line_pattern"] = True
    table = select_one(el, L.get("measurement_table", "Measurement/@Measurement-Table-Name"), ns)
    if table and M.get("measurement_table") != table:
        M["measurement_table"] = table
        out["table"] = table
    present = set()
    for t in select(el, M["path"], ns):
        code = (t.attrib.get(M["code"].lstrip("@")) or "").strip()
        if not code:
            continue
        name = (t.attrib.get(M["name"].lstrip("@")) or code).strip()
        try:
            val = float((t.attrib.get(M["value"].lstrip("@")) or "").replace(",", "."))
        except ValueError:
            val = None
        present.add(code)
        f = fields.get(code)
        if f is None:
            fields[code] = {"variable": code, "label": {"ar": name, "en": name}, "unit": "cm", "kind": "length",
                            "meaning": f"{name} (from the customer's XML)", "required": False, "status": "proposed"}
            out["added"].append(code)
        elif f.get("range") and val is not None and not (f["range"][0] <= val <= f["range"][1]):
            f.pop("range")                                  # this customer's numbers mean something else
            out["ranges_dropped"].append(code)
    for code, f in fields.items():
        if f.get("required") and code not in present:
            f["required"] = False
            out["not_required"].append(code)
    keep = []
    for cr in prof.get("conditional_required", []):
        req = [c for c in cr.get("require", []) if c in present]
        if req:
            keep.append({**cr, "require": req})
        else:
            out["not_required"] += [c for c in cr.get("require", []) if c not in out["not_required"]]
    prof["conditional_required"] = keep
    O = prof["options"]
    known = {_plain(k) for k in O.get("map", {})}
    for v in select(el, O["path"], ns):
        if _plain(v) not in known:
            out["unknown_variations"].append(v)
    prof["version"] = _next_version(ws, "mapping", prof["profile_id"], prof["version"])
    with ws.for_customer(cid):
        ws.set_active("mapping", ws.save_document("mapping", prof, "learned from the customer's XML"))
    res = (root.attrib.get("Reseller") or "").strip()
    c = get(ws, cid)
    out["reseller"] = None
    if res and c is not None and not c.get("reseller"):        # his Reseller number, as in his files
        update_customer(ws, cid, reseller=res)
        out["reseller"] = res
    return out


def reread_file(ws, path: str):
    """Import an XML again after the customer's settings changed: its order lines that were held for review (and
    went no further) are taken back, then the file is read anew (models written as on import). Returns the
    import outcome."""
    import hashlib
    import os
    with open(path, "rb") as fh:
        sha = hashlib.sha256(fh.read()).hexdigest()
    name = os.path.basename(path)
    f = ws.db.one("SELECT id FROM import_files WHERE sha256=?", (sha,))
    if f:
        _take_back(ws, f["id"], name)
    from .grafis_orders import import_and_prepare
    return import_and_prepare(ws, path)


HELD = ("needs_review", "ready", "imported", "validation_failed")


def _take_back(ws, fid: int, name: str) -> None:
    """The order lines of an imported XML that were held (and went no further) are removed, so it reads anew."""
    revs = ws.db.q("SELECT id, order_id, status FROM revisions WHERE import_file=?", (fid,))
    if any(r["status"] not in HELD for r in revs):
        raise WorkflowError("this XML has order lines that went further (model written to GRAFIS, nested): "
                            "it is not read again")
    with ws.db.tx() as c:
        for r in revs:
            for t in ("grafis_orders", "manual_edits", "generations", "nesting_items"):
                c.execute(f"DELETE FROM {t} WHERE revision_id=?", (r["id"],))
            c.execute("DELETE FROM revisions WHERE id=?", (r["id"],))
            if not c.execute("SELECT id FROM revisions WHERE order_id=?", (r["order_id"],)).fetchone():
                c.execute("DELETE FROM orders WHERE id=?", (r["order_id"],))
        c.execute("UPDATE import_files SET status='failed', message='read again' WHERE id=?", (fid,))
        ws.db.audit(c, ws.actor, "import.reread", "import_file", fid, {"file": name, "lines": [r["id"] for r in revs]})


def _family(ws, cid: str) -> set[str]:
    """The customer, his products and (for a product) its customer: an XML of one of them may now be read with
    another (e.g. orders read before the products were added)."""
    every = list_customers(ws)
    me = next((c for c in every if c["id"] == cid), None)
    top = (me or {}).get("parent") or cid
    return {top} | {c["id"] for c in every if c.get("parent") == top}


def held_files(ws, cid: str | None = None) -> list[int]:
    """Imported XML files of the customer (or his products) whose order lines all wait (nothing went further)."""
    import json
    fam = _family(ws, cid or ws.customer())
    out = []
    for f in ws.db.q("SELECT DISTINCT import_file AS id FROM revisions WHERE status='needs_review'"):
        revs = ws.db.q("SELECT status, normalized FROM revisions WHERE import_file=?", (f["id"],))
        if revs and all(r["status"] in HELD for r in revs) and all(
                (json.loads(r["normalized"]).get("customer") or DEFAULT_CUSTOMER) in fam for r in revs):
            out.append(f["id"])
    return out


def reread_held(ws, cid: str | None = None) -> list:
    """Read the customer's held XML files again with his current settings (models written as on import)."""
    return [_reread(ws, fid) for fid in held_files(ws, cid)]


def reread_lines(ws, rids: list[int]) -> tuple[list, list[str]]:
    """Read the XML files of these order lines again with today's settings (right click → Read again). A file
    with a line that went further (model written to GRAFIS, nested) is not read again. Returns (outcomes,
    skipped reasons)."""
    fids, skipped = [], []
    for rid in rids:
        r = ws.db.one("SELECT import_file FROM revisions WHERE id=?", (rid,))
        if r and r["import_file"] and r["import_file"] not in fids:
            fids.append(r["import_file"])
    outs = []
    for fid in fids:
        revs = ws.db.q("SELECT status FROM revisions WHERE import_file=?", (fid,))
        if any(r["status"] not in HELD for r in revs):
            name = ws.db.one("SELECT file_name FROM import_files WHERE id=?", (fid,))["file_name"]
            skipped.append(f"{name}: has order lines that went further, not read again")
            continue
        outs.append(_reread(ws, fid))
    return outs, skipped


def _reread(ws, fid: int):
    from .grafis_orders import order_mdl, settings
    f = ws.db.one("SELECT file_name, source_path, raw FROM import_files WHERE id=?", (fid,))
    _take_back(ws, fid, f["file_name"])
    out = ws.import_data(bytes(f["raw"]), f["file_name"], f["source_path"] or "")
    if settings(ws)["enabled"] and out.status == "imported":
        for ln in out.lines:
            if ln["action"] in ("new", "new_revision"):
                out.grafis.append(order_mdl(ws, ln["revision_id"]))
    return out


def measurement_table(ws, cid: str | None = None) -> str:
    """The Measurement-Table-Name his XML files must have ("" = not checked)."""
    with ws.for_customer(cid or ws.customer()):
        _, prof = ws.active("mapping")
        return prof.get("measurements", {}).get("measurement_table") or ""


def set_measurement_table(ws, cid: str, name: str) -> bool:
    """Set the Measurement-Table-Name of the customer / product (a new mapping version; an order whose XML has
    another name waits for review). Returns True when changed."""
    from .xtable import _next_version
    name = (name or "").strip()
    with ws.for_customer(cid):
        _, prof = ws.active("mapping")
        if (prof.get("measurements", {}).get("measurement_table") or "") == name:
            return False
        prof = copy.deepcopy(prof)
        if name:
            prof["measurements"]["measurement_table"] = name
        else:
            prof["measurements"].pop("measurement_table", None)
        prof["version"] = _next_version(ws, "mapping", prof["profile_id"], prof["version"])
        ws.set_active("mapping", ws.save_document("mapping", prof, f"Measurement-Table-Name: {name or '(any)'}"))
    return True


def basic_sizes_seen(ws, cid: str | None = None) -> list[str]:
    """The Basic-Size values that came in the XML of the customer's / product's orders (shown only)."""
    import json
    cid = cid or ws.customer()
    seen: list[str] = []
    for r in ws.db.q("SELECT raw_line, normalized FROM revisions ORDER BY id DESC LIMIT 2000"):
        if (json.loads(r["normalized"]).get("customer") or DEFAULT_CUSTOMER) != cid:
            continue
        b = json.loads(r["raw_line"]).get("basic_size")
        if b and b not in seen:
            seen.append(str(b))
    return seen
