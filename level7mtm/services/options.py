"""Design option groups (collar, sleeve …), their values and the ERP names that select them.

Kept in two documents, both saved as new versions:
* catalog style: ``option_groups`` {group: [values]}, ``option_group_settings`` {group: {label, required,
  default}} and ``option_rules`` (one entry per value: label, pieces, variables — existing entries are kept);
* ERP mapping profile: ``options.map`` {Variation name in the XML: {group, value}}.

A group that is ``required`` blocks an order without a value for it; otherwise its ``default`` value is used
(shown as information) or the group is simply not set.
"""
from __future__ import annotations

import copy
import re

from .xtable import _next_version, _style

CODE_RE = re.compile(r"[a-z0-9_]+")


def code(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.strip().lower()).strip("_")


def option_table(ws) -> list[dict]:
    _, cat = ws.active("catalog")
    _, prof = ws.active("mapping")
    st = _style(cat)
    settings = st.get("option_group_settings", {})
    labels = {(o["group"], o["value"]): o.get("label", {}) for o in st.get("option_rules", [])}
    erp: dict[tuple, list[str]] = {}
    for name, m in prof["options"].get("map", {}).items():
        erp.setdefault((m["group"], m["value"]), []).append(name)
    out = []
    for g, values in st.get("option_groups", {}).items():
        s = settings.get(g, {})
        out.append({"group": g, "label": s.get("label", {"ar": g, "en": g}), "required": s.get("required", True),
                    "default": s.get("default") or "",
                    "values": [{"value": v, "label": labels.get((g, v), {"ar": v, "en": v}),
                                "erp_names": erp.get((g, v), [])} for v in values]})
    return out


def save_options(ws, groups: list[dict], note: str = "design options edited") -> dict:
    """``groups`` as returned by option_table. Raises ValueError with the reason when something is invalid."""
    _, cat = ws.active("catalog")
    _, prof = ws.active("mapping")
    cat, prof = copy.deepcopy(cat), copy.deepcopy(prof)
    st = _style(cat)
    new_groups, settings, seen_erp = {}, {}, {}
    for g in groups:
        gc = g["group"]
        if not CODE_RE.fullmatch(gc or ""):
            raise ValueError(f"group code {gc!r}: use a-z, 0-9 and _")
        if gc in new_groups:
            raise ValueError(f"group {gc} appears twice")
        vals = []
        for v in g["values"]:
            if not CODE_RE.fullmatch(v["value"] or ""):
                raise ValueError(f"{gc}: value code {v['value']!r}: use a-z, 0-9 and _")
            if v["value"] in vals:
                raise ValueError(f"{gc}: value {v['value']} appears twice")
            vals.append(v["value"])
            for n in v["erp_names"]:
                k = n.strip().lower()
                if k in seen_erp:
                    raise ValueError(f"XML name '{n}' is used twice ({seen_erp[k]} and {gc}={v['value']})")
                seen_erp[k] = f"{gc}={v['value']}"
        if not vals:
            raise ValueError(f"group {gc} has no value")
        if g.get("default") and g["default"] not in vals:
            raise ValueError(f"group {gc}: default {g['default']!r} is not one of its values")
        new_groups[gc] = vals
        settings[gc] = {"label": g.get("label") or {"ar": gc, "en": gc}, "required": bool(g.get("required", True)),
                        "default": g.get("default") or None}
    # options still used by rules / GRAFIS parts must not disappear
    used = []
    for r in st.get("rules", []):
        s = r["source"]
        if s["type"] == "option" and s.get("value") not in new_groups.get(s.get("group"), []):
            used.append(f"rule {r['id']} ({s['group']}={s['value']})")
    for row in st.get("grafis_parts") or []:
        opt = row.get("option") or ""
        if opt not in ("", "*"):
            gg, _, vv = opt.partition("=")
            if vv not in new_groups.get(gg, []):
                used.append(f"GRAFIS part {row['part']} ({opt})")
    if used:
        raise ValueError("still used, change these first: " + "; ".join(used))
    old_rules = {(o["group"], o["value"]): o for o in st.get("option_rules", [])}
    orules = []
    for g in groups:
        for v in g["values"]:
            o = copy.deepcopy(old_rules.get((g["group"], v["value"]),
                                            {"pieces": [], "variables": [], "properties": {}}))
            o.update({"group": g["group"], "value": v["value"], "label": v.get("label") or {"ar": v["value"],
                                                                                           "en": v["value"]}})
            orules.append(o)
    st["option_groups"] = new_groups
    st["option_group_settings"] = settings
    st["option_rules"] = orules
    omap = {}
    for g in groups:
        for v in g["values"]:
            for n in v["erp_names"]:
                omap[n.strip()] = {"group": g["group"], "value": v["value"], **({"label": v["label"]}
                                                                                 if v.get("label") else {})}
    out = {"catalog_version": None, "mapping_version": None}
    if omap != prof["options"].get("map", {}):
        prof["options"]["map"] = omap
        prof["version"] = _next_version(ws, "mapping", prof["profile_id"], str(prof["version"]))
        out["mapping_version"] = prof["version"]
        map_pk = ws.save_document("mapping", prof, note)
    else:
        map_pk = None
    cat["version"] = _next_version(ws, "catalog", cat["catalog_id"], str(cat["version"]))
    cat_pk = ws.save_document("catalog", cat, note)
    ws.set_active("catalog", cat_pk)
    if map_pk:
        ws.set_active("mapping", map_pk)
    out["catalog_version"] = cat["version"]
    return out
