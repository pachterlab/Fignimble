#!/bin/bash
# Install Fignimble on Linux (install_fignimble.sh runs this there): a Python environment with the engine and the
# editor (PySide6) in ${XDG_DATA_HOME:-~/.local/share}/fignimble/python, the `fignimble` and `fignimble-app` commands
# in ~/.local/bin, and Fignimble in the applications menu. PYTHON=/path/to/python3 chooses the Python to build on.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
SUPPORT="$DATA/fignimble"
ENV="$SUPPORT/python"
BIN="$HOME/.local/bin"
PY="${PYTHON:-$(command -v python3 || true)}"
[ -n "$PY" ] || { echo "Fignimble needs Python 3.10 or later: install python3 (and python3-venv) first" >&2; exit 1; }
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Fignimble needs Python 3.10 or later"'
"$PY" -c 'import venv, ensurepip' 2>/dev/null || {
  echo "This Python can't make environments: install your distribution's python3-venv package" >&2; exit 1; }
if [ ! -x "$ENV/bin/python" ]; then
  echo "making a Python environment in $ENV"
  mkdir -p "$SUPPORT"
  "$PY" -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install -q --upgrade pip
"$ENV/bin/python" -m pip install -q -r engine/requirements.txt
"$ENV/bin/python" -m pip install -q "PySide6-Essentials>=6.5"
"$ENV/bin/python" -m pip install -q -e "$ROOT"          # `import fignimble` there, and the editor (apps/desktop)

mkdir -p "$BIN" "$DATA/applications" "$DATA/icons/hicolor/scalable/apps"
ln -sf "$ROOT/bin/fignimble" "$BIN/fignimble"
cat > "$BIN/fignimble-app" <<SH
#!/bin/sh
# Fignimble's editor (made by install_fignimble.sh from $ROOT)
exec "$ENV/bin/python" -m fignimble_app "\$@"
SH
chmod 755 "$BIN/fignimble-app"
cp "$ROOT/apps/desktop/fignimble_app/assets/fignimble.svg" "$DATA/icons/hicolor/scalable/apps/fignimble.svg"
cat > "$DATA/applications/fignimble.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Fignimble
GenericName=Figure Editor
Comment=Multi-panel figures for publication, assembled from Python
Exec="$BIN/fignimble-app" %f
Icon=fignimble
Terminal=false
Categories=Graphics;Science;Development;
StartupWMClass=fignimble
DESK
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" 2>/dev/null || true

echo "installed: $BIN/fignimble, $BIN/fignimble-app, and Fignimble in the applications menu"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN isn't on your PATH; add  export PATH=\"\$HOME/.local/bin:\$PATH\"  to ~/.bashrc" ;; esac
# figures are measured in their fonts: Arial, or Liberation Sans (the same widths), keeps them as on a Mac or Windows
if command -v fc-list >/dev/null && ! fc-list | grep -qiE "arial|liberation ?sans|arimo"; then
  echo "note: no Arial-compatible font; install one so text measures as elsewhere (Debian/Ubuntu: sudo apt install fonts-liberation)"
fi
command -v gs >/dev/null || echo "note: EPS, CMYK and outlined-text export need Ghostscript (sudo apt install ghostscript)"
"$ENV/bin/python" -c "import ctypes.util, sys; sys.exit(0 if ctypes.util.find_library('xcb-cursor') else 1)" 2>/dev/null \
  || echo "note: if the window doesn't open (Qt's \"xcb\" plugin), install libxcb-cursor0 (sudo apt install libxcb-cursor0)"
echo "try: fignimble-app \"$ROOT/examples/demo\""
