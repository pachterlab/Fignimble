# Introduction

Most scientific figures are assembled the same way: each panel is plotted in Python or R and saved as a PDF, and the
panels are then arranged, lettered, aligned and annotated by hand in Illustrator. The second half is slow, it has to
be redone whenever a panel changes, and nothing records how the figure was put together.

Fignimble replaces that second half. A figure is a short Python file:

```python
from fignimble import *
from style import STYLE                       # shared by every figure of the paper

fig = Figure(width=183, height=118, style=STYLE)        # millimetres; 183 mm = Nature double column

fig.panel("panels/expression.py", x=4, y=3, w=56, h=52)            # lettered a
fig.panel("panels/scatter.py", x=65, y=3, w=56, h=52)              # lettered b
fig.image("images/cells.png", x=4, y=63, w=52)                     # pictures are panels too
fig.text("Treated", x=30, y=104, size=7, weight="bold", align="center")
fig.arrow(57, 82, 64, 82, width=0.6)
fig.bracket(147, 169, 67, text="**")                               # a significance bracket
```

and each panel is a matplotlib function that draws into a figure of exactly the panel's size:

```python
def draw(fig, genes=("GAPDH", "CD3E")):
    ax = fig.add_subplot()
    ...
```

Fignimble shows the figure and lets you edit it as you would in a drawing app. Dragging a panel changes its `x` and
`y` in the figure file; resizing it re-runs its code at the new size; retyping an axis label changes the string in
the panel's code. You can also edit the code yourself, in Fignimble's code pane or any editor, or ask a coding agent
to. All three change the same files, and Fignimble redraws within a second whenever they change.

A conference poster is made the same way. It is a figure on a larger page, with a title, text and the paper's
figures placed whole from their own files, so it changes when they do:

```python
fig = Figure(poster="a0")                                   # 841 x 1189 mm, with type sizes for a poster
fig.text(source="intro.md", x=20, y=150, width=250)         # prose, with bullets and headings, from Markdown
fig.figure("fig2.py", x=290, y=150, w=530)                  # Figure 2 of the paper, drawn from its code
fig.qr("https://github.com/pachterlab/Fignimble", x=740, y=1090, w=80)
```

## What you get

- **Automatic panel letters**, in reading order, re-lettered when panels move, styled once for every figure.
- **Panels that redraw at their size**, so their text and lines are the size the journal sees.
- **One style for every panel**: font, text size, line widths and colors, set in `style.py`.
- **Journal presets** for 71 journals, in biology, chemistry and materials science: column widths, fonts, label
  convention and the resolutions they require.
- **Plot areas that line up** across a row or column, whatever the tick labels, as
  [patchwork](https://patchwork.data-imaginist.com) does for ggplot.
- **Preflight**: small text, low-resolution pictures, colors color-blind readers confuse or that print alike in
  grayscale, scale bars without a pixel size, elements off the page.
- **Source data**: the values plotted in each panel, one spreadsheet sheet per panel, as journals ask.
- **Export** to vector PDF, EPS and SVG with real text, to TIFF, PNG and JPEG at the journal's resolution, and to
  PowerPoint; tables export as Word or Excel.
- **A dark version** of every figure for talks, from the same code.
- **Figures inside figures**: place a figure whole in another, for an overview figure, a graphical abstract or a
  poster.
- **Posters**: templates for the usual conference sizes, poster type sizes, text boxes with bullets and headings
  (or a Markdown file), QR codes, and PDFs with bleed and crop marks for printing.
- **Provenance**: every exported file records the git commit and a fingerprint of each file it was made from, and
  `fignimble provenance` checks it against the project.
- **Reproducibility**: the project keeps a PDF of every figure, a CSV of every table and of each panel's values, and
  a lock of what each was made from; recipes in `fignimble.toml` say how pictures made elsewhere are made;
  `fignimble reproduce` runs them again, and versions (`fignimble tag submitted`) can be compared with
  `fignimble diff`.
- **An AI agent in the terminal** that knows how the project works and can look at its own renders.

## Requirements

Fignimble runs on macOS 13 or later, on Apple Silicon and Intel Macs. It draws with Python 3, which it can install
for you the first time it opens. See [installation](installation.md).
