# Reproducibility

A figure in a paper should come with what it takes to make it again. Fignimble keeps three things at the top of a
project for this, all meant to be committed to git:

| File | Written by | Holds |
|---|---|---|
| `fignimble.toml` | you | how files made outside Fignimble are made: recipes, the environments they run in, data the repository doesn't hold |
| `exports/` | Fignimble | a PDF of every figure, a CSV of every table, and the values each panel plots |
| `fignimble.lock` | Fignimble | what each export and each recipe output was made from, by SHA-256 |

With these, anyone (a reviewer, a coauthor, an AI agent) can ask three questions of a repository and get answers
from commands rather than from reading code:

1. **How was each figure and table made?** Panels written in Fignimble are their own recipe. Pictures and tables
   made elsewhere have a recipe in `fignimble.toml`.
2. **Does what's in the repository still match how it was made?** `fignimble check`, in seconds.
3. **Does the project now match a version, such as the one submitted?** `fignimble tag` and `fignimble diff`.

`fignimble reproduce` goes further: it runs the recipes again and compares what they make with what the project
holds.

## exports/ and fignimble.lock

A project with a `fignimble.toml` keeps `exports/` and `fignimble.lock` up to date by itself while it is open in
Fignimble: a few seconds after you (or an agent) stop changing it, Fignimble draws each figure again and writes what
changed. `fignimble record` does the same from a terminal.

```text
exports/
  fig1.pdf  fig2.pdf               one PDF per figure
  table1.csv                       one CSV per table (Table files), as shown, rounded as on the page
  fig2.table1.csv                  tables drawn inside figures
  data/fig1/a.csv                  the values panel a plots: a table for each thing it draws (each line, each
                                   set of bars), one under another, each under a "# title" row
```

