"""Where things are: the engine, a Python that can render, the `fignimble` command, the log. The engine's own
fignimble.system answers what differs between macOS, Linux and Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import ROOT

CHECKOUT = ROOT.parent.parent.parent                    # apps/desktop/fignimble_app -> the repository


def engine_dir() -> str:
    """The folder that holds the `fignimble` package: $FIGNIMBLE_ENGINE, the checkout's engine/, or where pip put it."""
    if os.environ.get("FIGNIMBLE_ENGINE"):
        return os.environ["FIGNIMBLE_ENGINE"]
    if (CHECKOUT / "engine" / "fignimble" / "__init__.py").is_file():
        return str(CHECKOUT / "engine")
    import fignimble                                     # installed with pip
    return str(Path(fignimble.__file__).resolve().parent.parent)


if engine_dir() not in sys.path:                         # fignimble.system, from the engine this editor drives
    sys.path.insert(0, engine_dir())
from fignimble import system                             # noqa: E402

WINDOWS, MACOS, LINUX = system.WINDOWS, system.MACOS, system.LINUX
NEEDS = "import matplotlib, pymupdf, PIL"


def can_render(python: str) -> bool:
    """Whether `python` has what the renderer needs (matplotlib, PyMuPDF, Pillow)."""
    try:
        r = subprocess.run([python, "-c", NEEDS], capture_output=True, timeout=40,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def python_candidates(saved: str | None = None) -> list[str]:
    """Pythons to try, in order: $FIGNIMBLE_PYTHON, the one chosen in the editor, the one running the editor, the
    installer's, the checkout's .venv, then python3 / python on the PATH."""
    venv = CHECKOUT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    out = [os.environ.get("FIGNIMBLE_PYTHON"), saved, sys.executable, str(system.support_python()), str(venv)]
    out += [shutil.which(n) for n in (("python", "python3") if WINDOWS else ("python3", "python"))]
    seen, keep = set(), []
    for p in out:
        if p and os.path.isfile(p) and p not in seen:
            seen.add(p)
            keep.append(p)
    return keep


def find_python(saved: str | None = None) -> str | None:
    return next((p for p in python_candidates(saved) if can_render(p)), None)


def bin_dir() -> str | None:
    """The folder with the `fignimble` command (put first on the PATH of terminals the editor opens)."""
    if (CHECKOUT / "bin" / "fignimble").is_file():
        return str(CHECKOUT / "bin")
    scripts = Path(sys.executable).parent
    return str(scripts) if any((scripts / n).exists() for n in ("fignimble", "fignimble.exe")) else None


def log_path() -> Path:
    p = system.log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def log_tail(lines: int = 12) -> str:
    try:
        return "\n".join(log_path().read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]).strip()
    except OSError:
        return ""


def note(text: str):
    """A line from the editor in the log (the engine's messages go there too)."""
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write("[app] " + text + "\n")
    except OSError:
        pass


def reveal(path: str):
    """Show a file or folder in Explorer / the file manager."""
    p = Path(path)
    try:
        if WINDOWS:
            subprocess.Popen(["explorer", "/select,", str(p)] if p.is_file() else ["explorer", str(p)])
        elif MACOS:
            subprocess.Popen(["open", "-R", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
    except OSError:
        pass


def open_file(path: str):
    """Open a file with the application the system uses for it."""
    try:
        if WINDOWS:
            os.startfile(path)                          # noqa: S606  (Windows only)
        elif MACOS:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass
