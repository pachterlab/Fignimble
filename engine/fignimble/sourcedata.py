"""Source data: the values plotted in each panel of a figure, as one spreadsheet, as journals ask for.

Each panel becomes a sheet named after its letter. A panel whose code has a source-data function supplies its own
tables: `source_data(...)` next to `draw(fig, ...)`, or `<name>_source_data(...)` next to a panel function `<name>`,
called with the panel's arguments and returning a pandas DataFrame, a dict of them (one block each), or rows (a list
of lists, the first the header). Otherwise the values are read from what the panel drew: lines, scatter points,
bars with their error bars, error bars, heatmaps (images and meshes), segments and pie wedges. What can't be read
back as numbers (shaded areas, violins, a picture) is said in the sheet. A first sheet says what the file is.
"""
from __future__ import annotations

import datetime as _dt
import math
import runpy
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection, PathCollection, PolyCollection, QuadMesh
from matplotlib.container import BarContainer, ErrorbarContainer
from matplotlib.patches import Wedge

from . import api, tables

MAX_CELLS = 250_000           # a heatmap larger than this is left out (say so), not written cell by cell


# --- reading what a panel drew ---

def _num(v):
    """A cell: a number, a string, or None for missing."""
    if v is None or v is np.ma.masked:
        return None
    if isinstance(v, (np.datetime64,)):
        return str(np.datetime_as_string(v, unit="auto"))
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, (bool, np.bool_)):
        return str(bool(v))
    if isinstance(v, (int, np.integer)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) or math.isinf(v) else float(v)
    return str(v)


def _values(a) -> list:
    if np.ma.isMaskedArray(a):
        a = a.astype(object).filled(None)
    return [_num(v) for v in np.asarray(a, dtype=object).ravel()]


def _categories(axis) -> dict:
    """Names along an axis: a categorical axis's categories (strings plotted with matplotlib), and tick labels that
    aren't numbers at whole-number positions (ax.set_xticks(range(4), ["WT", "KO", ...])): {position: name}."""
    mapping = getattr(getattr(axis, "units", None), "_mapping", None)
    out = {v: k for k, v in mapping.items()} if mapping else {}
    for loc, lab in zip(axis.get_majorticklocs(), axis.get_majorticklabels()):
        t = lab.get_text().strip()
        if t and abs(loc - round(loc)) < 1e-9 and round(loc) not in out:
            try:
                float(t.replace("−", "-").replace(",", ""))
            except ValueError:
                out[round(loc)] = t
    return out


def _named(v, cats):
    if cats and isinstance(v, (int, float, np.number)) and abs(v - round(v)) < 1e-9 and round(v) in cats:
        return cats[round(v)]
    return _num(v)


def _label(artist, fallback: str) -> str:
    lab = artist.get_label() if hasattr(artist, "get_label") else ""
    return fallback if not lab or lab.startswith("_") else lab


def _axes_name(ax, k: int, n: int) -> str:
    title = ax.get_title() or ax.get_title(loc="left") or ""
    if n == 1:
        return title
    return f"axes {k}" + (f" ({title})" if title else "")


def _is_colorbar(ax) -> bool:
    return hasattr(ax, "_colorbar") or ax.get_label() == "<colorbar>"