A figure's PDF is written again only when it **looks** different, so a change that doesn't show (a comment, a
refactored panel) leaves git nothing to commit. Its panels' values are written again whenever anything the figure
is made from changed: a picture can stay the same while its numbers move (an axis without tick labels rescales),
and the CSVs catch that. Panel values are those of [source data](export.md#source-data): a panel's own
`source_data()`, or what it draws, read back.

`fignimble.lock` is JSON. For each export it gives the figure it comes from, a hash of how it looks, a hash of each
file it was made from (figure, panel code, style, pictures), and where each lettered panel is. For each recipe
output and each picture a figure places it gives a hash, the hashes of its inputs, the environment, the commit, and
`how` Fignimble knows:

- `made`: `fignimble reproduce` ran the recipe and saw these inputs make this output.
- `observed`: the output was found next to these inputs. That's a claim, not a proof: it becomes `made` once
  `fignimble reproduce` runs it.
- `manual`: a recipe that says it was made by hand.

Don't edit `exports/` or the lock. To stop writing them while the project is open, add to `fignimble.toml`:

```toml
[exports]
on_save = false       # write them only with `fignimble record` (and `fignimble tag`)
data = false          # without the panels' values
picture_dpi = 150     # pictures in exports/ at most this resolution as printed (the default); 0 keeps them whole
```

Pictures in `exports/` are brought down to 150 dpi as printed, so that a micrograph doesn't put megabytes into git
each time its figure changes: `exports/` is a record to read and compare, not what you send a journal. Make those
files with [`fignimble export`](export.md).

## Recipes

Panels Fignimble draws need no recipe. Pictures and tables made by scripts and notebooks elsewhere do, and so
does anything made by hand, so that a reader knows which is which:

```toml
[project]
fignimble = "0.5.2"

[env.analysis]
image = "ghcr.io/lab/paper@sha256:3f1c..."   # best: an image pinned by digest
dockerfile = "../analysis/Dockerfile"        # how it was built (built here when no image is named)
# conda = "../analysis/conda-lock.yml"       # or a conda or Python lock file, with
# python = "../analysis/uv.lock"
# command = "uv run --frozen"                # what runs a command in it on this machine

[[input]]
path = "../analysis/data/counts.h5ad"
fetch = "https://zenodo.org/records/123/files/counts.h5ad"
sha256 = "9b2e..."
size = "4.1 GB"
access = "public"                            # or how to ask for it: "restricted: dbGaP phs000123"

[[recipe]]
name = "umap"
cwd = "../analysis"                          # where it runs; the paths below are relative to it
outputs = ["out/umap.png", "out/umap_legend.png"]
data = ["out/umap.csv"]                      # the numbers plotted, compared instead of pixels
run = "python scripts/umap.py --seed 0 data/counts.h5ad out/"
inputs = ["scripts/umap.py", "data/counts.h5ad"]
env = "analysis"
seed = 0
deterministic = false                        # UMAP drifts a little between machines
compare = { rtol = 1e-4, atol = 1e-9, pixels = 0.001 }
resources = { time = "20 min", memory = "16 GB", gpu = false }

[[recipe]]
outputs = ["images/confocal_b.tif"]
manual = "Zeiss LSM 880, maximum projection in Fiji, contrast 0-4000; raw files in BioImage Archive S-BIAD123"

[[recipe]]
outputs = ["images/*.svg"]
manual = "schematics drawn in Inkscape"
```

`[env.NAME]`
:   An environment. `image` runs commands with Docker (`docker run`, the recipe's folders mounted at their own
    paths); `dockerfile` (and `context`) builds the image when `image` isn't given. `conda` and `python` name the
    files that define an environment, so that the lock notices when they change; `command` says how to run a
    command in it here. Without an environment a recipe runs on the machine as it is, and the result says so.
    A `Dockerfile` alone doesn't reproduce an environment (packages move on): pin the image by digest, or lock files
    with exact versions.

`[[input]]`
:   Data the repository doesn't hold: where to `fetch` it, its `sha256`, its `size`, and its `access`.
    `fignimble reproduce` downloads what is missing and checks it; `fignimble check` says what is missing and how to
    get it.

`[[recipe]]`
:   `outputs`, and `run`, the command (a script, `jupyter nbconvert --execute`, `snakemake out/umap.png`), or
    `manual`, how it was made by hand, or `where = "cluster"` for a command that runs elsewhere. `name` defaults to
    the first output's name. `inputs` are what it reads (its code too); recipes run after those that make their
    inputs. `data` are numbers the outputs show: when given, they decide whether a remake matches. `compare` sets
    the tolerances: `rtol` and `atol` for numbers, `pixels` the fraction of pixels that may differ. `resources`
    (`time`, `memory`, `gpu`) let `reproduce` skip what doesn't fit its budget. `seed` is passed to the command as
    `FIGNIMBLE_SEED`.

Outputs may be globs (`images/*.svg`). Fignimble doesn't replace a workflow manager: a recipe can call Make,
Snakemake or Nextflow, and Fignimble's part is knowing which figure shows which file and checking it.

## fignimble check

```console
$ fignimble check
fig1.py: ok
...
reproducibility (exports/, fignimble.lock, fignimble.toml):
  ! stale      exports/data/fig2/ (11 files)  written before its figure last changed
    current    exports/fig1.pdf  drawn again: looks the same
  ! stale      exports/fig2.pdf  drawn now, it differs from the export: 0.4% of pixels differ (panel d)
  ? missing    ../analysis/data/counts.h5ad  fetch it from https://zenodo.org/records/123/files/counts.h5ad
  ! stale      ../analysis/out/umap.png  data/counts.h5ad changed (in fig2.py)
  · manual     images/confocal_b.tif  Zeiss LSM 880, ... (in fig3.py)
  ? no-recipe  ../qc/violin.png  no recipe says how it was made (in fig4.py)
```

`check` draws each figure and compares it with its export (`--fast` draws nothing and judges exports by the
files they were made from), checks inputs against their checksums, and says for each recipe output whether it is
`recorded`, `made`, `stale` (an input, the recipe or its environment changed since), `unrecorded` (the output
changed and wasn't recorded), `missing`, `manual` or `no-recipe`. Pictures kept in the project's `images/` without
a recipe are noted, and those outside it warned about. The exit status is 1 when something is out of date or
missing; `--json` gives everything as JSON.

## fignimble reproduce

```console
$ fignimble reproduce --check --budget 1h
would run:
  umap: docker run --rm -v /home/me/paper/analysis:/home/me/paper/analysis ... ghcr.io/lab/paper@sha256:3f1c... sh -c 'python scripts/umap.py ...'
      downloads https://zenodo.org/records/123/files/counts.h5ad
run them? [y/N] y
...
within-tolerance       umap  out/umap.csv: all 4000 cells within rtol 0.0001 (the pictures differ in pixels; the numbers they show match)
not-run: manual        confocal_b  Zeiss LSM 880, ...
not-run: resources     velocity  says it takes 6 h; 3300 s left
```

`reproduce` runs the recipes in order, downloads missing inputs, and compares each output (and its `data`) with
the project's file. Verdicts are `exact`, `within-tolerance`, `differs`, `failed` (the command failed; its output
is in a log), and `not-run` with a reason: `missing-data`, `restricted`, `resources`, `environment` or `manual`.

`--check` puts the project's files back afterwards. Without it, what was made is kept, recorded in the lock as
`made`, and `exports/` brought up to date. Either way, what was made and pictures of the differences are in
`.fignimble/reproduce/`. It asks before running commands; `--yes` runs them without asking, `--plan` only shows
them. A recipe from someone else's repository is code like any other: read it before you run it.

## Versions: fignimble tag and fignimble diff

A version is a git tag. Versions need the project to be in a git repository:

```console
$ fignimble tag submitted -m "As submitted to Nature"
committed exports/ and fignimble.lock (3c9f2e1a07)
tagged 3c9f2e1a07 as fignimble/submitted
share it with: git push origin fignimble/submitted
```

`tag` writes `exports/` and the lock, checks the project, commits them, and tags the commit. It refuses while other
files of the project aren't committed (the tag must name the code the exports came from) or the check finds a
problem; `--force` tags anyway, and says so in the tag. `fignimble tags` lists the versions.

`fignimble diff` compares the newest version with the project now, or any two versions
(`fignimble diff submitted revision-1`), at three levels:

```console
$ fignimble diff
submitted  ->  the project now

how things are made:
  changed  recipe umap  (run: 'python scripts/umap.py --seed 0' -> 'python scripts/umap.py --seed 1')
  changed  file panels/expression.py

what is shown (tables, panels' values):
  differs           exports/data/fig2/c.csv  14 of 2000 cells differ beyond the tolerance (rtol 1e-06, atol 1e-09)
  exact             exports/table1.csv  identical

how figures look:
  differs           exports/fig2.pdf  0.8% of pixels differ; panel c; made from changed out/umap.png; see .fignimble/diff/submitted..the-project-now/fig2.png
```

The picture shows the figure as it is now, faded, with what changed in red. The exit status is 0 when the two
versions are the same; `--json` gives everything as JSON.

## In the app

While a project with a `fignimble.toml` is open, Fignimble writes `exports/` and `fignimble.lock` by itself a few
seconds after the project stops changing; nothing needs doing. If a figure can't be exported (its code doesn't run),
a message says so and its last export stays as it was. Three items of the **File** menu do the rest:

