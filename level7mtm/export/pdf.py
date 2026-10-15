"""PDF review sheet: data page (order, versions, measurements, checks) + scaled pattern overview.

The overview is scaled to fit the page and says so ("scale 1:N"); a 100 mm calibration bar is drawn at the same
scale so a printout can be checked. The PDF is a review document — production output is DXF / nesting exchange.
"""
from __future__ import annotations

import os

from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from ..pattern.geometry import bbox
from .layout import arrange

_FONT = "Helvetica"


def _font() -> str:
    """Use a font with Arabic glyphs when one is installed (Windows: Tahoma/Arial)."""
    global _FONT
    if _FONT != "Helvetica":
        return _FONT
    for cand in (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(cand):
            try:
                pdfmetrics.registerFont(TTFont("L7", cand))
                _FONT = "L7"
                break
            except Exception:  # noqa: BLE001 - fall back to Helvetica
                pass
    return _FONT


def _shape(text: str) -> str:
    """Arabic needs shaping + bidi for ReportLab; without those libraries Arabic is written as-is."""
    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore
        return get_display(arabic_reshaper.reshape(text))
    except ImportError:
        return text


def write_pdf(result, path: str, header: dict, measurements: list[tuple[str, str, str]]) -> str:
    font = _font()
    W, H = landscape(A3)
    c = canvas.Canvas(path, pagesize=(W, H))
    c.setTitle(f"LEVEL7 MTM {header.get('order', '')}")
    y = H - 20 * mm
    c.setFont(font, 16)
    c.drawString(15 * mm, y, _shape(f"LEVEL7 MTM — {header.get('order', '')}"))
    if result.model_status != "approved":
        c.setFillColorRGB(0.8, 0, 0)
        c.drawString(200 * mm, y, f"MODEL STATUS: {result.model_status.upper()} — NOT FOR PRODUCTION")
        c.setFillColorRGB(0, 0, 0)
    c.setFont(font, 9)
    y -= 9 * mm
    for k, v in header.items():
        c.drawString(15 * mm, y, _shape(f"{k}: {v}"))
        y -= 4.6 * mm
    y -= 3 * mm
    c.setFont(font, 10)
    c.drawString(15 * mm, y, "Measurements (mm)")
    c.setFont(font, 8)
    y -= 5 * mm
    for name, value, note in measurements:
        c.drawString(15 * mm, y, _shape(f"{name}"))
        c.drawRightString(75 * mm, y, value)
        c.drawString(78 * mm, y, _shape(note)[:70])
        y -= 4 * mm
    y -= 3 * mm
    c.setFont(font, 10)
    c.drawString(15 * mm, y, "Checks")
    c.setFont(font, 8)
    y -= 5 * mm
    for chk in result.checks:
        c.drawString(15 * mm, y, f"[{'OK' if chk['ok'] else 'FAIL'}] {chk['label']}: {chk['actual']:.1f} / "
                                 f"{chk['target']:.1f} ± {chk['tolerance']:g}")
        y -= 4 * mm
    for rel in result.seam_relations:
        c.drawString(15 * mm, y, f"[{'OK' if rel['ok'] else 'FAIL'}] {rel['a']} − {rel['b']}: {rel['diff']:.1f} "
                                 f"(expected {rel['expected_diff']:.1f} ± {rel['tolerance']:g})")
        y -= 4 * mm
    for iss in result.issues:
        c.drawString(15 * mm, y, _shape(f"[{iss.severity}] {iss.message}")[:150])
        y -= 4 * mm
        if y < 15 * mm:
            break

    # overview
    offs = arrange(result.pieces)
    allp = [(x + offs[p.id][0], y_ + offs[p.id][1]) for p in result.pieces for x, y_ in p.cut]
    if allp:
        x0, y0, x1, y1 = bbox(allp)
        area_x, area_y, area_w, area_h = 140 * mm, 20 * mm, W - 155 * mm, H - 50 * mm
        s = min(area_w / ((x1 - x0) * mm), area_h / ((y1 - y0) * mm))
        denom = 1 / s
        c.setFont(font, 9)
        c.drawString(area_x, H - 25 * mm, f"Overview — scale 1:{denom:.1f} (NOT 1:1). Bar below = 100 mm at this scale.")
        c.line(area_x, H - 30 * mm, area_x + 100 * mm * s, H - 30 * mm)

        def tr(pt):
            return area_x + (pt[0] - x0) * mm * s, area_y + (pt[1] - y0) * mm * s
        for p in result.pieces:
            dx, dy = offs[p.id]
            for poly, dash in ((p.cut, None), (p.seam, (3, 2))):
                pts = [tr((x + dx, y_ + dy)) for x, y_ in poly]
                c.setDash(*(dash or ()))
                pth = c.beginPath()
                pth.moveTo(*pts[0])
                for q in pts[1:]:
                    pth.lineTo(*q)
                pth.close()
                c.drawPath(pth)
            c.setDash()
            if p.grain:
                a, b = (tr((q[0] + dx, q[1] + dy)) for q in p.grain)
                c.line(*a, *b)
    c.showPage()
    c.save()
    return path
