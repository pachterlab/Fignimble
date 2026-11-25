"""What differs between macOS, Linux and Windows, in one place: where Fignimble keeps its Python and its log, how to
find Ghostscript and a CMYK profile, and what to tell someone who lacks them.

The rest of the engine asks here instead of naming a platform's paths, so the same code runs on all three.
"""

from __future__ import annotations

import glob
import os
import shutil
import sys
from pathlib import Path

MACOS = sys.platform == "darwin"
WINDOWS = os.name == "nt"
LINUX = not MACOS and not WINDOWS


def support_dir() -> Path:
    """Where Fignimble keeps its own Python (`python/` in it): Application Support on a Mac, %LOCALAPPDATA% on
    Windows, $XDG_DATA_HOME (~/.local/share) elsewhere. FIGNIMBLE_SUPPORT overrides it."""
    if os.environ.get("FIGNIMBLE_SUPPORT"):
        return Path(os.environ["FIGNIMBLE_SUPPORT"])
    if MACOS:
        return Path.home() / "Library" / "Application Support" / "Fignimble"
    if WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Fignimble"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "fignimble"


def support_python() -> Path:
    """The Python the installer (or the Mac app's setup) made in support_dir()."""
    env = support_dir() / "python"
    return env / "Scripts" / "python.exe" if WINDOWS else env / "bin" / "python"


def log_path() -> Path:
    """The log the app writes the engine's messages to: ~/Library/Logs on a Mac, %LOCALAPPDATA%\\Fignimble\\Logs on
    Windows, $XDG_STATE_HOME (~/.local/state)/fignimble elsewhere."""
    if MACOS:
        return Path.home() / "Library" / "Logs" / "fignimble.log"
    if WINDOWS:
        return support_dir() / "Logs" / "fignimble.log"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "fignimble" / "fignimble.log"


def shown(p: str | os.PathLike) -> str:
    """A path as people write it: ~ for the home folder."""
    s, home = str(p), str(Path.home())
    return "~" + s[len(home):] if s == home or s.startswith(home + os.sep) else s


def ghostscript() -> str | None:
    """The Ghostscript command: `gs` on the PATH (gswin64c/gswin32c on Windows), or where installers put it."""
    for name in (("gswin64c", "gswin32c", "gs") if WINDOWS else ("gs",)):
        found = shutil.which(name)
        if found:
            return found
    if WINDOWS:
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
        hits = [h for r in roots if r for h in glob.glob(os.path.join(r, "gs", "gs*", "bin", "gswin*c.exe"))]
        return sorted(hits)[-1] if hits else None                  # the newest version
    return next((p for p in ("/opt/homebrew/bin/gs", "/usr/local/bin/gs", "/usr/bin/gs") if Path(p).exists()), None)


def ghostscript_hint() -> str:
    """How to install Ghostscript here."""
    if MACOS:
        return "brew install ghostscript"
    if WINDOWS:
        return "install it from ghostscript.com (or: winget install ArtifexSoftware.GhostScript)"
    return "install your distribution's ghostscript package (sudo apt install ghostscript, sudo dnf install ghostscript)"


def cmyk_profile() -> Path | None:
    """A generic CMYK ICC profile the system has (FIGNIMBLE_CMYK_PROFILE names one), or None."""
    cands = [os.environ.get("FIGNIMBLE_CMYK_PROFILE")]
    if MACOS:
        cands += ["/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc"]
    elif WINDOWS:
        color = Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / "spool" / "drivers" / "color"
        cands += [str(color / n) for n in ("RSWOP.icm", "USWebCoatedSWOP.icc", "CoatedFOGRA39.icc")]
    else:
        cands += ["/usr/share/color/icc/colord/FOGRA39L_coated.icc", "/usr/share/color/icc/colord/SWOP-Coated-20-16.icc",
                  "/usr/share/color/icc/ghostscript/default_cmyk.icc"]
        cands += sorted(glob.glob("/usr/share/ghostscript/*/iccprofiles/default_cmyk.icc"))
    return next((Path(c) for c in cands if c and Path(c).is_file()), None)
