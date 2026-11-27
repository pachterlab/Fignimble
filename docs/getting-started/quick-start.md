# Quick start

This walk-through makes a two-panel figure, letters it, styles it for a journal and exports it.

## 1. Make a project

Choose **File ▸ New Project…** (<kbd>⇧</kbd><kbd>⌘</kbd><kbd>N</kbd>), pick where the folder goes (it is called
`fignimble` unless you rename it; inside your paper's folder is a good place) and the journal to style for. Fignimble
makes the folder and opens it:

- `fignimble.toml`: marks the folder as a Fignimble project (and records the Fignimble version).
- `style.py`: the look every figure of the project shares (font, text size, line widths, panel labels, colors).
- `fig1.py`: the first figure, empty, at the journal's double-column width.

Once the project has figures, Fignimble also keeps `exports/` (a PDF of each figure, a CSV of each table) and
`fignimble.lock` up to date: commit them with the rest. See [Reproducibility](../guide/reproducibility.md).

From a terminal, `fignimble new` does the same (in a folder called `fignimble`, or
`fignimble new FOLDER`). A project is just a folder. Put it under git, open it in another editor, or send it to a
colleague. New Project needs a new or empty folder; to make figures in a folder you already have, see
[starting from a folder you already have](#7-starting-from-a-folder-you-already-have).

## 2. Add a panel

Choose **Insert ▸ New Panel from Code…** (<kbd>⇧</kbd><kbd>⌘</kbd><kbd>P</kbd>) and name it `growth`. Fignimble
writes `panels/growth.py` with a `draw(fig)` function, places it on the figure, and opens its code. Replace the body
with your plot:

```python
import numpy as np


def draw(fig):
    ax = fig.add_subplot()
    t = np.linspace(0, 24, 100)
    ax.plot(t, 1 / (1 + np.exp(-(t - 10) / 2)), label="WT")
    ax.plot(t, 1 / (1 + np.exp(-(t - 14) / 2)), label="KO")
    ax.set_xlabel("Time (h)")
    ax.set_ylabel("OD600")
    ax.legend()
```

Press <kbd>⌘</kbd><kbd>S</kbd> and the panel draws. Leave out font sizes and line widths: the project's style sets
them for every panel, so plots made at different times match.

## 3. Arrange it

Drag the panel; smart guides show when its edges line up with others. Pull a corner to resize it: the panel is drawn
again at the new size, so its text stays 7 pt. Add a second panel the same way, or drop a `.py` file, a PNG or a
PDF on the figure. The panels are lettered **a**, **b** in reading order, and re-lettered if you swap them.

Select both panels and use **Align** in the inspector, or **Object ▸ Make Layout** (<kbd>⌘</kbd><kbd>L</kbd>) to
turn them into a [layout](../guide/layouts.md) whose plot areas line up.

## 4. Edit what's inside a panel

Click the x-axis label of a panel. The inspector edits that text: retype it, change its size or color. Fignimble
changes the string in the panel's code. Click a line and the inspector recolors it.

## 5. Check and export

The status bar counts preflight warnings; **View ▸ Preflight** (<kbd>⌥</kbd><kbd>⌘</kbd><kbd>P</kbd>) lists them:
text below the journal's minimum size, pictures below 300 dpi, colors color-blind readers confuse.

Choose **File ▸ Export Figure…** (<kbd>⌘</kbd><kbd>E</kbd>), pick PDF, EPS, TIFF or another format and the journal,
and Fignimble writes a file at the resolution the journal asks for. **Edit ▸ Copy Figure as Picture**
(<kbd>⇧</kbd><kbd>⌘</kbd><kbd>C</kbd>) puts the whole figure on the clipboard for Word, PowerPoint or Keynote.

## 6. Bring in an agent

Choose **View ▸ Terminal** (<kbd>⌥</kbd><kbd>⌘</kbd><kbd>T</kbd>) and click **Run claude** (or type `codex` or
`gemini`). Ask for a panel, "add a heatmap of `data/correlations.csv` as panel c", and watch it appear. See
[AI agents and the terminal](../ai/agents.md).

## 7. Starting from a folder you already have

Your plotting code may already live in a folder, such as an analysis repository. Choose **File ▸ Open Project…**
(<kbd>⌘</kbd><kbd>O</kbd>) and pick that folder. Fignimble opens any folder. If the folder has no figures yet, the
canvas is empty and the status bar says so.

Choose **File ▸ New Figure…** (<kbd>⌘</kbd><kbd>N</kbd>). Fignimble writes `fig1.py` and, if the folder has none, a
`style.py`. That is all it takes to make the folder a project. Nothing else in it is moved or changed.

Fignimble decides what each file is by reading it:

- **Figures** are the top-level `.py` files that make a `Figure(...)`.
- **Panels** are the other `.py` files, in any subfolder, that define functions.
- **`style.py`** holds the `STYLE` the figures import.

Your existing scripts are not converted. A script that makes its own figure and saves it needs a small change before
it can be a panel. Move the plotting into a function that draws on the `fig` it is given, and leave the figure size,
fonts and line widths to Fignimble.

A script like this, `analysis/umap.py`, makes and saves its own figure:

```python
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv("data/umap.csv")
fig, ax = plt.subplots(figsize=(4, 4))
ax.scatter(df.x, df.y, s=2)
ax.set_xlabel("UMAP 1")
fig.savefig("umap.pdf")
```

As a panel, the same file looks like this:

```python
import matplotlib.pyplot as plt
import pandas as pd


def draw(fig):
    df = pd.read_csv("data/umap.csv")
    ax = fig.add_subplot()
    ax.scatter(df.x, df.y, s=2)
    ax.set_xlabel("UMAP 1")


if __name__ == "__main__":
    f = plt.figure(figsize=(4, 4))
    draw(f)
    f.savefig("umap.pdf")
```

The `if __name__ == "__main__":` block is optional. It keeps `python analysis/umap.py` working as before, and
Fignimble does not run it. Relative paths such as `data/umap.csv` are read from the project folder.

To place the panel, choose **Insert ▸ Panel from Existing Code…**, or drop the file on the figure. If one script draws
several plots, give each its own function and choose one with `function="..."`. List the packages your code needs,
beyond matplotlib and NumPy, in a `requirements.txt` in the folder. Fignimble offers to install them when the project
opens.

An agent can make these changes for you. In the terminal, ask something like "turn the plot in `analysis/umap.py` into
a panel and put it on fig1 as panel a". While a project is open, the instructions Fignimble writes into the folder tell
the agent how panels work. See [panels and their code](../guide/panels.md) for everything a panel can do.
