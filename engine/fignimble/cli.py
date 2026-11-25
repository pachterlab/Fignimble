"""The fignimble command: render, check and export figures from a terminal (and let a coding agent see its work).

    fignimble new myproject [--journal nature]     a new project: style.py and an empty fig1.py
                                                   (--poster a0, 48x36, ...: a poster.py to start from)
    fignimble snapshot fig1.py [--dpi 150]         render to .fignimble/snapshots/fig1.png and print the path
    fignimble info fig1.py [--json]                elements, boxes (mm), labels and preflight warnings
    fignimble check [fig1.py ...]                  preflight warnings of every figure; exit status 1 if any
    fignimble table fig1.py 0 table1.docx          a table of a figure (its element number or name) as a Word
                                                   file; or fignimble table data.csv table1.docx
    fignimble export fig1.py fig1.pdf [--dpi 600]  PDF, SVG, EPS, PNG, TIFF, JPEG or PPTX (--cmyk, --outline for
                                                   print; --journal cell: its resolutions, else the style's
                                                   journal's; --bleed 3 --crop-marks for a print shop's PDF)
    (snapshot and export take --dark: the figure's dark version, for talks)
    fignimble provenance fig1.pdf                  what an export was made from, and whether the project still
                                                   has those files as they were
    fignimble journals                             the journal presets and their column widths, and poster sizes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _project_of(fig: Path) -> Path:
    """The project of a figure file: the folder holding it (and style.py, panels/ ...)."""
    return fig.resolve().parent


def _figures(project: Path) -> list[Path]:
    from .render import is_figure_source
    return [p for p in sorted(project.glob("*.py")) if is_figure_source(p.read_text(encoding="utf-8", errors="replace"))]


def _renderer(fig: Path):
    from .render import Renderer
    return Renderer(str(_project_of(fig)))


def main(argv=None):
    if os.name == "nt":                                  # a Windows code page can't print every character (▸, µ, ≥)
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
    ap = argparse.ArgumentParser(prog="fignimble", description="Multi-panel figures for publication, from Python.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="make a new project")
    p.add_argument("folder")
    p.add_argument("--journal", default="nature", help="a journal key from `fignimble journals` (nature, cell, bioinformatics, plos-computational-biology, ...), or none")
    p.add_argument("--poster", help="make a poster instead: a template from `fignimble journals` (a0, 48x36, 72x48, 24x36, ...)")
    p.add_argument("--landscape", action="store_true", help="the poster wider than tall")
    p = sub.add_parser("snapshot", help="render a figure to PNG and print where")
    p.add_argument("figure")
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--output", "-o")
    p.add_argument("--dark", action="store_true", help="the figure's dark version")
    p = sub.add_parser("info", help="describe a figure: elements, labels, warnings")
    p.add_argument("figure")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("check", help="preflight check figures")
    p.add_argument("figures", nargs="*")
    p = sub.add_parser("export", help="export a figure")
    p.add_argument("figure")
    p.add_argument("output")
    p.add_argument("--dpi", type=int, default=600)
    p.add_argument("--dark", action="store_true", help="the figure's dark version")
    p.add_argument("--cmyk", action="store_true", help="CMYK colors for print (PDF, EPS, TIFF, JPEG)")
    p.add_argument("--outline", action="store_true", help="text as shapes (PDF, EPS, SVG)")
    p.add_argument("--journal", help="resolution rules of nature, science, cell, pnas, elife (default: the style's)")
    p.add_argument("--bleed", type=float, default=0, help="mm of bleed past each edge (PDF)")
    p.add_argument("--crop-marks", action="store_true", help="crop marks outside the figure (PDF)")
    p = sub.add_parser("provenance", help="what an exported file was made from, checked against the project")
    p.add_argument("file")
    p.add_argument("--project", help="the project folder (default: the current folder)")
    p = sub.add_parser("table", help="write a table as a Word file (.docx)")
    p.add_argument("source", help="a figure file (then the table's element number or name) or a .csv/.tsv file")
    p.add_argument("args", nargs="+", help="[element] output.docx")
    p.add_argument("--decimals", type=int)
    p.add_argument("--no-header", action="store_true")
    p.add_argument("--title")
    sub.add_parser("journals", help="list journal presets")
    a = ap.parse_args(argv)

    if a.cmd == "new":
        from .templates import new_project
        r = new_project(a.folder, None if a.journal == "none" else a.journal, poster=a.poster, landscape=a.landscape)
        print(f"made {r['project']} ({'poster.py, a poster to start from' if a.poster else 'style.py and an empty fig1.py'})")
        return 0
    if a.cmd == "provenance":
        return _provenance(a.file, Path(a.project) if a.project else Path.cwd())
    if a.cmd == "journals":
        from . import journals
        from .journals import JOURNALS
        checked = {"guidelines": "checked against its guidelines", "secondary": "from summaries of its guidelines",
                   "publisher": "the publisher's usual values, not checked"}
        publisher = None
        for k, j in JOURNALS.items():
            if j["publisher"] != publisher:
                publisher = j["publisher"]
                print(f"\n{publisher}")
            ws = ", ".join(f"{n} {w:g} mm" for n, w in j["widths"].items())
            lab = j["labels"]
            d = j["dpi"]
            print(f"  {k:34} {j['name']}: {ws}; {j['font']} {j['font_size']} pt (min {j['min_font']}); labels "
                  f"{lab['weight']} {lab['case']}case {lab['size']} pt; {d['photo']}/{d['combination']}/{d['line']} dpi "
                  f"(photo/combination/line art); {checked.get(j.get('checked'), '')}")
        print("\nJournals revise their rules: check before submitting (fignimble/journals.py has the sources).")
        print("\nPosters (Figure(poster=...), Style.poster(...); landscape=True turns them)")
        for k, pr in journals.POSTERS.items():
            w, h = pr["size"]
            print(f"  {k:8} {pr['name']:12} {w:g} x {h:g} mm; {pr['body']} pt text (min {pr['min_font']}), "
                  f"title {pr['title']} pt, margin {pr['margin']} mm")
        print("\nPoster templates (fignimble new --poster KEY; check your conference's instructions)")
        for t in journals.POSTER_TEMPLATES:
            print(f"  {t['key']:13} {t['name']:22} {t['use']}")
        d = journals.POSTER_DPI
        print(f"  pictures {d['photo']} dpi, rasters exported at {d['line']} dpi at most")
        return 0
    if a.cmd == "snapshot":
        fig = Path(a.figure)
        r = _renderer(fig)
        out = Path(a.output) if a.output else r.cache / "snapshots" / f"{fig.stem}{'.dark' if a.dark else ''}.png"
        res = r.export(str(fig.resolve()), str(out.resolve()), "png", dpi=a.dpi, dark=a.dark)
        print(res["path"])
        for w in res["warnings"]:
            print("warning:", w, file=sys.stderr)
        return 0
    if a.cmd == "export":
        fig = Path(a.figure)
        res = _renderer(fig).export(str(fig.resolve()), str(Path(a.output).resolve()), dpi=a.dpi, dark=a.dark,
                                    cmyk=a.cmyk, outline=a.outline, journal=a.journal, bleed=a.bleed,
                                    marks=a.crop_marks)
        print(f"wrote {res['path']}")
        rz = res["resolution"]
        for n in rz.get("notes", []):
            print("note:", n)
        if rz.get("dpi") and "kind" not in rz:            # pptx: the slide's picture, not checked against a journal
            print(f"resolution: {rz['dpi']} dpi")
        elif rz.get("dpi"):
            print(f"resolution: {rz['dpi']} dpi ({rz['kind']} figure; {rz['need'][rz['kind']]} dpi asked)")
        elif rz.get("pictures"):
            print(f"resolution: vector, pictures at {rz['pictures']} dpi or more ({rz['need']['photo']} dpi asked)")
        for w in res["warnings"]:
            print("warning:", w, file=sys.stderr)
        return 0
    if a.cmd == "table":
        from . import tables
        src = Path(a.source)
        out = a.args[-1]
        if src.suffix.lower() == ".py":
            if len(a.args) != 2:
                print("give the table's element number (as fignimble info shows it) or name, then the .docx")
                return 2
            res = _renderer(src).render(str(src.resolve()), sprites=False)
            if res.get("error"):
                print(res["error"])
                return 1
            want = a.args[0].lstrip("#")
            e = next((e for e in res["elements"] if e["kind"] == "table" and want in (e["oid"], e["props"].get("name"))),
                     None)
            if e is None:
                print(f"{src.name} has no table {a.args[0]}")
                return 1
            p = e["props"]
            rows = p["rows"] if p.get("rows") is not None else tables.read_rows(src.parent / p["source"])
            st = res["style"]["props"]
            path = tables.write_docx(rows, out, header=p.get("header", True), decimals=p.get("decimals"),
                                     align=p.get("align"), font=p.get("font") or st.get("font") or "Arial",
                                     size=p.get("size") or st.get("size") or 7, title=a.title,
                                     grid=bool(p.get("grid")))
        else:
            path = tables.write_docx(tables.read_rows(src), out, header=not a.no_header, decimals=a.decimals,
                                     title=a.title)
        print(f"wrote {path}")
        return 0
    if a.cmd == "info":
        fig = Path(a.figure)
        res = _renderer(fig).render(str(fig.resolve()), sprites=False)
        if a.json:
            print(json.dumps(res, indent=1))
            return 0
        if res.get("error"):
            print(res["error"])
            return 1
        _describe(res)
        return 0
    if a.cmd == "check":
        figs = [Path(f) for f in a.figures] or _figures(Path.cwd())
        bad = 0
        for fig in figs:
            res = _renderer(fig).render(str(fig.resolve()), sprites=False)
            if res.get("error"):
                print(f"{fig.name}: does not run\n{res['error']}")
                bad += 1
                continue
            ws = res["warnings"]
            print(f"{fig.name}: {'ok' if not ws else ''}")
            for w in ws:
                print(f"  {w['level']}: {w['message']}")
            bad += sum(1 for w in ws if w["level"] != "info")
        return 1 if bad else 0
    return 2


def _provenance(file: str, project: Path) -> int:
    """Print what an export says it was made from, and whether the project's files still match."""
    import hashlib
    import subprocess
    from .render import provenance_line, read_provenance
    prov = read_provenance(file)
    if prov is None:
        print(f"{file} has no Fignimble provenance (it was exported before Fignimble recorded it, or another "
              "program saved it since)")
        return 1
    print(provenance_line(prov))
    if prov.get("uncommitted"):
        print("  files that differed from that commit: " + ", ".join(prov["uncommitted"][:10]))
    changed = 0
    for rel, h in prov.get("files", {}).items():
        f = project / rel
        try:
            now = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
        except OSError:
            now = None
        state = "same" if now == h else ("missing" if now is None else "changed")
        changed += state != "same"
        print(f"  {state:8} {rel}")
    if prov.get("git"):
        try:
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True).stdout.strip()
            if head and head != prov["git"]:
                print(f"  the project is now at git {head[:10]}")
        except OSError:
            pass
    print("matches the project" if not changed else
          f"{changed} file{'s differ' if changed != 1 else ' differs'} from what the export was made from")
    return 0 if not changed else 1


