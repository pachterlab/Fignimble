"""The figure on screen: the engine's sprites drawn in order on the figure's background, and every pointer edit
(select, move with smart guides, resize, line ends, guides, layout gaps, typing in place, drawing new elements)
turned into one of the engine's edit operations (docs/protocol.md). Millimetres from the top-left, y down."""

from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QLineEdit, QWidget

TOOLS = {"select": ("Select", "V"), "text": ("Text", "T"), "line": ("Line", "L"), "arrow": ("Arrow", "A"),
         "rect": ("Rectangle", "R"), "ellipse": ("Ellipse", "E"), "bracket": ("Significance Bracket", "B"),
         "pen": ("Pen", "P")}
BOXED = {"panel", "image", "rect", "ellipse", "layout", "figure", "qr", "table", "legend", "inset"}
PX_STEPS = [2.0, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64]
SELECT = QColor(0, 122, 255)
GUIDE = QColor(0, 153, 217, 190)
SNAP = QColor(255, 45, 140)

# colour vision (Machado, Oliveira and Fernandes 2009, severity 1), and greyscale
VISION = {
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
    "grayscale": ((0.2126, 0.7152, 0.0722),) * 3,
}


def r1(v: float) -> float:
    return round(float(v), 1)


def box_of(e: dict) -> QRectF:
    b = e.get("box") or [0, 0, 0, 0]
    return QRectF(float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def ends_of(e: dict):
    p = e.get("props") or {}
    if all(k in p for k in ("x1", "y1", "x2", "y2")) and e.get("kind") == "line":
        return QPointF(float(p["x1"]), float(p["y1"])), QPointF(float(p["x2"]), float(p["y2"]))
    return None


class Canvas(QWidget):
    selectionChanged = Signal()
    cursorMoved = Signal(object)            # QPointF in mm, or None
    toolChanged = Signal(str)
    viewChanged = Signal()

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.fig: dict | None = None
        self.elements: list[dict] = []
        self.labels: list[dict] = []
        self.by_oid: dict[str, dict] = {}
        self.selection: list[str] = []
        self.part: dict | None = None               # a text inside a panel, picked to retype
        self.scale = 4.0                            # px per mm
        self.offset = QPointF(40, 40)
        self.fit_mode = True
        self.tool = "select"
        self.show_grid = False
        self.snap_grid = False
        self.grid_step = 5.0
        self.show_guides = True
        self.smart = True
        self.vision = "normal"
        self.pixmaps: dict[str, QPixmap] = {}
        self.drag: tuple | None = None
        self.down = QPointF()
        self.current = QPointF()
        self.moved = False
        self.live_delta = QPointF()
        self.live_box: tuple[str, QRectF] | None = None
        self.live_ends: tuple[str, QPointF, QPointF] | None = None
        self.live_value: float | None = None
        self.snaps: list[tuple[bool, float]] = []
        self.moving: set[str] = set()
        self.pending = False
        self.pan_from: QPointF | None = None
        self.space = False
        self.pen: list[QPointF] = []
        self.editor: QLineEdit | None = None
        self.nudge = QPointF()
        self.nudge_timer = QTimer(self, singleShot=True, interval=300, timeout=self._send_nudge)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setMinimumSize(200, 200)

    # --- the figure ---

    def set_figure(self, fig: dict | None):
        same = fig and self.fig and fig.get("figure") == self.fig.get("figure")
        self.fig = fig
        self.elements = list(fig.get("elements") or []) if fig else []
        self.labels = list(fig.get("labels") or []) if fig else []
        self.by_oid = {e["oid"]: e for e in self.elements + self.labels}
        used = {e["sprite"]["file"] for e in self.elements + self.labels if e.get("sprite")}
        self.pixmaps = {k: v for k, v in self.pixmaps.items() if k in used}
        self.selection = [o for o in self.selection if o in self.by_oid]
        if self.part and self.part["oid"] not in self.by_oid:
            self.part = None
        self.clear_pending()
        if not same or self.fit_mode:
            self.fit()
        self.update()

    def clear_pending(self):
        self.pending = False
        self.live_delta = QPointF()
        self.live_box = None
        self.live_ends = None
        self.live_value = None
        self.moving = set()
        self.snaps = []
        self.update()

    def size_mm(self) -> tuple[float, float]:
        s = (self.fig or {}).get("size") or [100, 100]
        return float(s[0]), float(s[1])

    def wanted_px(self) -> float:
        px = self.scale * self.devicePixelRatioF()
        return next((s for s in PX_STEPS if s >= px * 0.9), PX_STEPS[-1])

    def pixmap(self, path: str) -> QPixmap | None:
        pm = self.pixmaps.get(path)
        if pm is None and os.path.exists(path):
            pm = QPixmap(path)
            self.pixmaps[path] = pm
        return pm

    # --- zoom and pan ---

    def fit(self):
        w, h = self.size_mm()
        m = 36
        if w <= 0 or h <= 0:
            return
        self.scale = max(0.2, min((self.width() - 2 * m) / w, (self.height() - 2 * m) / h))
        self.offset = QPointF((self.width() - w * self.scale) / 2, (self.height() - h * self.scale) / 2)
        self.fit_mode = True
        self.update()
        self.viewChanged.emit()

    def zoom(self, factor: float, around: QPointF | None = None):
        c = around if around is not None else QPointF(self.width() / 2, self.height() / 2)
        m = self.to_mm(c)
        self.scale = max(0.2, min(self.scale * factor, 200))
        self.offset = QPointF(c.x() - m.x() * self.scale, c.y() - m.y() * self.scale)
        self.fit_mode = False
        self.update()
        self.viewChanged.emit()

    def actual_size(self):
        dpi = self.logicalDpiX() or 96
        self.zoom((dpi / 25.4) / self.scale)

    def resizeEvent(self, e):
        if self.fit_mode:
            self.fit()
        super().resizeEvent(e)

    def wheelEvent(self, e):
        d = e.angleDelta()
        if e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self.zoom(1.0015 ** d.y(), e.position())
        else:
            dx, dy = (d.y(), 0) if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else (d.x(), d.y())
            self.offset += QPointF(dx / 2, dy / 2)
            self.fit_mode = False
            self.update()

    def to_view(self, p):
        if isinstance(p, QRectF):
            return QRectF(self.offset.x() + p.x() * self.scale, self.offset.y() + p.y() * self.scale,
                          p.width() * self.scale, p.height() * self.scale)
        return QPointF(self.offset.x() + p.x() * self.scale, self.offset.y() + p.y() * self.scale)

    def to_mm(self, p: QPointF) -> QPointF:
        return QPointF((p.x() - self.offset.x()) / self.scale, (p.y() - self.offset.y()) / self.scale)

    # --- drawing ---

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pal = self.palette()
        p.fillRect(self.rect(), pal.color(pal.ColorRole.Mid).darker(115) if pal.color(pal.ColorRole.Window).lightness() < 128
                   else QColor(222, 222, 226))
        if not self.fig:
            p.setPen(pal.color(pal.ColorRole.Text))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "Open a project (Ctrl+O), one of File ▸ Open Recent, or make a new one (Ctrl+Shift+N).")
            return
        paper = self.to_view(QRectF(0, 0, *self.size_mm()))
        p.fillRect(paper.translated(3, 3), QColor(0, 0, 0, 40))
        if self.vision != "normal" and self.vision in VISION:
            img = QImage(paper.size().toSize(), QImage.Format.Format_ARGB32_Premultiplied)
            img.fill(Qt.GlobalColor.transparent)
            q = QPainter(img)
            q.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            q.translate(-paper.topLeft())
            self._paint_paper(q, paper)
            q.end()
            p.drawImage(paper.topLeft(), _simulate(img, VISION[self.vision]))
        else:
            self._paint_paper(p, paper)
        self._paint_aids(p)

    def _paint_paper(self, p: QPainter, paper: QRectF):
        bg = (self.fig or {}).get("background")
        if bg in (None, "none", "transparent"):
            _checker(p, paper)
        else:
            p.fillRect(paper, QColor(bg))
        if self.show_grid:
            self._paint_grid(p)
        for e in self.elements + self.labels:
            s = e.get("sprite")
            if not s:
                continue
            pm = self.pixmap(s["file"])
            if pm is None or pm.isNull():
                continue
            r = QRectF(float(s["x"]), float(s["y"]), float(s["w"]), float(s["h"]))
            p.drawPixmap(self.to_view(self._live(e, r)), pm, QRectF(pm.rect()))

    def _paint_grid(self, p: QPainter):
        w, h = self.size_mm()
        step = self.grid_step
        while step * self.scale < 6:
            step *= 2
        minor, major = QPen(QColor(0, 120, 220, 40), 0.5), QPen(QColor(0, 120, 220, 85), 0.5)
        for vertical, end in ((True, w), (False, h)):
            v, i = 0.0, 0
            while v <= end + 1e-6:
                p.setPen(major if i % 5 == 0 else minor)
                if vertical:
                    p.drawLine(self.to_view(QPointF(v, 0)), self.to_view(QPointF(v, h)))
                else:
                    p.drawLine(self.to_view(QPointF(0, v)), self.to_view(QPointF(w, v)))
                v += step
                i += 1

    def _live(self, e: dict, r: QRectF) -> QRectF:
        """Where a piece is while a drag is under way (before the engine has drawn it there)."""
        oid = e["oid"]
        if oid in self.moving:
            return r.translated(self.live_delta)
        if self.live_box and (oid == self.live_box[0]):
            ob, nb = box_of(e), self.live_box[1]
            sx = nb.width() / ob.width() if ob.width() > 0 else 1
            sy = nb.height() / ob.height() if ob.height() > 0 else 1
            return QRectF(nb.x() + (r.x() - ob.x()) * sx, nb.y() + (r.y() - ob.y()) * sy, r.width() * sx, r.height() * sy)
        if self.live_ends and oid == self.live_ends[0]:
            a0, b0 = ends_of(e) or (QPointF(), QPointF())
            a, b = self.live_ends[1], self.live_ends[2]
            o0 = QPointF(min(a0.x(), b0.x()), min(a0.y(), b0.y()))
            n0 = QPointF(min(a.x(), b.x()), min(a.y(), b.y()))
            ow, oh = abs(b0.x() - a0.x()), abs(b0.y() - a0.y())
            nw, nh = abs(b.x() - a.x()), abs(b.y() - a.y())
            sx, sy = (nw / ow if ow > 0.01 else 1), (nh / oh if oh > 0.01 else 1)
            return QRectF(n0.x() + (r.x() - o0.x()) * sx, n0.y() + (r.y() - o0.y()) * sy, r.width() * sx, r.height() * sy)
        return r

    def _paint_aids(self, p: QPainter):
        f = self.fig
        w, h = self.size_mm()
        if self.show_guides:
            p.setPen(QPen(GUIDE, 1))
            for i, g in enumerate(f.get("guides") or []):
                if self.drag and self.drag[0] == "guide" and self.drag[1] == i and self.live_value is not None:
                    g = {("x" if "x" in g else "y"): self.live_value}
                if g.get("x") is not None:
                    x = self.to_view(QPointF(float(g["x"]), 0)).x()
                    p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
                elif g.get("y") is not None:
                    y = self.to_view(QPointF(0, float(g["y"]))).y()
                    p.drawLine(QPointF(0, y), QPointF(self.width(), y))
        # elements that failed
        p.setPen(QPen(QColor(220, 40, 40), 1.5))
        for e in self.elements:
            if e.get("error"):
                p.drawRect(self.to_view(box_of(e)))
        # hover and selection
        sel = [self.by_oid[o] for o in self.selection if o in self.by_oid]
        for e in sel:
            pen = QPen(SELECT, 1.5)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            ends = ends_of(e)
            if ends:
                a, b = (self.live_ends[1], self.live_ends[2]) if self.live_ends and self.live_ends[0] == e["oid"] else ends
                if e["oid"] in self.moving:
                    a, b = a + self.live_delta, b + self.live_delta
                p.drawLine(self.to_view(a), self.to_view(b))
                if len(sel) == 1:
                    for q in (a, b):
                        self._handle(p, self.to_view(q))
                continue
            r = self._live(e, box_of(e))
            p.drawRect(self.to_view(r).adjusted(-1, -1, 1, 1))
            if len(sel) == 1:
                pts = points_of(e)
                if self.drag and self.drag[0] == "point" and self.drag[1] == e["oid"] and self.live_value is not None:
                    pts[self.drag[2]] = self.live_value
                for q in pts:
                    self._handle(p, self.to_view(q))
            if len(sel) == 1 and e.get("kind") in BOXED and not e.get("locked"):
                for _, q in self._handles(self.to_view(r)):
                    self._handle(p, q)
            for d in self._dividers(e):
                self._paint_divider(p, d)
        if self.part:
            e = self.by_oid.get(self.part["oid"])
            t = next((t for t in (e or {}).get("texts") or [] if t.get("key") == self.part["key"]), None)
            if t:
                p.setPen(QPen(QColor(255, 149, 0), 1.2))
                p.drawRect(self.to_view(box_of(t)).adjusted(-1.5, -1.5, 1.5, 1.5))
        # smart guides
        p.setPen(QPen(SNAP, 1))
        for vertical, at in self.snaps:
            if vertical:
                x = self.to_view(QPointF(at, 0)).x()
                p.drawLine(QPointF(x, self.to_view(QPointF(0, 0)).y()), QPointF(x, self.to_view(QPointF(0, h)).y()))
            else:
                y = self.to_view(QPointF(0, at)).y()
                p.drawLine(QPointF(self.to_view(QPointF(0, 0)).x(), y), QPointF(self.to_view(QPointF(w, 0)).x(), y))
        # a marquee, or a shape being drawn
        if self.drag and self.drag[0] in ("marquee", "draw"):
            a, b = self.to_view(self.down), self.to_view(self.current)
            p.setPen(QPen(SELECT, 1, Qt.PenStyle.DashLine))
            if self.drag[0] == "marquee":
                p.setBrush(QColor(0, 122, 255, 30))
                p.drawRect(QRectF(a, b).normalized())
            else:
                kind = self.drag[1]
                p.setBrush(Qt.BrushStyle.NoBrush)
                if kind in ("rect",):
                    p.drawRect(QRectF(a, b).normalized())
                elif kind == "ellipse":
                    p.drawEllipse(QRectF(a, b).normalized())
                elif kind == "bracket":
                    p.drawLine(a, QPointF(b.x(), a.y()))
                else:
                    p.drawLine(a, b)
        if self.pen:
            p.setPen(QPen(SELECT, 1.2))
            pts = [self.to_view(q) for q in self.pen] + [self.to_view(self.current)]
            for i in range(len(pts) - 1):
                p.drawLine(pts[i], pts[i + 1])
            for q in pts[:-1]:
                self._handle(p, q)

    def _handle(self, p: QPainter, q: QPointF):
        p.setPen(QPen(SELECT, 1))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
        p.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _handles(v: QRectF):
        xs = {"l": v.left(), "c": v.center().x(), "r": v.right()}
        ys = {"t": v.top(), "m": v.center().y(), "b": v.bottom()}
        for k in ("lt", "ct", "rt", "lm", "rm", "lb", "cb", "rb"):
            yield k, QPointF(xs[k[0]], ys[k[1]])

    def _dividers(self, e: dict) -> list[dict]:
        lay = e if e.get("kind") == "layout" else self.by_oid.get(e.get("parent") or "")
        if not lay:
            return []
        return [dict(d, layout=lay["oid"]) for d in lay.get("dividers") or [] if d.get("editable", True)]

    def _paint_divider(self, p: QPainter, d: dict):
        at = self.live_value if (self.drag and self.drag[0] == "divider" and self.drag[1] == d) else float(d["at"])
        a, b = float(d["from"]), float(d["to"])
        p.setPen(QPen(QColor(255, 149, 0), 2))
        if d["axis"] == "col":
            p.drawLine(self.to_view(QPointF(at, a)), self.to_view(QPointF(at, b)))
        else:
            p.drawLine(self.to_view(QPointF(a, at)), self.to_view(QPointF(b, at)))

    # --- picking ---

    def hit(self, mm: QPointF) -> dict | None:
        tol = 5 / self.scale
        found = []
        for e in self.labels + list(reversed(self.elements)):
            if e.get("locked") or e.get("hidden"):
                continue
            ends = ends_of(e)
            if ends and _seg_dist(mm, *ends) <= tol:
                found.append(e)
            elif not ends and box_of(e).adjusted(-tol / 2, -tol / 2, tol / 2, tol / 2).contains(mm):
                found.append(e)
        if not found:
            return None
        if found[0].get("kind") == "label":
            return found[0]
        plain = [e for e in found if e.get("kind") != "layout"]
        return plain[0] if plain else found[0]

    def handle_at(self, v: QPointF):
        sel = [self.by_oid[o] for o in self.selection if o in self.by_oid]
        if len(sel) != 1 or sel[0].get("locked"):
            return None
        e = sel[0]
        for i, q in enumerate(points_of(e)):
            if (self.to_view(q) - v).manhattanLength() < 9:
                return ("point", e["oid"], i)
        ends = ends_of(e)
        if ends:
            for i, q in enumerate(ends):
                if (self.to_view(q) - v).manhattanLength() < 9:
                    return ("ends", e["oid"], i)
            return None
        if e.get("kind") in BOXED:
            for k, q in self._handles(self.to_view(box_of(e))):
                if (q - v).manhattanLength() < 9:
                    return ("resize", e["oid"], k)
        return None

    def divider_at(self, v: QPointF):
        for o in self.selection:
            e = self.by_oid.get(o)
            for d in self._dividers(e or {}):
                a, b = float(d["from"]), float(d["to"])
                if d["axis"] == "col":
                    q = self.to_view(QPointF(float(d["at"]), 0)).x()
                    lo, hi = self.to_view(QPointF(0, a)).y(), self.to_view(QPointF(0, b)).y()
                    if abs(v.x() - q) < 5 and lo <= v.y() <= hi:
                        return d
                else:
                    q = self.to_view(QPointF(0, float(d["at"]))).y()
                    lo, hi = self.to_view(QPointF(a, 0)).x(), self.to_view(QPointF(b, 0)).x()
                    if abs(v.y() - q) < 5 and lo <= v.x() <= hi:
                        return d
        return None

    def guide_at(self, v: QPointF):
        if not self.show_guides or not self.fig:
            return None
        for i, g in enumerate(self.fig.get("guides") or []):
            if g.get("x") is not None and abs(self.to_view(QPointF(float(g["x"]), 0)).x() - v.x()) < 4:
                return i
            if g.get("y") is not None and abs(self.to_view(QPointF(0, float(g["y"]))).y() - v.y()) < 4:
                return i
        return None

    def moving_set(self, oids) -> set[str]:
        """What moves with the selection: a layout's panels, a panel's layout, insets on what moves, labels."""
        out = set()
        for o in oids:
            e = self.by_oid.get(o)
            if not e:
                continue
            if e.get("kind") == "label":                # labels share one offset: they move together
                out.update(x["oid"] for x in self.labels if not x.get("detached")) if not e.get("detached") else out.add(o)
                continue
            top = self.by_oid.get(e.get("parent") or "") or e
            out.add(top["oid"])
            out.update(top.get("children") or [])
        for e in self.elements:
            if e.get("host") in out or e.get("parent") in out:
                out.add(e["oid"])
        for lab in self.labels:
            if lab.get("panel") in out and not lab.get("detached"):
                out.add(lab["oid"])
        return out

    def set_selection(self, oids: list[str]):
        self.selection = [o for o in oids if o in self.by_oid]
        self.part = None
        self.update()
        self.selectionChanged.emit()

    def set_tool(self, t: str):
        self.tool = t
        self.pen = []
        self.setCursor(Qt.CursorShape.ArrowCursor if t == "select" else Qt.CursorShape.CrossCursor)
        self.toolChanged.emit(t)
        self.update()

    # --- the pointer ---

    def mousePressEvent(self, e):
        self.setFocus()
        self.close_editor(commit=True)
        v = e.position()
        mm = self.to_mm(v)
        self.down = self.current = mm
        self.moved = False
        if e.button() == Qt.MouseButton.MiddleButton or (self.space and e.button() == Qt.MouseButton.LeftButton):
            self.pan_from = v
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() == Qt.MouseButton.RightButton and self.fig and self.tool == "select":
            el = self.hit(mm)
            if el and el["oid"] not in self.selection:
                self.set_selection([el["oid"]])
            if self.selection:
                self.main.context_menu(e.globalPosition().toPoint())
            return
        if e.button() != Qt.MouseButton.LeftButton or not self.fig:
            return
        if self.tool == "pen":
            if len(self.pen) > 2 and (self.to_view(self.pen[0]) - v).manhattanLength() < 9:
                self.finish_pen(closed=True)
            else:
                self.pen.append(self._snap_point(mm))
            self.update()
            return
        if self.tool == "text":
            self.open_editor(None, mm, "", 9)
            return
        if self.tool != "select":
            self.drag = ("draw", self.tool)
            self.down = self.current = self._snap_point(mm)
            return
        h = self.handle_at(v)
        if h:
            self.drag = h
            return
        d = self.divider_at(v)
        if d:
            self.drag = ("divider", d)
            self.live_value = float(d["at"])
            return
        el = self.hit(mm)
        shift = bool(e.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier))
        if el is None:
            g = self.guide_at(v)
            if g is not None:
                self.drag = ("guide", g)
                return
            if not shift:
                self.set_selection([])
            self.drag = ("marquee", list(self.selection))
            return
        oid = el["oid"]
        part = self._part_at(el, mm) if oid in self.selection and el.get("kind") == "panel" else None
        if shift:
            self.set_selection([o for o in self.selection if o != oid] if oid in self.selection else self.selection + [oid])
        elif oid not in self.selection:
            self.set_selection([oid])
        if part:
            self.part = part
            self.update()
            self.selectionChanged.emit()
        self.drag = ("move", list(self.selection))
        self.moving = self.moving_set(self.selection)
        if oid.startswith("L") and e.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.moving = {oid}                         # Alt-drag nudges one label on its own

    def mouseMoveEvent(self, e):
        v = e.position()
        mm = self.to_mm(v)
        self.cursorMoved.emit(mm)
        if self.pan_from is not None:
            self.offset += v - self.pan_from
            self.pan_from = v
            self.fit_mode = False
            self.update()
            return
        if self.pen:
            self.current = mm
            self.update()
            return
        if not self.drag:
            self._hover_cursor(v, mm)
            return
        if not self.moved and (self.to_view(mm) - self.to_view(self.down)).manhattanLength() < 3:
            return
        self.moved = True
        self.current = mm
        kind = self.drag[0]
        no_snap = bool(e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if kind == "move":
            self.live_delta = self._snapped_delta(mm - self.down, no_snap)
        elif kind == "resize":
            self.live_box = (self.drag[1], self._resized(self.drag[1], self.drag[2], mm,
                                                         bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)))
        elif kind == "ends":
            el = self.by_oid[self.drag[1]]
            a, b = ends_of(el)
            q = self._snap_point(mm)
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                q = _snap45(b if self.drag[2] == 0 else a, q)
            self.live_ends = (el["oid"], q, b) if self.drag[2] == 0 else (el["oid"], a, q)
        elif kind == "point":
            self.live_value = self._snap_point(mm)
        elif kind == "guide":
            g = (self.fig.get("guides") or [])[self.drag[1]]
            self.live_value = r1(mm.x() if g.get("x") is not None else mm.y())
        elif kind == "divider":
            self.live_value = mm.x() if self.drag[1]["axis"] == "col" else mm.y()
        elif kind == "marquee":
            r = QRectF(self.down, mm).normalized()
            inside = [x["oid"] for x in self.elements if not x.get("locked") and not x.get("hidden")
                      and not x.get("parent") and r.contains(box_of(x))]
            self.selection = list(dict.fromkeys(self.drag[1] + inside))
            self.selectionChanged.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        if self.pan_from is not None:
            self.pan_from = None
            self.set_tool(self.tool)
            return
        drag, self.drag = self.drag, None
        if not drag:
            return
        kind = drag[0]
        if kind == "draw":
            self._insert_drawn(drag[1], self.down, self.current, click=not self.moved)
            self.update()
            return
        if not self.moved:
            if kind == "move":
                self.moving = set()
                hit = self.hit(self.current)
                if hit and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier) and len(self.selection) > 1:
                    self.set_selection([hit["oid"]])
            self.clear_pending()
            return
        self.pending = True
        if kind == "move":
            d = self.live_delta
            labels = [o for o in drag[1] if o.startswith("L")]
            if labels and len(drag[1]) == 1 and not (e.modifiers() & Qt.KeyboardModifier.AltModifier):
                self.main.edit({"op": "move", "oids": labels, "dx": r1(d.x()), "dy": r1(d.y()), "labels": "all"})
            else:
                self.main.edit({"op": "move", "oids": drag[1], "dx": r1(d.x()), "dy": r1(d.y())})
        elif kind == "resize" and self.live_box:
            b = self.live_box[1]
            self.main.edit({"op": "resize", "oid": drag[1], "box": [r1(b.x()), r1(b.y()), r1(b.width()), r1(b.height())]})
        elif kind == "ends" and self.live_ends:
            _, a, b = self.live_ends
            self.main.edit({"op": "endpoints", "oid": drag[1], "x1": r1(a.x()), "y1": r1(a.y()), "x2": r1(b.x()),
                            "y2": r1(b.y())})
        elif kind == "point" and self.live_value is not None:
            pts = points_of(self.by_oid[drag[1]])
            pts[drag[2]] = self.live_value
            self.main.edit({"op": "points", "oid": drag[1], "points": [[r1(q.x()), r1(q.y())] for q in pts]})
        elif kind == "guide":
            w, h = self.size_mm()
            v = self.live_value
            if v is None or v < -2 or v > (w if "x" in (self.fig.get("guides") or [])[drag[1]] else h) + 2:
                self.main.edit({"op": "guide_delete", "index": drag[1]})
            else:
                self.main.edit({"op": "guide_move", "index": drag[1], "value": v})
        elif kind == "divider":
            d = drag[1]
            self.main.edit({"op": "divider", "oid": d["layout"], "index": d["index"],
                            "delta": r1(self.live_value - float(d["at"]))})
        else:
            self.clear_pending()

    def mouseDoubleClickEvent(self, e):
        mm = self.to_mm(e.position())
        if self.tool == "pen":
            self.finish_pen(closed=False)
            return
        el = self.hit(mm)
        if not el:
            return
        part = self._part_at(el, mm) if el.get("kind") == "panel" else None
        if part:
            t = part["text"]
            self.part = part
            self.open_editor(("panel_text", el["oid"], part["key"], t.get("text", "")), QPointF(*t["box"][:2]),
                             t.get("text", ""), float(t.get("size") or 7), width=float(t["box"][2]))
            return
        kind = el.get("kind")
        if kind == "text":
            b = box_of(el)
            self.open_editor(("set", el["oid"]), b.topLeft(), str((el.get("props") or {}).get("text") or ""),
                             float((el.get("props") or {}).get("size") or 7), width=b.width())
        elif kind == "label":
            b = box_of(el)
            self.open_editor(("label", el["oid"]), b.topLeft(), str(el.get("text") or ""), 9, width=max(b.width(), 8))
        else:
            self.main.open_code(*self.main.code_of(el))

    def _hover_cursor(self, v: QPointF, mm: QPointF):
        if self.tool != "select":
            return
        h = self.handle_at(v)
        if h and h[0] == "resize":
            k = h[2]
            shape = {"lt": Qt.CursorShape.SizeFDiagCursor, "rb": Qt.CursorShape.SizeFDiagCursor,
                     "rt": Qt.CursorShape.SizeBDiagCursor, "lb": Qt.CursorShape.SizeBDiagCursor,
                     "ct": Qt.CursorShape.SizeVerCursor, "cb": Qt.CursorShape.SizeVerCursor}.get(k, Qt.CursorShape.SizeHorCursor)
            self.setCursor(shape)
        elif h or self.divider_at(v) is not None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif self.guide_at(v) is not None:
            self.setCursor(Qt.CursorShape.SplitHCursor)
        elif self.hit(mm):
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _e):
        self.cursorMoved.emit(None)

    # --- snapping ---

    def _snap_point(self, q: QPointF) -> QPointF:
        if self.snap_grid:
            s = self.grid_step
            return QPointF(round(q.x() / s) * s, round(q.y() / s) * s)
        return QPointF(r1(q.x()), r1(q.y()))

    def _snapped_delta(self, d: QPointF, off: bool) -> QPointF:
        self.snaps = []
        boxes = [box_of(self.by_oid[o]) for o in self.moving if o in self.by_oid and not o.startswith("L")]
        if not boxes:
            return d
        u = boxes[0]
        for b in boxes[1:]:
            u = u.united(b)
        moved = u.translated(d)
        if self.snap_grid and not off:
            s = self.grid_step
            return QPointF(round(moved.x() / s) * s - u.x(), round(moved.y() / s) * s - u.y())
        if not self.smart or off:
            return d
        w, h = self.size_mm()
        xs, ys = [0.0, w / 2, w], [0.0, h / 2, h]
        for e in self.elements:
            if e["oid"] in self.moving or e.get("hidden"):
                continue
            b = box_of(e)
            xs += [b.left(), b.center().x(), b.right()]
            ys += [b.top(), b.center().y(), b.bottom()]
        for g in (self.fig or {}).get("guides") or []:
            if g.get("x") is not None:
                xs.append(float(g["x"]))
            if g.get("y") is not None:
                ys.append(float(g["y"]))
        tol = 6 / self.scale
        dx, dy = d.x(), d.y()
        best = min(((abs(t - m), t - m, t) for m in (moved.left(), moved.center().x(), moved.right()) for t in xs),
                   default=None)
        if best and best[0] <= tol:
            dx += best[1]
            self.snaps.append((True, best[2]))
        best = min(((abs(t - m), t - m, t) for m in (moved.top(), moved.center().y(), moved.bottom()) for t in ys),
                   default=None)
        if best and best[0] <= tol:
            dy += best[1]
            self.snaps.append((False, best[2]))
        return QPointF(dx, dy)

    def _resized(self, oid: str, k: str, mm: QPointF, keep: bool) -> QRectF:
        e = self.by_oid[oid]
        b = box_of(e)
        q = self._snap_point(mm)
        x0, y0, x1, y1 = b.left(), b.top(), b.right(), b.bottom()
        if k[0] == "l":
            x0 = min(q.x(), x1 - 1)
        if k[0] == "r":
            x1 = max(q.x(), x0 + 1)
        if k[1] == "t":
            y0 = min(q.y(), y1 - 1)
        if k[1] == "b":
            y1 = max(q.y(), y0 + 1)
        r = QRectF(QPointF(x0, y0), QPointF(x1, y1))
        if (keep or e.get("kind") == "image") and b.width() > 0 and b.height() > 0 and "c" not in k and "m" not in k:
            ratio = b.width() / b.height()
            nw = max(r.width(), r.height() * ratio)
            nh = nw / ratio
            r = QRectF(x1 - nw if k[0] == "l" else x0, y1 - nh if k[1] == "t" else y0, nw, nh)
        return r

    # --- drawing new elements ---

    def _insert_drawn(self, kind: str, s: QPointF, e: QPointF, click: bool):
        if kind in ("rect", "ellipse"):
            r = QRectF(s, QPointF(s.x() + 20, s.y() + 12)) if click else QRectF(s, e).normalized()
            self.main.edit({"op": "insert", "kind": kind, "props": {"x": r1(r.x()), "y": r1(r.y()),
                                                                    "w": r1(r.width()), "h": r1(r.height())}})
        elif kind in ("line", "arrow"):
            end = QPointF(s.x() + 15, s.y()) if click else self._snap_point(e)
            self.main.edit({"op": "insert", "kind": kind, "props": {"x1": r1(s.x()), "y1": r1(s.y()),
                                                                    "x2": r1(end.x()), "y2": r1(end.y())}})
        elif kind == "bracket":
            x2 = s.x() + 15 if click else e.x()
            self.main.edit({"op": "insert", "kind": "bracket", "props": {"x1": r1(min(s.x(), x2)), "x2": r1(max(s.x(), x2)),
                                                                         "y": r1(s.y()), "text": "*"}})
        self.set_tool("select")

    def finish_pen(self, closed: bool):
        pts, self.pen = self.pen, []
        if len(pts) >= 2:
            props = {"points": [[r1(q.x()), r1(q.y())] for q in pts]}
            if closed:
                props["closed"] = True
            self.main.edit({"op": "insert", "kind": "path", "props": props})
        self.set_tool("select")

    # --- typing on the canvas ---

    def _part_at(self, el: dict, mm: QPointF):
        for t in el.get("texts") or []:
            if t.get("box") and box_of(t).contains(mm):
                return {"oid": el["oid"], "key": t["key"], "text": t}
        return None

    def open_editor(self, what, at_mm: QPointF, text: str, size_pt: float, width: float | None = None):
        self.close_editor(commit=False)
        ed = QLineEdit(self)
        ed.setText(text)
        f = ed.font()
        f.setPointSizeF(max(8.0, size_pt * self.scale * 25.4 / 72 * 0.75))
        ed.setFont(f)
        v = self.to_view(at_mm)
        w = max(120, int((width or 0) * self.scale) + 24)
        ed.setGeometry(int(v.x()) - 2, int(v.y()) - 4, w, ed.sizeHint().height())
        ed.returnPressed.connect(lambda: self.close_editor(commit=True))
        ed.installEventFilter(self)
        ed.setProperty("what", what)
        ed.setProperty("at", at_mm)
        ed.show()
        ed.setFocus()
        ed.selectAll()
        self.editor = ed

    def eventFilter(self, obj, ev):
        if obj is self.editor and ev.type() == ev.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self.close_editor(commit=False)
            return True
        return super().eventFilter(obj, ev)

    def close_editor(self, commit: bool):
        ed, self.editor = self.editor, None
        if ed is None:
            return
        what, at, text = ed.property("what"), ed.property("at"), ed.text()
        ed.hide()
        ed.deleteLater()
        self.setFocus()
        if not commit:
            if self.tool == "text":
                self.set_tool("select")
            return
        if what is None:
            if text.strip():
                self.main.edit({"op": "insert", "kind": "text", "props": {"text": text, "x": r1(at.x()), "y": r1(at.y())}})
            self.set_tool("select")
        elif what[0] == "set":
            self.main.edit({"op": "set", "oid": what[1], "props": {"text": text}})
        elif what[0] == "label":
            self.main.edit({"op": "set", "oid": what[1], "props": {"text": text}})
        elif what[0] == "panel_text" and text != what[3]:
            self.main.edit({"op": "panel_text", "oid": what[1], "key": what[2], "text": text, "old": what[3]})

    # --- keys ---

    def keyPressEvent(self, e):
        k, mods = e.key(), e.modifiers()
        if k == Qt.Key.Key_Space and not e.isAutoRepeat():
            self.space = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        if k == Qt.Key.Key_Escape:
            if self.pen:
                self.pen = []
            elif self.part:
                self.part = None
                self.selectionChanged.emit()
            elif self.tool != "select":
                self.set_tool("select")
            else:
                self.set_selection([])
            self.update()
            return
        if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.pen:
                self.finish_pen(closed=False)
            elif len(self.selection) == 1:
                el = self.by_oid.get(self.selection[0])
                if el and el.get("kind") in ("text", "label"):
                    b = box_of(el)
                    txt = str((el.get("props") or {}).get("text") or el.get("text") or "")
                    self.open_editor(("set", el["oid"]) if el["kind"] == "text" else ("label", el["oid"]), b.topLeft(),
                                     txt, 9, width=b.width())
            return
        arrows = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if k in arrows and self.selection:
            step = 5.0 if mods & Qt.KeyboardModifier.ShiftModifier else 0.1 if mods & Qt.KeyboardModifier.AltModifier else 0.5
            dx, dy = arrows[k]
            self.nudge += QPointF(dx * step, dy * step)
            self.moving = self.moving_set(self.selection)
            self.live_delta = QPointF(self.nudge)
            self.nudge_timer.start()
            self.update()
            return
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selection:
            self.main.delete_selected()
            return
        if not mods or mods == Qt.KeyboardModifier.ShiftModifier:
            letter = e.text().upper()
            for t, (_, key) in TOOLS.items():
                if letter == key and self.fig:
                    self.set_tool(t)
                    return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self.space = False
            self.set_tool(self.tool)
            return
        super().keyReleaseEvent(e)

    def _send_nudge(self):
        d, self.nudge = self.nudge, QPointF()
        if self.selection and (d.x() or d.y()):
            self.pending = True
            a = {"op": "move", "oids": list(self.selection), "dx": round(d.x(), 2), "dy": round(d.y(), 2)}
            if all(o.startswith("L") for o in self.selection):
                a["labels"] = "all"                     # as dragging a label: they share one offset
            self.main.edit(a)

    # --- dropping files ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.fig:
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths and os.path.isdir(paths[0]):                 # a folder: open it as a project
            self.main.open_project(paths[0])
            e.acceptProposedAction()
        elif paths:
            mm = self.to_mm(e.position())
            self.main.import_files(paths, r1(mm.x()), r1(mm.y()))
            e.acceptProposedAction()


