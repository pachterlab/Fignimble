# How Fignimble works

Fignimble is a Python engine and a macOS app that talk JSON lines over standard input and output. The design comes
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
                    repro.py      reproducibility: fignimble.toml's recipes, exports/ and fignimble.lock, check,
                                  reproduce, and versions as git tags (tag, diff)
                    sourcedata.py the values each panel plots, read back from what it drew
                    cli.py        the fignimble command
apps/macos/         the macOS app (Swift, AppKit, SwiftTerm)
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

A second worker process keeps `exports/` and `fignimble.lock` up to date (`repro.record`): the session starts it
a few seconds after the project stops changing, so the editor's renders never wait for it. It draws every figure,
writes a figure's PDF only when the figure looks different (pages compared as pictures, not bytes, which carry
dates), and writes its panels' values again when anything the figure was made from changed. Both are left out of the
fingerprints that decide what to draw again, so writing them starts no render. See
[Reproducibility](../guide/reproducibility.md).

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

Releases are built, signed and notarized by GitHub Actions when a `v*` tag is pushed.
