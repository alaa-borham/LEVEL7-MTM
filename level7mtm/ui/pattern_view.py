"""Pattern preview: zoom (wheel), pan (drag), piece selection, layer toggles, comparison overlay and issue
markers. Scene units are millimetres; zooming changes the view only, never the geometry."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView

from ..export.layout import arrange
from .i18n import label

COLORS = ["#2563eb", "#16a34a", "#c2410c", "#7c3aed", "#0891b2", "#be185d", "#4d7c0f", "#a16207"]


def _path(points, close=True) -> QPainterPath:
    p = QPainterPath()
    if not points:
        return p
    p.moveTo(points[0][0], -points[0][1])
    for x, y in points[1:]:
        p.lineTo(x, -y)
    if close:
        p.closeSubpath()
    return p


class PatternView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#ffffff"))
        self.layers = {"cut": True, "seam": True, "notches": True, "grain": True, "labels": True}
        self.result = None
        self.compare = None
        self.on_select = None

    def show_result(self, result, compare=None):
        self.result, self.compare = result, compare
        self.redraw()
        self.fit()

    def set_layer(self, name: str, on: bool):
        self.layers[name] = on
        self.redraw()

    def redraw(self):
        sc = self.scene()
        sc.clear()
        if not self.result:
            return
        offs = arrange(self.result.pieces)
        if self.compare:
            cmp = {p.id: p for p in self.compare.pieces}
            pen = QPen(QColor("#9ca3af"), 0)
            pen.setCosmetic(True)
            pen.setStyle(Qt.DashLine)
            for p in self.result.pieces:
                c = cmp.get(p.id)
                if c:
                    dx, dy = offs[p.id]
                    sc.addPath(_path([(x + dx, y + dy) for x, y in c.cut]), pen)
        for i, p in enumerate(self.result.pieces):
            dx, dy = offs[p.id]
            col = QColor(COLORS[i % len(COLORS)])
            sh = lambda pts: [(x + dx, y + dy) for x, y in pts]     # noqa: E731
            if self.layers["cut"] and p.cut:
                pen = QPen(col, 0)
                pen.setCosmetic(True)
                pen.setWidthF(1.6)
                fill = QColor(col)
                fill.setAlpha(18)
                item = sc.addPath(_path(sh(p.cut)), pen, QBrush(fill))
                item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                item.setData(0, p.id)
                item.setToolTip(f"{label(p.name)} — {p.material} — {p.copies}+{p.mirrored} mirrored")
            if self.layers["seam"]:
                pen = QPen(col, 0)
                pen.setCosmetic(True)
                pen.setStyle(Qt.DashLine)
                sc.addPath(_path(sh(p.seam)), pen)
            if self.layers["notches"]:
                for n in p.notches:
                    x, y = n["position"]
                    sc.addEllipse(x + dx - 3, -(y + dy) - 3, 6, 6, QPen(col, 0), QBrush(col))
            if self.layers["grain"] and p.grain:
                (ax, ay), (bx, by) = p.grain
                pen = QPen(QColor("#111827"), 0)
                pen.setCosmetic(True)
                pen.setWidthF(1.2)
                sc.addLine(ax + dx, -(ay + dy), bx + dx, -(by + dy), pen)
            if self.layers["labels"]:
                for lb in p.labels:
                    t = sc.addSimpleText(lb["text"], QFont("Arial", 10))
                    t.setScale(max(lb["height"], 6) / 12)
                    x, y = lb["position"]
                    t.setPos(x + dx - t.boundingRect().width() * t.scale() / 2, -(y + dy))
        for iss in self.result.issues:
            if iss.position is not None and iss.piece in offs:
                dx, dy = offs[iss.piece]
                x, y = iss.position
                col = QColor("#dc2626" if iss.severity == "blocking" else "#d97706")
                pen = QPen(col, 0)
                pen.setCosmetic(True)
                pen.setWidthF(3)
                e = sc.addEllipse(x + dx - 12, -(y + dy) - 12, 24, 24, pen)
                e.setToolTip(iss.message)
        # 100 mm scale bar (true size in scene units)
        r = sc.itemsBoundingRect()
        pen = QPen(QColor("#111827"), 0)
        pen.setCosmetic(True)
        pen.setWidthF(2)
        sc.addLine(r.left(), r.bottom() + 40, r.left() + 100, r.bottom() + 40, pen)
        t = sc.addSimpleText("100 mm")
        t.setPos(r.left(), r.bottom() + 45)
        sc.selectionChanged.connect(self._selected)

    def _selected(self):
        items = self.scene().selectedItems()
        if items and self.on_select:
            self.on_select(items[0].data(0))

    def fit(self):
        r = self.scene().itemsBoundingRect()
        if not r.isEmpty():
            self.fitInView(r.adjusted(-30, -30, 30, 30), Qt.KeepAspectRatio)

    def wheelEvent(self, ev):
        f = 1.25 if ev.angleDelta().y() > 0 else 0.8
        self.scale(f, f)

    def mm_per_pixel(self) -> float:
        t: QTransform = self.transform()
        return 1.0 / max(t.m11(), 1e-9)


__all__ = ["PatternView", "QPointF"]
