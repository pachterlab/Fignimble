"""The fignimble command: render, check and export figures from a terminal (and let a coding agent see its work).

    fignimble new [folder] [--journal nature]      a new project (folder: fignimble): its fignimble.toml marker,
                                                   style.py and an empty fig1.py (--poster a0, 48x36, ...: a
                                                   poster.py to start from)
    fignimble snapshot fig1.py [--dpi 150]         render to .fignimble/snapshots/fig1.png and print the path
    fignimble info fig1.py [--json]                elements, boxes (mm), labels and preflight warnings
    fignimble check [fig1.py ...]                  preflight warnings of every figure of the project this folder
                                                   is in; exit status 1 if any
    fignimble table fig1.py 0 table1.docx          a table of a figure (its element number or name) as a Word
                                                   file; or fignimble table data.csv table1.docx
    fignimble export fig1.py fig1.pdf [--dpi 600]  PDF, SVG, EPS, PNG, TIFF, JPEG or PPTX (--cmyk, --outline for
                                                   print; --journal cell: its resolutions, else the style's
                                                   journal's; --bleed 3 --crop-marks for a print shop's PDF)
    (snapshot and export take --dark: the figure's dark version, for talks)
    fignimble provenance fig1.pdf                  what an export was made from, and whether the project still
                                                   has those files as they were
    fignimble journals                             the journal presets and their column widths, and poster sizes

Reproducibility (fignimble.toml's recipes, exports/ and fignimble.lock; see docs/guide/reproducibility.md):
    fignimble record [--force]                     write exports/ (a PDF per figure, a CSV per table and per panel's
                                                   values) and fignimble.lock, as the app does when the project changes
    fignimble check [--json]                       also: exports/ and recipe outputs against the lock
    fignimble reproduce [--check] [--budget 1h]    run the recipes again and compare what they make (--check puts the
                                                   project's files back afterwards); --yes to run without asking
    fignimble tag NAME [-m MESSAGE]                name this version (a git tag, fignimble/NAME); fignimble tags lists
    fignimble diff [A] [B] [--json]                compare version A (default: the newest) with B (default: the
                                                   project now): recipes, values shown, and how figures look
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True          # nothing written into the app bundle, whose signature it would break


def _project_of(fig: Path) -> Path:
    """The project of a figure file: the folder holding it (and style.py, panels/ ...)."""
    return fig.resolve().parent


def _here() -> Path:
    """The project the current folder is in (the nearest fignimble.toml at or above it), else the current folder."""
    from .templates import find_project
    return find_project(Path.cwd()) or Path.cwd()


def _figures(project: Path) -> list[Path]:
    from .render import is_figure_source
    return [p for p in sorted(project.glob("*.py")) if is_figure_source(p.read_text(encoding="utf-8", errors="replace"))]


def _renderer(fig: Path):
    from .render import Renderer
    return Renderer(str(_project_of(fig)))


def main(argv=None):
    from . import __version__
    ap = argparse.ArgumentParser(prog="fignimble", description="Multi-panel figures for publication, from Python.")
    ap.add_argument("--version", action="version", version=f"fignimble {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("new", help="make a new project")
    p.add_argument("folder", nargs="?", default="fignimble", help="the new project's folder (default: fignimble)")
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
    p = sub.add_parser("check", help="preflight check figures, and exports/ and recipe outputs against the lock")
    p.add_argument("figures", nargs="*")
    p.add_argument("--json", action="store_true", help="the results as JSON")
    p.add_argument("--fast", action="store_true", help="draw nothing: preflight skipped, exports judged by the files "
                                                     "they were made from")
    p = sub.add_parser("record", help="write exports/ and fignimble.lock (as the app does when the project changes)")
    p.add_argument("--force", action="store_true", help="write every export again, even those that look the same")
    p.add_argument("--no-data", action="store_true", help="without the values each panel plots")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("reproduce", help="run the recipes in fignimble.toml again and compare what they make")
    p.add_argument("recipes", nargs="*", help="only these recipes (names, or output file names)")
    p.add_argument("--check", action="store_true",
                   help="put the project's files back afterwards (what was made is kept in .fignimble/reproduce/)")
    p.add_argument("--budget", help="how long it may all take (30min, 2h): recipes that say they take longer than "
                                    "what is left are skipped")
    p.add_argument("--no-fetch", action="store_true", help="don't download missing inputs")
    p.add_argument("--yes", "-y", action="store_true", help="run the commands without asking")
    p.add_argument("--plan", action="store_true", help="only print what would run")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("tag", help="name this version of the project (a git tag, fignimble/NAME)")
    p.add_argument("name")
    p.add_argument("-m", "--message")
    p.add_argument("--force", action="store_true", help="tag even with uncommitted files or problems the check finds")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("tags", help="the project's versions")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("diff", help="compare two versions of the project, or a version with the project now")
    p.add_argument("a", nargs="?", help="a version (default: the newest), or any git commit")
    p.add_argument("b", nargs="?", help="another version (default: the project now)")
    p.add_argument("--no-record", action="store_true", help="compare exports/ as they are, without writing them first")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("export", help="export a figure or a table (a table also as .docx, .xlsx, .csv or .tsv)")
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
    p.add_argument("--project", help="the project folder (default: the project the current folder is in)")
    p = sub.add_parser("table", help="write a table as a Word, Excel, CSV or TSV file")
    p.add_argument("source", help="a table file (table1.py), a figure file (then the table's element number or "
                                  "name, if it has more than one) or a .csv/.tsv file")
    p.add_argument("args", nargs="+", help="[element] output (.docx, .xlsx, .csv or .tsv)")
    p.add_argument("--decimals", type=int)
    p.add_argument("--no-header", action="store_true")
    p.add_argument("--title")
    p = sub.add_parser("source-data", help="write the values plotted in each panel to a spreadsheet (.xlsx)")
    p.add_argument("figure")
    p.add_argument("output", nargs="?", help="default: <figure>_source_data.xlsx")
    sub.add_parser("journals", help="list journal presets")
    p = sub.add_parser("install-skills", help="install Fignimble's agent skills for Claude Code, Codex or another agent",
                       description="Install the agent skills that come with Fignimble, so a coding agent knows how to "
                                   "make, check and export figures and posters with it. Run it again after updating "
                                   "Fignimble to update them.")
    p.add_argument("names", nargs="*", metavar="SKILL",
                   help="only these skills, e.g. fignimble-poster (or just poster); default: all")
    p.add_argument("--agent", choices=("claude", "codex"), default="claude",
                   help="claude: ~/.claude/skills (default); codex: ~/.codex/skills")
    where = p.add_mutually_exclusive_group()
    where.add_argument("--project", nargs="?", const=".", metavar="DIR",
                       help="into the project at DIR (default: this folder) instead of your own skills folder")
    where.add_argument("--dest", metavar="DIR", help="into this skills folder, for another agent")
    p.add_argument("--overwrite", action="store_true",
                   help="replace a skill folder of the same name that this command didn't install")
    act = p.add_mutually_exclusive_group()
    act.add_argument("--list", action="store_true", help="list the skills and whether each is installed")
    act.add_argument("--uninstall", action="store_true", help="remove the skills this command installed")
    a = ap.parse_args(argv)

    if a.cmd == "install-skills":
        from . import skills
        return skills.main(a.names, a.agent, a.project, a.dest, a.overwrite, a.list, a.uninstall)

    if a.cmd == "new":
        from .templates import new_project
        r = new_project(a.folder, None if a.journal == "none" else a.journal, poster=a.poster, landscape=a.landscape)
        print(f"made {r['project']} (fignimble.toml, "
              f"{'poster.py, a poster to start from' if a.poster else 'style.py and an empty fig1.py'})")
        return 0
    if a.cmd == "provenance":
        return _provenance(a.file, Path(a.project) if a.project else _here())
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
    if a.cmd == "export" and Path(a.output).suffix.lower().lstrip(".") in ("docx", "xlsx", "csv", "tsv"):
        return main(["table", a.figure, a.output])            # a table, as a file people edit
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
    if a.cmd == "source-data":
        from . import sourcedata
        fig = Path(a.figure)
        out = a.output or f"{fig.stem}_source_data.xlsx"
        try:
            res = sourcedata.export(_renderer(fig), str(fig.resolve()), str(Path(out).resolve()))
        except ValueError as exc:
            print(exc)
            return 1
        print(f"wrote {res['path']}")
        for p_ in res["panels"]:
            print(f"  {p_['sheet']}: {p_['blocks']} table{'s' if p_['blocks'] != 1 else ''}, {p_['how']}")
        return 0
    if a.cmd == "table":
        from . import tables
        src = Path(a.source)
        out = a.args[-1]
        if src.suffix.lower() == ".py":
            if len(a.args) > 2:
                print("give the table's element number (as fignimble info shows it) or name, then the file")
                return 2
            res = _renderer(src).render(str(src.resolve()), sprites=False)
            if res.get("error"):
                print(res["error"])
                return 1
            try:
                path = tables.write_rendered(res, out, oid=a.args[0].lstrip("#") if len(a.args) == 2 else None,
                                             title=a.title)
            except ValueError as exc:
                print(f"{src.name}: {exc}")
                return 1
        else:
            path = tables.write_table(tables.read_rows(src), out, header=not a.no_header, decimals=a.decimals,
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
        return _check(a)
    if a.cmd in ("record", "reproduce", "tag", "tags", "diff"):
        from . import repro
        try:
            return globals()[f"_{a.cmd}"](a)
        except repro.GitError as exc:
            if getattr(a, "json", False):
                print(json.dumps({"ok": False, "error": str(exc)}))
            else:
                print(exc, file=sys.stderr)
            return 1
    return 2


def _project_renderer(project: Path):
    from .render import Renderer
    return Renderer(str(project))


def _check(a) -> int:
    """Preflight of each figure, then (in a project with a marker) the reproducibility check."""
    from . import repro
    from .templates import MARKER
    project = _here()
    figs = [Path(f) for f in a.figures] or ([] if a.fast else _figures(project))
    bad = 0
    pre = {}
    for fig in figs:
        res = _renderer(fig).render(str(fig.resolve()), sprites=False)
        if res.get("error"):
            pre[fig.name] = {"error": res["error"]}
            if not a.json:
                print(f"{fig.name}: does not run\n{res['error']}")
            bad += 1
            continue
        ws = res["warnings"]
        pre[fig.name] = {"warnings": ws}
        if not a.json:
            print(f"{fig.name}: {'ok' if not ws else ''}")
            for w in ws:
                print(f"  {w['level']}: {w['message']}")
        bad += sum(1 for w in ws if w["level"] != "info")
    rep = None
    if (project / MARKER).is_file() and not a.figures:
        rep = repro.check(project, None if a.fast else _project_renderer(project))
        if not rep["ok"]:
            bad += 1
        if not a.json:
            _print_check(rep)
    if a.json:
        print(json.dumps({"ok": not bad, "preflight": pre, "reproducibility": rep}, indent=1))
    return 1 if bad else 0


ICON = {"ok": " ", "info": "·", "warning": "?", "error": "!"}


def _print_check(rep: dict):
    print("\nreproducibility (exports/, fignimble.lock, fignimble.toml):")
    for p in rep["problems"]:
        print(f"  ! fignimble.toml: {p}")
    order = {"lock": 0, "export": 1, "input": 2, "output": 3, "recipe": 4}
    groups: dict = {}                                  # a figure's panel values: one line, unless they disagree
    for it in rep["items"]:
        if it["kind"] == "export" and it["path"].startswith("exports/data/"):
            groups.setdefault(str(Path(it["path"]).parent), []).append(it)
    for it in sorted(rep["items"], key=lambda it: (order.get(it["kind"], 9), it["path"])):
        folder = str(Path(it["path"]).parent)
        if folder in groups:
            same = len({(g["state"], g["detail"]) for g in groups[folder]}) == 1
            if same:
                if it is groups[folder][0] and it["level"] != "ok":
                    print(f"  {ICON[it['level']]} {it['state']:10} {folder}/ ({len(groups[folder])} files)"
                          + (f"  {it['detail']}" if it["detail"] else ""))
                continue
        print(f"  {ICON[it['level']]} {it['state']:10} {it['path']}" + (f"  {it['detail']}" if it["detail"] else ""))
    s = rep["summary"]
    print("  " + ", ".join(f"{n} {k}" for k, n in sorted(s.items())) + ("" if rep["ok"] else "  (problems)"))


def _record(a) -> int:
    from . import repro
    project = _here()
    res = repro.record(_project_renderer(project), force=a.force, data=False if a.no_data else None)
    if a.json:
        print(json.dumps(res, indent=1))
        return 1 if res["errors"] else 0
    for k in res["written"]:
        print(f"wrote   {k}")
    for k in res["removed"]:
        print(f"removed {k}")
    for f, e in res["errors"].items():
        print(f"error   {f}: {e}", file=sys.stderr)
    print(f"{len(res['written'])} written, {len(res['unchanged'])} unchanged, {len(res['removed'])} removed"
          + ("; fignimble.lock updated" if res["lock"] else "; fignimble.lock unchanged"))
    return 1 if res["errors"] else 0


def _reproduce(a) -> int:
    from . import repro
    project = _here()
    pl = repro.plan(project, a.recipes or None)
    if pl["problems"]:
        for p in pl["problems"]:
            print(f"fignimble.toml: {p}", file=sys.stderr)
        return 1
    if not pl["recipes"]:
        print("fignimble.toml has no recipes" + (" by those names" if a.recipes else ""))
        return 1
    if a.plan or not a.yes:
        if not a.json or a.plan:
            out = []
            for r in pl["recipes"]:
                if r["command"]:
                    line = f"  {r['recipe']}: {' '.join(r['command'])}  (in {r['cwd']})"
                else:
                    line = f"  {r['recipe']}: not run ({r['manual'] or r['where'] or 'no command'})"
                out.append(line)
                for f in r["fetch"]:
                    out.append(f"      downloads {f}")
            if a.json:
                print(json.dumps(pl, indent=1))
            else:
                print("would run:\n" + "\n".join(out))
        if a.plan:
            return 0
        if not sys.stdin.isatty():
            print("pass --yes to run these commands", file=sys.stderr)
            return 2
        if input("run them? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
    log = (lambda m: print(m, file=sys.stderr)) if a.json else print
    res = repro.reproduce(project, check_only=a.check, budget=a.budget, only=a.recipes or None,
                          fetch=not a.no_fetch, log=log)
    if not a.check and not res.get("problems"):
        repro.record(_project_renderer(project))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0 if res["ok"] else 1
    print()
    for r in res["results"]:
        why = f": {r['why']}" if r.get("why") else ""
        print(f"{r['verdict'] + why:22} {r['recipe']}  {r.get('detail', '')}")
        for o in r.get("outputs", []):
            if o["verdict"] != "exact":
                print(f"{'':22}   {o['path']}: {o['verdict']}, {o['detail']}"
                      + (f" (panels {', '.join(o['panels'])})" if o.get("panels") else ""))
    print("\n" + ", ".join(f"{n} {k}" for k, n in sorted(res["summary"].items())))
    if res.get("kept"):
        print(f"what was made, and pictures of differences: {res['kept']}")
    if a.check:
        print("the project's files were put back as they were (--check)")
    return 0 if res["ok"] else 1


def _tag(a) -> int:
    from . import repro
    project = _here()
    res = repro.tag(project, a.name, a.message, a.force, renderer=_project_renderer(project),
                    log=lambda m: print(m, file=sys.stderr))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0
    if res["committed"]:
        print(f"committed exports/ and fignimble.lock ({res['committed'][:10]})")
    print(f"tagged {res['commit'][:10]} as {res['tag']}")
    for b in res["problems"]:
        print(f"  despite: {b['path']}: {b['detail']}")
    if res["uncommitted"]:
        print("  despite uncommitted files: " + ", ".join(res["uncommitted"][:8]))
    print(f"share it with: {res['push']}")
    return 0


def _tags(a) -> int:
    from . import repro
    ts = repro.tags(_here())
    if a.json:
        print(json.dumps(ts, indent=1))
        return 0
    if not ts:
        print("no versions yet: fignimble tag NAME makes one")
    for t in ts:
        print(f"{t['name']:28} {t['date'][:16].replace('T', ' ')}  {t['commit'][:10]}  {t['message']}")
    return 0


def _diff(a) -> int:
    from . import repro
    project = _here()
    res = repro.diff(project, a.a, a.b, renderer=None if (a.no_record or a.b) else _project_renderer(project))
    if a.json:
        print(json.dumps(res, indent=1))
        return 0 if res["same"] else 1
    print(f"{res['a']}  ->  {res['b']}")
    if res["how"]:
        print("\nhow things are made:")
        for h in res["how"]:
            print(f"  {h['change']:8} {h['what']} {h['name']}" + (f"  ({h['detail']})" if h.get("detail") else ""))
    for title, part in (("what is shown (tables, panels' values):", res["shown"]), ("how figures look:", res["looks"])):
        if not part:
            continue
        print("\n" + title)
        for it in part:
            extra = []
            if it.get("panels"):
                extra.append(f"panel{'s' if len(it['panels']) > 1 else ''} {', '.join(it['panels'])}")
            if it.get("because") and it["verdict"] not in ("exact",):
                extra.append("made from changed " + ", ".join(it["because"][:4]))
            if it.get("highlight"):
                extra.append(f"see {it['highlight']}")
            print(f"  {it['verdict']:17} {it['path']}  {it['detail']}" + (f"; {'; '.join(extra)}" if extra else ""))
    print("\n" + ("the same" if res["same"] else "they differ") + ": "
          + ", ".join(f"{n} {k}" for k, n in sorted(res["summary"].items())))
    return 0 if res["same"] else 1


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
    table = (res.get("figureProps") or {}).get("kind") == "table"
    if table:
        print(f"{Path(res['file']).name}: a table, its page {W:g} x {H:g} mm (sized to it)")
    else:
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
                "table": lambda: (f"from {e['fromCode']}()" if e.get("fromCode") else p.get("source") or "rows=")
                + (f", {len(p['cells'])} rows x {max(len(r) for r in p['cells'])} columns" if p.get("cells") else ""),
                "qr": lambda: repr(p["data"]),
                "bracket": lambda: repr(p["text"])}.get(e["kind"], lambda: "")()
        lab = f" [{labels[e['oid']]}]" if e["oid"] in labels else ""
        line = e.get("site", {}).get("line")
        kind = p.get("role", e["kind"]) if e.get("method") == "Table" else e["kind"]
        print(f"  #{e['oid']:<3} {kind:8}{lab:5} {x:7.1f} {y:7.1f} {w:7.1f} {h:7.1f}  {what}  (line {line})")
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
