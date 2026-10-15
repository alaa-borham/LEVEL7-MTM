"""Geometric DXF export (R2000, millimetres, 1:1).

Layers: CUT (cut line), SEAM (seam line), NOTCH (5 mm notch slits from the cut line inward), GRAIN, TEXT,
INTERNAL. Each piece is a block named ``<order>_<piece>`` inserted at a display offset; the block geometry
itself is the unmodified piece geometry. This is a generic DXF — it is NOT labelled AAMA or ASTM.
"""
from __future__ import annotations

import ezdxf

from ..pattern.finishing import cut_notch
from .layout import arrange

LAYERS = {"CUT": 7, "SEAM": 3, "NOTCH": 1, "GRAIN": 5, "TEXT": 2, "INTERNAL": 4, "INFO": 8}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def write_dxf(result, path: str, order_ref: str = "", info: list[str] | None = None) -> str:
    doc = ezdxf.new("R2000", setup=False)
    doc.header["$INSUNITS"] = 4          # millimetres
    doc.header["$MEASUREMENT"] = 1
    for name, color in LAYERS.items():
        doc.layers.add(name, color=color)
    msp = doc.modelspace()
    offs = arrange(result.pieces)
    for p in result.pieces:
        bname = _safe(f"{order_ref}_{p.id}" if order_ref else p.id)
        blk = doc.blocks.new(bname)
        blk.add_lwpolyline(p.cut, close=True, dxfattribs={"layer": "CUT"})
        blk.add_lwpolyline(p.seam, close=True, dxfattribs={"layer": "SEAM"})
        for ip in p.internal:
            blk.add_lwpolyline(ip.flatten(0.05), dxfattribs={"layer": "INTERNAL"})
        for n in p.notches:
            base, d = cut_notch(p, n["position"])
            if base is not None and d is not None:
                blk.add_line(base, (base[0] + 5 * d[0], base[1] + 5 * d[1]), dxfattribs={"layer": "NOTCH"})
        if p.grain:
            blk.add_line(p.grain[0], p.grain[1], dxfattribs={"layer": "GRAIN"})
        for lb in p.labels:
            blk.add_text(lb["text"], dxfattribs={"layer": "TEXT", "height": lb["height"]}).set_placement(
                lb["position"], align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        dx, dy = offs[p.id]
        msp.add_blockref(bname, (dx, dy))
    y = 60.0
    for line in (info or []):
        msp.add_text(line, dxfattribs={"layer": "INFO", "height": 8}).set_placement((0, y))
        y += 12
    doc.saveas(path)
    return path
