"""The Fignimble window for Windows and Linux: figures and layers on the left, the canvas, the inspector on the
right, and the code, preflight, history and caption below. Every change goes to the engine as the macOS app sends
it (docs/protocol.md), so the files, undo and rendering behave the same on every system."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from functools import partial
from pathlib import Path

from PySide6.QtCore import QMimeData, QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QIcon, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QStyleFactory, QVBoxLayout, QWidget, QLineEdit, QTextEdit, QAbstractSpinBox,
                               QComboBox)

from . import dialogs, locate, terminal, version
from .canvas import TOOLS, Canvas
from .codepane import CodePane
from .engine import EngineClient
from .inspector import LABEL_FORMATS, Inspector

PASTE_TYPE = "application/x-fignimble-elements"
IMAGE_FILTER = "Pictures (*.png *.jpg *.jpeg *.tif *.tiff *.pdf *.svg *.gif *.bmp *.webp)"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("pachterlab", "Fignimble")
        self.engine = EngineClient(self)
        self.engine.event.connect(self.on_event)
        self.engine.exited.connect(self.on_engine_exit)
        self.engine.busy.connect(self.on_busy)
        self.python: str | None = None
        self.project: str | None = None
        self.remote: dict | None = None
        self.figure: dict | None = None
        self.current: str | None = None
        self.state: dict = {}
        self.figures: list[dict] = []
        self.journals: dict = {}
        self.posters: dict = {}
        self.templates: list = []
        self.last_px = 8.0
        self.dark_preview = False
        self._background = False
        self.setWindowTitle("Fignimble")
        self.setAcceptDrops(True)

        self.canvas = Canvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.selectionChanged.connect(self.selection_changed)
        self.canvas.cursorMoved.connect(self.cursor_moved)
        self.canvas.viewChanged.connect(self.view_changed)
        self.sharpen = QTimer(self, singleShot=True, interval=350, timeout=self._sharpen)

        self.figure_list = QListWidget()
        self.figure_list.setIconSize(QSize(96, 72))
        self.figure_list.itemClicked.connect(lambda it: self.show_figure(it.data(Qt.ItemDataRole.UserRole)))
        self.layer_list = QListWidget()
        self.layer_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.layer_list.itemSelectionChanged.connect(self._layers_picked)
        self.inspector = Inspector(self)
        self.code = CodePane(self)
        self.problems = QListWidget()
        self.problems.itemClicked.connect(self._problem_picked)
        self.history = QListWidget()
        self.history.itemClicked.connect(self._history_picked)
        self.caption = QPlainTextEdit()
        cap = QWidget()
        cv = QVBoxLayout(cap)
        cv.setContentsMargins(4, 4, 4, 4)
        cv.addWidget(QLabel("The figure's caption (fig.caption(...)); exported with the figure as text."))
        cv.addWidget(self.caption)
        b = QPushButton("Apply Caption")
        b.clicked.connect(lambda: self.edit({"op": "caption", "text": self.caption.toPlainText()}))
        cv.addWidget(b)

        self.docks = {}
        for key, title, w, area in [("figures", "Figures", self.figure_list, Qt.DockWidgetArea.LeftDockWidgetArea),
                                    ("layers", "Layers", self.layer_list, Qt.DockWidgetArea.LeftDockWidgetArea),
                                    ("inspector", "Inspector", self.inspector, Qt.DockWidgetArea.RightDockWidgetArea),
                                    ("code", "Code", self.code, Qt.DockWidgetArea.BottomDockWidgetArea),
                                    ("preflight", "Preflight", self.problems, Qt.DockWidgetArea.BottomDockWidgetArea),
                                    ("history", "History", self.history, Qt.DockWidgetArea.BottomDockWidgetArea),
                                    ("caption", "Caption", cap, Qt.DockWidgetArea.BottomDockWidgetArea)]:
            d = QDockWidget(title, self)
            d.setObjectName(key)
            d.setWidget(w)
            self.addDockWidget(area, d)
            self.docks[key] = d
        for k in ("preflight", "history", "caption"):
            self.tabifyDockWidget(self.docks["code"], self.docks[k])
        self.docks["code"].raise_()

        self.status_text = QLabel("")
        self.cursor_text = QLabel("")
        self.zoom_text = QLabel("")
        self.statusBar().addWidget(self.status_text, 1)
        self.statusBar().addPermanentWidget(self.cursor_text)
        self.statusBar().addPermanentWidget(self.zoom_text)
        self.build_menus()
        self.resize(1400, 900)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        st = self.settings.value("windowState")
        if st is not None:
            self.restoreState(st)

    # --- starting ---

    def start(self, open_path: str | None = None):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            py = locate.find_python(self.settings.value("python"))
        finally:
            QApplication.restoreOverrideCursor()
        if not py:
            py = self.missing_python()
            if not py:
                return
        self.start_engine(py)
        if open_path:
            self.open_project(open_path)
        else:
            self.status("Open a project (Ctrl+O), one of File ▸ Open Recent, or make a new one (Ctrl+Shift+N).")

    def start_engine(self, py: str, tunnel: dict | None = None):
        self.python = py
        locate.engine_python = py
        self.engine.start(py, tunnel)
        if tunnel is None:
            self.engine.call("journals", {}, lambda r: setattr(self, "journals", r))
            self.engine.call("posters", {}, lambda r: setattr(self, "posters", r))
            self.engine.call("poster_templates", {}, lambda r: setattr(self, "templates", r.get("templates") or []))

    def missing_python(self) -> str | None:
        while True:
            m = QMessageBox(self)
            m.setWindowTitle("Fignimble needs a Python")
            m.setText("Fignimble draws figures with Python, and needs one that has matplotlib, PyMuPDF and Pillow.")
            m.setInformativeText("Run the installer (install_fignimble.sh on Linux, install_fignimble.ps1 on Windows), "
                                 "or choose a Python of your own (a conda or venv environment) where you ran\n\n"
                                 '    pip install matplotlib "pymupdf<2" pillow numpy')
            choose = m.addButton("Choose a Python…", QMessageBox.ButtonRole.AcceptRole)
            m.addButton("Quit", QMessageBox.ButtonRole.RejectRole)
            m.exec()
            if m.clickedButton() is not choose:
                QTimer.singleShot(0, QApplication.quit)
                return None
            path = self.pick_python_file()
            if path:
                return path

    def pick_python_file(self) -> str | None:
        flt = "Python (python.exe pythonw.exe)" if locate.WINDOWS else "Python (python python3 python3.*)"
        path, _ = QFileDialog.getOpenFileName(self, "A Python with matplotlib, pymupdf, pillow and numpy", "", flt)
        if not path:
            return None
        if locate.can_render(path):
            self.settings.setValue("python", path)
            return path
        QMessageBox.warning(self, "That Python can't draw figures", f"{path} can't import matplotlib, pymupdf and PIL.")
        return None

    def choose_python(self):
        path = self.pick_python_file()
        if path:
            proj, fig = self.project, self.current
            self.engine.stop()
            self.start_engine(path)
            if proj and not self.remote:
                self.open_project(proj, fig)

    def on_engine_exit(self, code: int):
        if self.remote:
            r = self.remote
            if QMessageBox.question(self, f"The connection to {r['host']} closed",
                                    "Edits that hadn't reached the server yet are kept here and sent when you "
                                    "reconnect.\n\n" + locate.log_tail(4),
                                    QMessageBox.StandardButton.Retry | QMessageBox.StandardButton.Close) \
                    == QMessageBox.StandardButton.Retry:
                self.open_remote(r, self.current)
            return
        QMessageBox.critical(self, "The Fignimble engine stopped",
                             f"Exit status {code}. The end of its log ({locate.system.shown(locate.log_path())}):\n\n"
                             + locate.log_tail())

    # --- the project ---

    def open_project(self, path: str, figure: str | None = None):
        p = Path(path).expanduser().resolve()
        if p.is_file():
            figure = figure or p.stem
            p = p.parent
        if self.code.dirty:
            self.code.save()
        if self.remote or not self.engine.running():
            self.remote = None
            self.engine.stop()
            self.start_engine(self.python)

        def done(r):
            self.project = r.get("project") or str(p)
            self.remote = None
            self.note_recent(self.project)
            self.opened(p.name, figure)
        self.engine.call("open", {"project": str(p)}, done,
                         lambda e: QMessageBox.warning(self, f"Couldn't open {p.name}", e))

    def open_remote(self, r: dict, figure: str | None = None):
        if locate.WINDOWS:
            QMessageBox.information(self, "Projects on SSH servers",
                                    "Opening a project on an SSH server needs macOS or Linux for now. On Windows, "
                                    "Insert ▸ Panel from SSH Server… copies panel code from a server.")
            return
        slug = hashlib.sha256(f"{r['host']}:{r['path']}".encode()).hexdigest()[:12]
        name = Path(r["path"]).name or "home"
        mirror = Path(cache_dir()) / "remote" / f"{name}-{slug}" / name
        mirror.mkdir(parents=True, exist_ok=True)
        self.engine.stop()
        self.start_engine(self.python, {**r, "mirror": str(mirror)})
        self.status(f"Connecting to {r['host']}…")

        def done(res):
            self.project = res.get("project") or str(mirror)
            self.remote = r
            self.note_recent(f"ssh:{r['host']}:{r['path']}")
            self.settings.setValue(f"sshPython/{r['host']}", r.get("python") or "python3")
            self.opened(f"{name} on {r['host']}", figure)

        def failed(e):
            self.engine.stop()
            QMessageBox.warning(self, f"Couldn't open {r['path']} on {r['host']}", e + "\n\n" + locate.log_tail(6))
        self.engine.call("open", {"project": str(mirror)}, done, failed)

    def opened(self, title: str, figure: str | None):
        self.setWindowTitle(f"Fignimble — {title}")
        self.current = None
        self.figure = None
        self.canvas.set_selection([])
        self.code.clear()

        def then(_s):
            name = figure if figure in [f["name"] for f in self.figures] else (self.figures[0]["name"] if self.figures else None)
            if name:
                self.load(name)
            else:
                self.canvas.set_figure(None)
                self.inspector.update_view()
                self.status("No figures yet: File ▸ New Figure (Ctrl+N).")
            self.check_packages(asked=False)
        self.refresh_state(then)

    def recent(self) -> list[str]:
        """The projects opened lately (QSettings gives back a one-item list as a string on some systems)."""
        rec = self.settings.value("recent") or []
        return [rec] if isinstance(rec, str) else [str(k) for k in rec]

    def note_recent(self, key: str):
        self.settings.setValue("recent", ([key] + [k for k in self.recent() if k != key])[:12])

    def refresh_state(self, then=None):
        if not self.project:
            return

        def got(s):
            self.state = s
            self.figures = s.get("figures") or []
            self._fill_figures()
            self._fill_history(s)
            files = []
            if self.figure and self.figure.get("file"):
                files.append(self.figure["file"])
            style = os.path.join(self.project, "style.py")
            if os.path.exists(style):
                files.append(style)
            files += [os.path.join(self.project, p) for p in s.get("panels") or []]
            self.code.set_files([f for f in files if os.path.exists(f)])
            self.update_actions()
            if then:
                then(s)
        self.engine.call("state", {}, got, lambda e: then and then({}))

    def _fill_figures(self):
        self.figure_list.clear()
        for f in self.figures:
            it = QListWidgetItem(f["name"] + (f"  ({f['warnings']} ⚠)" if f.get("warnings") else "")
                                 + ("  ✖" if f.get("error") else ""))
            it.setData(Qt.ItemDataRole.UserRole, f["name"])
            if f.get("thumb") and os.path.exists(f["thumb"]):
                it.setIcon(QIcon(QPixmap(f["thumb"])))
            self.figure_list.addItem(it)
            if f["name"] == self.current:
                it.setSelected(True)

    def _fill_history(self, s):
        self.history.clear()
        done, future = s.get("history") or [], s.get("future") or []
        start = QListWidgetItem("(as opened)")
        start.setData(Qt.ItemDataRole.UserRole, 0)
        self.history.addItem(start)
        for i, label in enumerate(done):
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, i + 1)
            self.history.addItem(it)
        self.history.setCurrentRow(len(done))
        for j, label in enumerate(future):
            it = QListWidgetItem("↷ " + label)
            it.setData(Qt.ItemDataRole.UserRole, len(done) + j + 1)
            it.setForeground(Qt.GlobalColor.gray)
            self.history.addItem(it)

    def _history_picked(self, it):
        if self.current:
            n = it.data(Qt.ItemDataRole.UserRole)
            self.engine.call("undo_to", {"n": n, "figure": self.current, "pxPerMM": self.last_px}, self._after_undo,
                             partial(self.toast, error=True))

    # --- figures ---

    def show_figure(self, name: str):
        if name and name != self.current:
            if self.code.dirty:
                self.code.save()
            self.canvas.set_selection([])
            self.load(name)

    def load(self, name: str, force: bool = False):
        px = self.canvas.wanted_px()
        self.status(f"Rendering {name}…")

        def got(r):
            self.last_px = px
            switched = self.current != name
            self.current = name
            self.apply(r)
            if switched and r.get("file"):
                self.code.open(r["file"])
            self.refresh_state()
        args = {"name": name, "pxPerMM": px, "dark": self.dark_preview}
        self.engine.call("figure", args, got, partial(self.toast, error=True))

    def refresh(self):
        if self.current:
            self.load(self.current)
        else:
            self.refresh_state()

    def apply(self, r: dict):
        self.figure = r
        self.canvas.set_figure(r)
        self._fill_layers()
        self._fill_problems(r.get("warnings") or [])
        cap = (r.get("caption") or {}).get("text") or ""
        if self.caption.toPlainText() != cap:
            self.caption.setPlainText(cap)
        focus = QApplication.focusWidget()
        if not (self._background and focus is not None and self.inspector.isAncestorOf(focus)):
            self.inspector.update_view()
        self._background = False
        if r.get("error"):
            self.status("The figure's code doesn't run: " + str(r["error"]).strip().splitlines()[-1])
        else:
            n = len([w for w in r.get("warnings") or [] if w.get("level") != "info"])
            w, h = (r.get("size") or [0, 0])[:2]
            j = r.get("journal")
            jname = j.get("name") if isinstance(j, dict) else None
            self.status(f"{locate.system.shown(r.get('file', ''))}  ·  {w:g} × {h:g} mm" + (f"  ·  {jname}" if jname else "")
                        + (f"  ·  {n} preflight warning{'s' if n != 1 else ''}" if n else ""))
        self.view_changed()
        self.update_actions()

    def _fill_layers(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for e in reversed((self.figure or {}).get("elements") or []):
            props = e.get("props") or {}
            what = props.get("source") or props.get("text") or e.get("name") or ""
            flags = ("🔒" if e.get("locked") else "") + ("👁̸" if e.get("hidden") else "")
            indent = "    " if e.get("parent") or e.get("host") else ""
            it = QListWidgetItem(f"{indent}{e.get('kind')} {what}".rstrip() + (f"  {flags}" if flags else ""))
            it.setData(Qt.ItemDataRole.UserRole, e["oid"])
            if e.get("error"):
                it.setForeground(Qt.GlobalColor.red)
            self.layer_list.addItem(it)
            it.setSelected(e["oid"] in self.canvas.selection)
        self.layer_list.blockSignals(False)

    def _layers_picked(self):
        oids = [it.data(Qt.ItemDataRole.UserRole) for it in self.layer_list.selectedItems()]
        if oids != self.canvas.selection:
            self.canvas.set_selection(oids)

    def _fill_problems(self, warnings):
        self.problems.clear()
        for w in warnings:
            icon = {"error": "✖", "warning": "⚠", "info": "ℹ"}.get(w.get("level"), "•")
            it = QListWidgetItem(f"{icon}  {w.get('message', '')}")
            it.setData(Qt.ItemDataRole.UserRole, w.get("oid"))
            self.problems.addItem(it)
        n = len([w for w in warnings if w.get("level") != "info"])
        self.docks["preflight"].setWindowTitle(f"Preflight ({n})" if n else "Preflight")

    def _problem_picked(self, it):
        oid = it.data(Qt.ItemDataRole.UserRole)
        if oid and oid in self.canvas.by_oid:
            self.canvas.set_selection([oid])

    def selection_changed(self):
        self.layer_list.blockSignals(True)
        for i in range(self.layer_list.count()):
            it = self.layer_list.item(i)
            it.setSelected(it.data(Qt.ItemDataRole.UserRole) in self.canvas.selection)
        self.layer_list.blockSignals(False)
        self.inspector.update_view()
        self.update_actions()
        if len(self.canvas.selection) == 1 and self.docks["code"].isVisible():
            e = self.canvas.by_oid.get(self.canvas.selection[0]) or {}
            where = e.get("code") or e.get("site")
            if where and where.get("file") and not self.code.dirty:
                self.code.open(where["file"], where.get("line"))

    # --- edits ---

    def edit(self, args: dict, then=None):
        if not self.current:
            return
        a = dict(args, figure=self.current, pxPerMM=self.last_px)

        def got(r):
            if r.get("figure"):
                self.apply(r["figure"])
            if r.get("ok"):
                if r.get("select"):
                    self.canvas.set_selection(list(r["select"]))
            else:
                self.canvas.clear_pending()
                self.toast(r.get("error") or "That didn't work", error=True)
            self.code.reload_if_changed()
            self.refresh_state()
            if then:
                then(r)

        def failed(e):
            self.canvas.clear_pending()
            self.toast(e, error=True)
        self.engine.call("edit", a, got, failed)

    def write_source(self, path: str, text: str, done):
        a = {"file": path, "text": text, "pxPerMM": self.last_px}
        if self.current:
            a["figure"] = self.current

        def got(r):
            if r.get("figure"):
                self.apply(r["figure"])
            done()
            self.refresh_state()
        self.engine.call("write_source", a, got, partial(self.toast, error=True))

    def _after_undo(self, r):
        if r.get("figure"):
            self.apply(r["figure"])
        if not r.get("ok") and r.get("error"):
            self.toast(r["error"], error=True)
        elif r.get("label"):
            self.toast(f"Undid {r['label']}" if r.get("_undo") else r["label"])
        self.code.reload_if_changed()
        self.refresh_state()

    def drain(self, timeout: float = 30.0):
        """Wait until the engine has answered everything sent so far."""
        import time
        end = time.time() + timeout
        while self.engine.running() and (self.engine.in_flight is not None or self.engine.queue) and time.time() < end:
            QApplication.processEvents()
            time.sleep(0.01)

    def _typing(self) -> bool:
        w = QApplication.focusWidget()
        return isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)) or \
            (isinstance(w, QComboBox) and w.isEditable())

    def undo(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "undo"):
                w.undo()
            return
        name = self.current or (self.figures[0]["name"] if self.figures else None)
        if name:
            self.engine.call("undo", {"figure": name, "pxPerMM": self.last_px},
                             lambda r: self._after_undo({**r, "_undo": True}), partial(self.toast, error=True))

    def redo(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "redo"):
                w.redo()
            return
        if self.current:
            self.engine.call("redo", {"figure": self.current, "pxPerMM": self.last_px}, self._after_undo,
                             partial(self.toast, error=True))

    def on_event(self, ev: dict):
        kind = ev.get("event")
        if kind == "changed":
            self.code.reload_if_changed()
            self._background = True
            self.refresh()
        elif kind == "rendered":
            self.refresh_state()
        elif kind == "needs_packages":
            pip = ev.get("pip") or []
            QMessageBox.warning(self, f"{ev.get('python')} on {ev.get('host')} lacks packages",
                                "Install them there with:\n\n    " + (ev.get("python") or "python3") + " -m pip install "
                                + " ".join(pip) if pip else "That Python is older than 3.10.")

    def on_busy(self, busy: bool):
        if busy:
            QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor) if not QApplication.overrideCursor() else None
        else:
            while QApplication.overrideCursor():
                QApplication.restoreOverrideCursor()

    # --- messages ---

    def status(self, text: str):
        self.status_text.setText(text)

    def toast(self, text: str, error: bool = False):
        self.statusBar().showMessage(("⚠ " if error else "✓ ") + str(text).strip().splitlines()[-1] if text else "",
                                     8000 if error else 3000)
        if error and len(str(text)) > 120:
            QMessageBox.warning(self, "Fignimble", str(text))

    def cursor_moved(self, mm):
        self.cursor_text.setText("" if mm is None or not self.figure else f"x {mm.x():.1f}  y {mm.y():.1f} mm")

    def view_changed(self):
        self.zoom_text.setText(f"{self.canvas.scale / (self.logicalDpiX() / 25.4) * 100:.0f}%" if self.figure else "")
        if self.current and self.canvas.wanted_px() > self.last_px + 1e-6:
            self.sharpen.start()

    def _sharpen(self):
        if self.current and self.canvas.wanted_px() > self.last_px + 1e-6:
            self.load(self.current)

    # --- menus ---

    def act(self, menu, text, fn, shortcut=None, checkable=False, enabled_when=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        a.triggered.connect(lambda checked=False: fn(checked) if checkable else fn())
        menu.addAction(a)
        if enabled_when:
            self._conditional.append((a, enabled_when))
        return a

    def build_menus(self):
        self._conditional = []
        mb = self.menuBar()
        has_fig = lambda: self.figure is not None                     # noqa: E731
        has_sel = lambda: bool(self.canvas.selection)                  # noqa: E731
        has_proj = lambda: self.project is not None                    # noqa: E731

        m = mb.addMenu("&File")
        self.act(m, "New Project…", self.new_project, "Ctrl+Shift+N")
        self.act(m, "New Figure…", self.new_figure, "Ctrl+N", enabled_when=has_proj)
        self.act(m, "Open Project…", self.open_dialog, "Ctrl+O")
        self.act(m, "Open Project on SSH Server…", self.open_remote_dialog, "Ctrl+Shift+O")
        self.recent_menu = m.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self.fill_recent)
        m.addSeparator()
        self.act(m, "Apply Code Changes", self.code.save, "Ctrl+S")
        self.act(m, "Show Project Folder", lambda: self.project and locate.reveal(self.project), enabled_when=has_proj)
        m.addSeparator()
        self.act(m, "Export Figure…", self.export_figure, "Ctrl+E", enabled_when=has_fig)
        self.act(m, "Export All Figures as PDF…", self.export_all, "Ctrl+Shift+E", enabled_when=has_proj)
        self.act(m, "Export Table as Word…", self.export_table, enabled_when=has_sel)
        m.addSeparator()
        self.act(m, "Choose Python for Figures…", self.choose_python)
        self.act(m, "Check Packages…", lambda: self.check_packages(asked=True), enabled_when=has_proj)
        self.act(m, "Install Command Line Tool…", self.install_cli)
        m.addSeparator()
        self.act(m, "Quit", self.close, "Ctrl+Q")

        m = mb.addMenu("&Edit")
        self.act(m, "Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.act(m, "Redo", self.redo, QKeySequence.StandardKey.Redo)
        m.addSeparator()
        self.act(m, "Cut", self.cut, QKeySequence.StandardKey.Cut)
        self.act(m, "Copy", self.copy, QKeySequence.StandardKey.Copy)
        self.act(m, "Paste", self.paste, QKeySequence.StandardKey.Paste)
        self.act(m, "Copy Figure as Picture", self.copy_figure, "Ctrl+Shift+C", enabled_when=has_fig)
        self.act(m, "Copy Style", self.copy_style, "Ctrl+Alt+C", enabled_when=has_sel)
        self.act(m, "Paste Style", self.paste_style, "Ctrl+Alt+V", enabled_when=has_sel)
        self.act(m, "Duplicate", lambda: self.sel_edit({"op": "duplicate", "dx": 4, "dy": 4}), "Ctrl+D", enabled_when=has_sel)
        self.act(m, "Delete", self.delete_selected, enabled_when=has_sel)
        self.act(m, "Select All", self.select_all, QKeySequence.StandardKey.SelectAll)

        m = mb.addMenu("&Insert")
        self.act(m, "New Panel from Code…", self.new_panel, "Ctrl+Shift+P", enabled_when=has_fig)
        self.act(m, "Panel from Existing Code…", self.insert_panel_code, enabled_when=has_fig)
        self.act(m, "Picture…", self.insert_image, "Ctrl+Shift+I", enabled_when=has_fig)
        self.act(m, "Figure…", self.insert_figure, enabled_when=has_fig)
        self.act(m, "QR Code…", self.insert_qr, enabled_when=has_fig)
        self.act(m, "New Table…", self.new_table, "Ctrl+Shift+T", enabled_when=has_proj)
        self.act(m, "Table from CSV…", self.insert_table, enabled_when=has_fig)
        self.act(m, "Panel from SSH Server…", self.insert_remote_panel, enabled_when=has_fig)
        self.act(m, "Scale Bar on Picture…", self.add_scalebar, enabled_when=has_sel)
        self.act(m, "Zoom Box", lambda: self.edit({"op": "add_zoom", "oids": self.sel_elements()[:2]}), enabled_when=has_sel)
        m.addSeparator()
        for t, (title, key) in list(TOOLS.items())[1:]:
            self.act(m, f"{title}\t{key}", partial(self.pick_tool, t), enabled_when=has_fig)

        m = mb.addMenu("&Object")
        a = m.addMenu("Arrange")
        for text, how, key in [("Bring to Front", "front", "Ctrl+Shift+]"), ("Bring Forward", "forward", "Ctrl+]"),
                               ("Send Backward", "backward", "Ctrl+["), ("Send to Back", "back", "Ctrl+Shift+[")]:
            self.act(a, text, partial(self.sel_edit, {"op": "order", "how": how}), key, enabled_when=has_sel)
        a = m.addMenu("Align")
        for text, how in [("Left Edges", "left"), ("Centres", "hcenter"), ("Right Edges", "right"), ("Tops", "top"),
                          ("Middles", "vcenter"), ("Bottoms", "bottom")]:
            self.act(a, text, partial(self.align, how), enabled_when=has_sel)
        a.addSeparator()
        self.align_figure = self.act(a, "To the Figure", lambda c: None, checkable=True)
        a = m.addMenu("Distribute")
        self.act(a, "Horizontally", partial(self.sel_edit, {"op": "distribute", "axis": "h"}), enabled_when=has_sel)
        self.act(a, "Vertically", partial(self.sel_edit, {"op": "distribute", "axis": "v"}), enabled_when=has_sel)
        a = m.addMenu("Match Size")
        self.act(a, "Width", partial(self.sel_edit, {"op": "match", "what": "width"}), enabled_when=has_sel)
        self.act(a, "Height", partial(self.sel_edit, {"op": "match", "what": "height"}), enabled_when=has_sel)
        m.addSeparator()
        self.act(m, "Group", partial(self.sel_edit, {"op": "group"}), "Ctrl+G", enabled_when=has_sel)
        self.act(m, "Ungroup", partial(self.sel_edit, {"op": "ungroup"}), "Ctrl+Shift+G", enabled_when=has_sel)
        self.act(m, "Unlink Label from Panel", self.toggle_label_link, enabled_when=has_sel)
        m.addSeparator()
        self.act(m, "Rotate 90° Left", partial(self.sel_edit, {"op": "rotate", "by": 90}), enabled_when=has_sel)
        self.act(m, "Rotate 90° Right", partial(self.sel_edit, {"op": "rotate", "by": -90}), enabled_when=has_sel)
        self.act(m, "Flip Horizontally", partial(self.sel_edit, {"op": "flip", "axis": "h"}), enabled_when=has_sel)
        self.act(m, "Flip Vertically", partial(self.sel_edit, {"op": "flip", "axis": "v"}), enabled_when=has_sel)
        m.addSeparator()
        self.act(m, "Lock", partial(self.toggle_prop, "locked"), "Ctrl+2", enabled_when=has_sel)
        self.act(m, "Hide", partial(self.toggle_prop, "hidden"), "Ctrl+3", enabled_when=has_sel)
        self.act(m, "Unlock and Show All", self.unlock_all, "Ctrl+Alt+2", enabled_when=has_fig)
        m.addSeparator()
        self.act(m, "Make Layout", partial(self.sel_edit, {"op": "make_layout"}), "Ctrl+L", enabled_when=has_sel)
        self.act(m, "Release Layout", self.release_layout, "Ctrl+Shift+L", enabled_when=has_sel)
        m.addSeparator()
        self.act(m, "Edit Code", self.edit_code, "Ctrl+K", enabled_when=has_sel)
        self.act(m, "Replace Picture…", self.replace_picture, enabled_when=has_sel)
        self.act(m, "Update Panel from Server", self.update_from_server, enabled_when=has_sel)

        m = mb.addMenu("&Labels")
        for i, (name, fmt, case) in enumerate(LABEL_FORMATS):
            self.act(m, f"Style  {name}", partial(self.label_format, fmt, case), enabled_when=has_fig)
        self.act(m, "No Letters", self.labels_none, enabled_when=has_fig)
        m.addSeparator()
        self.journal_menu = m.addMenu("Journal Convention")
        self.journal_menu.aboutToShow.connect(self.fill_journal_menu)
        m.addSeparator()
        self.act(m, "Labels Outside Panels (above left)", self.labels_outside, enabled_when=has_fig)
        self.act(m, "Labels at Panel Corners", lambda: self.edit({"op": "labels", "props": {"dx": 0, "dy": 0}}),
                 enabled_when=has_fig)
        m.addSeparator()
        self.act(m, "Line Up Labels in Rows and Columns", lambda: self.edit(
            {"op": "labels", "props": {"align": not self._ls().get("align", True)}}), enabled_when=has_fig)
        self.act(m, "Letter in Code Order (not Reading Order)", lambda: self.edit(
            {"op": "labels", "props": {"order": "reading" if self._ls().get("order") == "source" else "source"}}),
            enabled_when=has_fig)

        m = mb.addMenu("&View")
        self.act(m, "Zoom In", lambda: self.canvas.zoom(1.25), QKeySequence.StandardKey.ZoomIn)
        self.act(m, "Zoom Out", lambda: self.canvas.zoom(0.8), QKeySequence.StandardKey.ZoomOut)
        self.act(m, "Fit Figure", self.canvas.fit, "Ctrl+0")
        self.act(m, "Actual Size", self.canvas.actual_size, "Ctrl+1")
        m.addSeparator()
        c = self.act(m, "Grid Lines", self.toggle_grid, "Ctrl+'", checkable=True)
        c.setChecked(self.settings.value("showGrid", False, type=bool))
        self.canvas.show_grid = c.isChecked()
        g = m.addMenu("Grid Spacing")
        grp = QActionGroup(self)
        for mm, text in [(0.5, "0.5 mm"), (1, "1 mm"), (2, "2 mm"), (5, "5 mm"), (10, "10 mm"), (12.7, "0.5 in"), (25.4, "1 in")]:
            x = self.act(g, text, partial(self.set_grid_step, mm), checkable=True)
            grp.addAction(x)
            x.setChecked(abs(mm - float(self.settings.value("gridStep", 5.0))) < 1e-6)
        self.canvas.grid_step = float(self.settings.value("gridStep", 5.0))
        c = self.act(m, "Snap to Grid", self.toggle_snap, "Ctrl+Shift+'", checkable=True)
        c.setChecked(self.settings.value("snapToGrid", False, type=bool))
        self.canvas.snap_grid = c.isChecked()
        m.addSeparator()
        c = self.act(m, "Guides", self.toggle_guides, "Ctrl+;", checkable=True)
        c.setChecked(True)
        self.act(m, "Add Vertical Guide", lambda: self.edit({"op": "guide_add", "x": round(self.canvas.size_mm()[0] / 2, 1)}),
                 enabled_when=has_fig)
        self.act(m, "Add Horizontal Guide", lambda: self.edit({"op": "guide_add", "y": round(self.canvas.size_mm()[1] / 2, 1)}),
                 enabled_when=has_fig)
        self.act(m, "Clear Guides", lambda: self.edit({"op": "guide_clear"}), enabled_when=has_fig)
        c = self.act(m, "Smart Guides", self.toggle_smart, "Ctrl+U", checkable=True)
        c.setChecked(self.settings.value("smartGuides", True, type=bool))
        self.canvas.smart = c.isChecked()
        m.addSeparator()
        self.dark_action = self.act(m, "Dark Version of Figure", self.toggle_dark, "Ctrl+Alt+D", checkable=True)
        v = m.addMenu("Colour Vision")
        grp = QActionGroup(self)
        for key, text in [("normal", "Normal"), ("deuteranopia", "Deuteranopia (green-weak, most common)"),
                          ("protanopia", "Protanopia (red-weak)"), ("tritanopia", "Tritanopia (blue-weak)"),
                          ("grayscale", "Greyscale (printed in black and white)")]:
            x = self.act(v, text, partial(self.set_vision, key), checkable=True)
            grp.addAction(x)
            x.setChecked(key == "normal")
        ap = m.addMenu("Appearance")
        grp = QActionGroup(self)
        for key, text in [("system", "System"), ("light", "Light"), ("dark", "Dark")]:
            x = self.act(ap, text, partial(self.set_appearance, key), checkable=True)
            grp.addAction(x)
            x.setChecked(key == (self.settings.value("appearance") or "system"))
        m.addSeparator()
        for key, shortcut in [("code", "Ctrl+Alt+E"), ("preflight", "Ctrl+Alt+P"), ("history", "Ctrl+Alt+Shift+H"),
                              ("caption", "Ctrl+Alt+J"), ("figures", "Ctrl+Alt+1"), ("layers", None),
                              ("inspector", "Ctrl+Alt+I")]:
            a = self.docks[key].toggleViewAction()
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            m.addAction(a)
        m.addSeparator()
        self.act(m, "Render Again", lambda: self.current and self.load(self.current, True), "Ctrl+R", enabled_when=has_fig)

        m = mb.addMenu("&Terminal")
        self.act(m, "Open Terminal in Project", lambda: self.terminal(None), "Ctrl+Alt+T", enabled_when=has_proj)
        for title, cmd in terminal.AGENTS:
            self.act(m, f"Run {title} in Project", partial(self.terminal, cmd), enabled_when=has_proj)
        self.act(m, "Run a Command in Project…", self.terminal_command, enabled_when=has_proj)

        m = mb.addMenu("&Help")
        self.act(m, "User Guide", lambda: locate.open_file("https://pachterlab.github.io/Fignimble/"))
        self.act(m, "Show Log", lambda: locate.open_file(str(locate.log_path())))
        self.act(m, "About Fignimble", self.about)

    def update_actions(self):
        for a, cond in getattr(self, "_conditional", []):
            try:
                a.setEnabled(bool(cond()))
            except Exception:                            # noqa: BLE001
                a.setEnabled(True)

    def _ls(self) -> dict:
        return ((self.figure or {}).get("labelStyle") or {}).get("props") or {}

    def fill_recent(self):
        self.recent_menu.clear()
        rec = self.recent()
        for k in rec:
            if k.startswith("ssh:"):
                host, _, path = k[4:].partition(":")
                a = self.recent_menu.addAction(f"{Path(path).name or 'home'} on {host}")
                a.triggered.connect(partial(self.open_remote, {"host": host, "path": path,
                                                               "python": self.settings.value(f"sshPython/{host}") or "python3"}))
            else:
                a = self.recent_menu.addAction(f"{Path(k).name} — {locate.system.shown(str(Path(k).parent))}")
                a.triggered.connect(partial(self.open_project, k))
        if not rec:
            self.recent_menu.addAction("No Recent Projects").setEnabled(False)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear Menu").triggered.connect(lambda: self.settings.setValue("recent", []))

    def fill_journal_menu(self):
        self.journal_menu.clear()
        for k in sorted(self.journals, key=lambda k: self.journals[k].get("rank", 0)):
            j = self.journals[k]
            a = self.journal_menu.addAction(j.get("name", k))
            a.triggered.connect(partial(self.edit, {"op": "labels", "props": j.get("labels") or {}}))

    # --- actions: files ---

    def open_dialog(self):
        d = QFileDialog.getExistingDirectory(self, "Open a Fignimble project (a folder of figures)",
                                             self.project or str(Path.home()))
        if d:
            self.open_project(d)

    def new_project(self):
        d = QFileDialog.getExistingDirectory(self, "An empty folder for the new project", str(Path.home()))
        if not d:
            return
        keys = sorted(self.journals, key=lambda k: self.journals[k].get("rank", 0))
        names = ["(no journal)"] + [self.journals[k].get("name", k) for k in keys]
        name, ok = QInputDialog.getItem(self, "New Project", "The journal it is for (sizes, fonts, labels):", names, 0, False)
        if not ok:
            return
        args = {"dir": d}
        if names.index(name) > 0:
            args["journal"] = keys[names.index(name) - 1]

        def done(r):
            if r.get("ok") is False:
                self.toast(r.get("error") or "Couldn't make the project", error=True)
                return
            self.open_project(r.get("project") or d, r.get("figure"))
        self.engine.call("new_project", args, done, lambda e: QMessageBox.warning(self, "Couldn't make the project", e))

    def new_figure(self):
        st = ((self.figure or {}).get("style") or {}).get("props") or {}
        d = dialogs.NewFigureDialog(self, self.journals, self.templates, st.get("journal"), f"fig{len(self.figures) + 1}")
        if d.exec():
            req = d.request()

            def done(r):
                if r.get("ok"):
                    self.refresh_state(lambda _s: self.load(r["name"]))
                else:
                    self.toast(r.get("error") or "Couldn't make the figure", error=True)
            self.engine.call("new_figure", req, done, partial(self.toast, error=True))

    def export_figure(self):
        if not self.current:
            return
        st = ((self.figure or {}).get("style") or {}).get("props") or {}
        d = dialogs.ExportDialog(self, self.journals, st.get("journal"), bool(self.figure.get("poster")))
        d.dark.setChecked(self.dark_preview)
        if not d.exec():
            return
        req = d.request()
        start = os.path.join(self.settings.value("exportDir") or self.project or "", f"{self.current}.{req['format']}")
        path, _ = QFileDialog.getSaveFileName(self, "Export Figure", start, f"{req['format'].upper()} (*.{req['format']})")
        if not path:
            return
        self.settings.setValue("exportDir", os.path.dirname(path))
        self.status(f"Exporting {self.current}…")
        self.engine.call("export", {**req, "figure": self.current, "path": path}, self._exported,
                         lambda e: QMessageBox.warning(self, "Export failed", e))

    def _exported(self, r):
        res = r.get("resolution") or {}
        lines = [f"Exported {locate.system.shown(r.get('path', ''))}"]
        if res.get("dpi"):
            lines.append(f"{res['dpi']:.0f} dpi" if isinstance(res["dpi"], (int, float)) else str(res["dpi"]))
        lines += [str(n) for n in res.get("notes") or []] + [str(p) for p in res.get("problems") or []]
        lines += [w.get("message", "") for w in r.get("warnings") or [] if w.get("level") != "info"][:8]
        if res.get("ok") is False or len(lines) > 2:
            QMessageBox.information(self, "Export", "\n".join(lines))
        else:
            self.toast(lines[0])

    def export_all(self):
        d = QFileDialog.getExistingDirectory(self, "Folder for the PDFs", self.settings.value("exportDir") or self.project)
        if not d:
            return
        for f in self.figures:
            self.engine.call("export", {"figure": f["name"], "path": os.path.join(d, f["name"] + ".pdf"), "format": "pdf"},
                             lambda r: self.toast(f"Exported {Path(r.get('path', '')).name}"), partial(self.toast, error=True))

    def export_table(self):
        sel = [self.canvas.by_oid[o] for o in self.canvas.selection if self.canvas.by_oid.get(o, {}).get("kind") == "table"]
        if not sel:
            self.toast("Select a table first", error=True)
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Table as Word", os.path.join(self.project or "", "table.docx"),
                                              "Word (*.docx)")
        if path:
            self.engine.call("export_table", {"figure": self.current, "oid": sel[0]["oid"], "path": path},
                             lambda r: self.toast(f"Exported {Path(path).name}") if r.get("ok") else
                             self.toast(r.get("error"), error=True), partial(self.toast, error=True))

    def check_packages(self, asked: bool):
        def got(r):
            rows = (r.get("engine") or []) + (r.get("project") or [])
            reqs = [x.get("requirement") for x in rows if x.get("requirement")]
            if not reqs:
                if asked:
                    self.toast("Everything Fignimble and this project need is installed")
                return
            key = (self.project or "") + "|" + ",".join(reqs)
            seen = self.settings.value("packagesOffered") or []
            if isinstance(seen, str):
                seen = [seen]
            if not asked and key in seen:
                return
            self.settings.setValue("packagesOffered", (list(seen) + [key])[-50:])
            listing = "\n".join(f"• {x['requirement']}" + ("" if x.get("why") == "not installed" else f" ({x.get('why')})")
                                for x in rows)
            if QMessageBox.question(self, "Install the packages this needs?",
                                    f"Packages that {locate.system.shown(r.get('python', 'its Python'))} doesn't have:\n\n"
                                    f"{listing}\n\nInstall them with pip?") != QMessageBox.StandardButton.Yes:
                return
            self.status(f"Installing {len(reqs)} package(s)…")

            def installed(res):
                if res.get("ok"):
                    self.toast("Installed " + ", ".join(reqs))
                    self.refresh()
                else:
                    QMessageBox.warning(self, "The packages couldn't be installed", res.get("output") or "")
            self.engine.call("install", {"requirements": reqs}, installed, partial(self.toast, error=True))
        self.engine.call("requirements", {}, got)

    def install_cli(self):
        b = locate.bin_dir()
        if not b:
            QMessageBox.information(self, "The fignimble command", "The fignimble command comes with pip install: "
                                    "it is in the Scripts (Windows) or bin folder of the Python that runs Fignimble.")
            return
        if locate.WINDOWS:
            QMessageBox.information(self, "The fignimble command",
                                    f"install_fignimble.ps1 puts it on your PATH. Or add this folder to it:\n\n{b}\n\n"
                                    "(bin\\fignimble.cmd is the command.)")
            return
        dest = Path.home() / ".local" / "bin" / "fignimble"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(Path(b) / "fignimble")
        except OSError as exc:
            QMessageBox.warning(self, "Couldn't install the command", str(exc))
            return
        on_path = str(dest.parent) in os.environ.get("PATH", "").split(os.pathsep)
        QMessageBox.information(self, "The fignimble command", f"Installed {locate.system.shown(dest)}." + (
            "" if on_path else "\n\n~/.local/bin isn't on your PATH yet: add\n\n    export PATH=\"$HOME/.local/bin:$PATH\""
                               "\n\nto ~/.bashrc (or your shell's startup file)."))

    # --- actions: editing ---

    def sel_elements(self) -> list[str]:
        return [o for o in self.canvas.selection if not o.startswith("L")]

    def sel_edit(self, args: dict):
        if self.canvas.selection:
            self.edit({**args, "oids": list(self.canvas.selection)})

    def align(self, how: str):
        to = "figure" if self.align_figure.isChecked() or len(self.canvas.selection) == 1 else "selection"
        self.sel_edit({"op": "align", "how": how, "to": to})

    def delete_selected(self):
        oids = list(self.canvas.selection)
        if oids:
            self.canvas.set_selection([])
            self.edit({"op": "delete", "oids": oids})

    def select_all(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "selectAll"):
                w.selectAll()
            return
        self.canvas.set_selection([e["oid"] for e in self.canvas.elements if not e.get("parent") and not e.get("locked")
                                   and not e.get("hidden")])

    def toggle_prop(self, key: str):
        for o in self.sel_elements():
            e = self.canvas.by_oid.get(o) or {}
            self.edit({"op": "set", "oid": o, "props": {key: not e.get(key)}})

    def unlock_all(self):
        for e in self.canvas.elements:
            if e.get("locked") or e.get("hidden"):
                self.edit({"op": "set", "oid": e["oid"], "props": {"locked": False, "hidden": False}})

    def toggle_label_link(self):
        """Unlink a panel's label from it (or link it again): the label selected, or the selected panel's."""
        for o in self.canvas.selection:
            lab = self.canvas.by_oid.get(o) if o.startswith("L") else \
                next((x for x in self.canvas.labels if x.get("panel") == o), None)
            if lab:
                self.edit({"op": "label_detach", "oid": lab["oid"], "detach": not lab.get("detached")})
                return
        self.toast("Select a panel or its label", error=True)

    def release_layout(self):
        for o in self.sel_elements():
            e = self.canvas.by_oid.get(o) or {}
            lay = e if e.get("kind") == "layout" else self.canvas.by_oid.get(e.get("parent") or "")
            if lay:
                self.edit({"op": "release_layout", "oid": lay["oid"]})
                return

    def replace_picture(self):
        oids = self.sel_elements()
        if oids:
            path, _ = QFileDialog.getOpenFileName(self, "The picture to show in its place", self.project or "", IMAGE_FILTER)
            if path:
                self.edit({"op": "replace_source", "oid": oids[0], "path": path})

    def update_from_server(self):
        e = self.canvas.by_oid.get((self.sel_elements() or [""])[0]) or {}
        f = (e.get("code") or {}).get("file")
        if not f:
            self.toast("Select a panel fetched from a server", error=True)
            return
        self.engine.call("update_remote", {"file": f, "figure": self.current, "pxPerMM": self.last_px},
                         lambda r: (self._pasted(r), r.get("ok") and self.toast(f"Updated {Path(f).name}")),
                         partial(self.toast, error=True))

    def context_menu(self, at):
        """Right-click on the canvas: what applies to what is selected."""
        from PySide6.QtWidgets import QMenu
        sel = [self.canvas.by_oid[o] for o in self.canvas.selection if o in self.canvas.by_oid]
        if not sel:
            return
        e = sel[0]
        m = QMenu(self)
        if e.get("code") or e.get("site"):
            m.addAction("Edit Code", self.edit_code)
        if e.get("kind") in ("image", "inset"):
            m.addAction("Replace…", self.replace_picture)
        code = (e.get("code") or {}).get("file")
        if code and os.path.exists(code) and Path(code).read_text(encoding="utf-8", errors="replace").startswith("# Fetched from"):
            m.addAction("Update from Server", self.update_from_server)
        if e.get("kind") in ("panel", "label"):
            m.addAction("Unlink Label from Panel" if not e.get("detached") else "Link Label to Panel", self.toggle_label_link)
        m.addSeparator()
        m.addAction("Bring to Front", partial(self.sel_edit, {"op": "order", "how": "front"}))
        m.addAction("Send to Back", partial(self.sel_edit, {"op": "order", "how": "back"}))
        m.addAction("Duplicate", partial(self.sel_edit, {"op": "duplicate", "dx": 4, "dy": 4}))
        m.addAction("Unlock" if e.get("locked") else "Lock", partial(self.toggle_prop, "locked"))
        m.addAction("Show" if e.get("hidden") else "Hide", partial(self.toggle_prop, "hidden"))
        m.addSeparator()
        m.addAction("Delete", self.delete_selected)
        m.exec(at)

    def edit_code(self):
        if len(self.canvas.selection) == 1:
            e = self.canvas.by_oid.get(self.canvas.selection[0]) or {}
            where = e.get("code") or e.get("site")
            if where:
                self.open_code(where.get("file"), where.get("line"))

    def open_code(self, file, line=None):
        if file:
            self.docks["code"].show()
            self.docks["code"].raise_()
            self.code.open(file, line)

    def label_format(self, fmt, case):
        props = {"format": fmt, "case": case}
        if self._ls().get("numbering") == "none":
            props["numbering"] = "alpha"
        self.edit({"op": "labels", "props": props})

    def labels_none(self):
        self.edit({"op": "labels", "props": {"numbering": "alpha" if self._ls().get("numbering") == "none" else "none"}})

    def labels_outside(self):
        size = float(self._ls().get("size") or 8)
        self.edit({"op": "labels", "props": {"dx": -round(size * 0.35 + 0.5, 2), "dy": -round(size * 0.35 + 0.5, 2)}})

    def pick_tool(self, t):
        self.canvas.set_tool(t)
        self.canvas.setFocus()

    # --- clipboard ---

    def copy(self, then=None):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "copy"):
                w.copy()
            return
        oids = self.sel_elements()
        if not oids or not self.current:
            return
        name = self.current

        def got(r):
            code = r.get("code") or []
            md = QMimeData()
            md.setData(PASTE_TYPE, json.dumps({"figure": name, "project": self.project, "code": code}).encode("utf-8"))
            md.setText("\n".join(code).replace("§FIG§.", "fig."))
            QGuiApplication.clipboard().setMimeData(md)
            if then:
                then()
        self.engine.call("copy", {"figure": name, "oids": oids}, got, partial(self.toast, error=True))

    def cut(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "cut"):
                w.cut()
            return
        oids = list(self.canvas.selection)
        self.copy(then=lambda: (self.canvas.set_selection([]), self.edit({"op": "delete", "oids": oids})))

    def paste(self):
        if self._typing():
            w = QApplication.focusWidget()
            if hasattr(w, "paste"):
                w.paste()
            return
        if not self.current:
            return
        md = QGuiApplication.clipboard().mimeData()
        if md.hasFormat(PASTE_TYPE):
            data = json.loads(bytes(md.data(PASTE_TYPE)).decode("utf-8"))
            same = data.get("figure") == self.current and data.get("project") == self.project
            self.engine.call("paste", {"figure": self.current, "code": data.get("code") or [], "dx": 4 if same else 0,
                                       "dy": 4 if same else 0, "pxPerMM": self.last_px}, self._pasted,
                             partial(self.toast, error=True))
        elif md.hasImage():
            img = QImage(md.imageData())
            tmp = os.path.join(tempfile.mkdtemp(prefix="fignimble-paste-"), "pasted.png")
            img.save(tmp, "PNG")
            w, h = self.canvas.size_mm()
            self.engine.call("paste_image", {"figure": self.current, "path": tmp, "x": round(w / 4, 1), "y": round(h / 4, 1),
                                             "pxPerMM": self.last_px}, self._pasted, partial(self.toast, error=True))
        elif md.hasUrls():
            w, h = self.canvas.size_mm()
            self.import_files([u.toLocalFile() for u in md.urls() if u.isLocalFile()], round(w / 4, 1), round(h / 4, 1))

    def _pasted(self, r):
        if r.get("figure"):
            self.apply(r["figure"])
        if r.get("select"):
            self.canvas.set_selection(list(r["select"]))
        if r.get("ok") is False:
            self.toast(r.get("error") or "Couldn't paste", error=True)
        self.code.reload_if_changed()
        self.refresh_state()

    def copy_figure(self):
        if not self.current:
            return
        name, dark = self.current, self.dark_preview
        d = tempfile.mkdtemp(prefix="fignimble-copy-")
        pdf, png = os.path.join(d, name + ".pdf"), os.path.join(d, name + ".png")

        def put(_r):
            md = QMimeData()
            img = QImage(png)
            if not img.isNull():
                md.setImageData(img)
            md.setUrls([QUrl.fromLocalFile(pdf)])
            try:
                md.setData("application/pdf", Path(pdf).read_bytes())
            except OSError:
                pass
            QGuiApplication.clipboard().setMimeData(md)
            self.toast(f"Copied {name}{' (dark version)' if dark else ''} as a picture")
        self.engine.call("export", {"figure": name, "path": pdf, "format": "pdf", "dpi": 600, "dark": dark}, None,
                         partial(self.toast, error=True))
        self.engine.call("export", {"figure": name, "path": png, "format": "png", "dpi": 600, "dark": dark}, put,
                         partial(self.toast, error=True))

    def copy_style(self):
        oids = self.sel_elements()
        if oids:
            e = self.canvas.by_oid.get(oids[0]) or {}
            self._style = {"kind": e.get("kind"), "props": e.get("props") or {}}
            self.toast("Copied the style")

    def paste_style(self):
        st = getattr(self, "_style", None)
        if st and self.sel_elements():
            self.edit({"op": "paste_style", "oids": self.sel_elements(), "props": st["props"]})

    # --- inserting ---

    def _centre(self, w=60.0, h=45.0):
        fw, fh = self.canvas.size_mm()
        return round(max(0, fw / 2 - w / 2), 1), round(max(0, fh / 2 - h / 2), 1)

    def import_files(self, paths, x, y):
        for p in paths:
            self.engine.call("import_file", {"figure": self.current, "path": p, "x": x, "y": y, "pxPerMM": self.last_px},
                             self._pasted, partial(self.toast, error=True))
            x, y = x + 5, y + 5

    def new_panel(self):
        name, ok = QInputDialog.getText(self, "New panel", "Makes panels/<name>.py with a draw(fig) function to edit (or to "
                                        "ask an agent to write), and places it on the figure.\n\nName:", text="panel")
        if not ok:
            return
        x, y = self._centre()

        def got(r):
            self._pasted(r)
            if r.get("file"):
                self.refresh_state(lambda _s: self.open_code(r["file"], 1))
        self.engine.call("new_panel", {"figure": self.current, "name": name.strip().replace(" ", "_") or "panel",
                                       "x": x, "y": y, "w": 60, "h": 45, "pxPerMM": self.last_px}, got,
                         partial(self.toast, error=True))

    def insert_panel_code(self):
        start = os.path.join(self.project or "", "panels")
        path, _ = QFileDialog.getOpenFileName(self, "A Python file with a draw(fig) function", start, "Python (*.py)")
        if path:
            self.import_files([path], *self._centre())

    def insert_image(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Pictures to place", self.project or "", IMAGE_FILTER)
        if paths:
            self.import_files(paths, *self._centre(50, 40))

    def insert_figure(self):
        others = [f["name"] for f in self.figures if f["name"] != self.current]
        if not others:
            self.toast("The project has no other figure to place: File ▸ New Figure makes one", error=True)
            return
        name, ok = QInputDialog.getItem(self, "Place a figure", "The figure is drawn from its code every time, so it "
                                        "stays as that figure is.", others, 0, False)
        if ok:
            m = float(((self.figure or {}).get("poster") or {}).get("margin") or 5) if isinstance(
                (self.figure or {}).get("poster"), dict) else 5.0
            self.import_files([os.path.join(self.project, name + ".py")], max(5, m), max(5, m))

    def insert_qr(self):
        t, ok = QInputDialog.getText(self, "QR Code", "Link for the QR code:", text="https://")
        if ok and t.strip():
            fw, fh = self.canvas.size_mm()
            poster = isinstance((self.figure or {}).get("poster"), dict)
            w = 60.0 if poster else 25.0
            m = max(float(((self.figure or {}).get("poster") or {}).get("margin") or 0) if poster else 0, 5)
            self.edit({"op": "insert", "kind": "qr", "props": {"data": t.strip(), "x": max(0, round(fw - w - m)),
                                                               "y": max(0, round(fh - w - m)), "w": w}})

    def new_table(self):
        name, ok = QInputDialog.getText(self, "New table", "A table in a figure of its own. Name:",
                                        text=f"table{len([f for f in self.figures if f['name'].startswith('table')]) + 1}")
        if not ok:
            return
        rows, ok = QInputDialog.getInt(self, "New table", "Rows (with the header):", 4, 1, 200)
        if not ok:
            return
        cols, ok = QInputDialog.getInt(self, "New table", "Columns:", 3, 1, 50)
        if not ok:
            return

        def done(r):
            if r.get("name"):
                self.refresh_state(lambda _s: self.load(r["name"]))
            else:
                self.toast(r.get("error") or "Couldn't make the table", error=True)
        self.engine.call("new_table", {"name": name, "rows": rows, "cols": cols}, done, partial(self.toast, error=True))

    def insert_table(self):
        path, _ = QFileDialog.getOpenFileName(self, "A table", self.project or "", "Tables (*.csv *.tsv)")
        if path:
            self.import_files([path], *self._centre(80, 30))

    def add_scalebar(self):
        oids = self.sel_elements()
        if not oids:
            return
        length, ok = QInputDialog.getDouble(self, "Scale bar", "Length (in the unit below):", 50, 0.001, 1e6, 3)
        if not ok:
            return
        unit, ok = QInputDialog.getText(self, "Scale bar", "Unit:", text="µm")
        if not ok:
            return
        px, ok = QInputDialog.getDouble(self, "Scale bar", f"Size of one pixel of the picture ({unit}):", 1.0, 1e-6, 1e6, 6)
        if ok:
            self.edit({"op": "add_scalebar", "oid": oids[0], "props": {"length": length, "unit": unit, "pixel_size": px}})

    def insert_remote_panel(self):
        def hosts(r):
            d = dialogs.RemoteDialog(self, r.get("hosts") or [], "Panel from SSH Server", ask_python=False)
            if not d.exec():
                return
            req = d.request()

            def listed(res):
                if not res.get("ok"):
                    self.toast(res.get("error") or "Couldn't list the folder", error=True)
                    return
                files = [e["name"] for e in res.get("entries") or [] if not e.get("dir")]
                if not files:
                    self.toast(f"No .py files in {res.get('path')} on {req['host']}", error=True)
                    return
                name, ok = QInputDialog.getItem(self, "Panel from SSH Server", f"{req['host']}:{res.get('path')}", files, 0, False)
                if ok:
                    x, y = self._centre()
                    path = res.get("path", "").rstrip("/") + "/" + name
                    self.engine.call("import_remote", {"figure": self.current, "host": req["host"], "path": path,
                                                       "x": x, "y": y, "pxPerMM": self.last_px}, self._pasted,
                                     partial(self.toast, error=True))
            self.engine.call("remote_list", {"host": req["host"], "path": req["path"]}, listed, partial(self.toast, error=True))
        self.engine.call("remote_hosts", {}, hosts, partial(self.toast, error=True))

    def open_remote_dialog(self):
        if not self.python:
            return
        if locate.WINDOWS:
            self.open_remote({"host": "", "path": ""})
            return
        try:
            from fignimble import remote
            hosts = remote.hosts()
        except Exception:                                # noqa: BLE001
            hosts = []
        d = dialogs.RemoteDialog(self, hosts, "Open Project on SSH Server", ask_python=True)
        if d.exec():
            r = d.request()
            if r["host"]:
                self.open_remote(r)

    # --- view ---

    def toggle_grid(self, on):
        self.canvas.show_grid = on
        self.settings.setValue("showGrid", on)
        self.canvas.update()

    def set_grid_step(self, mm, _on=True):
        self.canvas.grid_step = mm
        self.settings.setValue("gridStep", mm)
        self.canvas.update()

    def toggle_snap(self, on):
        self.canvas.snap_grid = on
        self.settings.setValue("snapToGrid", on)

    def toggle_guides(self, on):
        self.canvas.show_guides = on
        self.canvas.update()

    def toggle_smart(self, on):
        self.canvas.smart = on
        self.settings.setValue("smartGuides", on)

    def toggle_dark(self, on):
        self.dark_preview = on
        if self.current:
            self.load(self.current)

    def set_vision(self, key, _on=True):
        self.canvas.vision = key
        self.canvas.update()

    def set_appearance(self, key, _on=True):
        self.settings.setValue("appearance", key)
        apply_appearance(QApplication.instance(), key)

    def terminal(self, command):
        if self.project:
            why = terminal.open_terminal(self.project, command)
            if why:
                QMessageBox.warning(self, "Couldn't open a terminal", why)

    def terminal_command(self):
        cmd, ok = QInputDialog.getText(self, "Run in Project", "Command (the fignimble command is on its PATH):",
                                       text=self.settings.value("lastCommand") or "claude")
        if ok and cmd.strip():
            self.settings.setValue("lastCommand", cmd.strip())
            self.terminal(cmd.strip())

    def about(self):
        QMessageBox.about(self, "About Fignimble",
                          f"<b>Fignimble {version()}</b><br>Multi-panel figures for publication, assembled from Python."
                          "<br><br>License: BSD-2<br>Developed by Joseph Rich and Lior Pachter. 2026"
                          f"<br><br>Engine Python: {self.python or '—'}")

    # --- the window ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths and (os.path.isdir(paths[0]) or not self.current):
            self.open_project(paths[0])

    def closeEvent(self, e):
        if self.code.dirty:
            r = QMessageBox.question(self, "Apply the code changes?", "The code pane has changes that aren't applied yet.",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if r == QMessageBox.StandardButton.Save:
                self.code.save()
        self.drain()                                     # what is still on its way to the engine (that save) arrives
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        if self.engine.running():
            self.engine.call("close", {})
        self.engine.stop(wait_ms=15000 if self.remote else 3000)
        super().closeEvent(e)


def cache_dir() -> str:
    """Where the mirrors of projects on servers go: ~/.cache/pachterlab/Fignimble on Linux."""
    from PySide6.QtCore import QStandardPaths
    p = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation) or \
        os.path.join(tempfile.gettempdir(), "fignimble")
    os.makedirs(p, exist_ok=True)
    return p


def apply_appearance(app: QApplication, key: str):
    """System, light or dark: Qt's own colour scheme where it has one (Qt 6.8), else the Fusion style's palette."""
    from PySide6.QtGui import QPalette, QColor
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):
        scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(key, Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)
        return
    if key == "dark":
        app.setStyle(QStyleFactory.create("Fusion"))
        p = QPalette()
        for role, c in [(QPalette.ColorRole.Window, "#2b2b2e"), (QPalette.ColorRole.WindowText, "#e8e8ea"),
                        (QPalette.ColorRole.Base, "#1f1f22"), (QPalette.ColorRole.AlternateBase, "#2b2b2e"),
                        (QPalette.ColorRole.Text, "#e8e8ea"), (QPalette.ColorRole.Button, "#3a3a3e"),
                        (QPalette.ColorRole.ButtonText, "#e8e8ea"), (QPalette.ColorRole.Highlight, "#2f7cf6"),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#3a3a3e"),
                        (QPalette.ColorRole.ToolTipText, "#e8e8ea"), (QPalette.ColorRole.Mid, "#3a3a3e")]:
            p.setColor(role, QColor(c))
        app.setPalette(p)
    else:
        app.setPalette(app.style().standardPalette())

