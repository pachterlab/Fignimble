# Windows and Linux

On Windows and Linux, Fignimble's window is a Qt editor (`apps/desktop`) around the same Python engine that the
macOS app uses. The figure language, rendering, canvas edits, undo, preflight, journal presets, posters, export,
provenance and the `fignimble` command are all the engine's, so a project behaves the same on every system and can
move between them. The macOS app is native (AppKit) and has some features the Windows and Linux editor doesn't have
yet; they are listed [below](#what-is-different).

See [installation](installation.md) to install it.

## The window

The layout is the one described in [the Fignimble window](../guide/window.md): **Figures** and **Layers** on the
left, the canvas in the middle, the **Inspector** on the right, and **Code**, **Preflight**, **History** and
**Caption** as tabs below. Each pane can be dragged elsewhere, tabbed with another, or closed and shown again from
**View**. The editor remembers its layout.

- **Canvas**: click to select, <kbd>Shift</kbd>-click or <kbd>Ctrl</kbd>-click to add to the selection, drag on the
  page to select with a box. Dragging moves the selection, with smart guides to other elements' edges and centres
  (hold <kbd>Ctrl</kbd> while dragging to turn snapping off). The handles resize; a line's ends move on their own
  (<kbd>Shift</kbd> keeps 45°). Arrow keys nudge 0.5 mm (<kbd>Shift</kbd> 5 mm, <kbd>Alt</kbd> 0.1 mm).
  <kbd>Ctrl</kbd>+scroll zooms, scroll pans (<kbd>Shift</kbd>+scroll sideways), and the middle button or
  <kbd>Space</kbd>+drag drags the view.
- **Tools**: <kbd>V</kbd> select, <kbd>T</kbd> text, <kbd>L</kbd> line, <kbd>A</kbd> arrow, <kbd>R</kbd> rectangle,
  <kbd>E</kbd> ellipse, <kbd>B</kbd> significance bracket, <kbd>P</kbd> pen (click points; double-click or
  <kbd>Return</kbd> finishes, clicking the first point closes the path). They are also in **Insert**.
- **Typing in place**: double-click a text or a panel label to retype it. With a panel selected, double-click an axis
  label, tick label, title or legend entry it draws to retype that; a single click on one picks it for the inspector
  (size, colour, bold, italic, **Back to What the Code Draws**). Double-clicking a panel elsewhere opens its code.
- **Layouts**: dragging a panel of a layout moves the layout; the gaps between its rows and columns have orange grips
  to drag (written back as `widths`/`heights`).
- **Guides**: **View ▸ Add Vertical / Horizontal Guide** adds one in the middle; drag it into place, or off the page to
  remove it.
- **Inspector**: with nothing selected, the figure's size and background, the shared style and the panel labels. With
  one element, its position and size, the colours a panel draws with (change one and every mark of that colour
  follows), and every argument of its line of code, each editable: type a Python value (`(0.1, 0, 0, 0)`, `True`,
  `"#D55E00"`); an empty field is `None`. A table's cells are a grid to type into (**Apply** writes its CSV file).
  With several, align, distribute, match, group and **Make Layout**.
- **Right-click** what is selected for Edit Code, Replace… (a picture's file), Update from Server (a panel fetched
  from a server), unlinking a label, the stacking order, Lock, Hide and Delete. A path's points have handles to drag.
- **Code**: the figure file, `style.py` and the panels' files. <kbd>Ctrl</kbd>+<kbd>S</kbd> applies the edits as one
  undo step; a file changed on disk (by an agent) is reloaded unless it has edits here.
- **History**: every edit; click one to go back to it.
- Drop pictures, `.py` panel files, CSV tables or another figure file of the project on the canvas to place them;
  drop a folder on the window to open it as a project.

## Keys

The menus have the same commands and shortcuts as on a Mac, with <kbd>Ctrl</kbd> for <kbd>⌘</kbd> and <kbd>Alt</kbd>
for <kbd>⌥</kbd>: **New Project** <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>N</kbd>, **Export Figure**
<kbd>Ctrl</kbd>+<kbd>E</kbd>, **Dark Version** <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>D</kbd>, **Code**
<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>E</kbd> and so on, as in [keyboard shortcuts](../reference/shortcuts.md).
<kbd>Ctrl</kbd>+<kbd>Y</kbd> also redoes on Windows. Where you are typing (the code pane, a field), the editing keys
(<kbd>Ctrl</kbd>+<kbd>Z</kbd>, <kbd>Ctrl</kbd>+<kbd>C</kbd>…) act on the text, not on the figure.

## The terminal and agents

The **Terminal** menu opens your system's terminal in the project folder (on Windows a console window, on Linux
`$TERMINAL` or the first of the usual terminal programs), with the `fignimble` command on its `PATH`. **Run Claude
Code / Codex / Gemini CLI in Project** opens one running that agent; **Run a Command in Project…** runs any command.
The agent instruction files and everything in [AI agents and the terminal](../ai/agents.md) work as on a Mac: the
editor redraws within a second of the agent's edits.

