"""Starting points for new projects, figures and panels, and what a coding agent is told about a project."""

from __future__ import annotations

import re
from pathlib import Path

from . import journals


def style_py(journal: str | None) -> str:
    if journal:
        return f'''"""The look every figure of this project shares: font, text size, line widths and panel labels.
Change it here and every figure follows."""
from fignimble import *

STYLE = Style.journal("{journal}", labels=Labels.journal("{journal}"), palette="okabe-ito")
'''
    return '''"""The look every figure of this project shares: font, text size, line widths and panel labels.
Change it here and every figure follows."""
from fignimble import *

STYLE = Style(font="Arial", size=7, linewidth=0.6, labels=Labels(format="{}", case="lower", size=8, weight="bold"),
              palette="okabe-ito")
'''


def figure_py(width: float, height: float) -> str:
    return f'''from fignimble import *
from style import STYLE

fig = Figure(width={width:g}, height={height:g}, style=STYLE)
'''


def table_py(source: str, title: str | None = None) -> str:
    """A table of the paper, as simple as a table gets: a grid of cells (in `source`, a CSV in the project) with lines
    between them. A title and notes (title=, notes=) can be added in the inspector or the code."""
    t = f", title={title!r}" if title else ""
    return f'''from fignimble import *
from style import STYLE

tab = Table("{source}", style=STYLE, grid=True{t})
'''


def poster_py(preset: str, landscape: bool = False, figures: list[str] | None = None) -> str:
    """A poster to start from: a title band, authors, columns of sections with text boxes, the project's first
    figure placed whole, and a QR code. Everything in mm from the top-left, for the preset's size."""
    from .journals import POSTERS, poster_size, resolve
    key, turned = resolve(preset)                         # a template ("48x36", "a0-landscape") or a preset
    landscape = landscape != turned
    pr = POSTERS[key]
    W, H = poster_size(key, landscape)
    m = pr["margin"] + 5
    cols = 3 if W / H <= 1.05 else (4 if W / H < 1.7 else 5)     # columns for the page's shape
    gut = round(W * 0.025, 1)
    cw = round((W - 2 * m - (cols - 1) * gut) / cols, 1)
    pt = 25.4 / 72                                       # mm per point
    band = min(H * (0.13 if W <= H else 0.17), (pr["title"] + pr["heading"]) * pt * 1.9)
    ty = round(max(band * 0.22, pr["margin"] + 3), 1)     # the title clear of the margin, the band around it
    ay = round(ty + pr["title"] * pt * 1.35, 1)
    band = round(max(band, ay + pr["heading"] * pt * 1.8), 1)
    title, head = pr["title"], pr["heading"]
    xs = [round(m + i * (cw + gut), 1) for i in range(cols)]
    top = band + m
    sec_h = round((H - top - m) / 2 - gut, 1)
    fig_name = (figures or [None])[0]
    lines = [
        "from fignimble import *",
        "",
        f'fig = Figure(poster="{key}"{", landscape=True" if landscape else ""})'
        "          # the size, and type sizes for reading from a meter or two (Style.poster)",
        "",
        "# title band, run past the edges so trimming leaves no white line (export with a bleed)",
        f'fig.rect(-5, -5, {W + 10:g}, {band + 5:g}, stroke=None, fill="#0B3C5D")',
        f'fig.text("Title of the poster", x={m:g}, y={ty:g}, size={title}, weight="bold", '
        f'color="#FFFFFF", width={round(W - 2 * m, 1):g})',
        f'fig.text("Author One, Author Two and Author Three · Lab, Institution", x={m:g}, '
        f'y={ay:g}, size={head}, color="#FFFFFF", width={round(W - 2 * m, 1):g})',
        "",
        "# sections: a heading and a text box each; height= makes preflight say when text overflows",
    ]
    sections = ["Background", "Approach", "Results", "Discussion", "Conclusions", "References"][:cols * 2 - (1 if fig_name else 0)]
    k = 0
    for i in range(cols):
        for j in range(2):
            if fig_name and i == 1 and j == 0:
                continue
            if k >= len(sections):
                break
            y = round(top + j * (sec_h + gut), 1)
            lines.append(f'fig.text("# {sections[k]}\\n{_PLACEHOLDER}", x={xs[i]:g}, y={y:g}, width={cw:g}, '
                         f'height={sec_h:g})')
            k += 1
    if fig_name:
        lines += ["", "# a figure of the paper, placed whole and drawn from its code (scaled: its text grows with it)",
                  f'fig.figure("{fig_name}.py", x={xs[1]:g}, y={top:g}, w={cw:g})']
    q = round(min(60.0, cw * 0.4), 1)
    lines += ["", "# the paper's or the code's link",
              f'fig.qr("https://example.org", x={round(W - m - q, 1):g}, y={round(H - m - q, 1):g}, w={q:g})', ""]
    return "\n".join(lines)


