"""GRAFIS MDL inspection report: sizes, parts, measurement tables and all Z formulas with descriptions,
edit history, X references and problems. Markdown for people, JSON for tools."""
from __future__ import annotations

import collections
import json
import os
import re

from .container import read_container
from .formulas import read_formulas, to_json
from .textsections import read_text


def write_report(mdl_path: str, outdir: str) -> list[str]:
    os.makedirs(outdir, exist_ok=True)
    cont = read_container(mdl_path)
    text = read_text(cont)
    parts = read_formulas(cont)
    names = {p.number: p.name for p in text.parts}
    xs = collections.defaultdict(list)
    for pf in parts.values():
        for r in pf.records:
            for v in r.references:
                if v.startswith("X"):
                    xs[v].append(f"P{r.part}.{r.name} {r.description}".strip())
    base = os.path.splitext(os.path.basename(mdl_path))[0]
    md = [f"# GRAFIS MDL report — {os.path.basename(mdl_path)}", "",
          f"* SHA-256 `{cont.sha256}` · {cont.size} bytes · signature variant `{cont.variant}` · "
          f"{len(cont.blocks)} blocks · container errors: {cont.errors or 'none'}",
          f"* #VERSION {text.version} · sizes: {', '.join(s.name + (f' => {s.ref}' if s.ref else '') for s in text.sizes)}",
          f"* parts: {len(text.parts)} ({sum(p.production for p in text.parts)} with production attributes)",
          f"* Z formula records: {sum(len(p.records) for p in parts.values())} in {len(parts)} parts", "",
          "Status: formulas, descriptions and history are read from blocks (3,part,6,34). The construction program "
          "(points/lines using these values) is NOT decoded; AB/GL values are measured in that construction and "
          "cannot be computed from formulas alone.", "",
          "## X values referenced by the formulas", "", "| X | used by (part.Z description) |", "|---|---|"]
    for x in sorted(xs, key=lambda s: (s.startswith("XG"), int(re.sub(r"\D", "", s)))):
        md.append(f"| {x} | {'; '.join(sorted(set(xs[x]))[:8])} |")
    md += ["", "## Formulas per part", ""]
    for pn, pf in sorted(parts.items()):
        md += [f"### Part {pn:03d} — {names.get(pn, '?')}", "", "| Z | formula | description | group | flags | notes | history |",
               "|---|---|---|---|---|---|---|"]
        for r in pf.records:
            if r.placeholder:
                continue
            note = r.parse_error and f"**syntax: {r.parse_error}**" or (r.measured and f"measured ({r.measured})") or ""
            hist = "<br>".join(f"{h.date}: `{h.formula}`" for h in r.history[:6])
            md.append(f"| {r.name} | `{r.formula}` | {r.description} | {r.group} | 0x{r.flags:04X} | {note} | {hist} |")
        md.append("")
    p_md = os.path.join(outdir, f"{base}.formulas.md")
    p_js = os.path.join(outdir, f"{base}.formulas.json")
    with open(p_md, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    with open(p_js, "w", encoding="utf-8") as fh:
        json.dump({"file": os.path.basename(mdl_path), "sha256": cont.sha256, "sizes": [s.__dict__ for s in text.sizes],
                   "parts": [p.__dict__ for p in text.parts],
                   "measure_tables": {k: [r.__dict__ for r in v] for k, v in text.measure_tables.items()},
                   "formulas": to_json(parts)}, fh, ensure_ascii=False, indent=1)
    return [p_md, p_js]
