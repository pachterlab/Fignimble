# Key concepts

## A project is a folder

By convention it is called `fignimble`, inside the paper's folder or repository (`mypaper/fignimble/`), and it is
marked by a `fignimble.toml` at its top, so that anyone looking at the folder sees that its figures are made with
Fignimble:

```text
mypaper/fignimble/
├── fignimble.toml      marks the project, and the Fignimble version it was made with
├── style.py            the look every figure shares
├── fig1.py             a figure file
├── fig2.py
├── panels/             the code that draws the panels
│   ├── expression.py
│   └── umap.py
├── images/             pictures: micrographs, gels, schematics, BioCartoon icons
├── data/               whatever your panels read
├── requirements.txt    packages the panels need (optional)
├── exports/            a PDF of every figure, a CSV of every table and of each panel's values (written by Fignimble)
├── fignimble.lock      what each export was made from (written by Fignimble)
└── .fignimble/         renders and caches (safe to delete; git-ignored)
```

**`exports/`** and **`fignimble.lock`** appear by themselves: a few seconds after the project changes, Fignimble
writes a PDF of each figure that looks different, a CSV of each table and of the values each panel plots, and a lock
of what each was made from. Commit them, and don't edit them. They let anyone check that the figures still match the
code (`fignimble check`), and compare versions (`fignimble tag`, `fignimble diff`). `fignimble.toml` can also say
how pictures made by scripts elsewhere are made. See [Reproducibility](../guide/reproducibility.md).

**`fignimble.toml`** says the folder is a Fignimble project and which version of Fignimble made it. Fignimble and
the `fignimble` command find the project from any folder inside it by looking for it, as git looks for `.git`:
opening `panels/` opens the project, and `fignimble check` run in `panels/` checks the project's figures. New
projects get one; when you open a folder of figures made before projects had one, Fignimble offers to add it. When it
names a newer Fignimble than yours, Fignimble says so.

**Figure files** are the top-level `.py` files that make a `Figure`. **Panel files** have a function
`draw(fig, ...)`; one file can hold several (`function="bars"`). **`style.py`** holds the `Style` every figure
imports.

## Units and coordinates

Everything is in **millimeters**, measured from the figure's **top-left corner, with y pointing down**, as in
Illustrator. Text sizes are in points. Each element is one call with literal numbers:

```python
fig.panel("panels/umap.py", x=65, y=3, w=56, h=52)
```

so that moving it on the canvas is a one-number change to its line. Values Fignimble can't write back, such as a
position computed in a loop, still draw; the canvas tells you to edit those in the code.

## Panels draw at their size

A panel's `draw(fig)` gets a matplotlib `Figure` exactly as large as the panel. Resizing the panel runs the code
again at the new size: text stays at its point size and lines at their width, instead of being scaled with the
picture. Fignimble measures each panel's plot area and, where panels share an edge, draws them again so their axes
line up.

## Three ways to edit, one set of files

The canvas, the code pane, your own editor and an AI agent all change the same files. Fignimble watches the project
and redraws about a second after anything changes. **Undo** (<kbd>⌘</kbd><kbd>Z</kbd>) undoes canvas and code-pane
edits; it leaves alone lines someone else has changed since, so it never throws away an agent's work.

## The style and the labels

`Style` sets the font, text size, line widths, colors and whether axes have top and right spines, for every panel.
`Labels` sets the panel letters: case, format (`a`, `(a)`, `A.`), size, weight and offset. `Style.journal("nature")`
sets both from a [journal preset](../reference/journals.md). Change `style.py` and every figure follows.

## Figures inside figures, and posters

A figure file can be placed whole in another with `fig.figure("fig2.py", x=..., y=..., w=...)`. It is drawn from its
own file each time, with its panels, letters and annotations, scaled to the size you give it. A poster is a figure
made this way: `Figure(poster="a0")` sets a poster's page and type sizes, and the paper's figures are placed on it.
See [posters](../guide/posters.md).

## Renders are cached

Fignimble keeps each panel's drawing in `.fignimble/` and draws a panel again only when its code, its data, its size
or the style changes. For slow work that several panels share, like loading data or fitting a model, decorate a
function with [`@cached`](../guide/panels.md#sharing-slow-work-between-panels).

## Files outside the project

A figure can use a picture or panel code that lives elsewhere, such as a plot your analysis writes or the functions
your notebook imports, without copying it in:

```python
fig.image("../analysis/out/umap.png", x=4, y=3, w=60)                         # rewritten by the analysis
fig.panel("../analysis/plots.py", x=70, y=3, w=60, h=50, function="umap")     # draws into the fig it's given
```

Fignimble watches those files too, and the modules that code imports from its own folder. When one changes, the
figure is drawn again, as it is for the project's own files. Two things to keep in mind:

- Panel code runs with the project as its working directory, so code kept elsewhere should find its data from its
  own location (`Path(__file__).parent / "data.csv"`).
- Retyping or recoloring on the canvas edits the code where the text or color is written, even outside the project.
  Keep the project and the analysis in one repository, side by side, so that a clone has both.

(Inserting a picture from the Insert menu, or dragging one in, copies it into `images/`; write its path in the code
to keep using the file where it is.)
