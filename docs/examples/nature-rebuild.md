# Rebuilding a Nature figure

To try Fignimble on a large, real figure, we rebuilt Extended Data Fig. 5 of

> Cao, M., Ammari, R., Chen, M. X., Wai, P., Jamieson, B. B., Liang, S., Husain, B. F. A., Sahni, A., Legrave, N.,
> Salgarella, I., MacRae, J., Strom, M. & Kohl, J. Integration of hunger and hormonal state gates infant-directed
> aggression. *Nature* **648**, 138–145 (2025). [doi:10.1038/s41586-025-09651-2](https://doi.org/10.1038/s41586-025-09651-2)

from the authors' published code and data. The figure has **37 panels, lettered a to ak**: patch-clamp recordings of
MPOA neurons, mostly box plots, bar charts and current-injection curves, and three schematics.

<img class="fg-shot" src="../../assets/kohl2025-ed5-compare.jpg" alt="Extended Data Fig. 5 as published (left) and rebuilt in Fignimble (right)">

The project is in the repository as
[`examples/kohl2025-ed5`](https://github.com/pachterlab/Fignimble/tree/main/examples/kohl2025-ed5), with the
authors' data. Open `ed5.py` in Fignimble; it offers to install pandas, seaborn and statsmodels, which the panels
use. [Download the rebuilt figure as PDF](../assets/kohl2025-ed5-rebuilt.pdf).

## How the figure was made, and how it is made now

The authors' code is two Jupyter notebooks in
[FrancisCrickInstitute/negative_parental_switch](https://github.com/FrancisCrickInstitute/negative_parental_switch)
(`NPS_ephys/ephys_analysis.ipynb` and `resampled_ephys.ipynb`). Each notebook cell draws one plot and saves it as
its own PDF into folders named `linked_figure_S5` and so on, and the figure was then assembled by hand from those
files.

In the rebuild, each of those cells became a panel function, and the figure is one file that places them:

```python
fig = Figure(width=183, height=228, style=STYLE)                 # Nature's full page width

PH = "panels/pharmacology.py"
fig.panel(PH, x=2, y=1, w=44, h=33, function="silent")           # a
fig.panel(PH, x=47, y=1, w=42, h=33, function="vm")              # b
fig.panel(PH, x=90, y=1, w=44, h=33, function="ri")              # c
...
fig.rect(3, 75, 11.5, 26, stroke=None, fill="#EBEBEB", label="auto")    # j, a schematic
fig.image("images/mouse.svg", x=4, y=84, w=9.5, label=None, flip="h")
```

A panel function is the notebook cell, drawing into the panel it is given instead of a new figure, with its
statistics recomputed by the test the caption names:

```python
def silent(fig):
    """a: % silent at rest, Pre to KD-Y (Fisher's exact test, Benjamini-Hochberg)."""
    df = ephys()
    df = df[df.ID_mouse != "JKAJ37.6g"]                 # left out in the notebook's silent-proportion cells
    ax = fig.add_subplot()
    bars(ax, SIX_GROUPS, silent_percent(df, "Behaviour_6hFD", SIX_GROUPS), SIX, SIX_LABELS, ylim=(0, 100),
         nbins=2, ylabel="Silent neurons (%)")
    p = family_p(df.dropna(subset=["I_zero_reclassified"]), "I_zero_reclassified", "Behaviour_6hFD", "fisher")
    bracket(ax, 0, 3, 108, stars(p[("Pre", "ZD7288")]))
    bracket(ax, 0, 4, 122, stars(p[("Pre", "npy_100")]))
```

Fignimble letters the panels a to ak, draws every panel at its size in Nature's style (Arial, 7 pt), lines up their
plot areas, and redraws a panel when its code or data change. The mixed-model statistics of the current-injection
panels are kept with [`@cached`](../guide/panels.md#sharing-slow-work-between-panels), so the whole figure renders
in about ten seconds.

## What reproduced and what didn't

Of the 34 data panels, **18 reproduced from the authors' code as it is**, apart from moving it into panel
functions. **7 needed a small fix**: cells that read variables left over from earlier cells, a unit conversion, and
cells without a measurement left out. **5 had no plotting code** in the repository and were drawn the same way as
their neighbors. **1 crashed** in the notebook. **3 differ** from the published figure, because of problems in the
published version. The number of cells in each group matches the caption for every panel we checked (a, k–w, y and
aa–aj).

| Panel | Result | Notes |
|---|---|---|
| a–e, g, i | reproduced | Stars match once Benjamini–Hochberg is applied within each comparison family the caption lists. |
| f, h | no code | No cells: drawn as g is, with membrane potential and input resistance. |
| j, q, x | redrawn | Schematics, drawn in Fignimble with BioCartoon icons. |
| k–n, p, r–u, w | reproduced | The stratified resampling is reproduced exactly: 108 Pre and 24 Post cells, as in the caption. |
| o, v | differ | One cell is classed `slent` in the data, a typo for *silent*. The published bars count it as not silent (11/27, 5/12); fixed, they are 12/27 and 6/12. |
| y, aa, ab | no code | The notebook drew a property only when its ANOVA was significant. |
| z | differs | The published bars come from a later data table than the one the caption's cell counts describe. Drawn from the later table, to match the figure. |
| ac | small fix | Matches the published bars when cells without a sag measurement are left out. |
| ad | crashed | The cell stops with `NameError: name 'corr' is not defined`. With the missing table built, the plot matches; its regression gives p = 0.004 and R² = 0.99, where the caption reports P = 0.006 and R² = 0.035. |
| ae–ah, aj | small fix | The cells read variables left over from earlier cells. |
| ai | small fix | The figure gives seconds, the data milliseconds. |
| ak | reproduced | |

The [project's README](https://github.com/pachterlab/Fignimble/tree/main/examples/kohl2025-ed5) has the details.

## Checking that it still reproduces

The project keeps what it shows next to its code: `exports/ed5.pdf`, one CSV per panel in `exports/data/ed5/`
(`a.csv` holds the bars of panel a and the brackets above them), and `fignimble.lock`, the hashes of what each was
made from. Its `fignimble.toml` says the one thing the code can't: the data were copied from the authors' repository
and the schematics' icons came from BioCartoon.

```console
$ cd examples/kohl2025-ed5
$ fignimble check --fast
reproducibility (exports/, fignimble.lock, fignimble.toml):
    current    exports/ed5.pdf
  · manual     data/estrous_data.csv  copied from NPS_ephys in FrancisCrickInstitute/negative_parental_switch ...
  · manual     images/mouse.svg  SciDraw icons from BioCartoon: ...
  35 current, 7 manual
```

Change a panel's statistics or the data, and `fignimble check` names the exports that are out of date;
`fignimble diff` against a version says which panels' values moved, by how much, and why. See
[Reproducibility](../guide/reproducibility.md).

## What this says about figures

The code was good: it ran end to end with today's pandas, seaborn and statsmodels after very few changes. The
problems are the ones that assembling panels by hand allows: panels whose code was never committed, a cell that only
ran with state left over from deleted or reordered cells, a figure drawn from a different table than its caption
describes, and a typo that changed two bars. When the figure is a program that draws every panel from the data, each
of these becomes an error or a visible line of code.

Rebuilding the figure also improved Fignimble: version 0.3.0's handling of packages in `requirements.txt`,
`@cached`, mirrored vector pictures, SVGs styled with CSS, letters on schematics and the preflight exemption for
superscripts all came from it.

## Credit and license

The article and its figures are licensed under the
[Creative Commons Attribution 4.0 International License](https://creativecommons.org/licenses/by/4.0/), © The
Author(s) 2025. The published figure on this page is Extended Data Fig. 5 of the article, unchanged. The rebuild
adapts it, using the authors' code and data in `NPS_ephys` of
[FrancisCrickInstitute/negative_parental_switch](https://github.com/FrancisCrickInstitute/negative_parental_switch):
the notebook cells were rewritten as Fignimble panel functions, the figure was assembled in Fignimble, the schematics
were redrawn, and the fixes above were applied. The schematics use "C57 MOUSE" by Long Li (SciDraw) and
"Rat pup P1" by Antonis Asiminas (SciDraw), both CC BY 4.0, from BioCartoon.
