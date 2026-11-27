# The fignimble command

The `fignimble` command renders, checks and exports figures from a terminal, and lets a coding agent look at its own
work. It is ready in Fignimble's terminal; **Fignimble ▸ Install Command Line Tool…** installs it for Terminal and
other apps.

| Command | Does |
|---|---|
| `fignimble new [FOLDER] [--journal nature]` | a new project in `FOLDER` (default `fignimble`): its `fignimble.toml` marker, `style.py` and an empty `fig1.py` |
| `fignimble new myposter --poster 48x36 [--landscape]` | a new project with a poster to start from: a [template](../guide/posters.md#making-a-poster) (`a0`, `48x36`, `72x48`, `24x36`, ...) |
| `fignimble snapshot fig1.py [--dpi 150] [--dark]` | renders to `.fignimble/snapshots/fig1.png` and prints the path |
| `fignimble info fig1.py [--json]` | every element's box in mm, the panel letters, panels' Python warnings and preflight warnings |
| `fignimble check [fig1.py ...] [--fast] [--json]` | preflight warnings of every figure (of the project the current folder is in, found by its `fignimble.toml`), then `exports/` and recipe outputs against `fignimble.lock` ([reproducibility](../guide/reproducibility.md#fignimble-check)); exit status 1 if there are problems |
| `fignimble export fig1.py fig1.pdf [--dpi 600]` | PDF, SVG, EPS, PNG, TIFF, JPEG or PowerPoint (.pptx), from the file's extension; a table also as Word (.docx), Excel (.xlsx), CSV or TSV |
| `fignimble table table1.py table1.docx` | a table (a `Table` file, or the only table of a figure) as a Word, Excel, CSV or TSV file, with its title and notes |
| `fignimble table fig1.py 3 table1.docx` | a table of a figure that has several (its element number or name) |
| `fignimble table data.csv table1.docx` | a CSV or TSV file as a Word table |
| `fignimble source-data fig1.py [out.xlsx]` | the values plotted in each panel, one sheet per panel ([source data](../guide/export.md#source-data)); default `fig1_source_data.xlsx` |
| `fignimble provenance fig1.pdf` | what an exported file was made from, and whether the project's files still match (exit status 1 if not) |
| `fignimble record [--force] [--no-data]` | writes `exports/` (a PDF per figure, a CSV per table and per panel's values) and `fignimble.lock`, as the app does when the project changes ([reproducibility](../guide/reproducibility.md)) |
| `fignimble reproduce [RECIPE ...] [--check] [--budget 1h] [--yes] [--plan] [--json]` | runs the recipes in `fignimble.toml` again, in order, and compares what they make with the project's files; `--check` puts the files back afterwards |
| `fignimble tag NAME [-m MESSAGE] [--force]` | names this version: writes and commits `exports/` and the lock and tags the commit `fignimble/NAME` (needs git) |
| `fignimble tags [--json]` | the project's versions |
| `fignimble diff [A] [B] [--no-record] [--json]` | compares version A (default: the newest) with B (default: the project now): recipes and files, tables' and panels' values, and how figures look (exit status 1 if they differ) |
| `fignimble journals` | the journal presets and their column widths, and the poster sizes |
| `fignimble --version` | the installed version (0.5.0 and later) |
| `fignimble install-skills [--agent codex] [--project DIR] [--dest DIR]` | installs the [agent skills](../ai/agents.md#agent-skills) for Claude Code (`~/.claude/skills`), Codex, or another agent; `--list` shows them, `--uninstall` removes them |

Options of `export`:

`--journal cell`
:   meet that journal's resolutions (default: the style's journal).

`--dpi N`
:   the resolution of what is drawn as pixels: the whole figure for TIFF, JPEG and PNG; panels' rasters (heatmaps,
    rasterized scatters) in PDF and EPS.

`--cmyk`, `--outline`
:   colors converted for print (PDF, EPS, TIFF, JPEG), and text turned into shapes (PDF, EPS, SVG). Both use
    Ghostscript.

`--dark`
:   the figure's dark version, for talks (also for `snapshot`).

`--bleed MM`, `--crop-marks`
:   for a print shop's PDF: the page extended MM millimeters past each edge, and marks showing where to cut.

Run the commands from the project folder, or give paths to the figure files.
