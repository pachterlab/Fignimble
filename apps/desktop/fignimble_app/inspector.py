"""The inspector: the figure, its style and labels when nothing is selected; an element's position and every argument
of its call when one is; alignment for several. Element arguments are listed from what the engine reports (`props`),
so a setting the engine gains shows up here without a change to the editor."""

from __future__ import annotations

import ast

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

LABEL_FORMATS = [("a", "{}", "lower"), ("(a)", "({})", "lower"), ("a)", "{})", "lower"), ("a.", "{}.", "lower"),
                 ("A", "{}", "upper"), ("(A)", "({})", "upper"), ("A)", "{})", "upper"), ("A.", "{}.", "upper")]
NUMBERING = [("a b c", "alpha"), ("i ii iii", "roman"), ("1 2 3", "arabic"), ("none", "none")]
GEOMETRY = {"x", "y", "w", "h", "x1", "y1", "x2", "y2", "box"}
# kept off the generic list: shown elsewhere, or edited by their own commands
HIDDEN_PROPS = {"texts", "colors", "text_style", "points", "cells"}


def literal(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return repr(v)


def parse(text: str, old):
    """What was typed, as the value it means: a Python literal, else the text itself; empty is None."""
    t = text.strip()
    if t == "":
        return None
    if isinstance(old, str) and not (t[:1] in "[({'\"" and t[-1:] in "])}'\""):
        return text
    try:
        return ast.literal_eval(t)
    except (ValueError, SyntaxError):
        return text


def is_color(key: str, v) -> bool:
    return ("color" in key or key in ("background", "fill", "stroke", "edgecolor", "facecolor")) and \
        (v is None or (isinstance(v, str) and (v.startswith("#") or v.isalpha())))


class ColorButton(QPushButton):
    def __init__(self, color: str | None, on_pick):
        super().__init__()
        self.setFixedWidth(28)
        self.color = color
        self.on_pick = on_pick
        self._show()
        self.clicked.connect(self._pick)

    def _show(self):
        c = self.color if self.color and QColor(self.color).isValid() else None
        self.setStyleSheet(f"background:{c};" if c else "")
        self.setText("" if c else "—")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color or "#000000"), self, "Colour",
                                  QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if c.isValid():
            self.color = c.name().upper() if c.alpha() == 255 else c.name(QColor.NameFormat.HexArgb)
            self._show()
            self.on_pick(self.color)