def extract(fig) -> tuple[list, list[str]]:
    """The values a matplotlib figure shows: ([(title, rows)], notes), rows a header then data."""
    blocks, notes = [], []
    axes = [ax for ax in fig.axes if ax.get_visible() and not _is_colorbar(ax)]
    for k, ax in enumerate(axes, 1):
        where = _axes_name(ax, k, len(axes))
        pre = f"{where}: " if where else ""
        xl, yl = ax.get_xlabel() or "x", ax.get_ylabel() or "y"
        if xl == yl:
            xl, yl = f"{xl} (x)", f"{yl} (y)"
        xc, yc = _categories(ax.xaxis), _categories(ax.yaxis)
        used = set()
        n_line = n_pts = n_bar = 0

        def errs(c: ErrorbarContainer):
            """(y low, y high) and (x low, x high) per point of an errorbar container, None where absent."""
            for part in (c.lines[1] or ()) + (c.lines[2] or ()):
                used.add(id(part))
            if c.lines[0] is not None:
                used.add(id(c.lines[0]))
            ye = xe = None
            for lc in c.lines[2] or ():
                segs = lc.get_segments()
                vertical = all(abs(s[0][0] - s[-1][0]) < 1e-12 for s in segs if len(s))
                pairs = [(min(s[0][1], s[-1][1]), max(s[0][1], s[-1][1])) if vertical
                         else (min(s[0][0], s[-1][0]), max(s[0][0], s[-1][0])) for s in segs if len(s)]
                if vertical and getattr(c, "has_yerr", True):
                    ye = pairs
                elif getattr(c, "has_xerr", False):
                    xe = pairs
            return ye, xe

        for c in ax.containers:
            if isinstance(c, BarContainer):
                n_bar += 1
                for p in c.patches:
                    used.add(id(p))
                horizontal = getattr(c, "orientation", "vertical") == "horizontal"
                ye, xe = errs(c.errorbar) if getattr(c, "errorbar", None) is not None else (None, None)
                e = xe if horizontal else ye
                cat_axis, val_axis = (yl, xl) if horizontal else (xl, yl)
                cats = yc if horizontal else xc
                head = [cat_axis, val_axis]
                bases = [p.get_x() if horizontal else p.get_y() for p in c.patches]
                stacked = any(abs(b) > 1e-12 for b in bases)
                if stacked:
                    head.append("bottom" if not horizontal else "left")
                if e:
                    head += ["error low", "error high"]
                rows = [head]
                for i, p in enumerate(c.patches):
                    pos = p.get_y() + p.get_height() / 2 if horizontal else p.get_x() + p.get_width() / 2
                    val = p.get_width() if horizontal else p.get_height()
                    r = [_named(pos, cats), _num(val)]
                    if stacked:
                        r.append(_num(bases[i]))
                    if e:
                        r += list(e[i]) if i < len(e) else [None, None]
                    rows.append(r)
                name = _label(c, "")
                blocks.append((f"{pre}bars" + (f" “{name}”" if name else (f" {n_bar}" if n_bar > 1 else "")), rows))
            elif isinstance(c, ErrorbarContainer):
                line = c.lines[0]
                if line is None:
                    continue
                ye, xe = errs(c)
                xs, ys = _values(line.get_xdata()), _values(line.get_ydata())
                head = [xl, yl] + (["y error low", "y error high"] if ye else []) + (["x error low", "x error high"] if xe else [])
                rows = [head]
                for i, (x, y) in enumerate(zip(xs, ys)):
                    r = [_named(x, xc), _named(y, yc)]
                    if ye:
                        r += list(ye[i]) if i < len(ye) else [None, None]
                    if xe:
                        r += list(xe[i]) if i < len(xe) else [None, None]
                    rows.append(r)
                n_line += 1
                blocks.append((f"{pre}points with error bars “{_label(c, f'series {n_line}')}”", rows))

        for line in ax.lines:
            if id(line) in used or not line.get_visible():
                continue
            if line.get_transform() != ax.transData:     # axhline/axvline and other guides, not data
                continue
            xs, ys = _values(line.get_xdata()), _values(line.get_ydata())
            if not xs:
                continue
            n_line += 1
            name = _label(line, f"line {n_line}")
            kind = "points" if line.get_linestyle() in ("None", "none", "", " ") else "line"
            blocks.append((f"{pre}{kind} “{name}”", [[xl, yl]] + [[_named(x, xc), _named(y, yc)] for x, y in zip(xs, ys)]))

        for coll in ax.collections:
            if id(coll) in used or not coll.get_visible():
                continue
            if isinstance(coll, PathCollection):
                off = coll.get_offsets()
                if not len(off):
                    continue
                n_pts += 1
                head = [xl, yl]
                vals = coll.get_array()
                sizes = coll.get_sizes()
                if vals is not None and len(vals) == len(off):
                    head.append("value (color)")
                if len(sizes) == len(off) and len(set(np.round(sizes, 6))) > 1:
                    head.append("marker size")
                rows = [head]
                for i, (x, y) in enumerate(np.asarray(off)):
                    r = [_named(x, xc), _named(y, yc)]
                    if "value (color)" in head:
                        r.append(_num(vals[i]))
                    if "marker size" in head:
                        r.append(_num(sizes[i]))
                    rows.append(r)
                blocks.append((f"{pre}points “{_label(coll, f'points {n_pts}')}”", rows))
            elif isinstance(coll, QuadMesh):
                a = coll.get_array()
                blk = _matrix(a, f"{pre}heatmap", notes, xc, yc)
                if blk:
                    blocks.append(blk)
            elif isinstance(coll, LineCollection):
                segs = coll.get_segments()
                if not segs:
                    continue
                rows = [["x start", "y start", "x end", "y end"]]
                for s in segs:
                    if len(s) >= 2:
                        rows.append([_named(s[0][0], xc), _named(s[0][1], yc), _named(s[-1][0], xc), _named(s[-1][1], yc)])
                blocks.append((f"{pre}segments “{_label(coll, 'segments')}”", rows))
            elif isinstance(coll, PolyCollection):
                notes.append(f"{pre}a shaded area or violin ({_label(coll, 'unnamed')}) isn't exported; give the "
                             f"panel a source_data() function for it")

        for im in ax.images:
            a = im.get_array()
            if a is None:
                continue
            if np.ndim(a) == 3:
                notes.append(f"{pre}a picture (color image) has no values to export")
                continue
            blk = _matrix(a, f"{pre}heatmap", notes, xc, yc)
            if blk:
                blocks.append(blk)

        wedges = [p for p in ax.patches if isinstance(p, Wedge) and id(p) not in used]
        if wedges:
            texts = [t.get_text() for t in ax.texts]
            rows = [["slice", "fraction"]]
            for i, w in enumerate(wedges):
                name = _label(w, texts[i] if i < len(texts) else f"slice {i + 1}")
                rows.append([name, _num(round(((w.theta2 - w.theta1) % 360 or 360) / 360, 6))])
            blocks.append((f"{pre}pie", rows))
    if not axes:
        notes.append("the panel has no plot axes")
    return blocks, notes