## Where things are

| | Windows | Linux |
|---|---|---|
| Fignimble's Python (made by the installer) | `%LOCALAPPDATA%\Fignimble\python` | `~/.local/share/fignimble/python` |
| The `fignimble` and `fignimble-app` commands | `%LOCALAPPDATA%\Fignimble\bin` (on your `PATH`) | `~/.local/bin` |
| The log | `%LOCALAPPDATA%\Fignimble\Logs\fignimble.log` | `~/.local/state/fignimble/fignimble.log` |
| Settings (recent projects, layout) | the registry, `HKCU\Software\pachterlab\Fignimble` | `~/.config/pachterlab/Fignimble.conf` |

`$XDG_DATA_HOME` and `$XDG_STATE_HOME` move the Linux folders, and `FIGNIMBLE_PYTHON` names the Python to draw with
on any system. **File ▸ Choose Python for Figures…** chooses another Python (a conda or venv environment with your
panels' packages) for the engine; the editor itself keeps running in its own.

## What is different

The Windows and Linux editor doesn't have these yet; the macOS app does:

- **The terminal drawer.** The terminal opens as a window of its own (above).
- **BioCartoon icons.** There is no icon browser. Search with the `biocartoon` command (in Fignimble's Python) and
  drop the SVG on the figure.
- **Cropping on the canvas.** Set a picture's `crop` in the inspector instead, as fractions of each side:
  `(left, top, right, bottom)`.
- **Rulers, the zoom tool and equal-spacing hints.** The status bar shows the pointer's position in millimetres.
- **The palette's swatches in colour wells, and dragging guides out of rulers.** The colour dialog has its own
  swatches and **Pick Screen Color**; **View ▸ Add Vertical / Horizontal Guide** adds a guide.
- **Projects on an SSH server** need macOS or Linux, and a server that accepts your key (ssh-agent, or a key in
  `~/.ssh`): the editor doesn't show ssh's password or code prompts. **Insert ▸ Panel from SSH Server…** works on
  Windows as well, with the same key.
- **Setting up Python on first launch.** The installers set it up instead.
- **Copy Figure as Picture** puts a 600 dpi PNG on the clipboard, and the PDF as a file to paste where files paste.

## Troubleshooting

**Linux: the window doesn't open, with a message about the "xcb" platform plugin.** Qt needs a few system
libraries: `sudo apt install libxcb-cursor0` (Debian, Ubuntu), `sudo dnf install xcb-util-cursor` (Fedora).

**Linux: text measures differently from a Mac.** Figures are drawn in Arial by default. Linux rarely has it, and then
falls back to a wider font. Install Liberation Sans, which has Arial's widths: `sudo apt install fonts-liberation`
(Debian, Ubuntu), `sudo dnf install liberation-sans-fonts` (Fedora). Then delete the project's `.fignimble` folder
so its panels are drawn again.

**EPS, CMYK or outlined text don't export.** They need Ghostscript: on Windows from
[ghostscript.com](https://www.ghostscript.com) (or `winget install ArtifexSoftware.GhostScript`), on Linux
`sudo apt install ghostscript`.

**Windows: the installer is blocked.** Run it as
`powershell -ExecutionPolicy Bypass -File install_fignimble.ps1`, which allows that one script.
