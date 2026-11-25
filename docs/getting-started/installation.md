# Installation

Fignimble runs on macOS, Windows and Linux. On a Mac it is a native app; on Windows and Linux it is an editor built
with Qt around the same engine (see [Windows and Linux](windows-linux.md) for how the two differ).

=== "macOS"

    ## Download

    1. Download `Fignimble-<version>-macOS.dmg` from the
       [latest release](https://github.com/pachterlab/Fignimble/releases/latest). It runs on Apple Silicon and Intel
       Macs with macOS 13 or later, and is signed and notarized by Apple.
    2. Open the disk image and drag **Fignimble** to **Applications**.
    3. Open Fignimble.

    ## Python

    Fignimble draws figures with Python. The first time it opens, it looks for a Python that has matplotlib, PyMuPDF
    and Pillow. If it finds none, it offers two choices:

    **Set Up Python** (recommended)
    :   Downloads Python 3.11 with matplotlib, PyMuPDF, Pillow, NumPy and BioCartoon into
        `~/Library/Application Support/Fignimble`. It takes about 260 MB and usually under a minute, needs no
        administrator password, and changes nothing else on your Mac.

    **Choose a Python…**
    :   Uses a Python of your own, such as a conda or venv environment, that has the packages installed:
        `pip install matplotlib "pymupdf<2" pillow numpy`.

=== "Windows"

    ## Install

    Fignimble installs from its source with a PowerShell script. You need
    [Python](https://www.python.org/downloads/) 3.10 or later (or `winget install Python.Python.3.12`) and
    [Git](https://git-scm.com/download/win), or the source as a zip from GitHub.

    ```powershell
    git clone https://github.com/pachterlab/Fignimble
    cd Fignimble
    powershell -ExecutionPolicy Bypass -File install_fignimble.ps1
    ```

    The script makes a Python environment in `%LOCALAPPDATA%\Fignimble\python` with matplotlib, PyMuPDF, Pillow,
    NumPy, BioCartoon and PySide6 (Qt, for the window), about 500 MB; adds **Fignimble** to the Start menu; and
    puts the `fignimble` and `fignimble-app` commands in `%LOCALAPPDATA%\Fignimble\bin`, which it adds to your
    `PATH`. It needs no administrator rights. `$env:PYTHON` chooses the Python it builds on.

    Start Fignimble from the Start menu, or open a project from a new terminal:

    ```powershell
    fignimble-app examples\demo
    ```

    Running the script again updates the environment after you `git pull`.

=== "Linux"

    ## Install

    Fignimble installs from its source. You need Python 3.10 or later with `venv` (Debian and Ubuntu:
    `sudo apt install python3-venv`), and git.

    ```bash
    git clone https://github.com/pachterlab/Fignimble
    cd Fignimble
    ./install_fignimble.sh
    ```

    The script makes a Python environment in `~/.local/share/fignimble/python` with matplotlib, PyMuPDF, Pillow,
    NumPy, BioCartoon and PySide6 (Qt, for the window), about 500 MB; puts the `fignimble` and `fignimble-app` commands in
    `~/.local/bin`; and adds **Fignimble** to the applications menu. It needs no root. `PYTHON=/path/to/python3`
    chooses the Python it builds on.

    Also install, from your distribution:

    - **Liberation Sans**, which has Arial's widths, so figures measure as on a Mac or Windows:
      `sudo apt install fonts-liberation` (Fedora: `liberation-sans-fonts`).
    - **libxcb-cursor0**, which Qt's window needs on X11 and most Wayland desktops:
      `sudo apt install libxcb-cursor0` (Fedora: `xcb-util-cursor`).

    Then start Fignimble from the applications menu, or:

    ```bash
    fignimble-app examples/demo
    ```

    Running the script again updates the environment after you `git pull`.

## With pip

On any system, Fignimble's engine, its command and the Windows/Linux editor install into a Python environment you
manage yourself:

```bash
pip install "fignimble[desktop] @ git+https://github.com/pachterlab/Fignimble"
fignimble-app            # the editor
fignimble journals       # the command
```

Leave out `[desktop]` for just the engine and the `fignimble` command (on a server, say).

## Packages your panels need

Panels can import anything: pandas, seaborn, statsmodels, scanpy. List the packages a project's panels need, beyond
matplotlib and NumPy, in a `requirements.txt` in the project folder:

```text
pandas
seaborn>=0.13
statsmodels
```

When the project opens, Fignimble checks them against its Python and offers to install the missing ones with pip.
It does the same for packages Fignimble itself needs. **Fignimble ▸ Check Packages…** (on Windows and Linux,
**File ▸ Check Packages…**) checks again.

To draw with a Python that already has your packages (a conda environment, say), choose it with **Choose a Python…**
(macOS: when Fignimble asks for one) or **File ▸ Choose Python for Figures…** (Windows and Linux), or set
`FIGNIMBLE_PYTHON` to its path.

## The `fignimble` command

The `fignimble` command renders, checks and exports figures from a terminal, and lets a coding agent look at its own
work. See [the fignimble command](../reference/cli.md).

- **macOS**: it is ready in Fignimble's terminal. To use it in Terminal, VS Code or an agent running elsewhere,
  choose **Fignimble ▸ Install Command Line Tool…**: it puts the command in `~/.local/bin` and offers to add that
  folder to your PATH.
- **Windows and Linux**: the installer puts it on your `PATH`, and the terminals Fignimble opens have it too.

## Optional

- **Ghostscript** for EPS export, CMYK conversion and text as outlines: `brew install ghostscript` on a Mac;
  [ghostscript.com](https://www.ghostscript.com) or `winget install ArtifexSoftware.GhostScript` on Windows;
  `sudo apt install ghostscript` (or your distribution's package) on Linux.
- **BioCartoon** icon search: Fignimble's Python setup includes it. With a Python of your own,
  `pip install biocartoon`, or let the BioCartoon pane install it (macOS).

## From source on a Mac

On macOS 13 or later with Xcode or its command line tools, and Python 3.10 or later:

```bash
git clone https://github.com/pachterlab/Fignimble
cd Fignimble
./install_fignimble.sh          # Python in ~/Library/Application Support/Fignimble/python, the app in
                                # ~/Applications, the fignimble command in ~/.local/bin
open ~/Applications/Fignimble.app --args "$PWD/examples/demo"
```

The Windows and Linux editor also runs on a Mac (`pip install PySide6-Essentials`, then `fignimble-app`), which is handy for
working on it. See [how Fignimble works](../developers/architecture.md) for building and testing.
