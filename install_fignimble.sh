#!/bin/bash
# Install Fignimble: a Python environment for its engine, the app in ~/Applications, and the `fignimble` command.
# On Linux this runs apps/desktop/install_linux.sh (the editor for Linux and Windows); on Windows use
# install_fignimble.ps1.
set -euo pipefail
cd "$(dirname "$0")"
case "$(uname -s)" in
  Darwin) ;;
  MINGW*|MSYS*|CYGWIN*) exec powershell.exe -ExecutionPolicy Bypass -File install_fignimble.ps1 ;;   # Git Bash on Windows
  *) exec bash apps/desktop/install_linux.sh "$@" ;;
esac
ROOT="$PWD"
ENV="$HOME/Library/Application Support/Fignimble/python"
PY="${PYTHON:-$(command -v python3)}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 10), "Fignimble needs Python 3.10 or later"'
if [ ! -x "$ENV/bin/python" ]; then
  echo "making a Python environment in $ENV"
  mkdir -p "$(dirname "$ENV")"
  "$PY" -m venv "$ENV"
fi
"$ENV/bin/python" -m pip install -q --upgrade pip
"$ENV/bin/python" -m pip install -q -r engine/requirements.txt
"$ENV/bin/python" -m pip install -q -e "$ROOT"          # `import fignimble` in that Python, for running figures yourself
apps/macos/build_app.sh
mkdir -p "$HOME/Applications" "$HOME/.local/bin"
rm -rf "$HOME/Applications/Fignimble.app"
cp -R apps/macos/Fignimble.app "$HOME/Applications/Fignimble.app"
ln -sf "$HOME/Applications/Fignimble.app/Contents/Resources/bin/fignimble" "$HOME/.local/bin/fignimble"
echo "installed ~/Applications/Fignimble.app and ~/.local/bin/fignimble"
echo "try: open ~/Applications/Fignimble.app --args \"$ROOT/examples/demo\""