class Inspector(QScrollArea):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setWidgetResizable(True)
        self.setMinimumWidth(270)
        self.update_view()

    # --- helpers ---

    def _number(self, value, on_done, lo=-10000.0, hi=10000.0, step=0.5, decimals=2):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setKeyboardTracking(False)
        s.setValue(float(value or 0))
        s.valueChanged.connect(lambda v: on_done(round(v, decimals)))
        return s

    def _text(self, value, on_done):
        e = QLineEdit(literal(value))
        e.editingFinished.connect(lambda: e.isModified() and (e.setModified(False), on_done(e.text())))
        return e

    def _combo(self, items, current, on_pick):
        c = QComboBox()
        c.addItems(items)
        if current in items:
            c.setCurrentIndex(items.index(current))
        else:
            c.setCurrentIndex(-1)
        c.activated.connect(lambda i: on_pick(i))
        return c

    def _button(self, text, fn):
        b = QPushButton(text)
        b.clicked.connect(lambda: fn())
        return b

    # --- what to show ---

    def update_view(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        fig = self.main.figure
        canvas = self.main.canvas
        sel = [canvas.by_oid[o] for o in canvas.selection if o in canvas.by_oid] if fig else []
        if not fig:
            v.addWidget(QLabel("No figure open."))
        elif canvas.part:
            self._part(v, canvas.part)
        elif len(sel) == 1:
            self._element(v, sel[0])
        elif len(sel) > 1:
            self._several(v, sel)
        else:
            self._figure(v, fig)
        v.addStretch(1)
        old = self.takeWidget()
        if old is not None:
            old.deleteLater()
        self.setWidget(w)

    def _figure(self, v, fig):
        m = self.main
        props = fig.get("figureProps") or {}
        g = QGroupBox(f"Figure {fig.get('figure', '')}")
        f = QFormLayout(g)
        w, h = (fig.get("size") or [0, 0])[:2]
        f.addRow("Width (mm)", self._number(w, lambda x: m.edit({"op": "figure", "props": {"width": x}}), 1, 5000, 1, 1))
        f.addRow("Height (mm)", self._number(h, lambda x: m.edit({"op": "figure", "props": {"height": x}}), 1, 5000, 1, 1))
        widths = (fig.get("journal") or {}).get("widths") if isinstance(fig.get("journal"), dict) else None
        if widths:
            names = list(widths)
            f.addRow("Journal width", self._combo([f"{k} ({widths[k]} mm)" for k in names], None,
                                                  lambda i: m.edit({"op": "figure", "props": {"width": widths[names[i]]}})))
        bg = props.get("background")
        row = QHBoxLayout()
        row.addWidget(self._text(bg, lambda t: m.edit({"op": "figure", "props": {"background": t or None}})))
        row.addWidget(ColorButton(bg, lambda c: m.edit({"op": "figure", "props": {"background": c}})))
        f.addRow("Background", row)
        v.addWidget(g)

        st = (fig.get("style") or {}).get("props") or {}
        g = QGroupBox("Style (every panel of every figure)")
        f = QFormLayout(g)
        f.addRow("Font", self._text(st.get("font"), lambda t: m.edit({"op": "style", "props": {"font": t}})))
        f.addRow("Text size (pt)", self._number(st.get("size"), lambda x: m.edit({"op": "style", "props": {"size": x}}),
                                                 1, 200, 0.5, 1))
        f.addRow("Line width (pt)", self._number(st.get("linewidth"),
                                                  lambda x: m.edit({"op": "style", "props": {"linewidth": x}}), 0, 20, 0.1, 2))
        c = QCheckBox("Despine axes")
        c.setChecked(bool(st.get("despine")))
        c.toggled.connect(lambda b: m.edit({"op": "style", "props": {"despine": b}}))
        f.addRow("", c)
        pals = list((fig.get("palettes") or {}).keys())
        cur = st.get("palette") if isinstance(st.get("palette"), str) else None
        f.addRow("Palette", self._combo(["(matplotlib's)"] + pals, cur or "(matplotlib's)",
                                        lambda i: m.edit({"op": "style", "props": {"palette": None if i == 0 else pals[i - 1]}})))
        j = (m.journals or {}).get(st.get("journal") or "")
        if j:
            f.addRow("Journal", QLabel(j.get("name", st.get("journal"))))
        v.addWidget(g)

        ls = (fig.get("labelStyle") or {}).get("props") or {}
        g = QGroupBox("Panel labels (every panel)")
        f = QFormLayout(g)
        cur = next((n for n, fm, cs in LABEL_FORMATS if fm == ls.get("format") and cs == ls.get("case")), None)
        f.addRow("Style", self._combo([n for n, _, _ in LABEL_FORMATS], cur, lambda i: m.edit(
            {"op": "labels", "props": {"format": LABEL_FORMATS[i][1], "case": LABEL_FORMATS[i][2]}})))
        f.addRow("Numbering", self._combo([n for n, _ in NUMBERING], next((n for n, k in NUMBERING if k == ls.get("numbering")), None),
                                          lambda i: m.edit({"op": "labels", "props": {"numbering": NUMBERING[i][1]}})))
        f.addRow("Size (pt)", self._number(ls.get("size"), lambda x: m.edit({"op": "labels", "props": {"size": x}}), 1, 200, 0.5, 1))
        f.addRow("Weight", self._combo(["bold", "normal"], ls.get("weight"),
                                       lambda i: m.edit({"op": "labels", "props": {"weight": ["bold", "normal"][i]}})))
        f.addRow("Offset x (mm)", self._number(ls.get("dx"), lambda x: m.edit({"op": "labels", "props": {"dx": x}}), -100, 100, 0.5, 1))
        f.addRow("Offset y (mm)", self._number(ls.get("dy"), lambda x: m.edit({"op": "labels", "props": {"dy": x}}), -100, 100, 0.5, 1))
        c = QCheckBox("Line up labels in rows and columns")
        c.setChecked(bool(ls.get("align", True)))
        c.toggled.connect(lambda b: m.edit({"op": "labels", "props": {"align": b}}))
        f.addRow("", c)
        v.addWidget(g)

    def _element(self, v, e):
        m = self.main
        oid, kind = e["oid"], e.get("kind", "?")
        props = e.get("props") or {}
        title = {"label": f"Label {e.get('text', '')}"}.get(kind, f"{kind.title()} ({e.get('method', kind)})")
        g = QGroupBox(title)
        f = QFormLayout(g)
        if e.get("error"):
            err = QLabel(str(e["error"]).strip().splitlines()[-1] if str(e["error"]).strip() else "error")
            err.setWordWrap(True)
            err.setStyleSheet("color:#d33;")
            f.addRow(err)
        ends = kind == "line" and all(k in props for k in ("x1", "y1", "x2", "y2"))
        if ends:
            def endpoint(k):
                def done(x):
                    p = {q: float(props[q]) for q in ("x1", "y1", "x2", "y2")}
                    p[k] = x
                    m.edit({"op": "endpoints", "oid": oid, **p})
                return done
            for k in ("x1", "y1", "x2", "y2"):
                f.addRow(k, self._number(props[k], endpoint(k), step=0.5, decimals=1))
        else:
            bx, by, bw, bh = (e.get("box") or [0, 0, 0, 0])[:4]
            f.addRow("x (mm)", self._number(bx, lambda x: m.edit({"op": "move", "oids": [oid], "dx": round(x - bx, 2), "dy": 0}),
                                            step=0.5, decimals=1))
            f.addRow("y (mm)", self._number(by, lambda y: m.edit({"op": "move", "oids": [oid], "dx": 0, "dy": round(y - by, 2)}),
                                            step=0.5, decimals=1))
            if kind in ("panel", "image", "rect", "ellipse", "layout", "figure", "qr", "table", "inset", "legend"):
                f.addRow("Width (mm)", self._number(bw, lambda x: m.edit({"op": "resize", "oid": oid, "box": [bx, by, x, bh]}),
                                                    0.5, 5000, 0.5, 1))
                f.addRow("Height (mm)", self._number(bh, lambda y: m.edit({"op": "resize", "oid": oid, "box": [bx, by, bw, y]}),
                                                     0.5, 5000, 0.5, 1))
        v.addWidget(g)
        if kind == "label":
            g = QGroupBox("Label")
            f = QFormLayout(g)
            f.addRow("Text", self._text(e.get("text"), lambda t: m.edit({"op": "set", "oid": oid, "props": {"text": t}})))
            f.addRow(QLabel("Empty: the automatic letter."))
            v.addWidget(g)
            return
        if kind == "table":
            self._cells(v, e)
        # recolouring what a panel draws
        marks = e.get("marks") or []
        if marks:
            g = QGroupBox("Colours this panel draws with")
            grid = QGridLayout(g)
            by_color: dict[str, list[str]] = {}
            for mk in marks:
                by_color.setdefault(mk.get("color") or "", []).append(mk["key"])
            for i, (c, keys) in enumerate(by_color.items()):
                grid.addWidget(ColorButton(c, lambda new, c=c, keys=keys: m.edit(
                    {"op": "panel_color", "oid": oid, "keys": keys, "color": new, "old": c})), i // 4, (i % 4) * 2)
                grid.addWidget(QLabel(c), i // 4, (i % 4) * 2 + 1)
            v.addWidget(g)
        # every other argument of the element's call
        shown = {k: val for k, val in props.items() if k not in GEOMETRY and k not in HIDDEN_PROPS}
        if kind == "inset":
            shown.update({k: val for k, val in (e.get("item") or {}).items() if k not in GEOMETRY and k not in HIDDEN_PROPS
                          and k not in shown})
        if shown:
            g = QGroupBox("Settings (the arguments of its line of code)")
            f = QFormLayout(g)
            for k, val in shown.items():
                f.addRow(k, self._prop(oid, k, val))
            v.addWidget(g)
        overrides = [k for k in ("texts", "colors", "text_style") if props.get(k)]
        row = QHBoxLayout()
        if e.get("code") or e.get("site"):
            row.addWidget(self._button("Edit Code", lambda: m.edit_code()))
        if overrides:
            row.addWidget(self._button("Back to What the Code Draws",
                                       lambda: m.edit({"op": "set", "oid": oid, "props": {k: None for k in overrides}})))
        v.addLayout(row)

    def _cells(self, v, e):
        """A table's cells, to type into; Apply writes them to its file (or its rows=)."""
        m = self.main
        props = e.get("props") or {}
        rows = [[literal(c) for c in r] for r in (props.get("cells") or props.get("rows") or [])]
        g = QGroupBox("Cells")
        lay = QVBoxLayout(g)
        t = QTableWidget(max(1, len(rows)), max(1, max((len(r) for r in rows), default=1)))
        for i, r in enumerate(rows):
            for j, c in enumerate(r):
                t.setItem(i, j, QTableWidgetItem(c))
        t.setMinimumHeight(min(320, 30 + 26 * t.rowCount()))
        lay.addWidget(t)
        row = QHBoxLayout()
        row.addWidget(self._button("Add Row", lambda: t.insertRow(t.rowCount())))
        row.addWidget(self._button("Add Column", lambda: t.insertColumn(t.columnCount())))

        def apply():
            cells = [[(t.item(i, j).text() if t.item(i, j) else "") for j in range(t.columnCount())]
                     for i in range(t.rowCount())]
            a = {"op": "table_rows", "oid": e["oid"], "rows": cells}
            if [x.get("kind") for x in (m.figure or {}).get("elements") or []] == ["table"]:
                a["then"] = {"op": "fit_figure", "margin": 3}        # a figure that is only this table: its size
            m.edit(a)
        row.addWidget(self._button("Apply", apply))
        lay.addLayout(row)
        v.addWidget(g)

    def _prop(self, oid, k, val):
        m = self.main

        def send(x):
            m.edit({"op": "set", "oid": oid, "props": {k: x}})
        if isinstance(val, bool):
            c = QCheckBox()
            c.setChecked(val)
            c.toggled.connect(send)
            return c
        if is_color(k, val):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(self._text(val, lambda t: send(parse(t, val))))
            h.addWidget(ColorButton(val, send))
            return w
        return self._text(val, lambda t: send(parse(t, val)))

    def _part(self, v, part):
        m = self.main
        t = part["text"]
        oid, key = part["oid"], part["key"]
        g = QGroupBox(f"Text the panel draws ({key})")
        f = QFormLayout(g)
        f.addRow("Text", self._text(t.get("text"), lambda s: m.edit(
            {"op": "panel_text", "oid": oid, "key": key, "text": s, "old": t.get("text", "")})))

        def style(**p):
            m.edit({"op": "panel_text_style", "oid": oid, "key": key, "text": t.get("text", ""), "props": p})
        f.addRow("Size (pt)", self._number(t.get("size"), lambda x: style(size=x), 1, 200, 0.5, 1))
        f.addRow("Colour", ColorButton(t.get("color"), lambda c: style(color=c)))
        b = QCheckBox("Bold")
        b.setChecked(bool(t.get("bold")))
        b.toggled.connect(lambda x: style(weight="bold" if x else "normal"))
        i = QCheckBox("Italic")
        i.setChecked(bool(t.get("italic")))
        i.toggled.connect(lambda x: style(style="italic" if x else "normal"))
        f.addRow("", b)
        f.addRow("", i)
        f.addRow(self._button("Back to What the Code Draws",
                              lambda: m.edit({"op": "panel_text_reset", "oid": oid, "key": key})))
        v.addWidget(g)

    def _several(self, v, sel):
        m = self.main
        g = QGroupBox(f"{len(sel)} selected")
        grid = QGridLayout(g)
        acts = [("Align left", "align", "left"), ("Align centres", "align", "hcenter"), ("Align right", "align", "right"),
                ("Align tops", "align", "top"), ("Align middles", "align", "vcenter"), ("Align bottoms", "align", "bottom"),
                ("Distribute ↔", "distribute", "h"), ("Distribute ↕", "distribute", "v"),
                ("Match width", "match", "width"), ("Match height", "match", "height"),
                ("Group", "group", None), ("Make layout", "make_layout", None)]
        for i, (text, op, how) in enumerate(acts):
            def go(op=op, how=how):
                a = {"op": op, "oids": list(m.canvas.selection)}
                if op == "align":
                    a.update(how=how, to="selection")
                elif op == "distribute":
                    a["axis"] = how
                elif op == "match":
                    a["what"] = how
                m.edit(a)
            grid.addWidget(self._button(text, go), i // 3, i % 3)
        v.addWidget(g)