def points_of(e: dict) -> list[QPointF]:
    """A path's points (mm)."""
    if e.get("kind") != "path":
        return []
    return [QPointF(float(q[0]), float(q[1])) for q in (e.get("props") or {}).get("points") or [] if len(q) >= 2]


def _seg_dist(p: QPointF, a: QPointF, b: QPointF) -> float:
    dx, dy = b.x() - a.x(), b.y() - a.y()
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((p.x() - a.x()) * dx + (p.y() - a.y()) * dy) / L))
    return math.hypot(p.x() - (a.x() + t * dx), p.y() - (a.y() + t * dy))


def _snap45(origin: QPointF, q: QPointF) -> QPointF:
    dx, dy = q.x() - origin.x(), q.y() - origin.y()
    ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
    L = math.hypot(dx, dy)
    return QPointF(r1(origin.x() + L * math.cos(ang)), r1(origin.y() + L * math.sin(ang)))


def _checker(p: QPainter, r: QRectF):
    p.fillRect(r, QColor("white"))
    s = 8
    p.save()
    p.setClipRect(r)
    brush = QBrush(QColor(230, 230, 230))
    y, row = r.top(), 0
    while y < r.bottom():
        x = r.left() + (0 if row % 2 == 0 else s)
        while x < r.right():
            p.fillRect(QRectF(x, y, s, s), brush)
            x += 2 * s
        y += s
        row += 1
    p.restore()


def _simulate(img: QImage, m) -> QImage:
    """`img` as a reader with that colour vision sees it."""
    import numpy as np
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    a = np.frombuffer(img.constBits(), np.uint8, count=img.sizeInBytes()).reshape(h, img.bytesPerLine())[:, :w * 4]
    a = a.reshape(h, w, 4).astype(np.float32) / 255.0
    rgb = a[..., :3]
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    out = np.clip(lin @ np.array(m, np.float32).T, 0, 1)
    srgb = np.where(out <= 0.0031308, out * 12.92, 1.055 * out ** (1 / 2.4) - 0.055)
    res = np.concatenate([srgb, a[..., 3:]], axis=2)
    buf = np.ascontiguousarray((res * 255 + 0.5).astype(np.uint8))
    q = QImage(buf.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
    return q.copy()
