# Quick start

This walk-through makes a two-panel figure, letters it, styles it for a journal and exports it.

!!! note "Windows and Linux"
    Keys are shown as on a Mac. On Windows and Linux use <kbd>Ctrl</kbd> for <kbd>⌘</kbd> and <kbd>Alt</kbd> for
    <kbd>⌥</kbd>: <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>N</kbd> makes a new project. See
    [Windows and Linux](windows-linux.md).

## 1. Make a project

Choose **File ▸ New Project…** (<kbd>⇧</kbd><kbd>⌘</kbd><kbd>N</kbd>), pick a folder name and the journal to style
for. Fignimble makes the folder with two files and opens it:

- `style.py`: the look every figure of the project shares (font, text size, line widths, panel labels, colors).
- `fig1.py`: the first figure, empty, at the journal's double-column width.

A project is just a folder. Put it under git, open it in another editor, or send it to a colleague.

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
`gemini`); on Windows and Linux, **Terminal ▸ Run Claude Code in Project** opens a terminal running it. Ask for a panel, "add a heatmap of `data/correlations.csv` as panel c", and watch it appear. See
[AI agents and the terminal](../ai/agents.md).
