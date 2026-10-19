"""X-value table editor: every X of a GRAFIS part next to the XML code(s) that fill it (owner decision #14:
"the user enters the X name as in the MDL and its X number, and links it to an XML measurement").

The table is a view on the catalog's measurement rules (source = XML code, target = part / X). Saving writes a new
catalog version (and, for XML codes the system does not know yet, a new mapping-profile version so the import reads
them); old versions stay in the library.
"""
from __future__ import annotations

import copy
import re

from ..grafis.container import read_container
from ..grafis.xvalues import read_xvalues

CODE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def x_parts(mdl_path: str) -> dict[int, int]:
    """{part number: number of X values} for the parts of the model that have an X table."""
    cont = read_container(mdl_path)
    out = {}
    for b in cont.find(3, 3, 1):
        xs = read_xvalues(cont, b.bid[1])
        if xs:
            out[b.bid[1]] = len(xs)
    return out


def _style(cat):
    pid = next(iter(cat["products"]))
    prod = cat["products"][pid]
    sid = prod.get("default_style") or next(iter(prod["styles"]))
    return prod["styles"][sid]


def x_table(ws, mdl_path: str, part: int) -> list[dict]:
    """One row per X of the part: x, name, default, codes (XML codes), formula, confirmed."""
    _, cat = ws.active("catalog")
    st = _style(cat)
    piece = f"{part:03d}"
    rows = []
    for num, xv in sorted(read_xvalues(mdl_path, part).items()):
        rules = [r for r in st["rules"] if r["source"]["type"] == "measurement" and r["target"]["piece"] == piece
                 and r["target"]["variable"] == f"X{num}"]
        rules.sort(key=lambda r: (r.get("order", 0), r["id"]))
        rows.append({"x": num, "name": xv.name, "default": xv.default,
                     "codes": [r["source"]["code"] for r in rules],
                     "formula": rules[0]["value"] if rules else "{M}",
                     "confirmed": bool(rules) and all(r.get("status") == "confirmed" for r in rules)})
    return rows


def _next_version(ws, kind, key, ver: str) -> str:
    nums = [int(n) for n in re.findall(r"\d+", ver)] or [1, 0, 0]
    while True:
        nums[-1] += 1
        cand = ".".join(map(str, nums))
        if not ws.db.one("SELECT id FROM documents WHERE kind=? AND doc_id=? AND version=?", (kind, key, cand)):
            return cand


def save_x_table(ws, mdl_path: str, part: int, rows: list[dict], note: str = "X values edited") -> dict:
    """``rows``: [{x, codes: [..], formula, confirmed}] for the part. Returns {catalog_version, mapping_version,
    new_codes}. Raises ValueError for an invalid code or formula."""
    return save_x_tables(ws, mdl_path, {part: rows}, note)


def save_x_tables(ws, mdl_path: str, parts: dict[int, list[dict]], note: str = "X values edited") -> dict:
    """Several parts at once (the main parts side by side): one new rules version for all of them."""
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = _style(cat)
    new_codes: list[str] = []
    for part, rows in sorted(parts.items()):
        _apply_part(cat, st, mdl_path, part, rows, new_codes)
    return _save_tables(ws, cat, st, new_codes, note)


def _apply_part(cat, st, mdl_path, part, rows, new_codes):
    piece = f"{part:03d}"
    table = read_xvalues(mdl_path, part)
    for row in rows:
        for c in row["codes"]:
            if not CODE_RE.fullmatch(c):
                raise ValueError(f"X{row['x']}: '{c}' is not a valid XML code (letters, digits, _)")
            if c not in cat["measurements"] and c not in new_codes:
                new_codes.append(c)
    edited = {f"X{r['x']}" for r in rows}
    kept = [r for r in st["rules"] if not (r["source"]["type"] == "measurement" and r["target"]["piece"] == piece
                                           and r["target"]["variable"] in edited)]
    old = {(r["source"].get("code"), r["target"]["variable"]): r for r in st["rules"]
           if r["target"]["piece"] == piece}
    used_ids = {r["id"] for r in kept}
    added = []
    for row in sorted(rows, key=lambda r: r["x"]):
        var, name = f"X{row['x']}", (table[row["x"]].name if row["x"] in table else "")
        for i, c in enumerate(row["codes"]):
            prev = old.get((c, var))
            rid = prev["id"] if prev and prev["id"] not in used_ids else f"X{part:03d}_{row['x']}_{c}"
            used_ids.add(rid)
            r = copy.deepcopy(prev) if prev else {"order": 100 + i, "factories": ["*"], "sizes": ["*"], "enabled": True}
            r.update({"id": rid, "source": {"type": "measurement", "code": c},
                      "target": {"piece": piece, "construction": (prev or {}).get("target", {}).get(
                          "construction", "Basic-Construction 0"), "variable": var, "x_name": name},
                      "value": row.get("formula") or "{M}",
                      "status": "confirmed" if row.get("confirmed", True) else "proposed",
                      "note": (prev or {}).get("note") or f"X value table: {c} → {piece}/{var} '{name}'"})
            added.append(r)
    st["rules"] = kept + added


