"""Shared helpers: arrange pieces side by side for previews (display only — never changes piece geometry)."""
from __future__ import annotations

from ..pattern.geometry import bbox


def arrange(pieces, gap: float = 60.0):
    """Return {piece_id: (dx, dy)} placing pieces left to right by their cut bounding box."""
    out = {}
    x = 0.0
    for p in pieces:
        pts = p.cut or p.seam
        x0, y0, x1, y1 = bbox(pts)
        out[p.id] = (x - x0, -y1)
        x += (x1 - x0) + gap
    return out