def _matrix(a, title, notes, cols=None, rows_=None):
    a = np.ma.asarray(a)
    if a.ndim == 1:
        return (title, [["value"]] + [[v] for v in _values(a)])
    if a.ndim != 2:
        return None
    if a.size > MAX_CELLS:
        notes.append(f"{title}: {a.shape[0]} × {a.shape[1]} values are more than this file takes; give the panel a "
                     f"source_data() function returning what it was drawn from")
        return None
    # rows and columns named by their tick labels when every one has one, else numbered from 1
    cn = [cols.get(j) for j in range(a.shape[1])] if cols else []
    rn = [rows_.get(i) for i in range(a.shape[0])] if rows_ else []
    cn = cn if cn and all(cn) else list(range(1, a.shape[1] + 1))
    rn = rn if rn and all(rn) else list(range(1, a.shape[0] + 1))
    rows = [["row \\ column"] + cn]
    for i in range(a.shape[0]):
        rows.append([rn[i]] + _values(a[i]))
    return (title, rows)


def _from_supplied(value) -> list:
    """Blocks from what a panel's source_data() returned."""
    def frame(df) -> list:
        df = df.reset_index() if getattr(df.index, "name", None) or getattr(df.index, "names", [None])[0] else df
        return [[str(c) for c in df.columns]] + [[_num(v) for v in row] for row in df.itertuples(index=False)]
    if hasattr(value, "itertuples"):
        return [("", frame(value))]
    if isinstance(value, dict):
        return [(str(k), frame(v) if hasattr(v, "itertuples") else [[_num(c) for c in r] for r in v])
                for k, v in value.items()]
    if isinstance(value, (list, tuple)):
        return [("", [[_num(c) for c in r] for r in value])]
    raise TypeError("source_data() should return a DataFrame, a dict of DataFrames, or a list of rows")


def _data_fn(props, renderer):
    """A panel's source-data function, if its code has one."""
    name = "source_data" if props["function"] == "draw" else f"{props['function']}_source_data"
    src = props["source"]
    if callable(src):
        return getattr(src, "__globals__", {}).get(f"{src.__name__}_source_data")
    ns = runpy.run_path(str((renderer.project / src).resolve()), run_name="__fignimble_panel__")
    fn = ns.get(name)
    return fn if callable(fn) else None


