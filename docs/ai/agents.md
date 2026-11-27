# AI agents and the terminal

Because a figure is ordinary Python, a coding agent can build it with you: write a panel from a description, restyle
a figure for another journal, lay out twelve panels as a grid, or turn a notebook's plots into panels. Its edits
appear in Fignimble within a second, and you keep arranging the figure by hand while it works.

## The terminal

**View ▸ Terminal** (<kbd>⌥</kbd><kbd>⌘</kbd><kbd>T</kbd>) opens a terminal in the project folder, in the drawer
below the figure; **New Terminal** (<kbd>⌥</kbd><kbd>⌘</kbd><kbd>⇧</kbd><kbd>T</kbd>) opens another tab. Click
**Run claude** to start [Claude Code](https://claude.com/claude-code), or type `codex`, `gemini` or any other command.
The `fignimble` command is ready there.

## What the agent is told

When a project opens, Fignimble writes `CLAUDE.md`, `AGENTS.md` and `GEMINI.md` into it, only where the project has
none of its own, and removes them when the project closes. They tell the agent:

- how a project is laid out: figure files, panel files with `draw(fig)`, `style.py`;
- to keep positions as literal millimeters, so you can still drag what it places;
- never to draw panel letters itself, and to leave fonts and sizes to the style;
- to list the packages its panels import in `requirements.txt`, and to put slow shared work in `@cached` functions;
- to leave alone the `texts=`, `colors=`, `locked=` and `hidden=` settings you made on the canvas;
- how to look at its own work.

An agent can make a poster as well: ask it to "make an A0 poster from fig1 and fig2 with the abstract in
intro.md". It is told to place figures whole (`fig.figure(...)`) rather than copy their panels, so the poster keeps
up with the paper.

## Agent skills

The instruction files only exist while Fignimble has the project open. The skills teach an agent the same things
anywhere: in Terminal or VS Code, and in a folder Fignimble hasn't opened. Install them for your agent:

**Claude Code** (two separate prompts):

```
/plugin marketplace add pachterlab/Fignimble
```
```
/plugin install fignimble@fignimble
```

**Codex:**

```bash
codex plugin marketplace add pachterlab/Fignimble
codex plugin add fignimble@fignimble
```

**From the `fignimble` command** (any agent):

```bash
fignimble install-skills                 # Claude Code, every project (~/.claude/skills)
fignimble install-skills --agent codex   # Codex (~/.codex/skills)
fignimble install-skills --dest DIR      # any other agent's skills directory
```

| Skill | What it does |
|---|---|
| `/fignimble` | how a project works, the rules that keep a figure draggable, and how to check it (the others build on it) |
| `/fignimble-new-figure` | make a figure, and the project if there is none, from data and a description |
| `/fignimble-new-table` | make a table of the paper from data or an analysis, check it, and export the Word table |
| `/fignimble-from-notebook` | turn a notebook's or scripts' plots into panels and assemble them into a figure |
| `/fignimble-journal` | restyle figures for a journal, fix what `fignimble check` reports, export files that meet its rules |
| `/fignimble-poster` | make a poster that places the paper's figures whole, and export it for printing |
| `/fignimble-reproduce` | check that a project's figures reproduce, look into what doesn't, write REPRODUCIBILITY.md, and add missing recipes |

For example: `/fignimble-poster Make an A0 poster from fig1 and fig2 with the abstract in intro.md.`

## Letting the agent see the figure

```bash
fignimble snapshot fig1.py      # render to .fignimble/snapshots/fig1.png and print the path
fignimble info fig1.py          # every element's box in mm, the panel letters, preflight warnings
fignimble check                 # preflight every figure; exit status 1 on problems
```

An agent that can read pictures looks at the snapshot to check its layout; `info` gives it exact positions and the
warnings to fix.

## Working side by side

The canvas, the code pane and the agent all change the same files. Undo (<kbd>⌘</kbd><kbd>Z</kbd>) undoes your
edits and leaves alone lines the agent has changed since, so you never undo its work by accident, and it never
overwrites yours: Fignimble edits files line by line, the way a person would.

## Prompts that work well

- "Make `panels/volcano.py` from `data/de.csv`: log2 fold change against −log10 adjusted p, the 20 most significant
  genes labeled, and place it as panel c, 60 by 50 mm."
- "Restyle the project for Cell: column widths, label style and minimum text size."
- "Lay out panels a to f as a 3 by 2 grid with their legends collected at the bottom."
- "This notebook draws twelve plots. Turn each into a panel function in `panels/`, and lay them out as figure 2."
- "Make table 1 from `data/de.csv`: the 20 most significant genes, with fold change and adjusted p, as a Word table."
- "Fix everything `fignimble check` reports."