def _save_tables(ws, cat, st, new_codes, note) -> dict:
    for c in new_codes:
        cat["measurements"][c] = {"name": {"ar": c, "en": c}, "unit": "mm", "kind": "other",
                                  "method": {"ar": "", "en": "XML code added in the X value table"},
                                  "order": 900 + len(cat["measurements"]), "visible": True, "editable": True,
                                  "status": "confirmed"}
        if c not in st.setdefault("measurement_table", []):
            st["measurement_table"].append(c)
    cat["version"] = _next_version(ws, "catalog", cat["catalog_id"], str(cat["version"]))
    out = {"catalog_version": cat["version"], "mapping_version": None, "new_codes": new_codes}
    map_pk = None
    if new_codes:
        _, prof = ws.active("mapping")
        prof = copy.deepcopy(prof)
        fields = prof["measurements"]["fields"]
        for c in new_codes:
            if c not in fields:
                fields[c] = {"variable": c, "label": {"ar": c, "en": c}, "unit": "cm", "kind": "other",
                             "meaning": "added in the X value table (value passed to GRAFIS as it is)",
                             "required": False, "status": "confirmed"}
        prof["version"] = _next_version(ws, "mapping", prof["profile_id"], str(prof["version"]))
        map_pk = ws.save_document("mapping", prof, note)
        out["mapping_version"] = prof["version"]
    cat_pk = ws.save_document("catalog", cat, note)
    ws.set_active("catalog", cat_pk)
    if map_pk:
        ws.set_active("mapping", map_pk)
    return out


def codes_elsewhere(ws, mdl_path: str) -> dict[str, int]:
    """{piece: number of X with XML codes} for the parts that hold codes but are not an X table of this model —
    e.g. a new customer copied from another whose model numbers its main part 002 while his own uses 007."""
    _, cat = ws.active("catalog")
    here = {f"{p:03d}" for p in x_parts(mdl_path)}
    out: dict[str, set] = {}
    for r in _style(cat)["rules"]:
        if r["source"]["type"] == "measurement" and r["target"]["piece"] not in here:
            out.setdefault(r["target"]["piece"], set()).add(r["target"]["variable"])
    return {p: len(v) for p, v in sorted(out.items())}


def _norm(name: str) -> str:
    return re.sub(r"[\s\-_]+", " ", (name or "").strip().lower())


def carry_codes(ws, mdl_path: str, from_piece: str, to_part: int, note: str | None = None) -> dict:
    """Move the XML codes of ``from_piece`` to the X values of ``to_part`` of this model: an X of the same name
    in GRAFIS, else the same X number. An X that has codes already keeps them. Returns {moved: [(from X, to X)],
    left: [X], catalog_version}; nothing is saved when nothing moves."""
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = _style(cat)
    table = read_xvalues(mdl_path, to_part)
    piece = f"{to_part:03d}"
    by_name: dict[str, list[int]] = {}
    for num, xv in table.items():
        by_name.setdefault(_norm(xv.name), []).append(num)
    taken = {r["target"]["variable"] for r in st["rules"]
             if r["source"]["type"] == "measurement" and r["target"]["piece"] == piece}
    src = [r for r in st["rules"] if r["source"]["type"] == "measurement" and r["target"]["piece"] == from_piece]
    plan: dict[str, int | None] = {}
    for var in sorted({r["target"]["variable"] for r in src}, key=lambda v: int(v[1:]) if v[1:].isdigit() else 0):
        name = next((_norm(r["target"].get("x_name", "")) for r in src if r["target"]["variable"] == var
                     and r["target"].get("x_name")), "")
        same = by_name.get(name, []) if name else []
        num = same[0] if len(same) == 1 else (int(var[1:]) if var[1:].isdigit() and int(var[1:]) in table
                                                else None)
        plan[var] = num if num is not None and f"X{num}" not in taken else None
        if plan[var] is not None:
            taken.add(f"X{num}")
    moved = [(v, f"X{n}") for v, n in plan.items() if n is not None]
    out = {"moved": moved, "left": [v for v, n in plan.items() if n is None], "catalog_version": None}
    if not moved:
        return out
    ids = {r["id"] for r in st["rules"]}
    for r in src:
        n = plan.get(r["target"]["variable"])
        if n is None:
            continue
        r["target"] = dict(r["target"], piece=piece, variable=f"X{n}", x_name=table[n].name)
        rid = f"X{piece}_{n}_{r['source']['code']}"
        if rid not in ids:
            ids.discard(r["id"])
            r["id"] = rid
            ids.add(rid)
    cat["version"] = _next_version(ws, "catalog", cat["catalog_id"], str(cat["version"]))
    ws.set_active("catalog", ws.save_document("catalog", cat, note or f"XML codes moved from part {from_piece} "
                                                                         f"to part {piece}"))
    out["catalog_version"] = cat["version"]
    return out
