# How Fignimble works

Fignimble is a Python engine and two editors, a native macOS app and a Qt editor for Windows and Linux, that each
talk to it in the same JSON lines over standard input and output. The design comes
from [Manimble](https://github.com/pachterlab/Manimble): the engine and app split, the protocol, the undo that
coexists with an agent's edits, the terminal drawer and the agent instruction files.

```text
engine/fignimble/   api.py        the figure language (Figure, Style, Labels, Panel, layouts, cached)
                    journals.py   journal presets, poster sizes and poster templates
                    layout.py     patchwork-style layouts: rows, columns, grids and designs as grids of tracks
                    render.py     runs figure and panel code, lines up plot areas, collects legends, composes with
                                  PyMuPDF, sprites, preflight, export, dark versions
                    qr.py         a QR code encoder, drawn as vector squares
                    slides.py     PowerPoint export
                    tables.py     tables, and their Word and Excel export
                    templates.py  new projects, figures and posters, and the agent instruction files
                    srcedit.py    canvas edits -> minimal source patches through Python's syntax tree
                    core.py       the session: render worker, edit operations, undo, watching the project
                    rpc.py        JSON lines over stdio for the app (see the engine protocol)
                    requirements.py  packages Fignimble and a project need, and installing them
                    system.py     what differs between macOS, Linux and Windows: Fignimble's folders, the log,
                                  Ghostscript, a CMYK profile
                    cli.py        the fignimble command
apps/macos/         the macOS app (Swift, AppKit, SwiftTerm)
apps/desktop/       the Windows and Linux editor (Python, PySide6), and the Linux installer
bin/                the fignimble command's launchers (fignimble for macOS and Linux, fignimble.cmd for Windows)
examples/demo/      a project to try
tests/              engine tests
```

## Rendering

A long-lived worker process runs the figure file, which records its elements, then draws each panel with matplotlib
at its size into a PDF, measures its plot area and texts, and draws again panels whose plot areas should line up.
PyMuPDF composes the pieces into the figure. Every piece is cached in the project's `.fignimble/` under a hash of
what it depends on: its code, the style, its size, and the project's other files.

A figure placed in another (`fig.figure`) is run in the same worker with its own list of elements, rendered as a
figure, and its page is placed as one piece; a figure that places itself, directly or through others, is reported
instead. Export writes a provenance record into the file (PDF metadata, a PNG text chunk, a TIFF description or SVG
metadata): the Fignimble version, the git commit, and a hash of each file the render read.

## Editing

A canvas edit (move, resize, retype, recolor) becomes an operation that `srcedit.py` turns into the smallest change
to the source: it finds the call through Python's syntax tree and rewrites one argument. Each edit is one undo step
holding the files' contents before and after; undo merges line by line, so lines changed since by an agent or another
editor are left alone.

## Building and testing

```bash
python3 -m venv .venv && .venv/bin/pip install -r engine/requirements.txt pytest
apps/macos/build_app.sh                          # the app finds the checkout's .venv
PYTHONPATH=engine .venv/bin/python -m pytest tests
```

Running the app with `FIGNIMBLE_SHOTS=<folder>` walks through the editor on a project (give it a copy) and saves
pictures of the window. `FIGNIMBLE_SHOTS_SCENE=audit` checks Select All, Copy, Cut, Paste, Undo and Delete in every
place you type, writing `audit PASS` and `audit FAIL` lines to `~/Library/Logs/fignimble.log`.

## The Windows and Linux editor

`apps/desktop/fignimble_app` starts the engine as the Mac app does (`python -m fignimble.rpc`, or
`fignimble.tunnel` for a project on a server) and sends it the same commands and edit operations. It keeps no
figure logic of its own: the canvas draws the engine's sprites and turns pointer gestures into `edit` operations,
and the inspector lists each element's arguments from the `props` the engine reports. A change to the engine (a new
argument, a new kind of element, a journal) therefore reaches Windows and Linux without a change to the editor; an
unknown element still draws, selects and moves. Anything that differs between systems belongs in
`engine/fignimble/system.py`, so the rest of the engine names no platform's paths. What lives only in an editor
(a new menu command, a pane) has to be added to both; `tests/test_desktop.py` and CI catch an engine change that
breaks the Windows and Linux editor.

```bash
python3 -m venv .venv && .venv/bin/pip install -r engine/requirements.txt pytest PySide6-Essentials
PYTHONPATH=engine:apps/desktop .venv/bin/python -m fignimble_app examples/demo   # the editor, from the checkout
QT_QPA_PLATFORM=offscreen PYTHONPATH=engine .venv/bin/python -m pytest tests     # its tests need no screen
```

On Windows, `.venv\Scripts\python` in place of `.venv/bin/python`, and `set PYTHONPATH=engine;apps\desktop`.

## Continuous integration

`.github/workflows/tests.yml` runs the tests on macOS, Linux and Windows, and the Linux and Windows installers, on
every push and pull request. `tests/test_portability.py` holds what has to hold on every system (what undo keeps is
what is on disk, whatever the line endings; the per-system folders and programs; paths between a Windows mirror and
a POSIX server; the command's launchers) and `tests/test_desktop.py` drives the Windows and Linux editor through the
real engine. Releases are built, signed and notarized by GitHub Actions when a `v*` tag is pushed.
