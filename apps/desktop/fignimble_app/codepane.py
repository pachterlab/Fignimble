"""The code behind the figure: the figure file, the style and the panels' files, editable. Applying (Ctrl+S) goes
through the engine (`write_source`), so it is one undo step like a canvas edit; a file changed on disk (by an agent)
is reloaded unless it has unapplied edits here."""

from __future__ import annotations

import keyword
import os
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from . import locate


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, doc, dark: bool):
        super().__init__(doc)
        c = (lambda light, dk: QColor(dk if dark else light))
        self.rules = []

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(color)
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f
        kw = fmt(c("#9B2393", "#FF7AB2"), bold=True)
        self.rules.append((QRegularExpression(r"\b(" + "|".join(keyword.kwlist) + r")\b"), kw))
        self.rules.append((QRegularExpression(r"\b[0-9]+(\.[0-9]+)?\b"), fmt(c("#1C00CF", "#D9C97C"))))
        self.rules.append((QRegularExpression(r"\b[A-Za-z_][A-Za-z0-9_]*(?=\()"), fmt(c("#326D74", "#67B7A4"))))
        self.rules.append((QRegularExpression(r"\"[^\"\\]*(\\.[^\"\\]*)*\"|'[^'\\]*(\\.[^'\\]*)*'"), fmt(c("#C41A16", "#FC6A5D"))))
        self.rules.append((QRegularExpression(r"#[^\n]*"), fmt(c("#5D6C79", "#7F8C98"), italic=True)))

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class CodePane(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.path: str | None = None
        self.loaded = ""
        self.mtime = None
        self.files = QComboBox()
        self.files.setMinimumContentsLength(24)
        self.files.activated.connect(lambda i: self.open(self.files.itemData(i)))
        self.where = QLabel("")
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status = QLabel("")
        apply_ = QPushButton("Apply (Ctrl+S)")
        apply_.clicked.connect(self.save)
        revert = QPushButton("Revert")
        revert.clicked.connect(self.revert)
        ext = QPushButton("Open in Editor")
        ext.clicked.connect(lambda: self.path and locate.open_file(self.path))
        self.edit = QPlainTextEdit()
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.edit.setFont(font)
        self.edit.setTabStopDistance(4 * self.edit.fontMetrics().horizontalAdvance(" "))
        self.edit.textChanged.connect(self._changed)
        dark = self.palette().color(self.palette().ColorRole.Base).lightness() < 128
        self.hl = PythonHighlighter(self.edit.document(), dark)
        bar = QHBoxLayout()
        for w in (self.files, self.status):
            bar.addWidget(w)
        bar.addStretch(1)
        for w in (apply_, revert, ext):
            bar.addWidget(w)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addLayout(bar)
        v.addWidget(self.where)
        v.addWidget(self.edit, 1)

    @property
    def dirty(self) -> bool:
        return self.path is not None and self.edit.toPlainText() != self.loaded

    def _changed(self):
        self.status.setText("edited: Ctrl+S applies" if self.dirty else "")

    def set_files(self, files: list[str]):
        cur = self.path
        self.files.blockSignals(True)
        self.files.clear()
        root = self.main.project or ""
        for f in files:
            self.files.addItem(os.path.relpath(f, root) if root else f, f)
        if cur and cur not in files:
            self.files.addItem(os.path.basename(cur), cur)
        if cur:
            self.files.setCurrentIndex(max(0, self.files.findData(cur)))
        self.files.blockSignals(False)

    def clear(self):
        self.path = None
        self.loaded = ""
        self.edit.blockSignals(True)
        self.edit.setPlainText("")
        self.edit.blockSignals(False)
        self.where.setText("")
        self.status.setText("")

    def open(self, path: str | None, line: int | None = None):
        if not path:
            return
        if self.dirty and path != self.path:
            self.save()
        if path != self.path or not self.dirty:
            try:
                text = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                self.main.toast(str(exc), error=True)
                return
            self.path = path
            self.loaded = text
            self.mtime = _mtime(path)
            self.edit.blockSignals(True)
            self.edit.setPlainText(text)
            self.edit.blockSignals(False)
        i = self.files.findData(path)
        if i < 0:
            self.files.addItem(os.path.basename(path), path)
            i = self.files.count() - 1
        self.files.setCurrentIndex(i)
        self.where.setText(locate.system.shown(path))
        self._changed()
        if line:
            c = QTextCursor(self.edit.document().findBlockByLineNumber(max(0, int(line) - 1)))
            c.select(QTextCursor.SelectionType.LineUnderCursor)
            self.edit.setTextCursor(c)
            self.edit.centerCursor()

    def reload_if_changed(self):
        """The file changed on disk (an agent, another editor): show it, unless there are edits here."""
        if not self.path or self.dirty:
            return
        m = _mtime(self.path)
        if m != self.mtime:
            pos = self.edit.textCursor().position()
            self.open(self.path)
            c = self.edit.textCursor()
            c.setPosition(min(pos, len(self.edit.toPlainText())))
            self.edit.setTextCursor(c)

    def save(self):
        if not self.dirty:
            return
        text, path = self.edit.toPlainText(), self.path

        def done():
            if self.path == path:                      # still this file (not switched to another since)
                self.loaded = text
                self.mtime = _mtime(path)
            self._changed()
        self.main.write_source(path, text, done)

    def revert(self):
        if self.path:
            p = self.path
            self.path = None
            self.open(p)


def _mtime(p):
    try:
        return os.stat(p).st_mtime_ns
    except OSError:
        return None
