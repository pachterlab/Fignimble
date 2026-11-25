# Fignimble

[![Downloads](https://img.shields.io/github/downloads/pachterlab/Fignimble/total?label=Downloads)](https://github.com/pachterlab/Fignimble/releases)

Fignimble assembles multi-panel figures for publication, and conference posters, the part of Illustrator scientists actually use, with Python
underneath everything. Each figure is a short Python file that places panels, pictures, text and arrows; each panel
is drawn by matplotlib code. You edit a figure on the canvas (drag, resize, align, retype), in the code pane, or by
asking a coding agent (Claude Code, Codex, Gemini CLI) in Fignimble's terminal. All three change the same files, and
Fignimble re-renders as they change. A poster is a figure too: it places the paper's figures whole, drawn from
their code, so it changes when they do.

Fignimble runs on **macOS** (a native app), and on **Windows and Linux** (a Qt editor around the same engine; see
[Windows and Linux](https://pachterlab.github.io/Fignimble/getting-started/windows-linux/)). Shortcuts below are
written as on a Mac; on Windows and Linux use Ctrl for ⌘ and Alt for ⌥.

**User guide: [pachterlab.github.io/Fignimble](https://pachterlab.github.io/Fignimble/)**

Fignimble is built on [Manimble](https://github.com/pachterlab/Manimble): the engine/app split, the JSON-lines
protocol, the undo model that coexists with an agent's edits, the terminal drawer and the agent instruction files
come from there.

![](docs/screenshot.png)

## A figure is Python

```python
from fignimble import *
from style import STYLE                       # shared by every figure of the paper

fig = Figure(width=183, height=118, style=STYLE)        # millimetres; 183 mm = Nature double column

fig.panel("panels/expression.py", x=4, y=3, w=56, h=52)            # labelled a
fig.panel("panels/scatter.py", x=65, y=3, w=56, h=52)              # labelled b
fig.image("images/cells.png", x=4, y=63, w=52)                     # pictures are panels too; h follows aspect
fig.image("images/schematic.svg", x=65, y=63, w=56, h=47)          # PDF and SVG stay vector
fig.panel("panels/expression.py", x=129, y=63, w=53, h=50, function="bars")
fig.text("Treated", x=30, y=104, size=7, weight="bold", align="center")
fig.arrow(57, 82, 64, 82, width=0.6)
fig.bracket(147, 169, 67, text="**")                               # significance bracket
```

A panel file is matplotlib code that draws into a figure of exactly the panel's size:

```python
def draw(fig, genes=("GAPDH", "CD3E")):
    ax = fig.add_subplot()
    ...
```

Coordinates are millimetres from the top-left corner, y down, as in Illustrator. One call per element, with literal
numbers, so that moving something on the canvas is a one-number change to its line of code.

Panels can use any package (pandas, seaborn, statsmodels...). List the ones beyond matplotlib and NumPy in the
project's `requirements.txt` and Fignimble offers to install them into its Python when the project opens (Fignimble ▸
Check Packages… asks again). Slow work that several panels share, like loading data or fitting a model, goes in a
function marked `@cached("data/table.csv")`: it runs once and is kept until its code, its arguments or that file
changes.

## Patchwork-style layouts

Panels can also be laid out the way [patchwork](https://patchwork.data-imaginist.com) composes ggplots, instead of
(or alongside) placing them by hand:

```python
expression, counts = Panel("panels/expression.py"), Panel("panels/counts.py")
heatmap, bars = Panel("panels/heatmap.py"), Panel("panels/expression.py", function="bars")

fig.layout((expression | counts) / (bars | heatmap), x=4, y=3, w=177, h=104, heights=[2, 1])   # | beside, / below
fig.layout(Grid(expression, counts, bars, heatmap, ncol=2), x=4, y=3, w=177, h=104)           # + wraps into a grid
fig.layout(Design("""
    AAC
    BDC
""", A=expression, B=bars, C=heatmap, D=counts), x=4, y=3, w=177, h=104, widths=[1, 1, 1.2],
           guides="collect", legend="bottom")                                                  # one shared legend
fig.inset(Panel("panels/counts.py", function="zoom"), within=counts, x=0.55, y=0.08, w=0.4, h=0.35)
```

- `|`, `/`, `+` and parentheses compose; `Row`, `Column`, `Grid(ncol=, nrow=, byrow=)` and `Design` strings (`#` for
  an empty cell) give parts their own relative `widths`/`heights`; `Spacer()` leaves a cell empty; `Image(...)` puts
  a picture in a cell.
- **Plot areas line up.** Each panel is drawn, its plot area measured, and panels that share an edge (a row's tops
  and bottoms, a column's left sides…) are redrawn with equal margins, whatever their tick labels, as patchwork does.
  This also applies to panels placed by hand whose edges line up (`Style(align_axes=False)` turns it off).
- `guides="collect"` takes the legends out of the panels and shows each distinct entry once, at the right or bottom.
- `fig.inset(...)` puts a panel on another, in fractions of its box; `fig.legend({...})` draws a legend of its own.
- On the canvas, dragging a panel of a layout moves the layout; the gaps between rows and columns have grips you drag
  to resize them (written back as `widths`/`heights`); the inspector sets the gap, widths, heights and legends.
  **Object ▸ Make Layout** (⌘L) turns selected hand-placed panels into a layout, and **Release Layout** turns a layout
  back into hand-placed panels where they are.

## Annotating and finishing a figure

```python
cells = fig.image("images/cells.png", x=4, y=3, w=80, rotation=90, flip="h", crop=(0.1, 0, 0, 0))
detail = fig.inset(Image("images/cells.png"), within=cells, x=0.6, y=0.6, w=0.38, h=0.38)
fig.zoom(cells, detail, region=(0.2, 0.2, 0.25, 0.25))     # box + lines; the inset shows what's in the box
fig.scalebar(cells, length=50, unit="µm", pixel_size=0.325) # sized from the picture's real pixels
fig.text("**GFP**^{+} cells express *Cd3e*", x=4, y=70, width=78)   # bold, italic, sup/sub; wraps at 78 mm
fig.arrow(96, 80, 120, 66, curve=0.25, tip="stealth")       # tips: triangle, stealth, open, bar, dot
fig.path([(130, 60), (140, 72), (152, 62)], smooth=True, closed=False, arrow="end")
fig.panel("panels/umap.py", x=142, y=3, w=40, h=46, texts={"ax0.xlabel": "UMAP 1"})   # a text the code draws
fig.rect(100, 40, 20, 10, locked=True, hidden=False)
STYLE = Style(..., palette={"WT": "#999999", "KO": "#d1495b"})     # color("KO") in panel code
```

In the editor:
- **Texts and marks inside panels:** click an axis label, tick, legend entry or title of a panel (or a line, bar or
  points of a selected panel) and the inspector edits it: its text, size, colour, bold and italic, or the mark's colour.
  Double-click a text (or press Return) to retype it on the canvas; Delete empties it, Esc goes back to the panel. A
  string literal in the panel's code is changed there (`ax.set_xlabel("UMAP 1", fontsize=9, color="#D55E00")`);
  anything else becomes a `texts={...}` / `text_style={...}` override on the panel's line, which **Back to What the
  Code Draws** removes.
- **Crop** a picture by double-clicking it (or Object ▸ Crop Picture): the handles crop, outward uncrops.
- **Zoom boxes** (Insert ▸ Zoom Box): on one picture, makes the magnified inset too; drag the box and the inset
  follows. **Scale bars** (Insert ▸ Scale Bar on Picture…).
- **Rotate and flip** (Object menu, inspector): pictures any angle (PDF/SVG in quarter turns), panels in quarter turns.
- **Pen (P)**: click points, double-click or Return to finish, click the first point to close; drag a path's points to
  change it. Lines bend (inspector) and take arrowhead styles.
- **Layers**: eye and lock buttons in the layers list (Object ▸ Lock ⌘2, Hide ⌘3); locked elements ignore the pointer.
- **Colour**: `Style(palette="okabe-ito")` (or "tol-bright", "tol-muted", "tol-vibrant", "colorbrewer-set2",
  "colorbrewer-dark2", "tableau", a list, or names → colours) sets the colours every panel plots with; new projects
  start with Okabe–Ito, which colour-blind readers can tell apart. Every colour well has a hex field and the palette's
  swatches; the picker's eyedropper takes any colour on screen; **Copy Style / Paste Style** (⌥⌘C, ⌥⌘V) carries colours,
  lines and text settings to other elements.
- **Recolouring inside panels**: the inspector lists each colour a panel draws with (lines, bars, points); change one
  and every mark of that colour follows. Double-click a single line, bar or point to recolour just it. The colour is
  changed in the panel's code when it is written there once, otherwise kept as `colors={...}` on the panel's line.
- **Colour vision**: preflight warns when two colours of a panel (or of the annotations) that most people tell apart
  look alike with deuteranopia, protanopia or tritanopia; **View ▸ Colour Vision** shows the figure as those readers
  (or a greyscale print) would see it.
- **Clipboard**: copy and paste between figures (a layout brings its panels; names are renumbered), Cut, and paste a
  picture from another app (saved into `images/`). **Edit ▸ Copy Figure as Picture** (⇧⌘C, or ⌘C with nothing
  selected) puts the whole figure on the clipboard as vector PDF and a 600 dpi PNG, for Word, PowerPoint, Keynote,
  Illustrator, mail or chat (the dark version when that is shown).
- **BioCartoon icons** (Insert ▸ BioCartoon Icons…, ⇧⌘B, or the toolbar button), as in Manimble: search 15,000+
  open-licensed science icons, filter by collection, category and licence, and drag one onto the figure (or
  double-click it). It is saved into `images/` (SVG when published as one, so it stays vector) and placed without a
  panel letter; right-click it for its credit. Needs the `biocartoon` command (`pip install biocartoon`; the pane
  offers to install it, and uses Manimble's or the BioCartoon app's copy if there is one).
- **Print**: export EPS, CMYK (PDF, EPS, TIFF, JPEG) and text as outlines (PDF, EPS, SVG); PDF/EPS conversions use
  Ghostscript (`brew install ghostscript`; Windows: ghostscript.com or `winget install ArtifexSoftware.GhostScript`;
  Linux: `sudo apt install ghostscript`). `fignimble export fig1.py fig1.eps --cmyk --outline`.
- Equal-spacing hints while dragging, **History** (⌥⌘⇧H: click a step to go back to it), **Replace…** for a picture's
  file, and the **zoom tool (Z)**: drag a box to zoom to it, ⌥-click to zoom out.

## Dark versions and dark mode

Every figure has a dark version for talks, made from the same code: the style's text and axes turn white, the
background black, black annotations white. **View ▸ Dark Version of Figure** (⌥⌘D, the moon in the toolbar) shows
it; Export has a *Dark version* box; `fignimble export fig1.py fig1-dark.pdf --dark` writes it. Colours that panel code
sets itself stay as they are; `is_dark()` lets panel code choose (`ecolor="white" if is_dark() else "black"`).
The app itself follows the system's light or dark appearance, or **View ▸ Appearance** chooses one.

## Posters, and figures inside figures

`fig.figure("fig2.py", x=20, y=130, w=360)` places another figure of the project whole, drawn from its code every
time and scaled with its text and lines (Insert ▸ Figure…, or drop the figure file on the canvas). A poster is a figure
built that way from the paper's own figures, so it keeps up with them:

```python
fig = Figure(poster="a1")                          # or a0, 48x36, 72x48, 24x36, b1, ...; landscape=True
fig.text(source="intro.md", x=20, y=135, width=170, height=480, fit="shrink")   # Markdown: headings, bullets
fig.figure("fig1.py", x=206, y=135, w=368)
fig.qr("https://github.com/pachterlab/Fignimble", x=504, y=756, w=70)
```

`Style.poster` sets type sizes for reading from a meter or two, and preflight checks text at the size it prints, inside
placed figures too, and keeps the margin along the edges clear. Export a PDF with a bleed and crop marks
(`--bleed 3 --crop-marks`), or PowerPoint on a slide the poster's size. Every export records the git commit and a
fingerprint of each file it was made from; `fignimble provenance poster.pdf` checks them against the project. See
[Posters](https://pachterlab.github.io/Fignimble/guide/posters/).

## What Fignimble does that Illustrator doesn't

- **Panel labels are automatic and consistent.** Panels are lettered in reading order (a, b, c… or A, B, C…,
  `(a)`, `a.`, roman or arabic numbering, or none: Labels ▸ No Letters, `Labels(numbering="none")`) and re-lettered when panels move. One `Labels(...)` in `style.py` sets font,
  size, weight and offset for every panel of every figure, and labels in a row or column line up. Dragging a label
  moves *all* labels (they share one offset); ⌥-drag nudges one. A panel can have its own label (`label="a'"`) or none.
- **Panels redraw at their size.** Resizing a panel re-runs its code at the new size, so its fonts and line widths
  stay true instead of being scaled.
- **One style for every panel.** `Style` (font, text size, line widths, despined axes) is applied to all panels, so
  plots made at different times match. `Style.journal("nature")` sets the journal's font, sizes and label convention.
- **Preflight.** Every render checks what a production office would: text below the minimum size (inside panels too),
  pictures below 300 dpi at their printed size, Type 3 fonts, elements outside the figure, duplicate labels, and
  whether the width matches the journal's columns.
- **40 journals**, computational biology's especially: the Nature Portfolio (Nature Methods, Nature
  Communications, Scientific Reports…), Cell Press (Cell Systems, Cell Genomics, Patterns…), PLOS (Computational
  Biology, ONE…), Oxford (Bioinformatics, NAR, Briefings in Bioinformatics, GigaScience…), BMC (Genome Biology, BMC
  Bioinformatics…), Genome Research, Molecular Systems Biology, eLife, Frontiers, IEEE/ACM TCBB, Science, PNAS. Each
  sets the figure widths, font sizes, panel label style and resolutions; `fignimble journals` lists them, with where
  each number comes from (the journal's guidelines, summaries of them, or the publisher's usual values). Check the
  journal's current guidelines before submitting.
- **Exports that meet journals' resolution rules**: vector PDF, EPS and SVG with real, editable TrueType text; TIFF
  (LZW), JPEG and PNG at any resolution (300 to 2400 offered; for PDF and EPS it is that of what panels draw as
  pixels, such as heatmaps). Export (⌘E) asks which journal (the style's by default): a TIFF or JPEG
  below what it asks is raised to it, 1200 dpi for line art, 600 with pictures (Cell Press 1000/500), an EPS has what
  Ghostscript rasterizes drawn at the line-art dpi, and the file written is measured: its dpi, or every picture inside
  a PDF or EPS against the 300 dpi asked for photographs. `fignimble export fig1.py fig1.tiff --journal cell`.
- **Tables as Word files**: `fig.table("tables/genes.csv", x=4, y=80, decimals=[None, 0, 2, 3])` draws a table from a
  CSV or TSV (Insert ▸ Table from CSV…, or drop the file on the figure), in the figure's font with rules above and
  below the header and at the bottom; numbers go right. Journals want tables as text, so **File ▸ Export Table as
  Word…** (and `fignimble table fig1.py 3 table1.docx`, or `fignimble table data.csv table1.docx`) writes it as a
  real, editable Word table.
- **Panels from an SSH server**: Insert ▸ Panel from SSH Server… lists a folder on a server (hosts from
  `~/.ssh/config`) and copies the panel code you pick into `panels/`, its first line saying where it came from;
  right-click the panel ▸ **Update from Server** fetches it again (one undo step). If the server asks for a password
  or a code, Fignimble shows the question. Data the panel reads on the server has to be copied too.

## The editor

- Canvas with rulers in mm, zoom (⌘+/⌘−, pinch, ⌘-scroll), pan (scroll, space-drag), grid and snap to grid.
- Select, shift-select, marquee; move with **smart guides** snapping to other elements' edges and centres, the figure's
  edges and centre, and guides; arrow keys nudge 0.5 mm (⇧ 5 mm, ⌥ 0.1 mm); ⌘ while dragging turns snapping off.
- Resize handles (⇧ keeps proportions; pictures keep theirs), line end handles (⇧ snaps to 45°).
- Tools: text (T), line (L), arrow (A), rectangle (R), ellipse (E), significance bracket (B); V to select.
- Double-click text or a label to retype it in place; double-click a panel to open its code.
- Align (left, centre, right, top, middle, bottom) to the selection, the first selected or the figure; distribute
  with equal gaps or a fixed gap; match width/height; group/ungroup; bring to front/send to back (= the order of the
  code). Guides: drag from a ruler.
- Inspector: position and size in mm, each element's settings, the figure size (with journal widths), the shared
  style and the label style. Layers list, figure thumbnails, preflight list.
- Drop PNG/TIFF/JPEG/PDF/SVG files or `.py` panel files onto the canvas.
- Code pane (⌥⌘E) with the figure, style and panel files; ⌘S applies. Undo (⌘Z) undoes canvas and code-pane
  edits; an undo leaves alone lines an agent has changed since.
- Terminals (⌥⌘T) in the project folder, with a **Run claude** button (or Codex, Gemini CLI, any command).

## Working with an agent

When a project opens, Fignimble writes `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` into it (only where the project has
none of its own, and removes them on close). They tell the agent how a project is laid out, to keep positions
literal so they stay draggable, never to draw panel letters itself, to leave fonts and sizes to the style, and how to
look at its own work:

```bash
fignimble snapshot fig1.py      # render to .fignimble/snapshots/fig1.png and print the path (an agent can read the picture)
fignimble info fig1.py          # every element's box in mm, the labels, preflight warnings
fignimble check                 # preflight every figure; exit status 1 on problems
fignimble export fig1.py fig1.pdf --dpi 600   # --journal cell: its resolutions (default: the style's journal)
fignimble table fig1.py 3 table1.docx          # a table of the figure as a Word file
fignimble new myfigures --journal cell
fignimble journals
```

## Install

### macOS

**Download:** the disk image `Fignimble-<version>-macOS.dmg` from the
[latest release](https://github.com/pachterlab/Fignimble/releases/latest) (universal, macOS 13+, signed and notarized):
open it and drag Fignimble to Applications. The first time it opens it offers to **Set Up Python**: it downloads
Python 3.11 with matplotlib, PyMuPDF, Pillow, NumPy and BioCartoon into `~/Library/Application Support/Fignimble`
(about 260 MB, a minute or less, no password; `apps/macos/setup_python.sh` does it). Or choose a Python of your own
that has them (`pip install matplotlib "pymupdf<2" pillow numpy`).

**The `fignimble` command** works in Fignimble's terminal; Fignimble ▸ Install Command Line Tool… puts it in
`~/.local/bin` for Terminal, VS Code and agents elsewhere (and offers to add that folder to your PATH).

**From source:** macOS 13 or later with Xcode (or its command line tools) and Python 3.10+.

```bash
./install_fignimble.sh          # Python environment in ~/Library/Application Support/Fignimble/python, the app in
                             # ~/Applications, the fignimble command in ~/.local/bin
open ~/Applications/Fignimble.app --args "$PWD/examples/demo"
```

### Windows

With [Python](https://www.python.org/downloads/) 3.10+ and the source (`git clone`, or a zip from GitHub), in
PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File install_fignimble.ps1   # Python environment in %LOCALAPPDATA%\Fignimble\python,
                                                                 # Fignimble in the Start menu, fignimble on the PATH
fignimble-app examples\demo                                      # in a new terminal
```

### Linux

With Python 3.10+ (and `python3-venv`), from the source:

```bash
./install_fignimble.sh          # Python environment in ~/.local/share/fignimble/python, the fignimble and
                                # fignimble-app commands in ~/.local/bin, Fignimble in the applications menu
fignimble-app examples/demo
```

Install Liberation Sans too (`sudo apt install fonts-liberation`), which has Arial's widths, so text measures as on a
Mac or Windows; and `libxcb-cursor0` if Qt's window doesn't open. Or, on any system, into an environment of your own:
`pip install "fignimble[desktop] @ git+https://github.com/pachterlab/Fignimble"`, then `fignimble-app`.

### Development

For development: `python3 -m venv .venv && .venv/bin/pip install -r engine/requirements.txt pytest`, then
`apps/macos/build_app.sh` (the app finds the checkout's `.venv`) and `.venv/bin/python -m pytest tests`. Running the
debug binary with `FIGNIMBLE_SHOTS=<folder>` walks through the editor on a project (give it a copy) and saves pictures of
the window; `FIGNIMBLE_SHOTS_SCENE=audit` checks Select All, Copy, Cut, Paste, Undo and Delete in the canvas, the code
pane, the terminal, the inspector, the BioCartoon search and the SSH dialog, and copying between them ("audit PASS/FAIL"
lines in ~/Library/Logs/fignimble.log).

The Windows and Linux editor runs from the checkout with `PYTHONPATH=engine:apps/desktop python -m fignimble_app`
(after `pip install PySide6-Essentials`); its tests run without a screen
(`QT_QPA_PLATFORM=offscreen PYTHONPATH=engine python -m pytest tests`). GitHub Actions runs the tests on macOS, Linux
and Windows, and the Linux and Windows installers, on every push (`.github/workflows/tests.yml`). What differs
between systems lives in `engine/fignimble/system.py`.

## Layout

```
engine/fignimble/   api.py       the figure language (Figure, Labels, Style)
                 layout.py    patchwork-style layouts: rows, columns, grids and designs as grids of tracks
                 render.py    runs figure and panel code, lines up plot areas, collects legends, composes with
                              PyMuPDF, sprites, preflight, export, dark versions
                 qr.py        QR codes (ISO/IEC 18004), without a package
                 srcedit.py   canvas edits -> minimal source patches through Python's syntax tree
                 core.py      session: render worker, edit operations, undo, watching the project
                 rpc.py       JSON lines over stdio for the app (docs/protocol.md)
                 cli.py       the fignimble command
                 system.py    what differs between macOS, Linux and Windows (folders, log, Ghostscript)
apps/macos/      the macOS app (Swift, AppKit, SwiftTerm)
apps/desktop/    the Windows and Linux editor (Python, PySide6), and the Linux installer
bin/             the fignimble command (fignimble for macOS and Linux, fignimble.cmd for Windows)
examples/demo/   a project to try
examples/kohl2025-ed5/   a 37-panel Nature figure (Cao et al. 2025, Extended Data Fig. 5) rebuilt from its authors'
                 code and data (CC BY 4.0)
tests/           engine tests, and the Windows and Linux editor's (tests/test_desktop.py)
```
