"""The dialogs: new figure (a size, a journal width or a poster template), export, a project on an SSH server."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QLineEdit, QRadioButton, QVBoxLayout, QWidget, QLabel)

FORMATS = ["pdf", "eps", "svg", "png", "tiff", "jpg", "pptx", "docx", "xlsx", "csv", "tsv"]     # the last four: a table
DPIS = [300, 600, 1000, 1200, 2400]


def _buttons(d: QDialog, ok: str = "OK") -> QDialogButtonBox:
    b = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    b.button(QDialogButtonBox.StandardButton.Ok).setText(ok)
    b.accepted.connect(d.accept)
    b.rejected.connect(d.reject)
    return b


class NewFigureDialog(QDialog):
    """A figure of a size (or a journal's column width), or a poster from a template."""

    def __init__(self, parent, journals: dict, templates: list, journal: str | None, name: str):
        super().__init__(parent)
        self.setWindowTitle("New Figure")
        self.journals, self.templates = journals, templates
        f = QFormLayout()
        self.name = QLineEdit(name)
        f.addRow("Name", self.name)
        self.plain = QRadioButton("Figure")
        self.poster = QRadioButton("Poster")
        self.plain.setChecked(True)
        f.addRow(self.plain)
        self.width = QComboBox()
        self.width.setEditable(True)
        self.sizes = []
        j = journals.get(journal or "") or {}
        for k, w in (j.get("widths") or {}).items():
            self.sizes.append(float(w))
            self.width.addItem(f"{w} mm ({j.get('name', journal)} {k})")
        for w in (183, 89, 120):
            if float(w) not in self.sizes:
                self.sizes.append(float(w))
                self.width.addItem(f"{w} mm")
        f.addRow("Width", self.width)
        self.height = QDoubleSpinBox()
        self.height.setRange(5, 5000)
        self.height.setValue(100)
        self.height.setSuffix(" mm")
        f.addRow("Height", self.height)
        f.addRow(self.poster)
        self.template = QComboBox()
        for t in templates:
            w, h = t.get("size", [0, 0])
            self.template.addItem(f"{t.get('name', t.get('key'))} ({w:.0f} × {h:.0f} mm)")
        f.addRow("Template", self.template)
        if not templates:
            self.poster.setEnabled(False)
        v = QVBoxLayout(self)
        v.addLayout(f)
        v.addWidget(_buttons(self, "Create"))

    def request(self) -> dict:
        name = self.name.text().strip() or "fig"
        if self.poster.isChecked() and self.templates:
            t = self.templates[self.template.currentIndex()]
            return {"name": name, "poster": t.get("poster"), "landscape": bool(t.get("landscape")), "template": t.get("key")}
        i = self.width.currentIndex()
        text = self.width.currentText().split()[0] if self.width.currentText() else "183"
        try:
            w = float(text)
        except ValueError:
            w = self.sizes[i] if 0 <= i < len(self.sizes) else 183.0
        return {"name": name, "width": w, "height": float(self.height.value())}


class ExportDialog(QDialog):
    def __init__(self, parent, journals: dict, journal: str | None, poster: bool):
        super().__init__(parent)
        self.setWindowTitle("Export Figure")
        f = QFormLayout()
        self.format = QComboBox()
        self.format.addItems([x.upper() for x in FORMATS])
        f.addRow("Format", self.format)
        self.dpi = QComboBox()
        self.dpi.addItems([f"{d} dpi" for d in DPIS])
        self.dpi.setCurrentIndex(1)
        f.addRow("Resolution", self.dpi)
        self.keys = sorted(journals, key=lambda k: journals[k].get("rank", 0))
        self.journal = QComboBox()
        self.journal.addItem("(the style's)")
        self.journal.addItems([journals[k].get("name", k) for k in self.keys])
        f.addRow("Journal's rules", self.journal)
        self.dark = QCheckBox("Dark version")
        self.cmyk = QCheckBox("CMYK (for print)")
        self.outline = QCheckBox("Text as outlines")
        for c in (self.dark, self.cmyk, self.outline):
            f.addRow(c)
        self.bleed = QDoubleSpinBox()
        self.bleed.setRange(0, 20)
        self.bleed.setSuffix(" mm")
        self.bleed.setValue(3 if poster else 0)
        self.marks = QCheckBox("Crop marks")
        f.addRow("Bleed (PDF)", self.bleed)
        f.addRow(self.marks)
        f.addRow(QLabel("EPS, CMYK and outlined text use Ghostscript.\nDOCX, XLSX, CSV and TSV export a table figure's cells."))
        v = QVBoxLayout(self)
        v.addLayout(f)
        v.addWidget(_buttons(self, "Export…"))

    def fmt(self) -> str:
        return FORMATS[self.format.currentIndex()]

    def request(self) -> dict:
        a = {"format": self.fmt(), "dpi": DPIS[self.dpi.currentIndex()], "dark": self.dark.isChecked(),
             "cmyk": self.cmyk.isChecked(), "outline": self.outline.isChecked()}
        if self.journal.currentIndex() > 0:
            a["journal"] = self.keys[self.journal.currentIndex() - 1]
        if self.fmt() == "pdf":
            a["bleed"] = float(self.bleed.value())
            a["marks"] = self.marks.isChecked()
        return a


class RemoteDialog(QDialog):
    """A host from ~/.ssh/config (or typed), a folder there, and the Python there."""

    def __init__(self, parent, hosts: list[str], title: str, ask_python: bool, prefill: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        p = prefill or {}
        f = QFormLayout()
        self.host = QComboBox()
        self.host.setEditable(True)
        self.host.addItems(hosts)
        if p.get("host"):
            self.host.setEditText(p["host"])
        f.addRow("Server", self.host)
        self.path = QLineEdit(p.get("path", "~"))
        f.addRow("Folder", self.path)
        self.python = QLineEdit(p.get("python", "python3"))
        if ask_python:
            f.addRow("Python there", self.python)
        note = QLabel("ssh runs without asking for a password here: the server has to accept your key "
                      "(ssh-agent, or a key in ~/.ssh).")
        note.setWordWrap(True)
        v = QVBoxLayout(self)
        v.addLayout(f)
        v.addWidget(note)
        v.addWidget(_buttons(self, "Open"))

    def request(self) -> dict:
        return {"host": self.host.currentText().strip(), "path": self.path.text().strip() or "~",
                "python": self.python.text().strip() or "python3"}


class ChoiceDialog(QDialog):
    """Pick one of several, with a short explanation."""

    def __init__(self, parent, title: str, text: str, items: list[str]):
        super().__init__(parent)
        self.setWindowTitle(title)
        v = QVBoxLayout(self)
        lab = QLabel(text)
        lab.setWordWrap(True)
        v.addWidget(lab)
        self.combo = QComboBox()
        self.combo.addItems(items)
        v.addWidget(self.combo)
        v.addWidget(_buttons(self))

    def index(self) -> int:
        return self.combo.currentIndex()


def spacer() -> QWidget:
    return QWidget()
