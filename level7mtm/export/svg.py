"""SVG preview of a pattern result (true scale: 1 SVG unit = 1 mm). For review screens and reports only."""
from __future__ import annotations

from html import escape

from ..pattern.geometry import bbox
from .layout import arrange

COLORS = ["#2563eb", "#16a34a", "#c2410c", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f"]


def to_svg(result, title: str = "", gap: float = 60.0) -> str:
    offs = arrange(result.pieces, gap)
    allpts = []
    for p in result.pieces:
        dx, dy = offs[p.id]
        allpts += [(x + dx, -(y + dy)) for x, y in (p.cut or p.seam)]
    if not allpts:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"/>'
    x0, y0, x1, y1 = bbox(allpts)
    m = 40
    w, h = x1 - x0 + 2 * m, y1 - y0 + 2 * m + 40
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.1f}mm" height="{h:.1f}mm" '
           f'viewBox="{x0 - m:.2f} {y0 - m - 40:.2f} {w:.2f} {h:.2f}">',
           f'<rect x="{x0 - m:.2f}" y="{y0 - m - 40:.2f}" width="{w:.2f}" height="{h:.2f}" fill="white"/>']
    if title:
        out.append(f'<text x="{x0:.1f}" y="{y0 - m + 5:.1f}" font-size="18" font-family="Arial">{escape(title)}</text>')

    def pts_attr(pts, dx, dy):
        return " ".join(f"{x + dx:.2f},{-(y + dy):.2f}" for x, y in pts)

    for i, p in enumerate(result.pieces):
        dx, dy = offs[p.id]
        col = COLORS[i % len(COLORS)]
        if p.cut:
            out.append(f'<polygon points="{pts_attr(p.cut, dx, dy)}" fill="{col}" fill-opacity="0.06" '
                       f'stroke="{col}" stroke-width="1.2"/>')
        out.append(f'<polygon points="{pts_attr(p.seam, dx, dy)}" fill="none" stroke="{col}" '
                   f'stroke-width="0.8" stroke-dasharray="6 4"/>')
        for n in p.notches:
            x, y = n["position"]
            out.append(f'<circle cx="{x + dx:.2f}" cy="{-(y + dy):.2f}" r="3" fill="{col}"/>')
        if p.grain:
            (ax, ay), (bx, by) = p.grain
            out.append(f'<line x1="{ax + dx:.2f}" y1="{-(ay + dy):.2f}" x2="{bx + dx:.2f}" y2="{-(by + dy):.2f}" '
                       f'stroke="#111" stroke-width="1"/>')
        for lb in p.labels:
            x, y = lb["position"]
            out.append(f'<text x="{x + dx:.2f}" y="{-(y + dy):.2f}" font-size="{lb["height"]}" text-anchor="middle" '
                       f'font-family="Arial">{escape(lb["text"])}</text>')
    for iss in result.issues:
        if iss.position is not None and iss.piece in offs:
            dx, dy = offs[iss.piece]
            x, y = iss.position
            col = "#dc2626" if iss.severity == "blocking" else "#d97706"
            out.append(f'<circle cx="{x + dx:.2f}" cy="{-(y + dy):.2f}" r="9" fill="none" stroke="{col}" '
                       f'stroke-width="2.5"/>')
    out.append("</svg>")
    return "\n".join(out)
