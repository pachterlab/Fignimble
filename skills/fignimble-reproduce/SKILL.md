---
name: fignimble-reproduce
description: >-
  Check whether a Fignimble project's figures and tables are reproducible,
  and make them so: read fignimble.toml's recipes, run `fignimble check`,
  `fignimble reproduce` and `fignimble diff`, look into every result that
  isn't exact, and write REPRODUCIBILITY.md with a verdict per figure, panel
  and table. Also writes the recipes a project is missing (how each picture
  placed from outside Fignimble was made, its inputs and environment), and
  tags a version (submitted, revision-1) to compare against later. Use when
  asked to "check that the figures reproduce", "are these figures
  reproducible", "reproduce figure 2", "add recipes for the panels", "what
  changed since the submitted version", "tag this version", or when pointed
  at a repository with a fignimble.toml.
---

# Check and record how a project's figures are made

Follow the `fignimble` skill for the figure language (load it if it is installed). Find the `fignimble` command
first (`command -v fignimble`, else `/Applications/Fignimble.app/Contents/Resources/bin/fignimble`). Run every
command from inside the project (the folder with `fignimble.toml`, or below it). The full reference is
`docs/guide/reproducibility.md` in Fignimble's repository (https://pachterlab.github.io/Fignimble/guide/reproducibility/).

## What a project holds

- `fignimble.toml`: `[env.NAME]` environments (`image`, `dockerfile`, `conda`, `python`, `command`), `[[input]]`
  data the repository doesn't hold (`path`, `fetch`, `sha256`, `size`, `access`), `[[recipe]]` how each file made
  outside Fignimble is made (`outputs`, `run`, `cwd`, `inputs`, `env`, `data`, `compare`, `seed`, `resources`, or
  `manual`/`where` for what isn't run here). Paths in a recipe are relative to its `cwd`.
- `exports/`: a PDF per figure, a CSV per table, and `exports/data/<figure>/<panel>.csv`, the values each panel
  plots. Written by Fignimble; never edit them.
- `fignimble.lock`: what each export and each recipe output was made from (SHA-256). `how` is `made` when
  `fignimble reproduce` made the output from those inputs, `observed` when the output was found next to those
  inputs (a claim, not a proof), `manual` for a manual recipe. Written by Fignimble; never edit it.
- Versions are git tags `fignimble/NAME`.

## 1. Where things stand (seconds, runs no recipe)

```bash
fignimble check --json          # preflight, then exports/ and recipe outputs against the lock
fignimble reproduce --plan      # what reproduce would run, in order, and what it would download
fignimble tags                  # the versions
```

Read `reproducibility.items` of the check: each has `path`, `kind` (export, input, output, recipe), `state` and
`level`. States: `current`, `stale` (made from files that changed since), `missing`, `edited` (an export that
isn't the file recorded), `unrecorded` (an output that changed and wasn't recorded), `recorded`, `made`, `manual`,
`no-recipe`, `checksum`, `no-output`. If the lock is missing or exports are stale, run `fignimble record` (what the
app does when the project changes) before anything else, and say so in the report.

## 2. Run the recipes again, within a budget

```bash
fignimble reproduce --check --yes --budget 1h --json
```

`--check` puts the project's files back afterwards; what was made is in `.fignimble/reproduce/<time>/after/`,
with pictures of the differences in `differences/` and each recipe's output in a `.log` beside it. Ask the user
before running without `--check` (that keeps what was made and records it), before raising the budget for slow
recipes, and before downloading large inputs (`size` in fignimble.toml). Never run with `--yes` on a repository
you weren't asked to run code from.

Each result has a `verdict`: `exact`, `within-tolerance`, `differs`, `failed` (the command failed: read its log),
or `not-run` with `why`: `missing-data`, `restricted`, `resources` (time budget, GPU, `where`), `environment` (no
Docker), `manual`.

## 3. Look into every verdict that isn't exact

- `within-tolerance`: say what the tolerance was. Fine unless the numbers are what the paper reports.
- `differs` with `data` (the recipe's numbers): read both CSVs (before: the project's file; after: in `after/`).
  Is it a seed (`seed`, `deterministic = false`), a library version (the environment isn't pinned), or a real
  change? A picture that differs while its data match is a rendering difference, not a result that changed.
- `differs` without `data`: look at the picture of the differences. Suggest adding `data` to the recipe (the
  numbers it plots, written by the script) so the next check compares numbers, not pixels.
- `failed`: the log's last lines. Missing packages point at the environment; say what to pin.
- `not-run`: say what would be needed (the data's `access`, the time, a GPU, Docker).

## 4. Compare with a version

```bash
fignimble diff --json                    # the newest version against the project now
fignimble diff submitted revision-1      # two versions
```

`how` lists recipes, environments and files that changed; `shown` the tables and panels' values; `looks` the
figures, with the `panels` whose pixels changed, a `highlight` picture, and `because`: the files the export was
made from that changed.

## 5. Write REPRODUCIBILITY.md

At the project's top, replacing an older one:

1. One paragraph: what was run, where (machine, Docker or not), how long, the Fignimble version, and the commit.
2. A table per figure: panel, how it is made (Fignimble panel code `panels/x.py:fn`, recipe `NAME`, manual),
   verdict, and a note. Tables: one row each. Take the panel letters from `fignimble info FIG.py`.
3. What wasn't run and why, and what each would need.
4. Differences, each with what you found out about it.
5. What to fix in the project (recipes to add, environments to pin, `data` to add, inputs without checksums).

Say plainly what you didn't check. Don't call a figure reproduced because its recipe is `observed` in the lock:
only `exact` or `within-tolerance` from `reproduce` (or panel code Fignimble draws itself) counts.

## Adding recipes to a project

When the check reports `no-recipe` for pictures a figure places, find what made each one (a script or notebook
that writes that file: search the repository for its name), then add a recipe:

```toml
[env.analysis]
image = "ghcr.io/lab/paper@sha256:..."     # best: an image pinned by digest
dockerfile = "../analysis/Dockerfile"      # how it was built (built here when no image is named)

[[input]]
path = "../analysis/data/counts.h5ad"
fetch = "https://zenodo.org/records/123/files/counts.h5ad"
sha256 = "..."                             # shasum -a 256 FILE
size = "4.1 GB"
access = "public"

[[recipe]]
name = "umap"
cwd = "../analysis"                        # paths below are relative to this
outputs = ["out/umap.png"]
data = ["out/umap.csv"]                    # the numbers plotted: compared instead of pixels
run = "python scripts/umap.py --seed 0"
inputs = ["scripts/umap.py", "data/counts.h5ad"]
env = "analysis"
seed = 0
compare = { rtol = 1e-6, pixels = 0.001 }
resources = { time = "20 min", memory = "16 GB", gpu = false }

[[recipe]]
outputs = ["images/*.svg"]
manual = "schematics drawn in Inkscape"
```

A notebook runs with `jupyter nbconvert --to notebook --execute --inplace analysis.ipynb` (or `papermill`). A
workflow manager's target runs as is (`snakemake out/umap.png`). Add `data` to a script by writing what it plots
to a CSV beside the picture. Run `fignimble check` afterwards: the recipe's outputs should show `recorded`. Never
invent a checksum, a URL or a command: ask, or leave a `TODO` comment and say so.

## Tagging a version

`fignimble tag NAME -m "..."` records exports/ and the lock, checks them, commits them, and tags the commit
`fignimble/NAME`. It refuses while other files of the project aren't committed or the check finds problems: fix
them rather than passing `--force`, unless the user asks. Tell the user to push the tag
(`git push origin fignimble/NAME`).
