"""GRAFIS parts table: design option → GRAFIS part number, material, normal / mirrored copies (owner decision #20).

The base model may have no production part at all (owner: production is switched off in the base model; the
parts follow the order's options). Rows with option "*" are produced for every order.
"""
from __future__ import annotations

import copy

from ..grafis.container import read_container
from ..grafis.parts import production
from .catalog import load_catalog
from .xtable import _next_version, _style


def model_parts(mdl_path: str) -> dict[int, tuple[str, str | None]]:
    """{part number: (name, material code of its record or None)} — every part of the model, produced or not."""
    from ..grafis.parts import MAT, _material_record
    cont = read_container(mdl_path)
    out = {}
    for n, (name, _) in production(cont).items():
        b = cont.get(3, n, *MAT)
        rec = _material_record(b.payload, b.bid) if b is not None else None
        out[n] = (name, (chr(rec[5]).strip() or None) if rec is not None else None)
    return out


def parts_table(ws) -> list[dict]:
    _, cat = ws.active("catalog")
    return [dict(r, option=r.get("option") or "*") for r in (_style(cat).get("grafis_parts") or [])]


def option_choices(ws) -> list[tuple[str, str]]:
    """[(key, label)] for the option column: "*" (always) and every group=value with Arabic names."""
    _, cat = ws.active("catalog")
    st = _style(cat)
    gs = st.get("option_group_settings", {})
    labels = {(o["group"], o["value"]): o.get("label", {}) for o in st.get("option_rules", [])}
    out = [("*", "*")]
    for g, values in st.get("option_groups", {}).items():
        gl = gs.get(g, {}).get("label", {}).get("ar") or g
        for v in values:
            out.append((f"{g}={v}", f"{gl}: {labels.get((g, v), {}).get('ar') or v}"))
    return out


def save_parts(ws, rows: list[dict], mdl_path: str | None = None, note: str = "GRAFIS parts edited") -> str:
    """Save the table as a new catalog version; part names are taken from the model. Returns the version."""
    _, cat = ws.active("catalog")
    cat = copy.deepcopy(cat)
    st = _style(cat)
    names = {n: v[0] for n, v in model_parts(mdl_path).items()} if mdl_path else {}
    out = []
    for r in rows:
        n = int(r["part"])
        if names and n not in names:
            raise ValueError(f"part {n:03d} is not in the GRAFIS model")
        out.append({"option": "" if r.get("option") in ("", "*", None) else r["option"], "part": n,
                    "name": names.get(n, r.get("name", "")).strip(), "material": (r.get("material") or "O").upper(),
                    "copies": int(r.get("copies", 1)), "mirrored": int(r.get("mirrored", 0))})
    if out:
        st["grafis_parts"] = out
    else:
        st.pop("grafis_parts", None)
    cat["version"] = _next_version(ws, "catalog", cat["catalog_id"], str(cat["version"]))
    load_catalog(cat)                                   # same checks as everywhere (options exist, copies …)
    ws.set_active("catalog", ws.save_document("catalog", cat, note))
    return cat["version"]


def set_option_parts(ws, wanted: dict[str, list[int]], mdl_path: str | None = None,
                     note: str = "part numbers set in the piece options") -> str | None:
    """The GRAFIS part numbers a design option value produces ("collar=square": [12, 13]), as typed next to the
    value in Piece options setup: its rows in the parts table are replaced (a part kept keeps its material, copies
    and mirroring; a new one gets material from the model, 1 copy). Returns the new version, None if unchanged."""
    rows = parts_table(ws)
    mats = {n: (v[1] or "O") for n, v in model_parts(mdl_path).items()} if mdl_path else {}
    changed = False
    for opt, nums in wanted.items():
        have = [r for r in rows if r["option"] == opt]
        if sorted(int(r["part"]) for r in have) == sorted(nums):
            continue
        changed = True
        keep = {int(r["part"]): r for r in have}
        rows = [r for r in rows if r["option"] != opt]
        for n in nums:
            rows.append(keep.get(n) or {"option": opt, "part": n, "material": mats.get(n, "O"), "copies": 1,
                                        "mirrored": 0})
    return save_parts(ws, rows, mdl_path, note) if changed else None
