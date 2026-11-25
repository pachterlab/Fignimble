"""The Windows and Linux editor (apps/desktop) driving the real engine, without a screen (QT_QPA_PLATFORM=offscreen):
it opens a project, shows a figure, moves a panel, undoes, edits through the inspector's operations, switches
figures and copies elements. Skipped where PySide6 isn't installed."""

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "desktop"))

from PySide6.QtCore import QEventLoop, QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def window(app, tmp_path, monkeypatch):
    monkeypatch.setenv("FIGNIMBLE_PYTHON", sys.executable)
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    from fignimble_app.window import MainWindow
    d = tmp_path / "demo"
    shutil.copytree(ROOT / "tests" / "fixtures" / "demo", d, ignore=shutil.ignore_patterns(".fignimble", "__pycache__"))
    w = MainWindow()
    w.resize(1200, 800)
    w.show()
    w.start(str(d))
    assert settle(app, w, lambda: w.figure is not None), "the figure never rendered"
    yield w
    dispose(app, w)


def dispose(app, w):
    """Close the window and delete it now, not when Python shuts down (after QApplication, which crashes Qt)."""
    from PySide6.QtCore import QEvent
    w.close()
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def settle(app, w, cond=lambda: True, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        if w.engine.in_flight is None and not w.engine.queue and cond():
            return True
        time.sleep(0.02)
    return False


def box(w, oid):
    return w.canvas.by_oid[oid]["box"]


def test_open_move_undo(app, window):
    w = window
    fig = Path(w.figure["file"])
    assert w.current == "fig1" and [f["name"] for f in w.figures][:1] == ["fig1"]
    assert all(Path(e["sprite"]["file"]).exists() for e in w.figure["elements"] if e.get("sprite"))
    before = fig.read_bytes()
    w.canvas.set_selection(["1"])
    w.edit({"op": "move", "oids": ["1"], "dx": 3, "dy": -1.5})
    assert settle(app, w)
    assert box(w, "1")[:2] == [68, 1.5]
    assert b"x=68, y=1.5" in fig.read_bytes()
    w.undo()
    assert settle(app, w)
    assert fig.read_bytes() == before
    assert box(w, "1")[:2] == [65, 3]


def test_drag_on_the_canvas(app, window):
    """A real drag: press on a panel, move 8 mm right, release."""
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    w, c = window, window.canvas
    x, y, bw, bh = box(w, "2")
    start = c.to_view(QPointF(x + bw / 2, y + bh / 2))
    end = c.to_view(QPointF(x + bw / 2 + 8, y + bh / 2))

    def send(kind, at, buttons, mods=Qt.KeyboardModifier.NoModifier):
        QApplication.sendEvent(c, QMouseEvent(kind, at, at, Qt.MouseButton.LeftButton, buttons, mods))
    nosnap = Qt.KeyboardModifier.ControlModifier                    # as while dragging with Ctrl held
    send(QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton)
    send(QEvent.Type.MouseMove, (start + end) / 2, Qt.MouseButton.LeftButton, nosnap)
    send(QEvent.Type.MouseMove, end, Qt.MouseButton.LeftButton, nosnap)
    send(QEvent.Type.MouseButtonRelease, end, Qt.MouseButton.NoButton)
    assert settle(app, w)
    assert abs(box(w, "2")[0] - (x + 8)) < 0.6, box(w, "2")


def test_inspector_edits_and_figures(app, window):
    w = window
    w.canvas.set_selection([])
    w.edit({"op": "figure", "props": {"width": 180}})
    assert settle(app, w)
    assert w.figure["size"][0] == 180
    w.inspector.update_view()
    w.edit({"op": "insert", "kind": "text", "props": {"text": "Hello", "x": 10, "y": 110}})
    assert settle(app, w)
    assert any((e.get("props") or {}).get("text") == "Hello" for e in w.figure["elements"])
    new = w.canvas.selection
    assert new, "the inserted text should be selected"
    w.inspector.update_view()
    w.edit({"op": "set", "oid": new[0], "props": {"size": 9}})
    assert settle(app, w)
    w.show_figure("fig2")
    assert settle(app, w, lambda: w.current == "fig2")
    assert any(e.get("kind") == "layout" for e in w.figure["elements"])
    w.copy()
    assert settle(app, w)


def test_code_pane_applies(app, window):
    w = window
    fig = w.figure["file"]
    w.code.open(fig)
    text = Path(fig).read_text(encoding="utf-8").replace("x=65, y=3", "x=66, y=3")
    w.code.edit.setPlainText(text)
    assert w.code.dirty
    w.code.save()
    assert settle(app, w)
    assert box(w, "1")[:2] == [66, 3]
    assert not w.code.dirty


@pytest.mark.skipif(os.name == "nt", reason="the ssh stand-in is a bash script; SSH projects need macOS or Linux")
def test_project_on_a_server(app, tmp_path, monkeypatch):
    """File ▸ Open Project on SSH Server: the tunnel starts the engine "there" (a folder reached through a stand-in for
    ssh, as in test_tunnel.py), and an edit in the editor reaches the server's file."""
    home = tmp_path / "server"
    shutil.copytree(ROOT / "tests" / "fixtures" / "demo", home / "proj",
                    ignore=shutil.ignore_patterns(".fignimble", "__pycache__"))
    fake = tmp_path / "fake_ssh"
    fake.write_text(f'#!/bin/bash\nwhile [ "$1" != "--" ]; do shift; done; shift\nexport HOME="{home}"\n'
                    'cd "$HOME"\nexec /bin/bash -c "$*"\n')
    fake.chmod(0o755)
    monkeypatch.setenv("FIGNIMBLE_SSH", str(fake))
    monkeypatch.setenv("FIGNIMBLE_PYTHON", sys.executable)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    from PySide6.QtCore import QSettings
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    from fignimble_app.window import MainWindow
    w = MainWindow()
    w.show()
    w.start(None)
    assert settle(app, w)
    w.open_remote({"host": "lab", "path": "~/proj", "python": sys.executable})
    try:
        assert settle(app, w, lambda: w.figure is not None and w.remote is not None), "the server's figure never came"
        w.edit({"op": "move", "oids": ["1"], "dx": 3, "dy": -1.5})
        assert settle(app, w)
        end = time.time() + 30
        while time.time() < end and b"x=68, y=1.5" not in (home / "proj" / "fig1.py").read_bytes():
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
            time.sleep(0.1)
        assert b"x=68, y=1.5" in (home / "proj" / "fig1.py").read_bytes()
    finally:
        dispose(app, w)


def test_every_menu_command(app, window, monkeypatch):
    """Every menu command, with something selected, dialogs answered with Cancel: none raises, the engine keeps
    running, and the canvas still paints (every colour-vision mode, the dark version, a path's points, typing)."""
    from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox
    from fignimble_app import locate, terminal
    w = window
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append(f"{t.__name__}: {v}"))
    for name, value in [("getOpenFileName", ("", "")), ("getOpenFileNames", ([], "")), ("getSaveFileName", ("", "")),
                        ("getExistingDirectory", "")]:
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *a, _v=value, **k: _v))
    for name, value in [("getText", ("", False)), ("getItem", ("", False)), ("getInt", (0, False)),
                        ("getDouble", (0.0, False))]:
        monkeypatch.setattr(QInputDialog, name, staticmethod(lambda *a, _v=value, **k: _v))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    for name in ("information", "warning", "critical", "about"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QMenu, "exec", lambda self, *a: None)
    monkeypatch.setattr(terminal, "open_terminal", lambda *a, **k: None)
    monkeypatch.setattr(locate, "open_file", lambda *a: None)
    monkeypatch.setattr(locate, "reveal", lambda *a: None)
    skip = {"Quit", "Install Command Line Tool…", "Choose Python for Figures…"}

    def actions(menu):
        for a in menu.actions():
            if a.menu():
                yield from actions(a.menu())
            elif not a.isSeparator() and a.text().split("\t")[0] not in skip:
                yield a
    done = []
    for top in w.menuBar().actions():
        top.menu().aboutToShow.emit()                    # menus filled when they open (recent, journals)
        for a in list(actions(top.menu())):
            if not w.canvas.selection and w.figure:
                panels = [e["oid"] for e in w.figure["elements"] if e.get("kind") == "panel"]
                w.canvas.set_selection(panels[:2])
            w.update_actions()
            a.trigger()
            assert settle(app, w), f"{a.text()} left the engine busy"
            w.canvas.grab()
            done.append(a.text())
    assert len(done) > 80, done
    assert not errors, errors
    assert w.engine.running()

    # a path drawn with the pen, one of its points dragged, then a text typed in place
    w.canvas.set_tool("pen")
    for x, y in [(20, 20), (40, 30), (60, 20)]:
        w.canvas.pen.append(QPointF(x, y))
    w.canvas.finish_pen(closed=False)
    assert settle(app, w)
    path = next(e for e in w.figure["elements"] if e.get("kind") == "path")
    w.canvas.set_selection([path["oid"]])
    w.canvas.drag, w.canvas.moved, w.canvas.live_value = ("point", path["oid"], 1), True, QPointF(42, 35)
    w.canvas.grab()
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    at = w.canvas.to_view(QPointF(42, 35))
    w.canvas.mouseReleaseEvent(QMouseEvent(QEvent.Type.MouseButtonRelease, at, at, Qt.MouseButton.LeftButton,
                                           Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
    assert settle(app, w)
    path = next(e for e in w.figure["elements"] if e.get("kind") == "path")
    assert [42, 35] in [list(map(float, q)) for q in path["props"]["points"]]
    w.canvas.set_tool("text")
    w.canvas.open_editor(None, QPointF(30, 100), "", 7)
    w.canvas.editor.setText("typed here")
    w.canvas.close_editor(commit=True)
    assert settle(app, w)
    assert any((e.get("props") or {}).get("text") == "typed here" for e in w.figure["elements"])
    assert not errors, errors
    from PySide6.QtGui import QGuiApplication
    QGuiApplication.clipboard().clear()          # what Copy put there, made from Python, crashes Qt at exit otherwise


def test_new_table_and_its_cells(app, window, monkeypatch):
    """Insert ▸ New Table makes a figure of its own; the inspector's cells write its CSV file."""
    from PySide6.QtWidgets import QInputDialog
    w = window
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("table1", True)))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (3, True)))
    w.new_table()
    assert settle(app, w, lambda: w.current == "table1")
    t = next(e for e in w.figure["elements"] if e["kind"] == "table")
    w.canvas.set_selection([t["oid"]])
    rows = [["gene", "log2FC", "p"], ["GAPDH", "0.1", "0.9"], ["CD3E", "2.3", "0.001"]]
    w.edit({"op": "table_rows", "oid": t["oid"], "rows": rows, "then": {"op": "fit_figure", "margin": 3}})
    assert settle(app, w)
    csv = (Path(w.project) / "tables" / "table1.csv").read_text(encoding="utf-8")
    assert "GAPDH" in csv and "CD3E" in csv
    w.inspector.update_view()
