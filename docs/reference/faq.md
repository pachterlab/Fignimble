# Troubleshooting and FAQ

## Fignimble says it needs Python

Choose **Set Up Python**: it downloads Python 3.11 with what Fignimble needs into
`~/Library/Application Support/Fignimble` (about 260 MB, no password). Or choose **Choose a Python…** and pick a
`python3` that has matplotlib, PyMuPDF (below version 2), Pillow and NumPy. If the setup fails, check the internet
connection; its error says which step failed.

On Windows and Linux, run the installer (`install_fignimble.ps1`, `install_fignimble.sh`) again, or choose a Python
with those packages with **Choose a Python…**.

## A panel shows an error instead of a plot

The panel's code raised an exception. The preflight list (<kbd>⌥</kbd><kbd>⌘</kbd><kbd>P</kbd>) shows the error and
the line; double-click the panel to open its code. `No module named 'pandas'` means a package is missing: add it to
the project's `requirements.txt`, and Fignimble offers to install it (or **Fignimble ▸ Check Packages…**).

## I can't drag an element

Locked elements ignore the pointer: unlock it in the layers list (or <kbd>⌘</kbd><kbd>2</kbd>). An element whose
position the code computes (in a loop, or from a variable) can't be written back from the canvas; Fignimble says so,
and you edit it in the code. Panels inside a layout move with the layout; resize its rows and columns with the grips
between them, or **Release Layout**.

## The letters are wrong, or there are two of them

Fignimble letters panels itself. If a panel's code draws its own letter (`ax.set_title("a")`), remove it. Panels are
lettered in reading order; `Labels(order="source")` letters them in the order of the code instead, and
`label="c"` gives a panel a letter of its own.

## Text in a panel is too small

Preflight compares every text, inside panels too, with the style's minimum (`Style(min_font=5)`, or the journal's).
Remove `fontsize=` from the panel's code so the style's size applies, or make the panel larger: it redraws with text
at the same point size. Superscripts and subscripts don't count.

On a poster, a figure placed whole scales its text with it: a 7 pt figure placed at twice its size prints at 14 pt,
which is small for a poster. Place it larger, or draw it with a style with larger text,
`fig.figure("fig2.py", ..., style=Style(size=10))`; its panels then redraw with that text before being scaled.

## EPS export, CMYK or outlines don't work

These use Ghostscript: `brew install ghostscript` on a Mac, [ghostscript.com](https://www.ghostscript.com) (or
`winget install ArtifexSoftware.GhostScript`) on Windows, `sudo apt install ghostscript` on Linux.

## The BioCartoon search is empty

It needs the `biocartoon` package (part of Fignimble's Python setup; otherwise `pip install biocartoon`, or let the
pane install it) and an internet connection the first time, to download the catalog.

## A render takes long

Fignimble draws only the panels that changed. A panel that loads large data or fits models each time can keep the
result with [`@cached`](../guide/panels.md#sharing-slow-work-between-panels). A panel that runs for more than three
minutes is stopped.

## Where is the log?

`~/Library/Logs/fignimble.log` on a Mac, `%LOCALAPPDATA%\Fignimble\Logs\fignimble.log` on Windows,
`~/.local/state/fignimble/fignimble.log` on Linux (**Help ▸ Show Log** there). Attach it to a
[bug report](https://github.com/pachterlab/Fignimble/issues).

## Text is wider on Linux than on a Mac

Figures use Arial by default, which Linux rarely has. Install Liberation Sans, which has Arial's widths
(`sudo apt install fonts-liberation`), and delete the project's `.fignimble` folder so its panels are drawn again.

## What goes over the network?

Nothing about your figures. Fignimble connects to the internet to download Python when you set it up, to install
packages you agree to, to reach SSH servers you choose, and for BioCartoon: to download its catalog and icons, to
read how many stars icons have, and to record a star you give one, under a random ID for this Mac.

## Can I use Fignimble without the app?

Yes: a project is Python. `pip install git+https://github.com/pachterlab/Fignimble` installs the engine and the
`fignimble` command, so `fignimble snapshot`, `check` and `export` work on any computer, including Linux servers and
CI.
