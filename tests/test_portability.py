"""What has to hold on macOS, Linux and Windows alike: what undo keeps is what is on disk (Windows would write
"\\r\\n"), the per-system places and programs (fignimble.system), paths between a Windows mirror and a POSIX server,
and the `fignimble` command's launcher. CI runs these on all three (.github/workflows/tests.yml)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from fignimble import system, tunnel  # noqa: E402
from fignimble.core import Session  # noqa: E402


@pytest.fixture()
def session(tmp_path):
    d = tmp_path / "demo"
    shutil.copytree(ROOT / "tests" / "fixtures" / "demo", d, ignore=shutil.ignore_patterns(".fignimble", "__pycache__"))
    s = Session(str(d), sys.executable)
    yield s
    s.close()


def test_undo_keeps_what_is_on_disk(session):
    fig = session.project / "fig1.py"
    before = fig.read_bytes()
    r = session.edit({"figure": "fig1", "op": "move", "oids": ["1"], "dx": 3, "dy": 0})
    assert r["ok"], r.get("error")
    kept = session.history[-1]["files"][str(fig)]
    assert kept[1] == fig.read_bytes(), "the file on disk isn't what undo kept (line endings?)"
    assert (b"\r\n" in fig.read_bytes()) == (b"\r\n" in before), "the edit changed the file's line endings"
    assert session.undo({"figure": "fig1"})["ok"]
    assert fig.read_bytes().replace(b"\r\n", b"\n") == before.replace(b"\r\n", b"\n")
    assert session.redo_({"figure": "fig1"})["ok"]


def test_crlf_files_keep_their_line_endings(session):
    """A Windows checkout has CRLF files: an edit keeps them CRLF, and undo matches what is on disk."""
    fig = session.project / "fig1.py"
    fig.write_bytes(fig.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    before = fig.read_bytes()
    assert session.edit({"figure": "fig1", "op": "move", "oids": ["0"], "dx": 1, "dy": 0})["ok"]
    data = fig.read_bytes()
    assert b"\r\n" in data and b"\n" not in data.replace(b"\r\n", b"")
    assert session.history[-1]["files"][str(fig)][1] == data
    assert session.undo({"figure": "fig1"})["ok"]
    assert fig.read_bytes() == before


def test_undo_after_an_editor_saved_with_other_line_endings(session):
    """Between an edit and its undo, an editor (or an agent) changed another line and saved the file with CRLF:
    undo takes back only the edit, and the file keeps the agent's line and its line endings."""
    fig = session.project / "fig1.py"
    assert session.edit({"figure": "fig1", "op": "move", "oids": ["0"], "dx": 1, "dy": 0})["ok"]
    text = fig.read_text(encoding="utf-8").replace("fig.arrow(57, 82, 64, 82", "fig.arrow(55, 82, 64, 82")
    fig.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    r = session.undo({"figure": "fig1"})
    assert r["ok"], r.get("error")
    t = fig.read_bytes().decode("utf-8")
    assert 'fig.panel("panels/expression.py", x=4, y=3' in t and "fig.arrow(55, 82" in t and "\r\n" in t


def test_new_figure_and_undo(session):
    r = session.new_figure({"name": "extra", "width": 89, "height": 60})
    assert r["ok"]
    path = session.project / "extra.py"
    assert session.history[-1]["files"][str(path)][1] == path.read_bytes()
    assert session.undo({})["ok"]
    assert not path.exists()


def test_system_places():
    for p in (system.support_dir(), system.log_path(), system.support_python()):
        assert isinstance(p, Path) and p.is_absolute()
    assert str(system.support_python()).endswith("python.exe" if os.name == "nt" else "python")
    assert system.ghostscript_hint()
    gs = system.ghostscript()
    assert gs is None or Path(gs).exists()
    prof = system.cmyk_profile()
    assert prof is None or prof.is_file()
    assert system.shown(Path.home() / "x").startswith("~")
    assert [system.MACOS, system.WINDOWS, system.LINUX].count(True) == 1


def test_mirror_paths():
    m = tunnel._map
    assert m("/home/u/proj/panels/a.py", "/home/u/proj", "/tmp/mirror/proj") == "/tmp/mirror/proj/panels/a.py"
    assert m({"/home/u/proj/a": ["/home/u/proj"]}, "/home/u/proj", "/m") == {"/m/a": ["/m"]}
    assert m("/home/u/projects/a", "/home/u/proj", "/m") == "/home/u/projects/a"
    if os.name == "nt":
        local = "C:\\Users\\u\\mirror\\proj"
        assert m("/home/u/proj/panels/a.py", "/home/u/proj", local) == local + "\\panels\\a.py"
        assert m(local + "\\panels\\a.py", local, "/home/u/proj") == "/home/u/proj/panels/a.py"


def test_command_line_launcher(tmp_path):
    cmd = [str(ROOT / "bin" / "fignimble.cmd")] if os.name == "nt" else ["bash", str(ROOT / "bin" / "fignimble")]
    env = dict(os.environ, FIGNIMBLE_PYTHON=sys.executable)
    r = subprocess.run(cmd + ["journals"], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "nature" in r.stdout.lower()
    r = subprocess.run(cmd + ["new", str(tmp_path / "p")], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "p" / "fig1.py").exists()
