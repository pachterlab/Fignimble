"""`python -m fignimble_app [project folder or figure.py]` (the `fignimble-app` command)."""

from __future__ import annotations

import os
import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Fignimble's editor needs PySide6 (Qt for Python): pip install PySide6-Essentials\n"
              "(the installers, install_fignimble.sh and install_fignimble.ps1, include it)", file=sys.stderr)
        return 1
    from . import ROOT, version
    from .window import MainWindow, apply_appearance

    # Qt 6.8+ on Linux asks the desktop portal for an app ID and prints a harmless warning when the session has
    # one already (launched from a terminal); keep it out of the terminal unless logging is set up already
    os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.services=false")
    app = QApplication(sys.argv[:1] + argv)
    app.setApplicationName("Fignimble")
    app.setOrganizationName("pachterlab")
    app.setApplicationVersion(version())
    app.setDesktopFileName("fignimble")
    icon = ROOT / "assets" / "fignimble.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    w = MainWindow()
    apply_appearance(app, w.settings.value("appearance") or "system")
    w.show()
    target = next((a for a in argv if not a.startswith("-") and os.path.exists(os.path.expanduser(a))), None)
    QTimer.singleShot(0, lambda: w.start(os.path.abspath(os.path.expanduser(target)) if target else None))
    rc = app.exec()
    # The window has stopped the engine and saved its settings. Qt is shut down here, which hands what was copied to
    # the system's clipboard; then the process ends without Python's own teardown, where clipboard data made from
    # Python crashes Qt.
    w.settings.sync()
    sys.stdout.flush()
    sys.stderr.flush()
    if hasattr(app, "shutdown"):
        app.shutdown()
    os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