def panel_blocks(renderer, job) -> tuple[list, list[str], str]:
    """(blocks, notes, how) for one panel job of the last render: its own source data, or read from its drawing."""
    props = job["props"]
    try:
        code, fn, path, where = renderer._panel_code(props)
        renderer._enter()
        supply = _data_fn(props, renderer)
        if supply is not None:
            return _from_supplied(supply(**props["args"])), [], "from its source_data()"
        draw = renderer._panel_fn(props, fn, path)
        w, h = job["size"]
        import matplotlib
        with matplotlib.rc_context(renderer.rc):
            fig = plt.figure(figsize=(w / 25.4, h / 25.4))
            try:
                api._DARK[0] = False
                draw(fig, **props["args"])
                fig.canvas.draw()
                blocks, notes = extract(fig)
            finally:
                plt.close("all")
        return blocks, notes, "read from what it draws"
    except Exception as exc:                                    # noqa: BLE001
        return [], [f"couldn't get its data: {type(exc).__name__}: {exc}"], "failed"


# --- the workbook ---

def _sheet_xml(rows: list, styles: dict) -> str:
    """rows: lists of cells; styles: {row index: style id} (1 header, 2 title)."""
    out = []
    widths: dict[int, int] = {}
    for i, r in enumerate(rows):
        st = styles.get(i, 0)
        cs = []
        for j, v in enumerate(r):
            if v is None or v == "":
                continue
            ref = f"{tables._col(j)}{i + 1}"
            s = f' s="{st}"' if st else ""
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                cs.append(f'<c r="{ref}"{s}><v>{repr(float(v)) if isinstance(v, float) else v}</v></c>')
                widths[j] = max(widths.get(j, 0), min(len(f"{v:g}") if isinstance(v, float) else len(str(v)), 14))
            else:
                text = str(v)
                cs.append(f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{escape(text)}</t></is></c>')
                if st != 2:                                     # titles may run across columns
                    widths[j] = max(widths.get(j, 0), min(len(text), 40))
        out.append(f'<row r="{i + 1}">{"".join(cs)}</row>')
    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{max(10, w + 2)}" customWidth="1"/>'
                   for j, w in sorted(widths.items()))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'{"<cols>" + cols + "</cols>" if cols else ""}<sheetData>{"".join(out)}</sheetData></worksheet>')


_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="{font}"/></font><font><b/><sz val="11"/><name val="{font}"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border>
<border><left/><right/><top/><bottom style="thin"/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""


def _sheet_name(name: str, taken: set) -> str:
    base = (name.translate({ord(ch): " " for ch in "[]:*?/\\"}).strip() or "Sheet")[:31]
    out, n = base, 2
    while out.lower() in taken:
        out = f"{base[:28]} {n}"
        n += 1
    taken.add(out.lower())
    return out