**File ▸ Check Reproducibility…**
:   Brings `exports/` and the lock up to date, draws each figure again, and lists everything as
    [`fignimble check`](#fignimble-check) does: exports that are current or out of date, inputs that are missing,
    recipe outputs whose inputs changed, and pictures no recipe says how to make. **!** marks what is out of date or
    missing, **?** what the project doesn't say how it was made, **·** what is worth knowing.

**File ▸ Tag Version…**
:   Names this version, as [`fignimble tag`](#versions-fignimble-tag-and-fignimble-diff) does: give it a name
    (`submitted`, `revision-1`) and a message. Commit the project's other files first; Fignimble commits `exports/`
    and the lock itself. If something is out of date, or files aren't committed, it says what and tags nothing. Push
    the tag to share it (`git push origin fignimble/submitted`, in the terminal).

**File ▸ Compare with Version…**
:   Pick a version; the project now is compared with it, as [`fignimble diff`](#versions-fignimble-tag-and-fignimble-diff)
    does: what changed in how things are made, in the values tables and panels show, and in how the figures look,
    with the panels whose pixels changed. **Show Pictures of Differences** opens the folder of pictures with what
    changed in red.

Running recipes again (`fignimble reproduce`) runs other people's commands, and can take hours: it is done from the
terminal (Fignimble's own, **View ▸ Terminal**, has the `fignimble` command ready).

## For AI agents

The `fignimble-reproduce` [agent skill](../ai/agents.md#agent-skills) teaches an agent to check a repository's
figures this way and to write `REPRODUCIBILITY.md`, a verdict per figure, panel and table, and to add the recipes a
project is missing. Pointed at a repository, an agent needs only:

```bash
fignimble check --json
fignimble reproduce --check --yes --budget 1h --json
fignimble diff --json
```

## Provenance in exported files

Separately from all this, every PDF, PNG, TIFF and SVG Fignimble exports records what it was made from, and
`fignimble provenance FILE` checks it against the project: see [Provenance](export.md#provenance).
