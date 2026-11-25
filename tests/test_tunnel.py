"""The tunnel: a project on an SSH server edited from a mirror on the Mac (fignimble.tunnel, fignimble.mirror).

The "server" is a folder here, reached through a stand-in for ssh that runs the remote command in a shell with that
folder as its home.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# the "ssh" here is a bash script; on Windows tests/test_portability.py checks the path mapping on its own
pytestmark = pytest.mark.skipif(os.name == "nt", reason="the ssh stand-in is a bash script")
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c63f8cfc0f01f0005000201a5f1d6a70000000049454e44ae426082")


class App:
    """Talks to the tunnel as the app does."""

    def __init__(self, tmp: Path, home: Path, python=sys.executable, mirror=None):
        fake = tmp / "fake_ssh"
        fake.write_text(f'#!/bin/bash\nwhile [ "$1" != "--" ]; do shift; done; shift\nexport HOME="{home}"\n'
                        'cd "$HOME"\nexec /bin/bash -c "$*"\n')
        fake.chmod(0o755)
        self.mirror = mirror or tmp / "mirror" / "proj"
        self.log = open(tmp / "tunnel.log", "a+")
        env = dict(os.environ, FIGNIMBLE_SSH=str(fake), PYTHONPATH=str(ROOT / "engine"))
        self.p = subprocess.Popen([sys.executable, "-m", "fignimble.tunnel", "--host", "lab", "--path", "~/proj",
                                   "--mirror", str(self.mirror), "--python", python], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1, env=env)
        self.n = 0
        self.events = []

    def __call__(self, cmd, **a):
        self.n += 1
        self.p.stdin.write(json.dumps({"id": self.n, "cmd": cmd, **a}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            assert line, "the tunnel stopped:\n" + self.logged()
            msg = json.loads(line)
            if "event" in msg:
                self.events.append(msg)
                continue
            assert msg["id"] == self.n
            assert "error" not in msg, msg["error"]
            return msg["result"]

    def logged(self):
        self.log.flush()
        self.log.seek(0)
        return self.log.read()

    def close(self):
        self.p.stdin.close()
        code = self.p.wait(timeout=60)
        return code


@pytest.fixture()
def server(tmp_path):
    home = tmp_path / "server home"
    shutil.copytree(ROOT / "tests" / "fixtures" / "demo", home / "proj",
                    ignore=shutil.ignore_patterns(".fignimble", "__pycache__"))
    (home / "proj" / "big.npy").write_bytes(b"\0" * 100)        # data stays on the server
    return home


def wait_for(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.2)
    return False


def test_remote_project(tmp_path, server):
    app = App(tmp_path, server)
    proj = server / "proj"
    r = app("open", project=str(app.mirror))
    assert r["project"] == str(app.mirror.resolve())
    m = app.mirror
    assert (m / "fig1.py").read_text() == (proj / "fig1.py").read_text()
    assert (m / "panels" / "scatter.py").exists() and not (m / "big.npy").exists()
    assert "copying the engine" in app.logged()

    # renders: the sprites and thumbnail come across, at the mirror's paths
    fig = app("figure", name="fig1")
    assert fig["file"] == str(m / "fig1.py")
    assert all(Path(e["sprite"]["file"]).is_file() and e["sprite"]["file"].startswith(str(m / ".fignimble"))
               for e in fig["elements"])
    st = app("state")
    assert st["figures"][0]["file"] == str(m / "fig1.py") and Path(st["figures"][0]["thumb"]).is_file()

    # a canvas edit changes the server's code, and the mirror has it by the time the answer comes
    r = app("edit", figure="fig1", op="move", oids=["1"], dx=3, dy=-1.5)
    assert r["ok"]
    assert 'x=68, y=1.5' in (proj / "fig1.py").read_text()
    assert (m / "fig1.py").read_text() == (proj / "fig1.py").read_text()
    app("undo", figure="fig1")
    assert (m / "fig1.py").read_text() == (proj / "fig1.py").read_text() and 'x=68, y=1.5' not in (m / "fig1.py").read_text()

    # the code pane saves through the engine, with the mirror's path
    text = (m / "panels" / "scatter.py").read_text() + "\n# from the code pane\n"
    assert app("write_source", file=str(m / "panels" / "scatter.py"), text=text, figure="fig1")["ok"]
    assert (proj / "panels" / "scatter.py").read_text() == text

    # an edit in the mirror (an editor on the Mac) goes to the server
    (m / "notes.md").write_text("from the Mac\n")
    assert wait_for(lambda: (proj / "notes.md").exists())
    assert (proj / "notes.md").read_text() == "from the Mac\n"
    # and one on the server (an agent in an ssh terminal) comes to the mirror
    (proj / "notes.md").write_text("from the server\n")
    assert wait_for(lambda: (m / "notes.md").read_text() == "from the server\n")
    # deletions both ways
    (m / "notes.md").unlink()
    assert wait_for(lambda: not (proj / "notes.md").exists())
    (proj / "README.txt").write_text("x")
    assert wait_for(lambda: (m / "README.txt").exists())
    (proj / "README.txt").unlink()
    assert wait_for(lambda: not (m / "README.txt").exists())

    # a picture from the Mac is uploaded and copied into the project's images
    pic = tmp_path / "photo.png"
    pic.write_bytes(PNG)
    r = app("import_file", figure="fig1", path=str(pic), x=5, y=5, w=10)
    assert r["ok"], r.get("error")
    assert (proj / "images" / "photo.png").read_bytes() == PNG and (m / "images" / "photo.png").exists()
    app("undo", figure="fig1")
    assert not (proj / "images" / "photo.png").exists() and not (m / "images" / "photo.png").exists()

    # an export lands where the app asked
    out = tmp_path / "exports" / "fig1.pdf"
    out.parent.mkdir()
    r = app("export", figure="fig1", path=str(out), format="pdf")
    assert r["path"] == str(out) and out.read_bytes().startswith(b"%PDF")

    # both changed: the server's version wins, the Mac's is kept
    (proj / "style.py").write_text((proj / "style.py").read_text() + "\n# server\n")
    (m / "style.py").write_text((m / "style.py").read_text() + "\n# mac\n")
    app("state")
    assert wait_for(lambda: "# server" in (m / "style.py").read_text())
    kept = list((tmp_path / "mirror" / "proj.conflicts").rglob("style.py"))
    assert kept and "# mac" in kept[0].read_text()

    assert app.close() == 0

    # the second time the engine is already there, and the mirror carries on from where it was
    app = App(tmp_path, server)
    log_before = app.logged().count("copying the engine")
    app("open", project=str(app.mirror))
    assert app.logged().count("copying the engine") == log_before
    assert (app.mirror / "style.py").read_text() == (proj / "style.py").read_text()
    assert not list((tmp_path / "mirror" / "proj.conflicts").rglob("fig1.py"))
    app.close()


def test_no_packages_on_the_server(tmp_path, server):
    # a Python without pymupdf: the app's `open` fails saying which, after an event it offers the install from
    py = tmp_path / "python_without_pymupdf"
    py.write_text('#!/bin/sh\necho "fignimble-missing pymupdf PIL"; exit 1\n')
    py.chmod(0o755)
    app = App(tmp_path, server, python=str(py))
    with pytest.raises(AssertionError, match="doesn't have pymupdf, pillow"):
        app("open", project=str(app.mirror))
    assert {"event": "needs_packages", "host": "lab", "python": str(py), "missing": ["pymupdf", "PIL"],
            "pip": ["pymupdf", "pillow"]} in app.events
    assert app.close() == 1

    # no Python there at all
    app = App(tmp_path, server, python="/usr/bin/false")
    with pytest.raises(AssertionError, match="couldn't run /usr/bin/false"):
        app("open", project=str(app.mirror))
    assert not app.events and app.close() == 1


def test_connection_lost(tmp_path, server):
    """When the server side goes, the tunnel ends at once (status 3), not at the app's next request."""
    app = App(tmp_path, server)
    app("open", project=str(app.mirror))
    kids = subprocess.run(["pgrep", "-P", str(app.p.pid)], capture_output=True, text=True).stdout.split()
    assert kids
    for k in kids:
        os.kill(int(k), 9)
    assert app.p.wait(timeout=20) == 3
    assert "the connection to lab closed" in app.logged()


def test_mirrored_files():
    sys.path.insert(0, str(ROOT / "engine"))
    from fignimble import mirror
    assert mirror.mirrored("fig1.py") and mirror.mirrored("panels/a.py") and mirror.mirrored("images/x.png")
    assert not mirror.mirrored(".fignimble/sprites/a.png") and not mirror.mirrored("venv/lib/x.py")
    assert not mirror.mirrored("data/big.h5ad") and not mirror.mirrored("CLAUDE.md")