def write_workbook(sheets: list, path, title: str, font: str = "Arial") -> str:
    """sheets: [(name, rows, {row: style})]; style 1 a header row, 2 a title."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    taken: set = set()
    names = [_sheet_name(n, taken) for n, _, _ in sheets]
    ct = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-'
                 f'officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(sheets) + 1))
    content_types = tables._XCT.replace(
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'spreadsheetml.worksheet+xml"/>', ct)
    wb = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="http://schemas.openxmlformats.'
          'org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
          '<sheets>' + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
                               for i, n in enumerate(names, 1)) + '</sheets></workbook>')
    n = len(sheets)
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      f'relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, n + 1))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
              f'relationships/styles" Target="styles.xml"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", tables._XRELS)
        z.writestr("docProps/core.xml", tables._CORE.format(title=escape(title)))
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/styles.xml", _STYLES.format(font=escape(font)))
        for i, (_, rows, styles) in enumerate(sheets, 1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(rows, styles))
    return str(path.resolve())


def _panel_sheet(name: str, where: str, blocks: list, notes: list, how: str) -> tuple:
    rows, styles = [[f"{name}: {where}"], [f"Values {how}."]], {0: 2}
    for title, data in blocks:
        rows.append([])
        if title:
            styles[len(rows)] = 2
            rows.append([title])
        if data:
            styles[len(rows)] = 1
            rows.extend(data)
    if notes:
        rows.append([])
        rows.extend([[f"Note: {n}"] for n in notes])
    if not blocks and not notes:
        rows += [[], ["Nothing to export."]]
    return (name, rows, styles)


def collect(renderer, file: str) -> list[dict]:
    """The values each panel of figure `file` plots, panels in letter order, then those of figures placed whole:
    [{key, panel, sheet, where, blocks, notes, how}]. key: a short name for files (the letter; "b-c" for panel c of
    a figure placed as b)."""
    fig = renderer.run_figure(file)
    fpath = renderer.resolve(file)
    if isinstance(fig, api.Table):
        raise ValueError(f"{Path(fpath).name} is a table: its cells are its data; export it as Excel with File ▸ Export "
                         f"Table… or `fignimble export {Path(fpath).name} {Path(fpath).stem}.xlsx`")
    renderer._stack, renderer.used, renderer.nested = [fpath], {fpath}, {}
    pieces, infos, labels = renderer.pieces(fig)
    letters = {l["panel"]: l["text"] for l in labels}
    jobs = sorted(renderer.jobs, key=lambda j: (letters.get(j["oid"]) is None, letters.get(j["oid"]) or "", j["oid"]))
    rc = renderer.rc
    out = []
    for j in jobs:
        letter = letters.get(j["oid"])
        src = j["props"]["source"]
        where = src.__name__ if callable(src) else (src + ("" if j["props"]["function"] == "draw"
                                                           else f", {j['props']['function']}()"))
        renderer.rc = rc
        blocks, notes, how = panel_blocks(renderer, j)
        out.append({"key": letter or f"panel-{j['oid']}", "panel": letter or j["oid"],
                    "sheet": f"Panel {letter}" if letter else f"Panel {j['oid']}", "where": where,
                    "blocks": blocks, "notes": notes, "how": how})
    for oid, (fname, sjobs, src_rc, slabels) in renderer.nested.items():
        outer = letters.get(oid)
        sl = {l["panel"]: l["text"] for l in slabels}
        for j in sorted(sjobs, key=lambda j: (sl.get(j["oid"]) or "", j["oid"])):
            inner = sl.get(j["oid"]) or j["oid"]
            name = f"{fname} {inner}" if not outer else f"Panel {outer} ({inner})"
            renderer.rc = src_rc
            blocks, notes, how = panel_blocks(renderer, j)
            out.append({"key": f"{outer}-{inner}" if outer else f"{Path(fname).stem}-{inner}", "panel": name,
                        "sheet": name, "where": f"panel {inner} of {fname}, placed whole", "blocks": blocks,
                        "notes": notes, "how": how})
    renderer.rc = rc
    renderer._collected = (fig, infos)
    return out


def export(renderer, file: str, path: str) -> dict:
    """Write the source data of figure `file` to `path` (.xlsx): {path, sheets, panels: [{panel, sheet, how, blocks}]}."""
    from .render import provenance_line
    panels = collect(renderer, file)
    fig, infos = renderer._collected
    fpath = renderer.resolve(file)
    sheets = [_panel_sheet(p["sheet"], p["where"], p["blocks"], p["notes"], p["how"]) for p in panels]
    report = [{"panel": p["panel"], "sheet": p["sheet"], "how": p["how"], "blocks": len(p["blocks"])} for p in panels]
    pictures = [i for i in infos if i["kind"] in ("image", "inset")]
    about = [["Source data"], [f"Figure: {Path(fpath).name}"]]
    try:
        about.append([provenance_line(renderer.provenance(file))])
    except Exception:                                           # noqa: BLE001
        pass
    about += [[], ["Sheet", "Panel", "Values"]]
    for r in report:
        about.append([r["sheet"], r["panel"], r["how"]])
    if pictures:
        about += [[], ["Pictures (micrographs, gels, schematics) have no plotted values; provide their raw files "
                       "if the journal asks."]]
    styles = {0: 2, 4: 1}
    sheets.insert(0, ("About", about, styles))
    out = write_workbook(sheets, path, f"Source data for {Path(fpath).stem}", font=fig.style.props.get("font", "Arial"))
    return {"path": out, "sheets": [s[0] for s in sheets], "panels": report}
