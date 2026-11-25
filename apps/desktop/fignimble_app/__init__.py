"""Fignimble's editor for Windows and Linux (it also runs on macOS): a Qt window around the same Python engine the
macOS app uses, talking to it in the same JSON lines (docs/protocol.md). Everything the figure language, rendering,
editing, undo, preflight and export do is the engine's, so the editor follows the engine as it changes.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def version() -> str:
    """The checkout's VERSION, else the installed package's."""
    for p in (ROOT.parent.parent.parent / "VERSION",):
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    try:
        from importlib.metadata import version as v
        return v("fignimble")
    except Exception:                                    # noqa: BLE001
        return "dev"