_PLACEHOLDER = ("Text in a box wraps to its width. Lines starting with - are bullets:\\n- **bold** and *italic*\\n"
                "- or take the text from a file: fig.text(source=\\\"intro.md\\\", ...)")


def panel_py(name: str = "panel") -> str:
    return '''"""A panel: draw(fig) gets a matplotlib Figure exactly the panel's size. Fonts, text sizes and line widths come
from the project's style, so leave them out here and every panel will match."""
import numpy as np


def draw(fig, kind="lines"):
    ax = fig.add_subplot()
    x = np.linspace(0, 10, 100)
    if kind == "bars":
        ax.bar(["A", "B", "C"], [3, 5, 2], color="#4c72b0")
        ax.set_ylabel("Count")
    else:
        ax.plot(x, np.sin(x), label="sin")
        ax.plot(x, np.cos(x), label="cos")
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Signal")
        ax.legend()
'''


DEFAULT_FOLDER = "fignimble"        # a new project's folder unless named otherwise: paper/fignimble/

MARKER = "fignimble.toml"           # at a project's root: says Fignimble made it, which version, and where the root is


def _version() -> str:
    from . import __version__
    return __version__


def marker_text(version: str | None = None) -> str:
    return (
        "# A Fignimble project: publication figures written in Python (https://github.com/pachterlab/Fignimble).\n"
        "# Each top-level .py file is a figure or a table, panels/ holds the code that draws their panels, and\n"
        "# style.py the look they share. Open this folder in Fignimble, or run `fignimble check` in it.\n"
        "#\n"
        "# Fignimble keeps exports/ (a PDF of every figure, a CSV of every table and of each panel's values) and\n"
        "# fignimble.lock (what each was made from) up to date: commit them. Say here how pictures made outside\n"
        "# Fignimble are made, with [[recipe]] tables: https://pachterlab.github.io/Fignimble/guide/reproducibility/\n"
        "\n"
        "[project]\n"
        f'fignimble = "{version or _version()}"    # the Fignimble version the project was made with\n'
    )


def write_marker(folder, version: str | None = None) -> Path:
    """Put the marker in a project folder (one already there is left alone)."""
    p = Path(folder) / MARKER
    if not p.exists():
        p.write_text(marker_text(version), encoding="utf-8")
    return p


def read_marker(folder) -> dict | None:
    """What a project's marker says ({"fignimble": "0.4.0", ...}): {} when nothing in it is readable, None when the
    folder has no marker."""
    try:
        text = (Path(folder) / MARKER).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        import tomllib                                   # Python 3.11+
    except ImportError:
        m = re.search(r'^\s*fignimble\s*=\s*"([^"]*)"', text, re.M)
        return {"fignimble": m.group(1)} if m else {}
    try:
        return dict(tomllib.loads(text).get("project") or {})
    except (tomllib.TOMLDecodeError, AttributeError, TypeError, ValueError):
        return {}                                        # a mangled marker still marks the project


def find_project(path) -> Path | None:
    """The project a file or folder is in: the nearest folder at or above it with a marker, as git finds .git."""
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        p = p.parent
    for d in (p, *p.parents):
        if (d / MARKER).is_file():
            return d
    return None


def project_root(path) -> Path:
    """The folder to open for `path`: itself when it is a project (marked, or figures made before markers), else the
    marked project it is inside (a panels/ folder, a panel file), else its own folder."""
    p = Path(path).expanduser().resolve()
    d = p if p.is_dir() else p.parent
    if (d / MARKER).is_file() or looks_like_project(d):
        return d
    return find_project(d) or d


def marker_state(folder) -> dict:
    """For the app when a project opens: whether it has a marker, the version it names, and whether that version is
    newer than this Fignimble."""
    m = read_marker(folder)
    v = (m or {}).get("fignimble")
    v = v if isinstance(v, str) else None
    return {"marker": m is not None, "madeWith": v, "newer": newer_than_installed(v),
            "version": _version(), "looksLikeProject": m is not None or looks_like_project(folder)}