def _describe(res: dict):
    W, H = res["size"]
    j, pr = res.get("journal"), res.get("poster")
    print(f"{Path(res['file']).name}: {W:g} x {H:g} mm" + (f" ({j['name']})" if j else "")
          + (f" ({pr['name']} poster{', landscape' if pr['landscape'] else ''})" if pr else ""))
    lp = res["labelStyle"]["props"]
    print(f"labels: format {lp['format']!r}, {lp['case']}case, {lp['size']} pt {lp['weight']}, offset "
          f"({lp['dx']}, {lp['dy']}) mm, order {lp['order']}")
    labels = {l["panel"]: l["text"] for l in res["labels"]}
    print("elements (drawing order; x, y, w, h in mm):")
    for e in res["elements"]:
        x, y, w, h = e["box"]
        p = e["props"]
        if e["kind"] == "inset":
            p = e["item"]
        what = {"panel": lambda: f"{p['source']}" + (f":{p['function']}" if p.get("function") != "draw" else ""),
                "inset": lambda: f"{p['source']}" + (f":{p['function']}" if p.get("function", "draw") != "draw" else "")
                + f" in #{e.get('host')}",
                "layout": lambda: f"{len(e.get('children', []))} parts, gap {p['gap']} mm"
                + (", legends collected" if p.get("guides") == "collect" else ""),
                "image": lambda: p["source"], "text": lambda: repr(p["text"]) if not p.get("source") else p["source"],
                "figure": lambda: f"{p['source']}, scaled {e.get('scale', 1):g}" + ("" if p.get("letters", True)
                                                                                   else ", no letters"),
                "qr": lambda: repr(p["data"]),
                "bracket": lambda: repr(p["text"])}.get(e["kind"], lambda: "")()
        lab = f" [{labels[e['oid']]}]" if e["oid"] in labels else ""
        line = e.get("site", {}).get("line")
        print(f"  #{e['oid']:<3} {e['kind']:8}{lab:5} {x:7.1f} {y:7.1f} {w:7.1f} {h:7.1f}  {what}  (line {line})")
        if e.get("error"):
            print("       ERROR " + e["error"].strip().replace("\n", "\n       "))
    said = [(labels.get(e["oid"], "#" + e["oid"]), m) for e in res["elements"] for m in e.get("pyWarnings") or []]
    if said:
        print("python warnings from panel code:")
        for who, m in said:
            print(f"  panel {who}: {m}")
    if res["warnings"]:
        print("preflight:")
        for w in res["warnings"]:
            print(f"  {w['level']}: {w['message']}")
    else:
        print("preflight: no problems")


if __name__ == "__main__":
    sys.exit(main())
