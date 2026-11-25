---
title: Fignimble
hide:
  - navigation
  - toc
---

<div class="fg-intro" markdown>
<div class="fg-hero" markdown>
<img src="assets/fignimble-icon.svg" alt="Fignimble icon">
# Fignimble
</div>

An editor for multi-panel scientific figures and conference posters, the part of Illustrator scientists actually
use, with Python underneath everything. Arrange panels with the mouse, write them in matplotlib, or ask an AI coding
agent to build them, all in the same figure. Then put the paper's figures on a poster that stays up to date with
their code.
{ .fg-lead }

<div class="fg-buttons" markdown>
[Download for macOS](https://github.com/pachterlab/Fignimble/releases/latest){ .md-button .md-button--primary }
[Install on Windows or Linux](getting-started/installation.md){ .md-button }
[Manual](getting-started/introduction.md){ .md-button }
[View on GitHub](https://github.com/pachterlab/Fignimble){ .md-button }
</div>
</div>

<img class="fg-shot" src="assets/editor.jpg" alt="The Fignimble window: figures and layers on the left, the figure in the middle, the inspector on the right and the code below">

## What Fignimble does

Fignimble looks and works like a drawing app: figures and layers on the left, the figure in the middle, an inspector
on the right. You drag a panel to move it, pull its corner to resize it, click an axis label to retype it, and align
a row of panels with one click. Underneath, every figure is a short Python file that places panels, pictures, text and
arrows, and every panel is drawn by matplotlib code. Each change you make on the canvas is written back into that
code as a small, readable edit.

Because the figure is code, it can do what a drawing can't. Panels redraw at their real size, so fonts and line
widths stay true. Panel letters are added automatically and kept in reading order. One style sets the font and sizes
of every panel, and a journal preset sets the widths, label style and resolution the journal asks for. Before you
export, a preflight check finds small text, low-resolution pictures and colors color-blind readers can't tell
apart. A figure can be placed whole in another, which makes [posters](guide/posters.md) built from the paper's own
figures, kept up to date by their code: templates for the usual conference sizes, text boxes with bullets and
headings, QR codes, and PDFs with bleed and crop marks for the print shop. Every exported file records the code and
data it was made from. And because the code stays clean, you can open a terminal in the app, start Claude Code, Codex or Gemini CLI,
and have the agent write panels while you watch the figure update. When the data are on a Linux server, Fignimble
can [open the project there](guide/remote.md): the server draws the panels and your computer shows them.

Fignimble runs on macOS, Windows and Linux. On a Mac it is a native app; on Windows and Linux it is a Qt editor
around the same engine, so a project opens, renders and exports the same on all three
([how they differ](getting-started/windows-linux.md)).

<div class="grid cards" markdown>

-   :material-cursor-default-click-outline:{ .lg .middle } **Edit on the canvas**

    ---

    Move, resize, align and distribute panels, pictures and annotations with smart guides. Retype and restyle the
    texts inside a matplotlib panel, and recolor its lines and bars, without opening its code.

    [:octicons-arrow-right-24: The Fignimble window](guide/window.md)

-   :material-language-python:{ .lg .middle } **Panels are Python**

    ---

    Each panel is a `draw(fig)` function, with pandas, seaborn or anything else you use. Lay panels out by hand or
    patchwork-style, and their plot areas line up by themselves.

    [:octicons-arrow-right-24: Panels and their code](guide/panels.md)

-   :material-robot-outline:{ .lg .middle } **Build figures with an AI agent**

    ---

    Run an AI coding agent in Fignimble's terminal. It is told how the project works and can look at its own
    renders; its edits appear within a second, side by side with yours.

    [:octicons-arrow-right-24: AI agents and the terminal](ai/agents.md)

-   :material-file-check-outline:{ .lg .middle } **Ready for the journal**

    ---

    40 journal presets set widths, fonts, panel labels and resolutions. Preflight checks the figure; export writes
    PDF, EPS, SVG, TIFF, PNG, JPEG or PowerPoint that meet the journal's rules, and tables as Word or Excel.

    [:octicons-arrow-right-24: Preflight and export](guide/export.md)

-   :material-presentation:{ .lg .middle } **Posters from the paper's figures**

    ---

    Templates for A0, 48 × 36 in and the other usual conference sizes. Place the paper's figures whole, drawn from
    their code; write the text in Markdown; add a QR code; export with bleed and crop marks, or as PowerPoint.

    [:octicons-arrow-right-24: Posters](guide/posters.md)

-   :material-source-commit:{ .lg .middle } **Every file knows where it came from**

    ---

    Exported PDFs, PNGs, TIFFs and SVGs record the git commit and a fingerprint of every file they were made from.
    `fignimble provenance` tells you whether a figure still matches its code and data.

    [:octicons-arrow-right-24: Provenance](guide/export.md#provenance)

</div>

## Get started

1. [Install Fignimble](getting-started/installation.md): on a Mac, download the disk image and drag the app to
   Applications (the first time it opens, it sets up the Python it draws with); on Windows or Linux, run the
   installer script from the source.
2. Follow the [quick start](getting-started/quick-start.md) to make a figure with two panels, letter it, style it
   for a journal and export it.
3. Read [key concepts](getting-started/concepts.md) to see how figures, panels and the canvas fit together.
4. See [a 37-panel Nature figure rebuilt](examples/nature-rebuild.md) from its authors' code and data, or
   [make a poster](guide/posters.md) from a paper's figures.

Fignimble is free and open source, from the [Pachter Lab](https://pachterlab.github.io) at Caltech. It is built on
[Manimble](https://pachterlab.github.io/Manimble/), the slide editor for Manim presentations, and shares its engine
design, its undo that coexists with an agent's edits, and its terminal.