def looks_like_project(folder) -> bool:
    """A folder of figures made before projects had a marker: it has a style.py or a top-level figure file."""
    from .render import is_figure_source
    d = Path(folder)
    if (d / "style.py").is_file():
        return True
    for p in d.glob("*.py"):
        try:
            if is_figure_source(p.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            pass
    return False


def newer_than_installed(version: str | None) -> bool:
    """Whether a marker names a newer Fignimble than this one."""
    def key(v: str):
        return tuple(int(n) for n in re.findall(r"\d+", v)[:3])
    return bool(version) and key(version) > key(_version())


def new_project(folder: str | None = None, journal: str | None = "nature", poster: str | None = None,
                landscape: bool = False) -> dict:
    """A new project folder (`fignimble` unless named) with its marker, a style and a first figure, empty: no
    panels, nothing from another project. With `poster` (a size: a0, a1, 36x48, ...), the first figure is a poster
    to start from."""
    d = Path(folder or DEFAULT_FOLDER)
    if d.exists() and any(p for p in d.iterdir() if not p.name.startswith(".")):
        raise FileExistsError(f"{d} is not empty")
    (d / "panels").mkdir(parents=True, exist_ok=True)
    (d / "images").mkdir(exist_ok=True)
    write_marker(d)
    (d / ".gitignore").write_text(".fignimble/\n", encoding="utf-8")
    if poster:
        (d / "poster.py").write_text(poster_py(poster, landscape), encoding="utf-8")
        return {"ok": True, "project": str(d.resolve()), "figure": "poster"}
    width = journals.width(journal, "double") if journal else 183
    (d / "style.py").write_text(style_py(journal), encoding="utf-8")
    (d / "fig1.py").write_text(figure_py(width, 100), encoding="utf-8")
    return {"ok": True, "project": str(d.resolve()), "figure": "fig1"}


AGENT_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")
AGENT_MARK = "<!-- written by Fignimble; removed when Fignimble closes the project -->"

AGENT_TEXT = AGENT_MARK + '''
# Editing figures in this project with Fignimble

This folder is a Fignimble project: publication figures with several panels, each figure written in Python. The user
sees the figures in Fignimble, a figure editor, while you edit the code here, and edits them by hand there too (moving,
resizing, retyping). Fignimble re-renders a figure about a second after you save a file. (This file was written by
Fignimble and is not part of the project; don't edit it.)

## How a project is laid out

- **`fignimble.toml`** marks the project's top folder and records the Fignimble version that made it. Leave its
  `[project]` table alone. Its `[[recipe]]`, `[env.*]` and `[[input]]` tables say how pictures and tables made
  outside Fignimble (by scripts, notebooks, or by hand) are made: when you place a picture an analysis made, add a
  recipe for it (outputs, the command, its inputs, its environment), and run `fignimble check`.
- **`exports/`** and **`fignimble.lock`** are written by Fignimble (a PDF per figure, a CSV per table and per panel's
  values, and what each was made from). Never edit them; commit them. `fignimble check` says what is out of date,
  `fignimble diff` what changed since a version (`fignimble tags`).
- **Figure files** are the top-level `.py` files that make a `Figure` (`fig1.py`, `fig2.py`, ...). Each places
  elements on its figure, one call per element. Units are **millimeters**, from the figure's **top-left corner, y
  pointing down**.
- **Panels** are drawn by Python files, usually in `panels/`. A panel file has a function `draw(fig, ...)` that gets
  a matplotlib `Figure` of exactly the panel's size (`fig.add_subplot()`, `fig.subplots(2, 2)`, `fig.colorbar(...)`
  all work). `fig.panel("panels/x.py", function="other", args={"gene": "CD3E"})` calls `other(fig, gene="CD3E")`,
  so one file can draw several panels.
- **`style.py`** holds the `Style` every figure imports: font, text size, line widths, and the `Labels` (a, b, c...).

```python
from fignimble import *
from style import STYLE

fig = Figure(width=183, height=120, style=STYLE)
fig.panel("panels/expression.py", x=2, y=2, w=88, h=55)           # labeled a automatically
fig.image("images/gel.png", x=93, y=2, w=88)                       # pictures are panels too (b); h follows aspect
fig.text("Treated", x=40, y=60, size=7, weight="bold", align="center")
fig.arrow(60, 70, 80, 70)                       # also fig.line, fig.rect, fig.ellipse, fig.bracket(x1, x2, y, "**")
```

Panels can also be laid out **patchwork style**: the layout gives each panel its place, lines up their plot areas
across rows and columns, and can collect the legends they share into one:

```python
a, b, c, d = Panel("panels/a.py"), Panel("panels/b.py"), Panel("panels/c.py"), Image("images/gel.png")
fig.layout((a | b | c) / (d | Spacer()), x=0, y=0, w=183, h=120, heights=[2, 1], gap=4)     # | beside, / below
fig.layout(Grid(a, b, c, d, ncol=2), x=0, y=0, w=183, h=120)                               # + wraps into a grid
fig.layout(Design("""
    AAB
    CDB
""", A=a, B=b, C=c, D=d), x=0, y=0, w=183, h=120, widths=[1, 1, 1.5], guides="collect", legend="bottom")
fig.inset(Panel("panels/zoom.py"), within=a, x=0.6, y=0.05, w=0.35, h=0.35)   # fractions of a's box
fig.legend({"WT": "#999999", "KO": "#d1495b"}, x=150, y=5)                   # one legend shared by panels
```

`Row(..., widths=[2, 1])`, `Column(..., heights=[...])`, `Grid(..., ncol=, nrow=)` give a part of a layout its own
sizes. Panels on the figure are lined up the same way whenever they share an edge (`Style(align_axes=False)` turns
this off). Prefer a layout for regular grids of plots, and free `fig.panel(...)` for figures arranged by hand.

Every figure also has a **dark version** for talks (`fignimble snapshot fig1.py --dark`): fignimble turns the style's
text and axes white and the background black. Colors panel code sets itself stay as they are, so in panel code
use `"white" if is_dark() else "black"` (from fignimble) wherever you would write black.

Annotations: `fig.text("**bold** *italic* 10^{3} CD8_{a}", width=60)` (markup; `width` wraps), `fig.arrow(...,
curve=0.3, tip="stealth")`, `fig.path(points, smooth=True, closed=True, fill=...)`, `fig.scalebar(picture, length=50,
unit="µm")` (the pixel size from the picture's metadata, or `pixel_size=0.325` from the microscope), `fig.zoom(picture, inset, region=(x, y, w, h))` (fractions; the inset of the same
picture is cropped to the region). Pictures take `rotation=` and `flip="h"`. `Style(palette={"WT": "#999999", ...})`
sets the colors panels plot with (or a named one: `palette="okabe-ito"`, color-blind safe); use `color("WT")` in
panel code rather than repeating hex codes, and keep color-blind readers in mind (`fignimble info` warns about colors
they can't tell apart). A panel's `texts={"ax0.xlabel": "..."}`, `text_style={"ax0.legend0": {"size": 8, "color": "#...", "weight": "bold"}}`
and `colors={"ax0.line1": "#..."}` hold texts, text settings and colors the user changed on the canvas: leave them alone, or move them into the panel's code and remove the entries. `locked=True` / `hidden=True` are the user's layer settings: keep them.

Panels may import any package. List those beyond matplotlib and numpy in the project's `requirements.txt` (pip's
format), and Fignimble offers to install them when the project opens. Slow work several panels share (loading and
cleaning data, fitting a model) goes in a function decorated `@cached("data/table.csv")` (from fignimble): it runs once
and is kept until its file, its arguments or the listed data change. A rectangle can carry a panel letter
(`fig.rect(..., label="auto")`), for schematics drawn from shapes and pictures.

**Tables of the paper** (Table 1, Supplementary Table 2) are files of their own, not figures: `table1.py` makes one
`Table`, which lays out its title, cells and notes and sizes its page (no positions, no panel letters):

```python
from fignimble import *
from style import STYLE

tab = Table("tables/genes.csv", style=STYLE, title="Differentially expressed genes",
            decimals=[None, 0, 2, 3], notes=["FDR, Benjamini-Hochberg.", "n, cells per gene."])
```

The cells come from a CSV/TSV in `tables/`, `rows=[[...], ...]`, or a function returning rows or a pandas DataFrame
(`Table(genes, ...)`, with `@cached` for slow work), so a table computed from data stays tied to it: prefer that to
pasting numbers. Headers carry units; `decimals=` rounds each column alike. Journals want tables as text:
`fignimble export table1.py table1.docx` writes a Word table with the title and notes (also .xlsx, .csv, .tsv). To
show a table in a figure or poster, place it whole: `fig.figure("table1.py", ...)`. Only a small table that is part of
a figure goes in the figure itself: `fig.table("tables/counts.csv", x=4, y=80)`.

**Figures inside figures, and posters.** `fig.figure("fig2.py", x=20, y=130, w=360)` places another figure of the
project whole, drawn from its code (scaled to `w`, text and lines with it; `letters=False` drops its panel letters,
`style=` redraws it with another Style, `label="auto"` letters it as one panel). Never copy a figure's panels into
another figure to show it again: place the figure. A poster is a figure: `Figure(poster="a0")` (also "48x36", "72x48",
"24x36", "a1", ...: `fignimble journals` lists them; `landscape=True`) sets its size and `Style.poster`, whose type sizes are for reading from
a meter or two; preflight checks text at the size it is printed, including inside placed figures, and keeps a margin
along the edges clear. Text boxes (`width=`) lay out lines starting with "- " as bullets, "1. " as numbered items and
"# " as headings; `fig.text(source="intro.md", width=..., height=...)` takes the text from a Markdown file (keep a
poster's prose there), `height=` reports overflow and `fit="shrink"` fits it. `fig.qr("https://...", x=, y=, w=40)`
is a QR code. Export a poster with `--bleed 3 --crop-marks` when a print shop asks for them.

Other options: `label=None` (no letter) or `label="a'"` on a panel; `Labels(numbering="none")` for no letters at all; `fit=`/`crop=` on images; `group="g1"` to keep
elements together; `fig.guide(x=91.5)` for an editor guide line. Run `python -c "import fignimble; help(fignimble.Figure)"`
for every argument.

## Look at what you made

- `fignimble snapshot fig1.py` renders the figure to a PNG and prints its path. Open that picture to check your work.
- `fignimble info fig1.py` lists every element with its box (mm), the panel labels, and the preflight warnings (text
  below the journal's minimum size, low-resolution pictures, elements outside the figure).
- `fignimble export fig1.py fig1.pdf` writes the figure (PDF, EPS, SVG, TIFF, PNG, JPEG, PPTX; `--dark` for its dark
  version, `--journal cell` for that journal's resolutions; TIFF/JPEG are raised to them and every export is measured).
  Don't run other renderers. Each export records what it was made from; `fignimble provenance fig1.pdf` checks it
  against the project.
- `fignimble source-data fig1.py` writes the values plotted in each panel to `fig1_source_data.xlsx`, one sheet per
  panel, as journals ask. A panel whose plotted values can't be read back (shaded areas, violins, huge heatmaps)
  supplies them with a `source_data(...)` function next to `draw` (`<name>_source_data` for a panel function
  `<name>`), taking the same arguments and returning a DataFrame, a dict of them, or rows.

## Keep the figures editable by hand

Fignimble moves and resizes an element by rewriting the numbers in the call that made it, so:

- Give `x`, `y`, `w`, `h` (and line ends) as **literal numbers**. A computed position (`x=a.right + 2`) can't be
  dragged; use one only when asked to tie elements together.
- **Never draw panel letters yourself** (no `fig.text("a", ...)`, no `ax.set_title("a")`). Labels are automatic, in
  reading order, styled by `Labels` in `style.py`; to change their look, change that one `Labels(...)`.
- In panel code, **don't set font sizes, font families or line widths, and don't call `plt.figure`, `figsize`,
  `savefig`, `tight_layout` or `plt.show`**. The style sets fonts and sizes so all panels match, and Fignimble lays out and
  saves each panel at its size. (Use `fig` rather than `plt` where you can.)
- Don't reformat or reorder code you weren't asked to change: the order of the calls is the stacking order, and the
  user's own edits are in those lines.

## Working with the user

- Make one change at a time and say which figure and panel it touched.
- If a panel fails, Fignimble shows it red with the error, and `fignimble info` prints it. Fix the code.
- The user's Undo in Fignimble undoes only Fignimble's own edits, not yours.
'''


def write_agent_files(project: Path) -> list[str]:
    """Agent instructions under each agent's file name, where the project has none of its own."""
    made = []
    for n in AGENT_FILES:
        p = project / n
        if not p.exists():
            p.write_text(AGENT_TEXT, encoding="utf-8")
            made.append(n)
    return made


def remove_agent_files(project: Path, names: list[str]):
    for n in names:
        p = project / n
        try:
            if p.read_text(encoding="utf-8") == AGENT_TEXT:
                p.unlink()
        except OSError:
            pass
