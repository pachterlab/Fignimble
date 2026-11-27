"""Rendering a figure file: run it, draw each element, compose them into one vector page, check the result.

Every element becomes a *piece*: a drawing that does not depend on where the element is, cached by a hash of what
it looks like. Moving an element therefore re-uses its drawing; resizing a panel redraws only that panel. Panels
and text are drawn by matplotlib, and everything is composed into a PDF page with PyMuPDF, which keeps panels and
vector pictures as vectors. The editor gets one transparent picture (sprite) per piece, to move about on screen.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import logging
import math
import os
import re
import shutil
import subprocess
import warnings
import tempfile
import runpy
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pymupdf  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.figure import Figure as MplFigure  # noqa: E402

import matplotlib.lines  # noqa: E402
from . import api, journals, layout, officefonts, qr, tables  # noqa: E402

officefonts.install()          # Microsoft Office's fonts (Aptos, Aptos Display, Calibri, ...) for matplotlib

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
sys.dont_write_bytecode = True          # running a project's code leaves no __pycache__ in it
pymupdf.TOOLS.mupdf_display_errors(False)

MM = 72 / 25.4                     # points per millimeter
RASTER = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")
CROP_SLUG = 10.0                   # mm outside the bleed for crop marks
VECTOR = (".pdf", ".svg")
SKIP_DIRS = {".fignimble", ".git", "__pycache__", ".venv", "venv", "node_modules", ".ipynb_checkpoints"}
# what Fignimble writes at a project's top for reproducibility (see repro.py): never an input to a figure
WRITTEN_DIRS, WRITTEN_FILES = {"exports"}, {"fignimble.lock"}


def project_walk(project):
    """os.walk over a project's files that figures may read: without caches, environments, and what Fignimble
    writes itself (exports/, fignimble.lock)."""
    project = str(project)
    for dirpath, dirs, files in os.walk(project):
        top = dirpath == project
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and not (top and d in WRITTEN_DIRS)]
        yield dirpath, dirs, [f for f in files if not (top and f in WRITTEN_FILES)]


CACHE_VERSION = 10                 # bump when a change to drawing makes cached pieces wrong
MAX_SPRITE_PX = 24e6               # pixels of the editor's picture of one piece, at most
MAX_SPRITE_SIDE = 12000


def _h(*parts) -> str:
    return hashlib.sha1(json.dumps((CACHE_VERSION, parts), sort_keys=True, default=str).encode()).hexdigest()[:20]


def rgb(c):
    if c is None:
        return None
    return tuple(mcolors.to_rgb(c))


def is_figure_source(text: str) -> bool:
    """A .py file that makes a figure or a table (and is not a panel or a style module)."""
    return "fignimble" in text and ("Figure(" in text or is_table_source(text))


def is_table_source(text: str) -> bool:
    """A .py file that makes a Table (`tab = Table(...)`)."""
    return "fignimble" in text and re.search(r"(?m)^\w+\s*=\s*(fignimble\.)?Table\(", text) is not None


def table_rows(p: dict, project: Path) -> list:
    """The cells of a table element: its rows=, the rows its function returns (a DataFrame becomes its column names
    and values), or its file."""
    if p.get("rows") is not None:
        return p["rows"]
    src = p["source"]
    if callable(src):
        got = src()
        if hasattr(got, "columns") and hasattr(got, "values"):           # a pandas DataFrame
            index = getattr(got, "index", None)
            named = index is not None and any(n is not None for n in getattr(index, "names", []) or [])
            if named:
                got = got.reset_index()
            return [[str(c) for c in got.columns]] + [[_cell_value(v) for v in r] for r in got.values.tolist()]
        rows = [list(r) for r in got]
        if not rows:
            raise ValueError(f"{getattr(src, '__name__', 'the function')} returned no rows")
        return rows
    return tables.read_rows(project / src)


def _cell_value(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if hasattr(v, "item"):                                   # numpy scalars
        v = v.item()
    return v


def run_isolated(path: Path) -> api.Figure:
    """Run a figure file while another figure is being drawn (one placed in it with fig.figure): the Figure it
    makes, without disturbing the list of figures the outer run is collecting."""
    saved = list(api._FIGURES)
    api._FIGURES.clear()
    try:
        runpy.run_path(str(path), run_name="__fignimble__")
        if not api._FIGURES:
            raise RuntimeError(f"{path.name} does not make a Figure")
        return api._FIGURES[0]
    finally:
        api._FIGURES[:] = saved


_SIZES: dict = {}


def nested_size(source: str) -> tuple[float, float]:
    """(width, height) mm of a figure file (relative to the project, where figure code runs), for the geometry of
    fig.figure(...) without w or h."""
    path = Path(source).resolve()
    st = path.stat()
    k = (str(path), st.st_mtime_ns, st.st_size)
    if k not in _SIZES:
        f = run_isolated(path)
        _SIZES[k] = (float(f.width), float(f.height))
    return _SIZES[k]


def image_aspect(source: str, crop=None, rotation: float = 0) -> float:
    """Width / height of a picture file after `crop` and `rotation`."""
    p = Path(source)
    ext = p.suffix.lower()
    if ext in VECTOR:
        with pymupdf.open(str(p)) as d:
            r = d[0].rect
            w, h = r.width, r.height
    else:
        from PIL import Image
        with Image.open(str(p)) as im:
            w, h = im.size
    if crop:
        l, t, r_, b = crop
        w, h = w * (1 - l - r_), h * (1 - t - b)
    if rotation:
        a = math.radians(rotation)
        c, s_ = abs(math.cos(a)), abs(math.sin(a))
        w, h = w * c + h * s_, w * s_ + h * c
    return w / h if h > 0 else 1.0


@dataclass
class Piece:
    """One element's drawing. `box` (x, y, w, h mm) is where it goes on the figure; `draw(page, ox, oy)` draws it
    with its box's top-left at (ox, oy) points. `pad` (mm) is how far it may draw outside its box (strokes)."""
    oid: str
    kind: str
    box: tuple
    key: str
    draw: Callable
    pad: float = 0.0
    meta: dict = field(default_factory=dict)


def _is_library(path: str) -> bool:
    """A file of an installed package (or of Python itself), never reloaded or watched as the user's code."""
    parts = Path(path).parts
    if "site-packages" in parts or "dist-packages" in parts:
        return True
    return any(path.startswith(os.path.abspath(p) + os.sep) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix})


def outside_stats(files) -> list:
    """(path, mtime, size) of each file, (path, None, None) for one that is gone: what changes when they do."""
    out = []
    for f in files:
        try:
            st = os.stat(f)
            out.append((f, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((f, None, None))
    return out


class Renderer:
    def __init__(self, project: str, cache: str | None = None):
        self.project = Path(project).resolve()
        self.cache = Path(cache) if cache else self.project / ".fignimble"
        for d in ("pieces", "sprites", "out", "images"):
            (self.cache / d).mkdir(parents=True, exist_ok=True)
        self._docs: dict[str, pymupdf.Document] = {}
        self.jobs, self.rc, self.nested = [], None, {}   # the last figure's panels, as pieces() drew them
        self._cwd = Path.cwd()                   # rendering moves into the project; relative paths mean this one
        self.dark = False
        self.raster_dpi = 600                    # what panels draw their rasters at (imshow, rasterized scatters)
        self._stack: list[Path] = []             # the figure files being drawn, outermost first (fig.figure nests)
        self.used: set[Path] = set()             # the files the last render read, for an export's provenance
        # files outside the project that figures read (pictures, panel code and the modules it imports), by figure
        # file: watched like the project's own, and kept on disk so that a fresh process knows them too
        self._outside: dict[str, list[str]] = {}
        try:
            got = json.loads((self.cache / "outside.json").read_text(encoding="utf-8"))
            self._outside = {k: [str(f) for f in v] for k, v in got.items() if isinstance(v, list)}
        except (OSError, ValueError, AttributeError):
            pass

    # --- running user code ---

    def _outside_dirs(self) -> list[str]:
        """The folders of code outside the project that panels run: modules imported from them are reloaded too."""
        return sorted({os.path.dirname(f) for fs in self._outside.values() for f in fs if f.endswith(".py")})

    def _purge_modules(self):
        roots = [str(self.project)] + self._outside_dirs()
        for name, mod in list(sys.modules.items()):
            f = getattr(mod, "__file__", None) or ""
            if name == "__main__" or not f or not os.path.isfile(f):
                continue
            f = os.path.abspath(f)
            if any(f.startswith(r + os.sep) for r in roots) and not _is_library(f):
                del sys.modules[name]

    def outside_files(self, file=None) -> list[str]:
        """Files outside the project that a figure (or, without `file`, any figure) read when last drawn."""
        if file is not None:
            return list(self._outside.get(str(Path(file).resolve()), []))
        return sorted({f for fs in self._outside.values() for f in fs})

    def _note_outside(self, file):
        """After drawing `file`: the files outside the project it read, and the modules its outside code imported
        from the folders that code is in."""
        root = str(self.project) + os.sep
        files = {str(f) for f in self.used if not str(f).startswith(root)}
        dirs = {os.path.dirname(f) for f in files if f.endswith(".py")}
        if dirs:
            for mod in list(sys.modules.values()):
                f = getattr(mod, "__file__", None) or ""
                if f and f.endswith(".py"):
                    f = os.path.abspath(f)
                    if any(f.startswith(d + os.sep) for d in dirs) and not _is_library(f):
                        files.add(f)
        key, new = str(Path(file).resolve()), sorted(files)
        if self._outside.get(key, []) == new:
            return
        if new:
            self._outside[key] = new
        else:
            self._outside.pop(key, None)
        try:
            (self.cache / "outside.json").write_text(json.dumps(self._outside, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _enter(self):
        os.chdir(self.project)
        if str(self.project) not in sys.path:
            sys.path.insert(0, str(self.project))

    def resolve(self, file: str) -> Path:
        """A figure file given relative to the working directory, or else to the project."""
        p = Path(file)
        if not p.is_absolute():
            p = self._cwd / p if (self._cwd / p).exists() else self.project / p
        return p.resolve()

    def run_figure(self, file: str) -> api.Figure:
        """Run a figure file; the Figure it made (its first)."""
        file = self.resolve(file)
        self._enter()
        self._purge_modules()
        api._FIGURES.clear()
        plt.close("all")
        runpy.run_path(str(Path(file).resolve()), run_name="__fignimble__")
        if not api._FIGURES:
            raise RuntimeError(f"{Path(file).name} does not make a Figure (fig = Figure(width=..., height=...))")
        return api._FIGURES[0]

    def fingerprint(self) -> str:
        """What a panel can read besides its own code: every other file of the project except figure files, and the
        files outside the project that figures read (code a panel imports from there)."""
        items = []
        for dirpath, dirs, files in project_walk(self.project):
            for f in files:
                p = Path(dirpath) / f
                if f.endswith(".py"):
                    try:
                        if is_figure_source(p.read_text(encoding="utf-8", errors="replace")):
                            continue
                    except OSError:
                        continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                items.append((str(p.relative_to(self.project)), st.st_mtime_ns, st.st_size))
        return _h(sorted(items), outside_stats(self.outside_files()))

    # --- pieces ---

    def _doc(self, path: str) -> pymupdf.Document:
        d = self._docs.get(path)
        if d is None:
            if path.lower().endswith(".svg"):          # MuPDF ignores an SVG's style sheet: write it onto the shapes
                d = pymupdf.open("svg", _svg_without_css(Path(path).read_bytes()))
            else:
                d = pymupdf.open(path)
            if not d.is_pdf:
                d = pymupdf.open("pdf", d.convert_to_pdf())
            self._docs[path] = d
        return d

    def _show_pdf(self, path: str, page, rect, clip=None):
        page.show_pdf_page(rect, self._doc(path), 0, clip=clip, keep_proportion=False)

    def _panel_code(self, props: dict):
        """(code text, function or None, file path or None, name for errors) of a panel's source."""
        src = props["source"]
        if callable(src):
            try:
                code = inspect.getsource(src)
            except (OSError, TypeError):
                code = repr(src.__code__.co_code)
            return code, src, None, src.__name__
        path = (self.project / src).resolve()
        return path.read_text(encoding="utf-8"), None, path, src

    def _panel_fn(self, props, fn, path):
        if fn is not None:
            return fn
        self._enter()
        here = str(Path(path).parent)
        if not str(path).startswith(str(self.project) + os.sep) and here not in sys.path:
            sys.path.insert(1, here)          # code kept with an analysis imports its neighbours, as when run there
        ns = runpy.run_path(str(path), run_name="__fignimble_panel__")
        fn = ns.get(props["function"])
        if not callable(fn):
            raise RuntimeError(f"{props['source']} has no function {props['function']}(fig)")
        return fn

    def _draw_panel(self, fn, args, w, h, rc, collect, target=None, background=None, texts=None, colors=None,
                    text_style=None):
        """Draw a panel: (pdf bytes, its plot area's margins in mm (l, t, r, b) or None, legend entries taken out,
        the texts it shows [{key, text, box (mm in the panel), size, rotation}])."""
        with matplotlib.rc_context(rc):
            fig = plt.figure(figsize=(w / 25.4, h / 25.4))
            try:
                api._DARK[0] = self.dark
                fn(fig, **args)
                _set_colors(fig, colors)
                entries = _take_legends(fig) if collect else []
                _set_texts(fig, texts, ticks=False)
                _set_text_style(fig, text_style, ticks=False)
                fig.canvas.draw()                    # lays it out (constrained layout), so the axes are where they go
                if _set_texts(fig, texts, ticks=True) | _set_text_style(fig, text_style, ticks=True):
                    fig.canvas.draw()
                area = _plot_area(fig)
                margins = None
                if area is not None:
                    x0, y0, x1, y1 = area
                    margins = [x0 * w, (1 - y1) * h, (1 - x1) * w, y0 * h]
                    if target is not None and any(abs(a - b) > 0.05 for a, b in zip(margins, target)):
                        _remap(fig, area, target, w, h)
                        fig.canvas.draw()
                found = _measure_texts(fig, h)
                marks = _measure_marks(fig, h)
                buf = io.BytesIO()
                # rasters inside the panel (imshow, rasterized scatters) at print resolution
                fig.savefig(buf, format="pdf", facecolor=background or "none", dpi=self.raster_dpi)
            finally:
                api._DARK[0] = False
                plt.close("all")
        return buf.getvalue(), margins, entries, found, marks

    def _panel_base(self, props, w, h, rc, deps, collect, background=None) -> dict:
        """The panel drawn at its size, as its own code lays it out: {pdf, margins, entries} or {error}."""
        try:
            code, fn, path, where = self._panel_code(props)
        except OSError as exc:
            return {"error": f"can't read panel code {props['source']}: {exc}"}
        key = _h("panel", self.raster_dpi, code, None if fn else props["source"], props["function"], props["args"], round(w, 3),
                 round(h, 3), rc, deps, bool(collect), background, props.get("texts") or {}, props.get("colors") or {},
                 props.get("text_style") or {})
        pdf, meta = self.cache / "pieces" / f"{key}.pdf", self.cache / "pieces" / f"{key}.json"
        if pdf.exists() and meta.exists():
            return {"pdf": str(pdf), "key": key, **json.loads(meta.read_text())}
        # the panel code's Python warnings (deprecations in the libraries it uses, say) are kept with the panel,
        # not printed, so they don't bury the messages that matter
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                data, margins, entries, found, marks = self._draw_panel(self._panel_fn(props, fn, path), props["args"],
                                                                        w, h, rc, collect, background=background,
                                                                        texts=props.get("texts"),
                                                                        colors=props.get("colors"),
                                                                        text_style=props.get("text_style"))
            except Exception:                           # noqa: BLE001
                return {"error": _user_traceback(where)}
        said = list(dict.fromkeys(f"{w_.category.__name__}: {str(w_.message).strip().splitlines()[0]}"
                                  for w_ in caught if w_.message))
        _write(pdf, data)
        meta.write_text(json.dumps({"margins": margins, "entries": entries, "texts": found, "marks": marks,
                                    "warnings": said}))
        ekey = _h("entries", code, None if fn else props["source"], props["function"], props["args"], rc, deps)
        (self.cache / "pieces" / f"{ekey}.entries.json").write_text(json.dumps(entries))
        return {"pdf": str(pdf), "key": key, "margins": margins, "entries": entries, "texts": found, "marks": marks,
                "warnings": said}

    def _panel_aligned(self, props, w, h, rc, deps, collect, base, target, background=None) -> tuple[str, dict]:
        """The panel redrawn with its plot area at `target` margins (mm): (pdf, {texts, marks})."""
        if not base.get("margins") or target is None or all(abs(a - b) <= 0.05 for a, b in zip(base["margins"], target)):
            return base["pdf"], {"texts": base.get("texts") or [], "marks": base.get("marks") or []}
        target = [round(v, 2) for v in target]
        key = _h(base["key"], target)
        pdf, meta = self.cache / "pieces" / f"{key}.pdf", self.cache / "pieces" / f"{key}.texts.json"
        if not (pdf.exists() and meta.exists()):
            code, fn, path, where = self._panel_code(props)
            with warnings.catch_warnings():             # said once already, when it was drawn as its code lays it out
                warnings.simplefilter("ignore")
                data, _, _, found, marks = self._draw_panel(self._panel_fn(props, fn, path), props["args"], w, h, rc,
                                                            collect, target, background, props.get("texts"),
                                                            props.get("colors"), props.get("text_style"))
            _write(pdf, data)
            meta.write_text(json.dumps({"texts": found, "marks": marks}))
        return str(pdf), json.loads(meta.read_text())

    def _entries(self, props, w, h, rc, deps) -> list:
        """The legend entries of a panel, for a layout that collects them (they don't depend on its size)."""
        try:
            code, fn, path, where = self._panel_code(props)
        except OSError:
            return []
        ekey = _h("entries", code, None if fn else props["source"], props["function"], props["args"], rc, deps)
        f = self.cache / "pieces" / f"{ekey}.entries.json"
        if f.exists():
            return json.loads(f.read_text())
        return self._panel_base(props, w, h, rc, deps, True).get("entries", [])

    def legend_piece(self, entries: list, rc: dict, title=None, ncol: int = 1, size=None) -> tuple[str, float, float]:
        """(pdf, w, h) of a legend drawn by matplotlib from entries ({label, kind, color, ...})."""
        entries = [{**e, "color": self.c(e.get("color")), "edgecolor": self.c(e.get("edgecolor"))} for e in entries]
        key = _h("legend", entries, rc, title, ncol, size)
        out = self.cache / "pieces" / f"{key}.pdf"
        if not out.exists():
            with matplotlib.rc_context(rc):
                fig = MplFigure(figsize=(4, 4))
                fig.legend(handles=[_proxy(e) for e in entries], labels=[e["label"] for e in entries],
                           loc="upper left", frameon=False, ncols=max(1, int(ncol)), title=title,
                           fontsize=size or rc.get("legend.fontsize"), title_fontsize=size or rc.get("legend.fontsize"),
                           borderpad=0, borderaxespad=0, handlelength=1.4, handletextpad=0.5, labelspacing=0.35)
                buf = io.BytesIO()
                fig.savefig(buf, format="pdf", bbox_inches="tight", pad_inches=0.01, transparent=True)
            _write(out, buf.getvalue())
        r = self._doc(str(out))[0].rect
        return str(out), r.width / MM, r.height / MM

    def table_piece(self, rows: list, header: bool, size: float, family: list, color: str, width: float | None,
                    decimals=None, align=None, grid: bool = False) -> tuple[str, float, float]:
        """(pdf, w mm, h mm) of a table: columns as wide as their text (widened evenly to `width`), numbers to the
        right, a rule above and below the header and at the bottom (`grid`: a line between every row and column and
        around the table)."""
        color = self.c(color)
        shown, auto = tables.cells(rows, header, decimals)
        al = tables.resolve_align(align, auto)
        key = _h("table2", shown, al, header, size, family, color, width, bool(grid))
        out = self.cache / "pieces" / f"{key}.pdf"
        meta = out.with_suffix(".json")
        if not (out.exists() and meta.exists()):
            rc = {"pdf.fonttype": 42, "font.family": "sans-serif", "font.sans-serif": family}
            pad, rh, rule = size * 0.9, size * 1.55, 0.6          # points
            with matplotlib.rc_context(rc):
                fig = MplFigure(figsize=(2, 2), dpi=72)
                canvas = FigureCanvasAgg(fig)
                r = canvas.get_renderer()
                n = len(al)
                cw = [0.0] * n
                for i, row in enumerate(shown):
                    for j, c in enumerate(row):
                        if c:
                            t = fig.text(0, 0, c, fontsize=size, fontweight="bold" if header and i == 0 else "normal")
                            cw[j] = max(cw[j], t.get_window_extent(r).width)
                            t.remove()
                # an empty column (a new table, still to be typed into) is still wide enough to click and type in
                cw = [max(c, size * 4.5) + 2 * pad for c in cw]
                W = sum(cw) or 10.0
                if width and width * MM > W:
                    cw = [c + (width * MM - W) / max(n, 1) for c in cw]
                    W = width * MM
                H = rh * len(shown) + 2 * rule
                fig.set_size_inches(W / 72, H / 72)
                xs = [sum(cw[:j]) for j in range(n)]
                for i, row in enumerate(shown):
                    y = rule + rh * (i + 0.5)
                    for j, c in enumerate(row):
                        x = {"l": xs[j] + pad, "c": xs[j] + cw[j] / 2, "r": xs[j] + cw[j] - pad}[al[j]]
                        fig.text(x / W, 1 - y / H, c, fontsize=size, color=color, va="center_baseline",
                                 ha={"l": "left", "c": "center", "r": "right"}[al[j]],
                                 fontweight="bold" if header and i == 0 else "normal")
                lines = [(rule / 2, rule), (H - rule / 2, rule)]
                if grid:
                    lines += [(rule + rh * i, rule * 0.6) for i in range(1, len(shown))]
                elif header and len(shown) > 1:
                    lines.append((rule + rh, rule * 0.6))
                for y, lw in lines:
                    fig.add_artist(matplotlib.lines.Line2D([0, 1], [1 - y / H] * 2, transform=fig.transFigure,
                                                           color=color, linewidth=lw, solid_capstyle="butt"))
                if grid:                                  # the columns' dividers, and the table's sides
                    for k, x in enumerate(xs[1:] + [0.0, W]):
                        x = min(max(x, rule / 2), W - rule / 2)
                        lw = rule if k >= n - 1 else rule * 0.6
                        fig.add_artist(matplotlib.lines.Line2D([x / W] * 2, [0, 1], transform=fig.transFigure,
                                                               color=color, linewidth=lw, solid_capstyle="butt"))
                buf = io.BytesIO()
                fig.savefig(buf, format="pdf", transparent=True)
            _write(out, buf.getvalue())
            meta.write_text(json.dumps({"w": W / MM, "h": H / MM,
                                        # where each cell is (mm from the table's top-left), for typing into it
                                        "cols": [[round(x / MM, 3), round(c / MM, 3)] for x, c in zip(xs, cw)],
                                        "rows": [[round((rule + rh * i) / MM, 3), round(rh / MM, 3)]
                                                 for i in range(len(shown))]}))
        m = json.loads(meta.read_text())
        self.last_table_grid = {"cols": m.get("cols", []), "rows": m.get("rows", [])}
        return str(out), m["w"], m["h"]

    def c(self, color):
        """A color as drawn: in dark mode, grays (black text and lines) turn light and light grays dark."""
        if color is None or not self.dark:
            return color
        try:
            r, g, b = mcolors.to_rgb(color)
        except ValueError:
            return color
        if max(r, g, b) - min(r, g, b) > 0.08:
            return color
        v = 1 - (r + g + b) / 3
        return mcolors.to_hex((v, v, v))

    def rc_for(self, style) -> dict:
        rc = style.rcparams()
        # mathtext (Agg$^{+}$, 10$^{3}$) in the style's font too, unless the style sets its own
        for k, v in _math_rc(rc["font.sans-serif"]).items():
            if k not in style.rc:
                rc[k] = v
        if self.dark:
            rc.update(DARK_RC)
            rc.update({k: v for k, v in style.rc.items()})
        return rc

    def text_piece(self, text: str, size: float, weight: str, style: str, color: str, rotation: float,
                   family: list, align: str = "left", width: float | None = None,
                   keep_color: bool = False, opacity: float = 1.0) -> tuple[str, float, float]:
        """(pdf, w mm, h mm) of a text drawn by matplotlib, tight to its box. The text may hold markup (**bold**,
        *italic*, ^{sup}, _{sub}); with `width` (mm) its lines wrap."""
        color = color if keep_color else self.c(color)
        key = _h("text", text, size, weight, style, color, rotation, family, align, width, opacity)
        out = self.cache / "pieces" / f"{key}.pdf"
        if not out.exists():
            rc = {"pdf.fonttype": 42, "font.family": "sans-serif", "font.sans-serif": family, **_math_rc(family)}
            with matplotlib.rc_context(rc):
                fig = MplFigure(figsize=(2, 2))
                FigureCanvasAgg(fig)                     # to measure lines while wrapping
                shown = _rich(text, weight == "bold", width, fig, dict(fontsize=size, fontweight=weight,
                                                                       fontstyle=style))
                fig.text(0, 0, shown, fontsize=size, fontweight=weight, fontstyle=style, color=color,
                         rotation=rotation, multialignment=align, rotation_mode="anchor", alpha=opacity)
                buf = io.BytesIO()
                fig.savefig(buf, format="pdf", bbox_inches="tight", pad_inches=0, transparent=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_bytes(buf.getvalue())
            tmp.replace(out)
        r = self._doc(str(out))[0].rect
        return str(out), r.width / MM, r.height / MM

    def flow_piece(self, text: str, markdown: bool, size: float, weight: str, style: str, color: str, family: list,
                   align: str, width: float, opacity: float = 1.0, height: float | None = None, fit: str | None = None,
                   min_size: float | None = None) -> tuple[str, float, float, float]:
        """(pdf, w mm, h mm, size used) of a text box laid out in blocks: paragraphs, bullets and numbered items
        with their lines indented under them, headings. With `height` and fit="shrink", the type is made smaller
        (in quarter points, never below `min_size`) until the text fits the height."""
        color = self.c(color)
        key = _h("flow", text, markdown, size, weight, style, color, family, align, width, opacity, height, fit,
                 min_size)
        out = self.cache / "pieces" / f"{key}.pdf"
        meta = out.with_suffix(".json")
        if not (out.exists() and meta.exists()):
            rc = {"pdf.fonttype": 42, "font.family": "sans-serif", "font.sans-serif": family, **_math_rc(family)}
            with matplotlib.rc_context(rc):
                fig = MplFigure(figsize=(max(width, 1) / 25.4, 2))
                FigureCanvasAgg(fig)
                blocks = _blocks(text, markdown)
                props = dict(fontsize=size, fontweight=weight, fontstyle=style)

                def lay(s_):
                    return _flow(blocks, s_, weight == "bold", width, fig, {**props, "fontsize": s_})

                def tall(lines) -> float:                   # mm
                    return (lines[-1]["y"] + lines[-1]["size"] * 0.25) / MM if lines else 0.0

                used = size
                lines = lay(used)
                if height and fit == "shrink" and tall(lines) > height:
                    lo, hi = max(min(min_size or 1.0, size), 1.0), size
                    if tall(lay(lo)) <= height:
                        for _ in range(10):
                            mid = (lo + hi) / 2
                            lo, hi = (mid, hi) if tall(lay(mid)) <= height else (lo, mid)
                    used = max(math.floor(lo * 4) / 4, min(min_size or 1.0, size))
                    lines = lay(used)
                H = max(tall(lines) * MM, 1.0)                 # points
                fig.set_size_inches(max(width, 1) / 25.4, H / 72)
                for ln in lines:
                    kw = dict(fontsize=ln["size"], fontweight="bold" if ln["bold"] else weight, fontstyle=style,
                              color=color, alpha=opacity, va="baseline", transform=fig.dpi_scale_trans)
                    x = {"center": (ln["x"] + width) / 2, "right": width}.get(align, ln["x"])
                    fig.text(x / 25.4, (H - ln["y"]) / 72, ln["text"], ha=align if align in ("center", "right") else "left",
                             **kw)
                    if ln["marker"]:
                        fig.text(ln["mx"] / 25.4, (H - ln["y"]) / 72, ln["marker"], ha="left", **kw)
                buf = io.BytesIO()
                fig.savefig(buf, format="pdf", bbox_inches="tight", pad_inches=0, transparent=True)
            _write(out, buf.getvalue())
            meta.write_text(json.dumps({"size": used}))
        r = self._doc(str(out))[0].rect
        return str(out), r.width / MM, r.height / MM, json.loads(meta.read_text())["size"]

    def figure_piece(self, e, oid: str) -> tuple[Piece, dict]:
        """Another figure of the project drawn whole (fig.figure): run, drawn and composed as it would be on its
        own, then placed as one vector picture, scaled to its box. Its errors and color warnings come with it."""
        p = e.props
        path = (self.project / p["source"]).resolve()
        if not str(path).startswith(str(self.project) + os.sep):
            raise ValueError(f"{p['source']} is outside the project; a figure placed in a figure is one of its own")
        if not path.exists():
            raise FileNotFoundError(f"no figure file {p['source']}")
        if path in self._stack:
            raise ValueError(f"{path.name} can't be placed in itself")
        self.used.add(path)
        sub = run_isolated(path)
        if getattr(e, "substyle", None) is not None:
            sub.style = e.substyle
            sub.labels = e.substyle.labels
        if p.get("background", "figure") != "figure":
            sub.props["background"] = p["background"]
        outer = api._STYLE[0]
        self._stack.append(path)
        try:
            sp, sinfos, slabels = self.pieces(sub)
            self.nested[oid] = (path.name, self.jobs, self.rc, slabels)     # for source data
            said = self.preflight(sub, sp, sinfos, slabels)
        finally:
            self._stack.pop()
            api._STYLE[0] = outer
        if not p.get("letters", True):
            # drawn without its letters; its warnings still name its panels by them
            sp = [pc for pc in sp if pc.kind != "label"]
        W, H = float(sub.width), float(sub.height)
        key = _h("figure", W, H, self.c(sub.props["background"]), [(pc.key, [round(v, 4) for v in pc.box]) for pc in sp])
        out = self.cache / "pieces" / f"{key}.pdf"
        if not out.exists():
            doc = self.compose(sub, sp)
            tmp = out.with_suffix(".tmp")
            doc.save(str(tmp), garbage=3, deflate=True)
            tmp.replace(out)
        w, h = p["w"], p["h"]
        if w is None and h is None:
            w, h = W, H
        elif w is None:
            w = h * W / H
        elif h is None:
            h = w * H / W
        box = (p["x"], p["y"], w, h)
        dest = _contain(W / H, w, h) if p["w"] is not None and p["h"] is not None else (0, 0, w, h)

        def draw(page, ox, oy, dest=dest):
            page.show_pdf_page(pymupdf.Rect(ox + dest[0] * MM, oy + dest[1] * MM, ox + (dest[0] + dest[2]) * MM,
                                            oy + (dest[1] + dest[3]) * MM), self._doc(str(out)), 0, keep_proportion=False)
        # what comes along from inside: what failed, and colors that clash (sizes are checked here, as printed)
        nested = [w_ for w_ in said if w_["level"] == "error" or w_.get("colors")]
        meta = {"pdf": str(out), "scale": dest[2] / W, "vector": True, "dest": dest, "nested": nested,
                "source": str(path)}
        info = {"code": {"file": str(path), "line": 1}, "natural": [W, H], "dest": [round(v, 4) for v in dest],
                "figure": path.stem if path.parent == self.project else None,
                "scale": round(dest[2] / W, 4)}
        return Piece(oid, "figure", box, _h("fig", str(out), dest), draw, meta=meta), info

    def _raster(self, src: Path, crop, opacity: float, aspect_box: float | None, rotation: float = 0,
                flip: str | None = None) -> tuple[str, int, int]:
        """A PNG of a raster picture cropped, mirrored and turned (and cut to `aspect_box` for "cover"):
        (path, px wide, px high)."""
        st = src.stat()
        key = _h("img", str(src), st.st_mtime_ns, st.st_size, crop, opacity, aspect_box, rotation, flip)
        out = self.cache / "images" / f"{key}.png"
        from PIL import Image
        if out.exists():
            with Image.open(out) as im:
                return str(out), im.width, im.height
        with Image.open(src) as im:
            im = im.convert("RGBA") if im.mode not in ("RGB", "RGBA") or opacity < 1 else im.copy()
            if aspect_box and rotation and round(rotation) % 180 == 90:
                aspect_box = 1 / aspect_box            # cut before turning: the box is turned too
            W, H = im.size
            l, t, r, b = crop or (0, 0, 0, 0)
            box = [W * l, H * t, W * (1 - r), H * (1 - b)]
            if aspect_box:
                bw, bh = box[2] - box[0], box[3] - box[1]
                if bw / bh > aspect_box:
                    nw = bh * aspect_box
                    box[0] += (bw - nw) / 2
                    box[2] = box[0] + nw
                else:
                    nh = bw / aspect_box
                    box[1] += (bh - nh) / 2
                    box[3] = box[1] + nh
            im = im.crop(tuple(int(round(v)) for v in box))
            if flip in ("h", "v"):
                im = im.transpose(Image.FLIP_LEFT_RIGHT if flip == "h" else Image.FLIP_TOP_BOTTOM)
            if rotation:
                im = im.convert("RGBA").rotate(rotation, expand=True, resample=Image.BICUBIC)
            if opacity < 1:
                a = im.getchannel("A").point(lambda v: int(v * opacity))
                im.putalpha(a)
            im.save(out)
            return str(out), im.width, im.height

    def _mirrored(self, src: Path, flip: str) -> Path:
        """A PDF of a PDF or SVG picture mirrored left to right ("h") or top to bottom ("v"), still vector."""
        st = src.stat()
        out = self.cache / "images" / f"{_h('mirror', str(src), st.st_mtime_ns, st.st_size, flip)}.pdf"
        if not out.exists():
            r = self._doc(str(src))[0].rect
            doc = pymupdf.open()
            page = doc.new_page(width=r.width, height=r.height)
            page.show_pdf_page(page.rect, self._doc(str(src)), 0)
            flipm = f"-1 0 0 1 {r.width:.4f} 0 cm" if flip == "h" else f"1 0 0 -1 0 {r.height:.4f} cm"
            xrefs = page.get_contents()
            body = b"\n".join(doc.xref_stream(x) for x in xrefs)
            doc.update_stream(xrefs[0], f"q {flipm}\n".encode() + body + b"\nQ")
            for x in xrefs[1:]:
                doc.update_stream(x, b"")
            tmp = out.with_suffix(".tmp")
            doc.save(str(tmp))
            tmp.replace(out)
        return out

    def image_box(self, props: dict) -> tuple:
        h = props["h"]
        if h is None:
            h = props["w"] / image_aspect(str(self.project / props["source"]), props["crop"], props.get("rotation") or 0)
        return (props["x"], props["y"], props["w"], h)

    def image_piece(self, p: dict, box: tuple, oid: str, fit: str) -> Piece:
        """A picture filling `box` (mm) as `fit` says: contain, cover or stretch."""
        src = (self.project / p["source"]).resolve()
        x, y, w, h = box
        ext = src.suffix.lower()
        meta = {"source": str(src)}
        rot = p.get("rotation") or 0
        if ext in VECTOR:
            if round(rot) % 90:
                raise ValueError("PDF and SVG pictures turn in quarter turns (90, 180, 270)")
            quarter = round(rot) % 180 == 90
            flip = p.get("flip")
            if flip in ("h", "v"):
                src = self._mirrored(src, flip)         # a PDF of the picture mirrored; the crop is the original's
            doc = self._doc(str(src))
            pr = doc[0].rect
            l, t, r, b = p["crop"] or (0, 0, 0, 0)
            if flip == "h":
                l, r = r, l
            elif flip == "v":
                t, b = b, t
            clip = pymupdf.Rect(pr.x0 + pr.width * l, pr.y0 + pr.height * t, pr.x1 - pr.width * r, pr.y1 - pr.height * b)
            dest = (0, 0, w, h)
            aspect = clip.height / clip.width if quarter else clip.width / clip.height
            if fit == "contain":
                dest = _contain(aspect, w, h)
            elif fit == "cover":
                clip = _cover_clip(clip, (h / w) if quarter else (w / h))
            st = src.stat()
            key = _h("vec", str(src), st.st_mtime_ns, st.st_size, tuple(clip), dest, rot)

            def draw(page, ox, oy, clip=clip, dest=dest):
                rect = pymupdf.Rect(ox + dest[0] * MM, oy + dest[1] * MM, ox + (dest[0] + dest[2]) * MM,
                                    oy + (dest[1] + dest[3]) * MM)
                page.show_pdf_page(rect, self._doc(str(src)), 0, clip=clip, keep_proportion=False, rotate=-rot)
            meta.update(vector=True, pdf=str(src), scale=(dest[2] * MM) / (clip.height if quarter else clip.width),
                        clip=tuple(clip), dest=dest)
            return Piece(oid, "image", box, key, draw, meta=meta)
        png, pw, ph = self._raster(src, p["crop"], p["opacity"], (w / h) if fit == "cover" else None, rot, p.get("flip"))
        dest = _contain(pw / ph, w, h) if fit == "contain" else (0, 0, w, h)
        key = _h("ras", png, dest)

        def draw(page, ox, oy, dest=dest):
            rect = pymupdf.Rect(ox + dest[0] * MM, oy + dest[1] * MM, ox + (dest[0] + dest[2]) * MM,
                                oy + (dest[1] + dest[3]) * MM)
            page.insert_image(rect, filename=png, keep_proportion=False)
        meta.update(vector=False, px=(pw, ph), dpi=pw / (dest[2] / 25.4) if dest[2] > 0 else 0, dest=dest,
                    turned=bool(rot and round(rot) % 90))
        return Piece(oid, "image", box, key, draw, meta=meta)

    # --- the figure ---

    def pieces(self, fig: api.Figure) -> tuple[list[Piece], list[dict], list[dict]]:
        """The figure's pieces in drawing order, a description of each element, and its labels.

        Panels are planned first (layouts solved, legends collected), then drawn and measured, then the plot
        areas of panels that share an edge are lined up, and only then are the pieces made."""
        style = fig.style
        api._STYLE[0] = style
        rc = self.rc_for(style)
        family = rc["font.sans-serif"]
        deps = self.fingerprint()
        dests: dict[int, tuple] = {}                # id(picture Element or Node) -> where the picture is drawn (mm)
        rasters: dict[int, dict] = {}               # id(picture) -> its piece's meta, for scale bars
        zooms = []
        _sync_zooms(fig, self.project)
        steps, infos, panels = [], [], []           # steps: Pieces, or panel jobs (dicts) drawn after aligning
        boxes: dict[int, tuple] = {}                # id(Element or Node) -> box, for insets
        oids: dict[int, str] = {}                   # id(Element or Node) -> oid

        def code_info(props):
            """Where the code that draws a panel is: its file, and the line of its function."""
            src = props["source"]
            if callable(src):
                return {"file": inspect.getsourcefile(src), "line": src.__code__.co_firstlineno}
            path = (self.project / src).resolve()
            line = 1
            try:
                text = path.read_text(encoding="utf-8")
                m = re.search(rf"^[ \t]*def[ \t]+{re.escape(props.get('function') or 'draw')}[ \t]*\(", text, re.M)
                if m:
                    line = text[:m.start()].count("\n") + 1
            except OSError:
                pass
            return {"file": str(path), "line": line, "function": props.get("function") or "draw"}

        def panel_job(oid, props, box, align, collect, background=None):
            steps.append({"oid": oid, "props": props, "box": box, "align": align and not (props.get("rotation") or 0),
                          "collect": collect, "background": background})

        def picture(obj, pc):
            d = pc.meta.get("dest") or (0, 0, pc.box[2], pc.box[3])
            dests[id(obj)] = (pc.box[0] + d[0], pc.box[1] + d[1], d[2], d[3])
            rasters[id(obj)] = pc.meta
            return pc

        table_page = isinstance(fig, api.Table)
        if table_page:
            self._table_widths(fig, family)
        for i, e in enumerate(fig.elements):
            oid = str(i)
            p = e.props
            info = {"oid": oid, "kind": e.kind, "method": e.method, "site": e.site, "props": _jsonable(p),
                    "group": p.get("group"), "name": p.get("name"), "locked": bool(p.get("locked")),
                    "hidden": bool(p.get("hidden"))}
            if p.get("hidden"):
                try:
                    box = self.image_box(p) if e.kind == "image" else e.box
                except Exception:                          # noqa: BLE001
                    box = _rough_box(e)
                boxes[id(e)] = box
                oids[id(e)] = oid
                info["box"] = [round(v, 4) for v in box]
                infos.append(info)
                continue
            try:
                if e.kind == "panel":
                    box = (p["x"], p["y"], p["w"], p["h"])
                    info["code"] = code_info(p)
                    panel_job(oid, p, box, style.align_axes, False)
                    panels.append((p, oid, box))
                elif e.kind == "image":
                    self.used.add((self.project / p["source"]).resolve())
                    box = self.image_box(p)
                    pc = picture(e, self.image_piece(p, box, oid, p["fit"] if p["h"] is not None else "stretch"))
                    steps.append(pc)
                    info["code"] = {"file": pc.meta["source"], "line": 1}
                    panels.append((p, oid, box))
                elif e.kind == "layout":
                    box = e.box
                    at = len(infos)
                    info["children"], info["dividers"] = self._plan_layout(i, e, rc, deps, steps, infos, panels,
                                                                           boxes, oids, code_info, panel_job, style,
                                                                           dests, rasters)
                elif e.kind == "inset":
                    host = boxes.get(id(e.host))
                    if host is None:
                        raise ValueError("an inset's `within` must be a panel placed before it")
                    hx, hy, hw, hh = host
                    box = (hx + p["x"] * hw, hy + p["y"] * hh, p["w"] * hw, p["h"] * hh)
                    item = e.node
                    info.update(inner=item.kind, item=_jsonable(item.props), itemSite=item.site,
                                itemMethod=item.method, host=oids.get(id(e.host)))
                    if item.kind == "panel":
                        info["code"] = code_info(item.props)
                        panel_job(oid, item.props, box, False, False, self.c(fig.props["background"]) or "#FFFFFF")
                    else:
                        pc = picture(e, self.image_piece(item.props, box, oid, item.props["fit"]))
                        dests[id(item)] = dests[id(e)]
                        rasters[id(item)] = pc.meta
                        steps.append(pc)
                    if item.props.get("label") not in (None, "auto") or p.get("label"):
                        panels.append(({**item.props, "label": p.get("label") or item.props.get("label")}, oid, box))
                elif e.kind == "text":
                    size = p["size"] or style.size
                    fam = [p["font"]] + family if p["font"] else family
                    text, markdown = p["text"], False
                    if p.get("source"):                      # the text of a file (Markdown)
                        src = (self.project / p["source"]).resolve()
                        self.used.add(src)
                        text, markdown = src.read_text(encoding="utf-8").rstrip(), True
                        info["code"] = {"file": str(src), "line": 1}
                    if p.get("width") and not p["rotation"] and (markdown or p.get("height") or is_flowing(text)):
                        pdf, w, h, used = self.flow_piece(text, markdown, size, p["weight"], p["style"], p["color"],
                                                          fam, p["align"], p["width"], p.get("opacity", 1.0),
                                                          p.get("height"), p.get("fit"), style.min_font)
                        if p.get("height") and h > p["height"] + 0.05:
                            info["overflow"] = round(h - p["height"], 1)
                        if used != size:
                            info["shrunk"] = used
                        size = used
                    else:
                        pdf, w, h = self.text_piece(text, size, p["weight"], p["style"], p["color"], p["rotation"],
                                                    fam, p["align"], p.get("width"), opacity=p.get("opacity", 1.0))
                    x0 = p["x"] - {"left": 0, "center": w / 2, "right": w}.get(p["align"], 0)
                    box = (x0, p["y"], w, h)
                    steps.append(_pdf_piece(self, oid, "text", box, pdf, meta={"pdf": pdf, "scale": 1.0, "size": size}))
                elif e.kind == "table":
                    size = p["size"] or style.size
                    fam = [p["font"]] + family if p["font"] else family
                    if p.get("rows") is None and callable(p["source"]):
                        fn = inspect.unwrap(p["source"])               # through @cached
                        info["code"] = {"file": inspect.getsourcefile(fn), "line": fn.__code__.co_firstlineno}
                        info["props"]["source"] = None
                        info["fromCode"] = getattr(fn, "__name__", "a function")
                    elif p.get("rows") is None:
                        self.used.add(self.resolve(p["source"]))
                    rows = table_rows(p, self.project)
                    pdf, w, h = self.table_piece(rows, p["header"], size, fam, p["color"], p.get("w"),
                                                 p.get("decimals"), p.get("align"), p.get("grid", False))
                    box = (p["x"], p["y"], w, h)
                    info["props"]["cells"] = _jsonable([list(r) for r in rows])      # for the editor's cell grid
                    info["grid"] = getattr(self, "last_table_grid", None)
                    steps.append(_pdf_piece(self, oid, "table", box, pdf, meta={"pdf": pdf, "scale": 1.0, "size": size}))
                elif e.kind == "legend":
                    pdf, w, h = self.legend_piece(_legend_entries(p), rc, p["title"], p["ncol"], p["size"])
                    box = (p["x"], p["y"], w, h)
                    steps.append(_pdf_piece(self, oid, "legend", box, pdf, meta={"size": p["size"] or style.size}))
                elif e.kind in ("line", "rect", "ellipse"):
                    pc = _shape_piece(oid, e, self.c)
                    box = pc.box
                    steps.append(pc)
                    if e.kind == "rect" and p.get("label") is not None:     # lettered like a panel
                        panels.append((p, oid, (p["x"], p["y"], p["w"], p["h"])))
                elif e.kind == "path":
                    pc = _path_piece(oid, e, self.c)
                    box = pc.box
                    steps.append(pc)
                elif e.kind == "figure":
                    pc, more = self.figure_piece(e, oid)
                    box = pc.box
                    info.update(more)
                    steps.append(pc)
                    if p.get("label") is not None:           # lettered as a panel of this figure
                        panels.append((p, oid, box))
                elif e.kind == "qr":
                    pc = _qr_piece(oid, p)
                    box = pc.box
                    info["module"] = round(pc.meta["module"], 3)
                    steps.append(pc)
                elif e.kind == "zoom":
                    zooms.append((len(steps), e, info))
                    box = (0, 0, 0, 0)
                elif e.kind == "scalebar":
                    host = e.host
                    meta = rasters.get(id(host))
                    if meta is None or id(host) not in dests:
                        raise ValueError("a scale bar goes on a picture placed before it (fig.image, or an Image)")
                    if meta.get("vector") or meta.get("turned"):
                        raise ValueError("a scale bar needs a PNG, TIFF or JPEG picture (not PDF/SVG, not turned)")
                    pc = self._scalebar_piece(oid, p, dests[id(host)], meta, family, style)
                    box = pc.box
                    info["host"] = oids.get(id(host))
                    info["anchor"] = pc.meta["anchor"]
                    info["dest"] = [round(v, 4) for v in dests[id(host)]]
                    info["pixelSize"] = round(pc.meta["pixel_size"], 6)
                    info["pixelFrom"] = pc.meta["pixel_from"]
                    steps.append(pc)
                elif e.kind == "bracket":
                    size = p["size"] or style.size
                    pdf, tw, th = self.text_piece(p["text"], size, "normal", "normal", p["color"], 0, family, "center")
                    pc = _bracket_piece(self, oid, e, pdf, tw, th)
                    pc.meta["size"] = size
                    box = pc.box
                    steps.append(pc)
                else:
                    continue
            except Exception as exc:                     # noqa: BLE001 - a bad element must not sink the figure
                box = _rough_box(e)
                info["error"] = f"{type(exc).__name__}: {exc}"
                steps.append(_error_piece(oid, box, info["error"]))
                # it keeps its letter, so one broken picture doesn't reletter every panel after it
                if (e.kind == "image" or (e.kind in ("rect", "figure") and p.get("label") is not None)) \
                        and not any(o == oid for _, o, _ in panels):
                    panels.append((p, oid, box))
            boxes[id(e)] = box
            oids[id(e)] = oid
            info["box"] = [round(v, 4) for v in box]
            if e.kind == "layout":
                infos.insert(at, info)                  # the layout before its children
            else:
                infos.append(info)

        # draw every panel as its code lays it out, and measure its plot area
        # zoom boxes, once everything they join is placed
        for at, e, info in reversed(zooms):
            try:
                pc, region = self._zoom_piece(info["oid"], e, boxes, dests)
                steps.insert(at, pc)
                info["box"] = [round(v, 4) for v in region]
                info["source"], info["target"] = oids.get(id(e.source)), oids.get(id(e.target))
                src_rect = dests.get(id(e.source)) or boxes.get(id(e.source))
                info["sourceBox"] = [round(v, 4) for v in src_rect]
            except Exception as exc:                   # noqa: BLE001
                info["error"] = f"{type(exc).__name__}: {exc}"
        jobs = [s_ for s_ in steps if isinstance(s_, dict)]
        for j in jobs:
            if not callable(j["props"]["source"]):
                self.used.add((self.project / j["props"]["source"]).resolve())
            _, _, w, h = j["box"]
            if round(j["props"].get("rotation") or 0) % 180 == 90:
                w, h = h, w                             # drawn upright, then turned into its box
            j["size"] = (w, h)
            j["base"] = self._panel_base(j["props"], w, h, rc, deps, j["collect"], j["background"])
        self.jobs, self.rc = jobs, rc                   # the panels and how they were drawn, for source data
        targets = _align_targets(jobs) if jobs else {}
        pieces = []
        for s_ in steps:
            if not isinstance(s_, dict):
                pieces.append(s_)
                continue
            oid, box, base = s_["oid"], s_["box"], s_["base"]
            info = next((x for x in infos if x["oid"] == oid), None)
            if base.get("error"):
                if info is not None:
                    info["error"] = base["error"]
                pieces.append(_error_piece(oid, box, base["error"]))
                continue
            try:
                pdf, found = self._panel_aligned(s_["props"], s_["size"][0], s_["size"][1], rc, deps, s_["collect"],
                                                 base, targets.get(oid), s_["background"])
            except Exception:                           # noqa: BLE001
                pdf, found = base["pdf"], {"texts": base.get("texts") or [], "marks": base.get("marks") or []}
            texts, marks = found.get("texts") or [], found.get("marks") or []
            rot = s_["props"].get("rotation") or 0
            if info is not None:
                info["margins"] = base.get("margins")
                if base.get("warnings"):
                    info["pyWarnings"] = base["warnings"]
                info["aligned"] = targets.get(oid)
                if not rot:                             # where its texts and marks are, to retype and recolor them
                    info["texts"] = [{**t, "box": [round(box[0] + t["box"][0], 3), round(box[1] + t["box"][1], 3),
                                                   round(t["box"][2], 3), round(t["box"][3], 3)]} for t in texts]
                    info["marks"] = [{**m, "box": [round(box[0] + m["box"][0], 3), round(box[1] + m["box"][1], 3),
                                                   round(m["box"][2], 3), round(m["box"][3], 3)]} for m in marks]
                else:
                    info["marks"] = [{**m, "box": [0, 0, 0, 0]} for m in marks]

            def draw(page, ox, oy, pdf=pdf, w=box[2], h=box[3], rot=rot):
                page.show_pdf_page(pymupdf.Rect(ox, oy, ox + w * MM, oy + h * MM), self._doc(pdf), 0,
                                   keep_proportion=False, rotate=-rot)
            pieces.append(Piece(oid, "panel", box, _h("p", pdf, rot), draw, meta={"pdf": pdf, "scale": 1.0}))
        if table_page:
            _stack_table(fig, pieces, infos)
        label_pieces, label_infos = self._labels(fig, panels, family)
        # labels are drawn above everything
        return pieces + label_pieces, infos, label_infos

    def _table_widths(self, fig, family):
        """A Table's title and notes wrap to the width of its cells (at least 60 mm)."""
        cells = fig.cells.props
        try:
            fam = [cells["font"]] + family if cells["font"] else family
            _, w, _ = self.table_piece(table_rows(cells, self.project), cells["header"],
                                       cells["size"] or fig.style.size, fam, cells["color"], cells.get("w"),
                                       cells.get("decimals"), cells.get("align"), cells.get("grid", False))
        except Exception:                                  # noqa: BLE001  (the table's own step reports it)
            w = 60.0
        for e in fig.elements:
            if e.props.get("role") in ("title", "notes"):
                e.props["width"] = round(max(w, 60.0), 2)

    def _plan_layout(self, i, e, rc, deps, steps, infos, panels, boxes, oids, code_info, panel_job, style,
                     dests=None, rasters=None):
        """Solve a layout: its children's boxes (as panel jobs and pieces) and its draggable dividers."""
        p = e.props
        x, y, w, h = e.box
        cells = (x, y, w, h)
        legend = None
        if p["guides"] == "collect":
            leaves0, _ = layout.solve(e.node, cells, p["gap"], p["widths"], p["heights"], e.site)
            seen, entries = set(), []
            for leaf, lb in leaves0:
                if isinstance(leaf, api.Panel):
                    for en in self._entries(leaf.props, lb[2], lb[3], rc, deps):
                        k = json.dumps(en, sort_keys=True)
                        if k not in seen:
                            seen.add(k)
                            entries.append(en)
            if entries:
                # a legend under the panels runs across, one beside them runs down (as in patchwork)
                pdf, lw, lh = self.legend_piece(entries, rc, ncol=len(entries) if p["legend"] == "bottom" else 1)
                if p["legend"] == "bottom":
                    cells = (x, y, w, max(h - lh - p["gap"], 1))
                    lbox = (x + (w - lw) / 2, y + h - lh, lw, lh)
                else:
                    cells = (x, y, max(w - lw - p["gap"], 1), h)
                    lbox = (x + w - lw, y + (h - lh) / 2, lw, lh)
                legend = (pdf, lbox)
        leaves, dividers = layout.solve(e.node, cells, p["gap"], p["widths"], p["heights"], e.site)
        children = []
        for k, (leaf, lb) in enumerate(leaves):
            oid = f"{i}.{k}"
            boxes[id(leaf)] = lb
            oids[id(leaf)] = oid
            if isinstance(leaf, api.Spacer):
                continue
            info = {"oid": oid, "kind": leaf.kind, "method": leaf.method, "site": leaf.site,
                    "props": _jsonable(leaf.props), "parent": str(i), "group": None, "name": leaf.props.get("name"),
                    "box": [round(v, 4) for v in lb]}
            if isinstance(leaf, api.Panel):
                info["code"] = code_info(leaf.props)
                panel_job(oid, leaf.props, lb, style.align_axes and p["align"], p["guides"] == "collect")
            else:
                self.used.add((self.project / leaf.props["source"]).resolve())
                pc = self.image_piece(leaf.props, lb, oid, leaf.props["fit"])
                d = pc.meta.get("dest") or (0, 0, lb[2], lb[3])
                dests[id(leaf)] = (lb[0] + d[0], lb[1] + d[1], d[2], d[3])
                rasters[id(leaf)] = pc.meta
                info["code"] = {"file": pc.meta["source"], "line": 1}
                steps.append(pc)
            panels.append((leaf.props, oid, lb))
            infos.append(info)
            children.append(oid)
        if legend:
            pdf, lbox = legend
            loid = f"{i}.legend"
            steps.append(_pdf_piece(self, loid, "legend", lbox, pdf, meta={"size": style.size}))
            infos.append({"oid": loid, "kind": "legend", "method": "collected", "site": e.site, "props": {},
                          "parent": str(i), "group": None, "name": None, "box": [round(v, 4) for v in lbox]})
            children.append(loid)
        divs = [{**d, "at": round(d["at"], 3), "from": round(d["from"], 3), "to": round(d["to"], 3)} for d in dividers]
        return children, divs

    def _zoom_piece(self, oid, e, boxes, dests):
        """The zoom box's rectangle on its source and the lines to its target; and the rectangle (mm)."""
        p = e.props
        src = dests.get(id(e.source)) or boxes.get(id(e.source))
        tgt = dests.get(id(e.target)) or boxes.get(id(e.target))
        if src is None or tgt is None:
            raise ValueError("a zoom joins two panels or pictures placed before it")
        rx, ry, rw, rh = p["region"]
        reg = (src[0] + rx * src[2], src[1] + ry * src[3], rw * src[2], rh * src[3])
        lines = _zoom_lines(reg, tgt) if p["lines"] else []
        xs = [reg[0], reg[0] + reg[2], tgt[0], tgt[0] + tgt[2]]
        ys = [reg[1], reg[1] + reg[3], tgt[1], tgt[1] + tgt[3]]
        box = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        col = rgb(self.c(p["color"]))
        key = _h("zoom", reg, tgt, lines, p["width"], p["dash"], col)

        def draw(page, ox, oy):
            X = lambda v: ox + (v - box[0]) * MM          # noqa: E731
            Y = lambda v: oy + (v - box[1]) * MM          # noqa: E731
            sh = page.new_shape()
            sh.draw_rect(pymupdf.Rect(X(reg[0]), Y(reg[1]), X(reg[0] + reg[2]), Y(reg[1] + reg[3])))
            for (a, b) in lines:
                sh.draw_line(pymupdf.Point(X(a[0]), Y(a[1])), pymupdf.Point(X(b[0]), Y(b[1])))
            sh.finish(color=col, width=p["width"], dashes=_dashes(p["dash"], p["width"]))
            sh.commit()
        return Piece(oid, "zoom", box, key, draw, pad=p["width"] / MM), reg

    def _scalebar_piece(self, oid, p, dest, meta, family, style) -> Piece:
        """A scale bar on a picture: its length from the picture's pixels and `pixel_size`, or, without one, the
        pixel size in the picture's metadata. Preflight hears of a bar with neither, or one that disagrees."""
        pw = meta["px"][0]
        mm_per_px = dest[2] / pw
        name = Path(meta["source"]).name
        found = pixel_size_of(meta["source"])
        per_unit = UNITS_M.get(_unit_key(p["unit"]))
        from_meta = found[0] / per_unit if found and per_unit else None    # in the bar's unit
        checks = []
        px_size = p["pixel_size"]
        if px_size is None and from_meta:
            px_size, how = from_meta, f"from {name}'s {found[1]} metadata"
        elif px_size is None:
            px_size, how = 1.0, "not set"
            checks.append({"level": "warning", "message": (
                f"the pixel size isn't given and {name} has none in its metadata"
                + ("" if per_unit else f" that converts to {p['unit']}")
                + f", so the bar assumes one pixel is 1 {p['unit']}: give pixel_size= (from the microscope)")})
        else:
            how = "given"
            if from_meta and abs(px_size - from_meta) > 0.01 * from_meta:
                checks.append({"level": "warning", "message": (
                    f"pixel_size={px_size:g} {p['unit']}, but {name}'s {found[1]} metadata says one pixel is "
                    f"{from_meta:.4g} {p['unit']}; check which is right (a picture resized after it was taken "
                    f"keeps metadata that no longer fits)")})
        bar = p["length"] / px_size * mm_per_px
        size = p["size"] or style.size
        label = f"{p['length']:g} {p['unit']}"
        tpdf, tw, th = (self.text_piece(label, size, "bold", "normal", p["color"], 0, family, "center", keep_color=True)
                        if p["label"] else (None, 0.0, 0.0))
        gap = 0.5 if p["label"] else 0.0
        w = max(bar, tw)
        h = p["thickness"] + gap + th
        if p["x"] is not None and p["y"] is not None:
            bx = dest[0] + p["x"] * dest[2]
            by = dest[1] + p["y"] * dest[3]             # the bar's bottom
        else:
            corner, m = p["corner"], p["margin"]
            bx = dest[0] + m if "left" in corner else dest[0] + dest[2] - m - bar
            by = dest[1] + m + h if "upper" in corner else dest[1] + dest[3] - m
        box = (min(bx, bx + bar / 2 - tw / 2), by - h, w, h)
        col = rgb(p["color"])
        key = _h("scalebar", bar, p["thickness"], tpdf, tw, th, col, box[2:], bx - box[0])

        def draw(page, ox, oy):
            x0 = ox + (bx - box[0]) * MM
            yb = oy + h * MM
            page.draw_rect(pymupdf.Rect(x0, yb - p["thickness"] * MM, x0 + bar * MM, yb), color=None, fill=col, width=0)
            if tpdf:
                cx = x0 + bar / 2 * MM
                self._show_pdf(tpdf, page, pymupdf.Rect(cx - tw / 2 * MM, oy, cx + tw / 2 * MM, oy + th * MM))
        return Piece(oid, "scalebar", box, key, draw, meta={"size": size, "bar": bar, "anchor": [round(bx, 4), round(by, 4)],
                                                            "pixel_size": px_size, "pixel_from": how, "checks": checks})

    def _labels(self, fig, panels, family):
        lab = fig.labels
        lp = lab.props
        if lp["numbering"] == "none":                    # no panel letters at all, a panel's own label included
            return [], []
        panels = [(e, oid, box) for e, oid, box in panels if not e.get("hidden")]
        auto = [(e, oid, box) for e, oid, box in panels if e.get("label") == "auto"]
        if lp["order"] == "reading":
            auto = _reading_order(auto)
        explicit = {e["label"] for e, _, _ in panels if isinstance(e.get("label"), str) and e["label"] != "auto"}
        names, n = {}, 0
        for e, oid, box in auto:
            while True:
                n += 1
                t = lab.text(n)
                if t not in explicit:
                    break
            if t:                                       # numbering "none": no letters
                names[oid] = t
        for e, oid, box in panels:
            if isinstance(e.get("label"), str) and e["label"] != "auto":
                names[oid] = e["label"]
        # where each label goes: panel corner + the shared offset, lined up by rows and columns, then its own nudge
        # a label unhooked from its panel (label_x/label_y) stays where it was put, out of the lining up
        free = {oid for e, oid, box in panels if oid in names and e.get("label_x") is not None
                and e.get("label_y") is not None}
        base = {oid: [box[0] + lp["dx"], box[1] + lp["dy"]] for e, oid, box in panels if oid in names and oid not in free}
        if lp["align"] and base:
            for axis in (1, 0):
                keys = sorted(base, key=lambda k: base[k][axis])
                group = []
                for k in keys + [None]:
                    if k is not None and group and base[k][axis] - base[group[0]][axis] <= 3.0:
                        group.append(k)
                        continue
                    if group:
                        m = min(base[g][axis] for g in group)
                        for g in group:
                            base[g][axis] = m
                    group = [k] if k is not None else []
        fam = [lp["font"]] + family if lp["font"] else family
        pieces, infos = [], []
        for e, oid, box in panels:
            if oid not in names:
                continue
            pdf, w, h = self.text_piece(names[oid], lp["size"], lp["weight"], lp["style"], lp["color"], 0, fam)
            if oid in free:
                x, y = float(e["label_x"]), float(e["label_y"])
            else:
                x = base[oid][0] + (e.get("label_dx") or 0)
                y = base[oid][1] + (e.get("label_dy") or 0)
            lb = (x, y, w, h)
            loid = f"L{oid}"
            pieces.append(_pdf_piece(self, loid, "label", lb, pdf, meta={"pdf": pdf, "scale": 1.0, "size": lp["size"]}))
            infos.append({"oid": loid, "kind": "label", "panel": oid, "text": names[oid],
                          "auto": e.get("label") == "auto", "detached": oid in free,
                          "box": [round(v, 4) for v in lb]})
        return pieces, infos

    def compose(self, fig: api.Figure, pieces: list[Piece], bleed: float = 0.0, marks: bool = False) -> pymupdf.Document:
        """The figure as a one-page PDF. `bleed` (mm) extends the page past each edge (the background, and
        whatever reaches past the edge, prints there so trimming leaves no white line); `marks` adds crop marks
        outside it. The page's TrimBox is then the figure and its BleedBox the bleed."""
        slug = CROP_SLUG if marks else 0.0
        o = (bleed + slug) * MM
        doc = pymupdf.open()
        page = doc.new_page(width=fig.width * MM + 2 * o, height=fig.height * MM + 2 * o)
        bg = self.c(fig.props["background"])
        if bg:
            page.draw_rect(pymupdf.Rect(slug * MM, slug * MM, page.rect.width - slug * MM,
                                        page.rect.height - slug * MM), color=None, fill=rgb(bg), width=0)
        for pc in pieces:
            pc.draw(page, o + pc.box[0] * MM, o + pc.box[1] * MM)
        if bleed or marks:
            trim = pymupdf.Rect(o, o, o + fig.width * MM, o + fig.height * MM)
            if marks:
                # the bleed area past the figure is cut away: white over what reaches beyond it
                for r in (pymupdf.Rect(0, 0, page.rect.width, slug * MM),
                          pymupdf.Rect(0, page.rect.height - slug * MM, page.rect.width, page.rect.height),
                          pymupdf.Rect(0, 0, slug * MM, page.rect.height),
                          pymupdf.Rect(page.rect.width - slug * MM, 0, page.rect.width, page.rect.height)):
                    page.draw_rect(r, color=None, fill=(1, 1, 1), width=0)
                sh = page.new_shape()
                gap, length = (bleed + 1.0) * MM, 5.0 * MM
                for cx, sx in ((trim.x0, -1), (trim.x1, 1)):
                    for cy, sy in ((trim.y0, -1), (trim.y1, 1)):
                        sh.draw_line(pymupdf.Point(cx + sx * gap, cy), pymupdf.Point(cx + sx * (gap + length), cy))
                        sh.draw_line(pymupdf.Point(cx, cy + sy * gap), pymupdf.Point(cx, cy + sy * (gap + length)))
                sh.finish(color=(0, 0, 0), width=0.25)
                sh.commit()
            page.set_bleedbox(pymupdf.Rect(slug * MM, slug * MM, page.rect.width - slug * MM,
                                           page.rect.height - slug * MM))
            page.set_trimbox(trim)
        return doc

    def sprite(self, pc: Piece, px_per_mm: float) -> dict:
        """A transparent PNG of one piece: {file, x, y, w, h} (mm, where it goes on the figure)."""
        pad = pc.pad + 0.5
        x, y, w, h = pc.box
        W, H = max(w + 2 * pad, 0.2), max(h + 2 * pad, 0.2)
        # a large piece (a figure placed whole on a poster) at a high zoom is shown from fewer pixels, scaled up
        px_per_mm = min(px_per_mm, math.sqrt(MAX_SPRITE_PX / (W * H)), MAX_SPRITE_SIDE / max(W, H))
        out = self.cache / "sprites" / f"{_h(pc.key, round(px_per_mm, 2), round(W, 3), round(H, 3))}.png"
        if not out.exists():
            doc = pymupdf.open()
            page = doc.new_page(width=W * MM, height=H * MM)
            pc.draw(page, pad * MM, pad * MM)
            z = px_per_mm / MM
            pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=True)
            tmp = out.with_suffix(".tmp.png")
            pix.save(str(tmp))
            tmp.replace(out)
        return {"file": str(out), "x": x - pad, "y": y - pad, "w": W, "h": H}

    def render(self, file: str, px_per_mm: float = 12.0, sprites: bool = True, thumb_px: int = 320,
               dark: bool = False) -> dict:
        """Run and draw a figure file: what the editor shows and edits. `dark`: its dark version."""
        self._docs.clear()
        self.dark = dark
        file = str(self.resolve(file))
        name = Path(file).stem
        try:
            fig = self.run_figure(file)
        except Exception:                              # noqa: BLE001
            return {"figure": name, "file": str(Path(file).resolve()), "error": _user_traceback(Path(file).name)}
        self._stack, self.used = [Path(file).resolve()], {Path(file).resolve()}
        pieces, infos, labels = self.pieces(fig)
        self._note_outside(file)
        doc = self.compose(fig, pieces)
        out = self.cache / "out"
        page = doc[0]
        z = thumb_px / max(page.rect.width, page.rect.height)
        thumb = out / f"{name}{'.dark' if dark else ''}.thumb.png"
        page.get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=False).save(str(thumb))
        by_oid = {i["oid"]: i for i in infos + labels}
        if sprites:
            for pc in pieces:
                if pc.oid in by_oid:
                    by_oid[pc.oid]["sprite"] = self.sprite(pc, px_per_mm)
        st, lab = fig.style, fig.labels
        return {
            "figure": name,
            "file": str(Path(file).resolve()),
            "size": [fig.width, fig.height],
            "background": self.c(fig.props["background"]),
            "dark": dark,
            "figureSite": fig.site,
            "figureProps": _jsonable(fig.props),
            "style": {"props": _jsonable(st.props), "site": st.site, "rc": _jsonable(st.rc),
                      "palette": [[k, v] for k, v in api.palette_colors(st.props.get("palette")).items()]},
            "palettes": api.PALETTES,
            "labelStyle": {"props": _jsonable(lab.props), "site": lab.site, "preset": lab.preset,
                           "viaFigure": lab is not st.labels},
            "journal": _journal_info(fig),
            "poster": _poster_info(fig),
            "elements": infos,
            "labels": labels,
            "guides": [{"x": g["x"], "y": g["y"], "site": g["site"]} for g in fig.guides],
            "caption": {"text": fig.caption_text, "site": fig.caption_site},
            "warnings": self.preflight(fig, pieces, infos, labels),
            "thumb": str(thumb),
            "pxPerMM": px_per_mm,
            "outside": self.outside_files(file),
        }

    # --- checks ---

    def preflight(self, fig, pieces, infos, labels) -> list[dict]:
        """What a journal's production office would complain about."""
        st = fig.style
        min_font, min_dpi = st.min_font, st.min_dpi
        names = {l["panel"]: l["text"] for l in labels}
        warn = []

        def who(oid):
            info = next((i for i in infos if i["oid"] == oid), None)
            if oid in names:
                return f"panel {names[oid]}"
            if info and info["kind"] == "text":
                t = info["props"]["text"] or Path(info["props"].get("source") or "").name
                return f"text “{t[:24]}”"
            if info and info["kind"] == "figure":
                return f"figure {Path(info['props']['source']).name}"
            if info and info["kind"] == "qr":
                return "the QR code"
            if info and info["kind"] == "scalebar":
                return f"the scale bar on {who(info['host'])}" if info.get("host") else "a scale bar"
            return f"{info['kind'] if info else 'element'} {oid}"

        for info in infos:
            if info.get("error"):
                last = info["error"].strip().splitlines()[-1]
                m = re.search(r"No module named '([\w.]+)'", last)
                if m:                                   # what to do about it
                    last += (f": add {m.group(1).split('.')[0]} to the project's requirements.txt and Fignimble offers "
                             "to install it")
                warn.append({"level": "error", "oid": info["oid"], "message": f"{who(info['oid'])} failed: {last}"})
        for pc in pieces:
            m = pc.meta
            if pc.kind in ("text", "label", "bracket", "table") and m.get("size", 99) < min_font - 1e-6:
                warn.append({"level": "warning", "oid": pc.oid,
                             "message": f"{who(pc.oid)}: {m['size']:g} pt text is below the {min_font:g} pt minimum"})
            for w_ in m.get("checks") or []:            # a piece's own findings (a scale bar's pixel size)
                warn.append({**w_, "oid": pc.oid, "message": f"{who(pc.oid)}: {w_['message']}"})
            for w_ in m.get("nested") or []:            # from inside a figure placed whole
                warn.append({**w_, "oid": pc.oid, "message": f"{who(pc.oid)}: {w_['message']}"})
            if m.get("pdf") and pc.kind in ("panel", "image", "figure"):
                try:
                    page = self._doc(m["pdf"])[0]
                except Exception:                      # noqa: BLE001
                    continue
                s = m.get("scale", 1.0)
                facts = self._facts(m["pdf"], page)
                if facts["min_size"] and facts["min_size"] * s < min_font - 0.05:
                    warn.append({"level": "warning", "oid": pc.oid,
                                 "message": f"{who(pc.oid)}: text as small as {facts['min_size'] * s:.1f} pt "
                                            f"(minimum {min_font:g} pt)"})
                if pc.oid in names and facts["letters"]:
                    warn.append({"level": "warning", "oid": pc.oid,
                                 "message": f"{who(pc.oid)}: has its own letter “{facts['letters'][0]}” drawn inside it "
                                            f"as well; remove it from the panel's code or picture (Fignimble letters "
                                            "the panels)"})
                other = [f for f in facts["fonts"] if not _same_font(f, st.font)]
                if other and st.font:
                    warn.append({"level": "warning", "oid": pc.oid,
                                 "message": f"{who(pc.oid)}: text in {_font_family(other[0])}, not the style's "
                                            f"{st.font}; draw it in {st.font} so every panel matches"})
                if facts["type3"]:
                    warn.append({"level": "warning", "oid": pc.oid,
                                 "message": f"{who(pc.oid)}: Type 3 font {facts['type3']}; journals want TrueType or "
                                            "Type 1 (pdf.fonttype 42)"})
                for im in facts["images"]:
                    bw = (im["bbox"][2] - im["bbox"][0]) * s / 72
                    if bw > 0.2 and im["width"] / bw < min_dpi * 0.98:
                        warn.append({"level": "warning", "oid": pc.oid,
                                     "message": f"{who(pc.oid)}: a picture at {im['width'] / bw:.0f} dpi "
                                                f"(minimum {min_dpi:g})"})
                        break
            if pc.kind == "image" and not m.get("vector") and 0 < m.get("dpi", 1e9) < min_dpi * 0.98:
                warn.append({"level": "warning", "oid": pc.oid,
                             "message": f"{who(pc.oid)}: {m['dpi']:.0f} dpi at this size (minimum {min_dpi:g}); "
                                        "make it smaller or use a larger picture"})
        W, H = fig.width, fig.height
        poster = fig.props.get("poster")
        margin = fig.props.get("margin")
        # what must print, kept off a poster's edges; shapes may run to the edge or past it (into the bleed)
        shapes = ("rect", "ellipse", "line", "path", "zoom")
        for info in infos + labels:
            x, y, w, h = info["box"]
            if poster and info["kind"] in shapes:
                continue
            if x < -0.01 or y < -0.01 or x + w > W + 0.01 or y + h > H + 0.01:
                warn.append({"level": "warning", "oid": info["oid"],
                             "message": f"{who(info.get('panel', info['oid']))}"
                                        f"{' label' if info['kind'] == 'label' else ''} reaches outside the figure"})
            elif margin and not info.get("parent") and w > 0 and (
                    x < margin - 0.01 or y < margin - 0.01 or x + w > W - margin + 0.01 or y + h > H - margin + 0.01):
                warn.append({"level": "warning", "oid": info["oid"],
                             "message": f"{who(info.get('panel', info['oid']))}"
                                        f"{' label' if info['kind'] == 'label' else ''} is within the {margin:g} mm "
                                        "margin along the edge, which printers may trim"})
        for info in infos:
            if info.get("overflow"):
                fix = (f"even at the {min_font:g} pt minimum; shorten it or make the box taller"
                       if info["props"].get("fit") == "shrink" else
                       "shorten it, make the box taller, or fit=\"shrink\"")
                warn.append({"level": "warning", "oid": info["oid"],
                             "message": f"{who(info['oid'])}: runs {info['overflow']:g} mm past its "
                                        f"{info['props']['height']:g} mm box, {fix}"})
            if info["kind"] == "qr" and info.get("module") is not None:
                if info["module"] < 0.4:
                    warn.append({"level": "warning", "oid": info["oid"],
                                 "message": f"the QR code's squares are {info['module']:.2f} mm; at under 0.4 mm "
                                            "phones may not read it: make it larger or its data shorter"})
                elif poster and info["box"][2] < 30:
                    warn.append({"level": "info", "oid": info["oid"],
                                 "message": "a QR code on a poster scans from a step away when it is 3 cm or more"})
        for w_ in _color_vision(infos, fig):
            if w_["oid"] is not None:
                w_["message"] = who(w_["oid"]) + w_["message"][w_["message"].index(":"):]
            warn.append(w_)
        boxes = [i for i in infos if i["kind"] in ("panel", "image", "figure", "table") and not i.get("hidden")
                 and not isinstance(fig, api.Table)]
        for k, a in enumerate(boxes):
            for b in boxes[k + 1:]:
                ax, ay, aw, ah = a["box"]
                bx, by, bw, bh = b["box"]
                ow, oh = min(ax + aw, bx + bw) - max(ax, bx), min(ay + ah, by + bh) - max(ay, by)
                if ow > 0.5 and oh > 0.5:
                    warn.append({"level": "warning", "oid": b["oid"],
                                 "message": f"{who(a['oid'])} and {who(b['oid'])} overlap by {ow:.1f} x {oh:.1f} mm; "
                                            "move one, or make the smaller an inset (fig.inset)"})
        if labels:
            for info in infos:
                t = (info["props"].get("text") or "").strip().strip("*")
                if info["kind"] == "text" and _LETTER.match(t):
                    warn.append({"level": "warning", "oid": info["oid"],
                                 "message": f"{who(info['oid'])} looks like a panel letter typed by hand; Fignimble "
                                            "letters the panels (Labels in the style), so remove it"})
        seen = {}
        for l in labels:
            if l["text"] in seen:
                warn.append({"level": "warning", "oid": l["oid"], "message": f"two panels are labeled {l['text']}"})
            seen[l["text"]] = 1
        for info in infos:
            if info["kind"] == "table" and info["props"].get("cells") and not info.get("hidden"):
                warn += [{**w_, "oid": info["oid"]} for w_ in tables.check(info["props"]["cells"],
                                                                           info["props"].get("header", True),
                                                                           info["props"].get("decimals"))]
        if isinstance(fig, api.Table):
            if not any(i["props"].get("role") == "title" for i in infos):
                warn.append({"level": "info", "oid": None,
                             "message": "the table has no title; journals ask for one (Table(..., title=...))"})
            return warn
        j = _journal_info(fig)
        if j and not j["fits"]:
            warn.append({"level": "info", "oid": None,
                         "message": f"{j['name']} figures are {', '.join(f'{v:g}' for v in j['widths'].values())} mm "
                                    f"wide; this one is {W:g} mm"})
        return warn

    _facts_cache: dict = {}

    def _facts(self, pdf: str, page) -> dict:
        """What preflight needs from a panel's PDF (smallest text, Type 3 fonts, pictures), once per drawing."""
        got = self._facts_cache.get(pdf)
        if got is None:
            spans = [{**sp, "dir": ln.get("dir", (1, 0))} for b in page.get_text("dict").get("blocks", [])
                     for ln in b.get("lines", []) for sp in ln.get("spans", []) if sp.get("text", "").strip()]
            sizes = [sp["size"] for sp in spans if not _is_script(sp, spans)]
            W, H = page.rect.width, page.rect.height
            # a bold lone letter in the top-left corner: a panel letter the drawing makes itself
            letters = [sp["text"].strip() for sp in spans
                       if _LETTER.match(sp["text"].strip()) and (sp["flags"] & 16 or "bold" in sp["font"].lower())
                       and sp["bbox"][0] < 0.15 * W and sp["bbox"][1] < 0.15 * H]
            fonts = sorted({sp["font"].split("+")[-1] for sp in spans
                            if re.search(r"[A-Za-z0-9]", sp["text"]) and not _SYMBOL_FONT.search(sp["font"])})
            got = {"min_size": min(sizes) if sizes else None, "letters": letters, "fonts": fonts,
                   "type3": next((f[3] for f in page.get_fonts() if f[2] == "Type3"), None),
                   "images": [{"bbox": tuple(i["bbox"]), "width": i["width"]} for i in page.get_image_info()]}
            self._facts_cache[pdf] = got
        return got

    def export(self, file: str, path: str, fmt: str | None = None, dpi: int = 600, dark: bool = False,
               cmyk: bool = False, outline: bool = False, journal: str | None = None, bleed: float = 0.0,
               marks: bool = False) -> dict:
        """Write the figure to `path` as PDF, SVG, EPS, PNG, TIFF, JPEG or PPTX (`dark`: its dark version). `cmyk`
        converts colors for print (PDF, EPS, TIFF, JPEG); `outline` turns text into shapes (PDF, EPS, SVG).
        PDF/EPS conversions use Ghostscript.

        `dpi` is the resolution of everything drawn as pixels: the whole figure for TIFF, JPEG and PNG; for PDF and
        EPS what panels draw as rasters (heatmaps, rasterized scatters) and what Ghostscript must rasterize for EPS
        (pictures placed from files keep their own pixels). It is never below what `journal` asks (else the style's
        journal, else the usual standard; see journals.dpi): the line-art dpi, or the combination dpi when the figure
        holds pictures, for TIFF and JPEG; the photo dpi for rasters inside PDF and EPS, and the line-art dpi for
        what Ghostscript rasterizes. The file written is then measured, and pictures below the dpi asked for
        photographs are reported (`resolution`, warnings).

        A poster (Figure(poster=...)) is held to poster resolutions instead (journals.POSTER_DPI) unless `journal`
        is named, and its rasters to at most 300 dpi. `bleed` (mm) and `marks` (crop marks) are for PDF: the page
        grows past the figure's edges, which become its TrimBox. Every PDF, PNG, TIFF and SVG records what it was
        made from (`provenance`), for `fignimble provenance` to check against the project later."""
        self._docs.clear()
        self.dark = dark
        path = str(Path(path) if Path(path).is_absolute() else self._cwd / path)
        fig = self.run_figure(file)
        fmt = (fmt or Path(path).suffix.lstrip(".")).lower()
        if (bleed or marks) and fmt != "pdf":
            raise ValueError("bleed and crop marks are for PDF, what print shops take")
        self._stack, self.used = [self.resolve(file)], {self.resolve(file)}
        pieces, infos, labels = self.pieces(fig)
        self._note_outside(self.resolve(file))
        doc = self.compose(fig, pieces, bleed, marks)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if fmt == "png" and cmyk:
            raise ValueError("PNG has no CMYK; export a TIFF or a PDF")
        journal = (journal or fig.style.props.get("journal") or None)
        poster = fig.props.get("poster") and not journal
        need = dict(journals.POSTER_DPI) if poster else journals.dpi(journal)
        kind = "combination" if doc[0].get_image_info() else "line"
        notes = []
        used = int(dpi)
        if fmt in ("tif", "tiff", "jpg", "jpeg") and used < need[kind]:
            notes.append(f"{used} dpi raised to {need[kind]} dpi: {self._jname(journal, poster)} that of {kind} figures")
            used = need[kind]
        top = journals.JOURNALS.get((journal or "").lower(), {}).get("max_dpi")
        if poster and fmt in ("tif", "tiff", "jpg", "jpeg", "png", "pptx"):
            cap = journals.POSTER_MAX_DPI if fmt != "pptx" else 200
            if used > cap:
                notes.append(f"{used} dpi lowered to {cap} dpi: at a poster's size more would be "
                             f"{fig.width / 25.4 * used * fig.height / 25.4 * used / 1e6:.0f} megapixels")
                used = cap
        elif top and fmt in ("tif", "tiff", "jpg", "jpeg", "png") and used > top:
            notes.append(f"{used} dpi is more than the {top} dpi {journals.JOURNALS[journal.lower()]['name']} takes "
                         "without resizing the figure")
        if fmt in ("pdf", "eps") and used != 600:
            # panels' rasters at the resolution asked (never below the photo dpi): drawn again for this export
            if used < need["photo"]:
                notes.append(f"{used} dpi raised to {need['photo']} dpi: {self._jname(journal)} that of pictures")
            self.raster_dpi = max(used, need["photo"])
            try:
                pieces, infos, labels = self.pieces(fig)
                doc = self.compose(fig, pieces, bleed, marks)
            finally:
                self.raster_dpi = 600
        prov = self.provenance(file)
        if fmt in ("pdf", "eps"):
            doc.set_metadata({"creator": "Fignimble", "producer": "Fignimble (PyMuPDF)", "title": Path(file).stem,
                              "subject": provenance_line(prov), "keywords": PROVENANCE_TAG + json.dumps(prov)})
            if fmt == "pdf" and not (cmyk or outline):
                doc.save(path, garbage=3, deflate=True)
            else:
                with tempfile.TemporaryDirectory() as tmp:
                    src = str(Path(tmp) / "in.pdf")
                    doc.save(src, garbage=3, deflate=True)
                    _ghostscript(src, path, fmt, cmyk, outline, dpi=max(used, need["line"]))
        elif fmt == "svg":
            svg = doc[0].get_svg_image(text_as_path=outline)
            at = svg.find(">", svg.find("<svg")) + 1
            svg = svg[:at] + f"\n<metadata>{PROVENANCE_TAG}{_xml_escape(json.dumps(prov))}</metadata>" + svg[at:]
            Path(path).write_text(svg, encoding="utf-8")
        elif fmt in ("png", "tif", "tiff", "jpg", "jpeg"):
            z = used / 72
            alpha = fmt == "png" and not self.c(fig.props["background"])
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=alpha)
            from PIL import Image
            im = Image.frombytes("RGBA" if alpha else "RGB", (pix.width, pix.height), pix.samples)
            if cmyk:
                im = _to_cmyk(im)
            if fmt == "png":
                from PIL.PngImagePlugin import PngInfo
                meta = PngInfo()
                meta.add_text("fignimble", json.dumps(prov))
                im.save(path, dpi=(used, used), pnginfo=meta)
            elif fmt in ("tif", "tiff"):
                im.save(path, dpi=(used, used), compression="tiff_lzw",
                        tiffinfo={270: PROVENANCE_TAG + json.dumps(prov)})
            else:
                im.save(path, dpi=(used, used), quality=95)
        elif fmt == "pptx":
            from . import slides
            z = used / 72
            pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(z, z), alpha=not self.c(fig.props["background"]))
            slides.write_pptx(path, pix.tobytes("png"), fig.width, fig.height,
                              svg=doc[0].get_svg_image(text_as_path=True), title=Path(file).stem,
                              alt=fig.caption_text, slide=(fig.width, fig.height) if fig.props.get("poster") else None)
        else:
            raise ValueError(f"can't export {fmt}; use pdf, svg, eps, png, tiff, jpg or pptx")
        if fmt == "pptx":
            return {"ok": True, "path": str(Path(path).resolve()), "format": fmt,
                    "resolution": {"dpi": used, "notes": notes},
                    "warnings": [f"element {i['oid']} failed: {i['error'].strip().splitlines()[-1]}"
                                 for i in infos if i.get("error")]}
        res = self._check_resolution(path, fmt, fig, pieces, need, kind, journal, poster,
                                     (bleed + (CROP_SLUG if marks else 0)) * MM)
        res["notes"] = notes + res.get("notes", [])
        errors = [i for i in infos if i.get("error")]
        return {"ok": True, "path": str(Path(path).resolve()), "format": fmt, "resolution": res,
                "warnings": [f"element {i['oid']} failed: {i['error'].strip().splitlines()[-1]}" for i in errors]
                + res["problems"]}

    @staticmethod
    def _jname(journal, poster=False):
        if poster:
            return "posters ask"
        j = journals.JOURNALS.get((journal or "").lower())
        return j["name"] + " asks" if j else "journals ask"

    def provenance(self, file: str) -> dict:
        """What a figure was made from: Fignimble's version, the project's git commit (and whether files differed
        from it), and a SHA-256 of each file the render read that is known (the figure, figures placed in it,
        panel code, pictures, tables, text files)."""
        import datetime
        import hashlib as hl
        from . import __version__
        files = {}
        for f in sorted(self.used):
            try:
                rel = str(f.relative_to(self.project))
            except ValueError:
                rel = str(f)
            try:
                files[rel] = hl.sha256(f.read_bytes()).hexdigest()[:16]
            except OSError:
                pass
        style = self.project / "style.py"
        if style.exists() and "style.py" not in files:
            files["style.py"] = hl.sha256(style.read_bytes()).hexdigest()[:16]
        out = {"fignimble": __version__, "figure": str(self.resolve(file).relative_to(self.project))
               if str(self.resolve(file)).startswith(str(self.project)) else str(file),
               "made": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "files": files}
        git = shutil.which("git")
        if git:
            try:
                head = subprocess.run([git, "rev-parse", "HEAD"], cwd=self.project, capture_output=True, text=True,
                                      timeout=10)
                if head.returncode == 0:
                    out["git"] = head.stdout.strip()
                    st = subprocess.run([git, "status", "--porcelain", "--untracked-files=no"], cwd=self.project,
                                        capture_output=True, text=True, timeout=10)
                    changed = [ln[3:] for ln in st.stdout.splitlines() if ln.strip()]
                    out["uncommitted"] = changed[:50]
            except (OSError, subprocess.SubprocessError):
                pass
        return out

    def _check_resolution(self, path, fmt, fig, pieces, need, kind, journal, poster=False, edge=0.0) -> dict:
        """Measure the file written: a raster's own dpi as printed, or every picture inside a PDF or EPS. `edge`
        (points): the bleed and crop-mark border around the figure on the page."""
        out = {"journal": journal, "kind": kind, "need": need, "problems": [], "notes": []}
        who = self._jname(journal, poster)
        if fmt in ("png", "tif", "tiff", "jpg", "jpeg"):
            from PIL import Image
            with Image.open(path) as im:
                got = im.size[0] / (fig.width / 25.4)
                out["dpi"] = round(got)
                tag = im.info.get("dpi")
            out["ok"] = got >= need[kind] * 0.99
            if not out["ok"]:                            # a PNG is a preview or for slides: said, not a problem
                (out["notes"] if fmt == "png" else out["problems"]).append(
                    f"{out['dpi']} dpi; {who} {need[kind]} dpi of {kind} figures")
            if tag and abs(float(tag[0]) - got) > 1:
                out["problems"].append(f"the file says {float(tag[0]):.0f} dpi but holds {got:.0f}")
            return out
        if fmt == "svg":
            out["ok"] = True
            return out
        check, tmp = path, None
        if fmt == "eps":
            gs = _gs()
            if gs is None:
                out["ok"] = None
                out["notes"].append("not measured (no Ghostscript)")
                return out
            tmp = tempfile.TemporaryDirectory()
            check = str(Path(tmp.name) / "back.pdf")
            subprocess.run([gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET", "-dEPSCrop", "-sDEVICE=pdfwrite",
                            f"-sOutputFile={check}", path], capture_output=True)
        try:
            with pymupdf.open(check) as d:
                page = d[0]
                sx = (fig.width * MM + 2 * edge) / page.rect.width    # an EPS may come back at another scale
                worst = None
                for im in page.get_image_info():
                    bx0, by0, bx1, by1 = im["bbox"]
                    wi = (bx1 - bx0) * sx / 72
                    if wi < 0.2 or not im.get("width"):
                        continue
                    got = im["width"] / wi
                    cx, cy = ((bx0 + bx1) / 2 * sx - edge) / MM, ((by0 + by1) / 2 * sx - edge) / MM
                    oid = next((pc.oid for pc in reversed(pieces) if pc.box[0] <= cx <= pc.box[0] + pc.box[2]
                                and pc.box[1] <= cy <= pc.box[1] + pc.box[3]), None)
                    if worst is None or got < worst[0]:
                        worst = (got, oid)
                    if got < need["photo"] * 0.98:
                        out["problems"].append(f"a picture{f' in element {oid}' if oid else ''} is {got:.0f} dpi as "
                                               f"printed; {who} {need['photo']} dpi: use a larger picture or "
                                               "print it smaller")
        except Exception as exc:                          # noqa: BLE001
            out["ok"] = None
            out["notes"].append(f"not measured ({exc})")
            return out
        finally:
            if tmp:
                tmp.cleanup()
        out["pictures"] = round(worst[0]) if worst else None
        out["ok"] = not out["problems"]
        return out


# --- helpers ---

PROVENANCE_TAG = "fignimble-provenance:"


def provenance_line(prov: dict) -> str:
    """A provenance record in a sentence, for a PDF's Subject."""
    where = f" at git {prov['git'][:10]}" + (" with uncommitted changes" if prov.get("uncommitted") else "") \
        if prov.get("git") else ""
    return f"Made with Fignimble {prov['fignimble']} from {prov['figure']}{where}, {prov['made']}"


def read_provenance(path: str) -> dict | None:
    """The provenance record an export wrote into a PDF, PNG, TIFF or SVG, or None."""
    p = Path(path)
    ext = p.suffix.lower()
    raw = None
    if ext == ".pdf":
        with pymupdf.open(str(p)) as d:
            raw = (d.metadata or {}).get("keywords") or ""
    elif ext == ".svg":
        m = re.search(r"<metadata>(.*?)</metadata>", p.read_text(encoding="utf-8", errors="replace"), re.S)
        if m:
            import html
            raw = html.unescape(m.group(1))
    elif ext in (".png", ".tif", ".tiff"):
        from PIL import Image
        with Image.open(p) as im:
            raw = im.info.get("fignimble") if ext == ".png" else (im.tag_v2.get(270) if hasattr(im, "tag_v2") else None)
            if raw and ext == ".png":
                raw = PROVENANCE_TAG + raw
    if not raw or PROVENANCE_TAG not in raw:
        return None
    return json.loads(raw.split(PROVENANCE_TAG, 1)[1])


def _xml_escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _contain(aspect: float, w: float, h: float) -> tuple:
    if w / h > aspect:
        dw = h * aspect
        return ((w - dw) / 2, 0, dw, h)
    dh = w / aspect
    return (0, (h - dh) / 2, w, dh)


def _cover_clip(clip, aspect_box: float):
    c = pymupdf.Rect(clip)
    if c.width / c.height > aspect_box:
        nw = c.height * aspect_box
        c.x0 += (c.width - nw) / 2
        c.x1 = c.x0 + nw
    else:
        nh = c.width / aspect_box
        c.y0 += (c.height - nh) / 2
        c.y1 = c.y0 + nh
    return c


def _reading_order(items):
    """Rows top to bottom (a panel starts a new row when its top is clearly below the row's first), left to right
    within a row."""
    items = sorted(items, key=lambda t: (t[2][1], t[2][0]))
    rows = []
    for it in items:
        if rows:
            first = rows[-1][0][2]
            if it[2][1] - first[1] < min(8.0, 0.4 * max(first[3], 1)):
                rows[-1].append(it)
                continue
        rows.append([it])
    return [it for row in rows for it in sorted(row, key=lambda t: t[2][0])]


def _pdf_piece(r: Renderer, oid, kind, box, pdf, meta=None) -> Piece:
    def draw(page, ox, oy, pdf=pdf, w=box[2], h=box[3]):
        r._show_pdf(pdf, page, pymupdf.Rect(ox, oy, ox + w * MM, oy + h * MM))
    return Piece(oid, kind, box, _h(kind, pdf, box[2], box[3]), draw, meta=meta or {})


def _dashes(dash, width):
    if dash == "dashed":
        return f"[{3 * max(width, 0.5):.2f} {2 * max(width, 0.5):.2f}] 0"
    if dash == "dotted":
        return f"[{0.01:.2f} {2 * max(width, 0.5):.2f}] 0"
    return None


def _shape_piece(oid: str, e: api.Element, c=lambda v: v) -> Piece:
    p = dict(e.props)
    kind = e.kind
    if kind == "line":
        a0, b0 = (p["x1"], p["y1"]), (p["x2"], p["y2"])
        q = _control(a0, b0, p.get("curve") or 0)
        xs, ys = [a0[0], b0[0]] + ([q[0]] if q else []), [a0[1], b0[1]] + ([q[1]] if q else [])
        x0, y0 = min(xs), min(ys)
        box = (x0, y0, max(xs) - x0, max(ys) - y0)
        pad = max(p["head"] if p["arrow"] else 0, p["width"] / MM) + 0.3
        rel = lambda v: ((v[0] - x0) * MM, (v[1] - y0) * MM)          # noqa: E731
        a, b, cq = rel(a0), rel(b0), (rel(q) if q else None)
        key = _h("line", a, b, cq, {k: p.get(k) for k in ("width", "color", "arrow", "dash", "head", "tip")}, c(p["color"]))

        def draw(page, ox, oy):
            P = lambda v: pymupdf.Point(ox + v[0], oy + v[1])          # noqa: E731
            pts = [P(a), P(b)] if cq is None else [P(a), P(cq), P(b)]
            _stroke(page, pts, cq is not None, False, p, rgb(c(p["color"])), None)
        return Piece(oid, "line", box, key, draw, pad=pad)

    box = (p["x"], p["y"], p["w"], p["h"])
    pad = p["width"] / MM / 2 + 0.2
    key = _h(kind, box[2:], {k: p.get(k) for k in ("stroke", "fill", "width", "radius", "dash", "opacity")},
             c(p.get("stroke")), c(p.get("fill")))

    def draw(page, ox, oy):
        r = pymupdf.Rect(ox, oy, ox + p["w"] * MM, oy + p["h"] * MM)
        sh = page.new_shape()
        if kind == "ellipse":
            sh.draw_oval(r)
        elif p.get("radius"):
            sh.draw_rect(r, radius=min(0.5, p["radius"] / max(min(p["w"], p["h"]), 1e-6)))
        else:
            sh.draw_rect(r)
        sh.finish(color=rgb(c(p["stroke"])), fill=rgb(c(p["fill"])), width=p["width"] if p["stroke"] else 0,
                  dashes=_dashes(p["dash"], p["width"]), fill_opacity=p["opacity"], stroke_opacity=p["opacity"])
        sh.commit()
    return Piece(oid, kind, box, key, draw, pad=pad)


def _bracket_piece(r: Renderer, oid, e, pdf, tw, th) -> Piece:
    p = e.props
    x0, x1 = min(p["x1"], p["x2"]), max(p["x1"], p["x2"])
    gap = 0.4
    top = p["y"] - th - gap
    box = (min(x0, (x0 + x1) / 2 - tw / 2), top, max(x1 - x0, tw), th + gap + p["tick"])
    key = _h("bracket", pdf, x1 - x0, p["tick"], p["width"], r.c(p["color"]), box[2:])

    def draw(page, ox, oy):
        # coordinates relative to the box's top-left
        bx, by = box[0], box[1]
        X = lambda v: ox + (v - bx) * MM          # noqa: E731
        Y = lambda v: oy + (v - by) * MM          # noqa: E731
        sh = page.new_shape()
        sh.draw_polyline([pymupdf.Point(X(x0), Y(p["y"] + p["tick"])), pymupdf.Point(X(x0), Y(p["y"])),
                          pymupdf.Point(X(x1), Y(p["y"])), pymupdf.Point(X(x1), Y(p["y"] + p["tick"]))])
        sh.finish(color=rgb(r.c(p["color"])), width=p["width"], closePath=False)
        sh.commit()
        cx = (x0 + x1) / 2
        r._show_pdf(pdf, page, pymupdf.Rect(X(cx - tw / 2), Y(top), X(cx + tw / 2), Y(top + th)))
    return Piece(oid, "bracket", box, key, draw, pad=p["width"] / MM)


def _qr_piece(oid: str, p: dict) -> Piece:
    """A QR code: its dark modules as filled squares on its light ones. Never recolored for a dark version
    (scanners want dark on light)."""
    mods = qr.modules(p["data"], p.get("level") or "M")
    q = max(int(p.get("quiet", 4)), 0)
    n = len(mods)
    w = float(p["w"])
    cell = w / (n + 2 * q)
    box = (p["x"], p["y"], w, w)
    fg, bg = rgb(p.get("color") or "#000000"), rgb(p["background"]) if p.get("background") else None
    key = _h("qr", p["data"], p.get("level"), q, w, fg, bg)

    def draw(page, ox, oy):
        if bg:
            page.draw_rect(pymupdf.Rect(ox, oy, ox + w * MM, oy + w * MM), color=None, fill=bg, width=0)
        sh = page.new_shape()
        c = cell * MM
        for y, row in enumerate(mods):
            x = 0
            while x < n:
                if not row[x]:
                    x += 1
                    continue
                x1 = x
                while x1 < n and row[x1]:
                    x1 += 1
                # a run of dark modules as one rectangle, a hair taller so rows don't show seams between them
                sh.draw_rect(pymupdf.Rect(ox + (q + x) * c, oy + (q + y) * c, ox + (q + x1) * c,
                                          oy + (q + y + 1) * c + (0.02 * c if y + 1 < n else 0)))
                x = x1
        sh.finish(color=None, fill=fg, width=0)
        sh.commit()
    return Piece(oid, "qr", box, key, draw, meta={"module": cell, "modules": n})


def _error_piece(oid, box, err) -> Piece:
    msg = err.strip().splitlines()[-1] if err.strip() else "error"

    def draw(page, ox, oy):
        r = pymupdf.Rect(ox, oy, ox + box[2] * MM, oy + box[3] * MM)
        page.draw_rect(r, color=(0.85, 0.2, 0.2), fill=(1, 0.93, 0.93), width=0.6)
        page.insert_textbox(r + (4, 4, -4, -4), "! " + msg, fontsize=6, fontname="helv",
                            color=(0.6, 0.1, 0.1))
    return Piece(oid, "error", box, _h("err", msg, box[2:]), draw)


def _rough_box(e) -> tuple:
    p = e.props
    if "x1" in p:
        return (min(p["x1"], p["x2"]), min(p.get("y1", p.get("y", 0)), p.get("y2", p.get("y", 0))),
                max(abs(p["x2"] - p["x1"]), 5), 5)
    return (p.get("x", 0), p.get("y", 0), p.get("w") or 20, p.get("h") or 15)


def _poster_info(fig):
    k = fig.props.get("poster")
    if not k:
        return None
    pr = journals.POSTERS[k]
    return {"key": k, "name": pr["name"], "landscape": bool(fig.props.get("landscape")), "margin": fig.props.get("margin"),
            "size": pr["size"], "minFont": pr["min_font"]}


def _journal_info(fig):
    j = fig.props.get("journal")
    if not j or j not in journals.JOURNALS:
        return None
    info = journals.JOURNALS[j]
    fits = any(abs(fig.width - w) < 0.6 for w in info["widths"].values())
    return {"key": j, "name": info["name"], "widths": info["widths"], "fits": fits}


def _stack_table(fig, pieces, infos):
    """A Table's page: its title, cells and notes one under the other, `pad` mm around them, the page their size."""
    pad = float(fig.props.get("pad", 3))
    by_oid = {pc.oid: pc for pc in pieces}
    y, right, last_gap = pad, pad, 0.0
    for info in infos:
        role = info["props"].get("role")
        if role is None or info.get("hidden"):
            continue
        w, h = info["box"][2], info["box"][3]
        info["box"] = [round(pad, 4), round(y, 4), w, h]
        pc = by_oid.get(info["oid"])
        if pc is not None:
            pc.box = (pad, y, pc.box[2], pc.box[3])
        right = max(right, pad + w)
        last_gap = 1.5 if role == "title" else 1.2
        y += h + last_gap
    fig.props["width"] = round(right + pad, 2)
    fig.props["height"] = round(y - last_gap + pad, 2)


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if callable(v):
        return f"<function {getattr(v, '__name__', '?')}>"
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return repr(v)


def _user_traceback(where: str) -> str:
    """The traceback of the exception being handled, without fignimble's own frames."""
    etype, exc, tb = sys.exc_info()
    frames = [f for f in traceback.extract_tb(tb) if os.path.dirname(os.path.abspath(f.filename)) != api._HERE
              and "runpy" not in f.filename]
    lines = traceback.format_list(frames[-6:]) + traceback.format_exception_only(etype, exc)
    return f"in {where}:\n" + "".join(lines)


DARK_RC = {
    "text.color": "#FFFFFF", "axes.labelcolor": "#FFFFFF", "axes.edgecolor": "#FFFFFF", "xtick.color": "#FFFFFF",
    "ytick.color": "#FFFFFF", "axes.facecolor": "none", "figure.facecolor": "none", "grid.color": "#4D4D4D",
    "patch.edgecolor": "#FFFFFF", "lines.color": "#FFFFFF", "hatch.color": "#FFFFFF", "legend.labelcolor": "#FFFFFF",
    "boxplot.boxprops.color": "#FFFFFF", "boxplot.whiskerprops.color": "#FFFFFF", "boxplot.capprops.color": "#FFFFFF",
    "boxplot.medianprops.color": "#FFFFFF", "boxplot.flierprops.markeredgecolor": "#FFFFFF",
}


def _write(path: Path, data: bytes):
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _plot_area(fig):
    """The union of the boxes the layout gave the panel's axes (not colorbars), in figure fractions (x0, y0, x1,
    y1), or None. The allotted box, not the drawn one: a plot of fixed aspect (imshow) draws smaller than its box,
    and its whitespace isn't a margin to line others up to."""
    boxes = [a.get_position(original=True) for a in fig.axes if a.get_visible() and not hasattr(a, "_colorbar")]
    if not boxes:
        return None
    return (min(b.x0 for b in boxes), min(b.y0 for b in boxes), max(b.x1 for b in boxes), max(b.y1 for b in boxes))


def _remap(fig, area, target, w, h):
    """Move every axes so the plot area takes the target margins (l, t, r, b mm), keeping their arrangement."""
    x0, y0, x1, y1 = area
    l, t, r, b = target
    nx0, nx1 = l / w, 1 - r / w
    ny0, ny1 = b / h, 1 - t / h
    if nx1 - nx0 < 0.2 * (x1 - x0) or ny1 - ny0 < 0.2 * (y1 - y0):
        return                                       # no room: leave it as its code laid it out
    sx, sy = (nx1 - nx0) / (x1 - x0), (ny1 - ny0) / (y1 - y0)
    fig.set_layout_engine("none")
    for a in fig.axes:
        p = a.get_position(original=True)
        a.set_position([nx0 + (p.x0 - x0) * sx, ny0 + (p.y0 - y0) * sy, p.width * sx, p.height * sy])


def _align_targets(jobs) -> dict:
    """Margins that line up the plot areas of panels sharing an edge: for each side, the largest margin of the
    panels whose box has that edge in the same place (as patchwork does for a row or a column)."""
    out = {}
    live = [j for j in jobs if j["align"] and j["base"].get("margins")]
    for j in live:
        out[j["oid"]] = list(j["base"]["margins"])
    for side, edge in ((0, lambda b: b[0]), (1, lambda b: b[1]), (2, lambda b: b[0] + b[2]), (3, lambda b: b[1] + b[3])):
        groups = {}
        for j in live:
            groups.setdefault(round(edge(j["box"]), 1), []).append(j)
        for g in groups.values():
            if len(g) > 1:
                m = max(j["base"]["margins"][side] for j in g)
                for j in g:
                    out[j["oid"]][side] = m
    return out


def _take_legends(fig) -> list:
    """Take the legends out of a panel: their entries ({label, kind, color, ...}), for a collected legend."""
    from matplotlib.collections import Collection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    out = []
    legends = [a.get_legend() for a in fig.axes if a.get_legend() is not None] + list(fig.legends)
    for lg in legends:
        for h, t in zip(lg.legend_handles, lg.get_texts()):
            e = {"label": t.get_text()}
            try:
                if isinstance(h, Line2D):
                    ls, mk = h.get_linestyle(), h.get_marker()
                    has_marker = mk not in (None, "None", "", " ")
                    e.update(kind="marker" if ls in ("None", "", " ") else "line", color=mcolors.to_hex(h.get_color()),
                             linestyle=ls, linewidth=h.get_linewidth(), marker=mk if has_marker else None,
                             markersize=h.get_markersize())
                elif isinstance(h, Patch):
                    e.update(kind="patch", color=mcolors.to_hex(h.get_facecolor()),
                             edgecolor=mcolors.to_hex(h.get_edgecolor()) if h.get_linewidth() else None)
                elif isinstance(h, Collection):
                    fc = h.get_facecolor()
                    e.update(kind="marker", marker="o", color=mcolors.to_hex(fc[0]) if len(fc) else "#000000",
                             markersize=5)
                else:
                    e.update(kind="patch", color="#888888")
            except (ValueError, TypeError, IndexError):
                e.update(kind="patch", color="#888888")
            out.append(e)
        lg.remove()
    return out


def _legend_entries(p: dict) -> list:
    """fig.legend's {label: color or settings} as entries."""
    out = []
    for label, v in p["entries"].items():
        e = {"label": str(label)}
        if isinstance(v, dict):
            e.update(v)
            e.setdefault("kind", "marker" if p.get("marker") and "linestyle" not in v else
                     ("line" if "linestyle" in v or "linewidth" in v else "patch"))
        else:
            e.update(color=v, kind="marker" if p.get("marker") else "patch")
        if e["kind"] == "marker":
            e.setdefault("marker", p.get("marker") or "o")
        out.append(e)
    return out


def _proxy(e: dict):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    color = e.get("color") or "#000000"
    if e.get("kind") == "line":
        return Line2D([], [], color=color, linestyle=e.get("linestyle") or "-", linewidth=e.get("linewidth") or 1.2,
                      marker=e.get("marker") or None, markersize=e.get("markersize") or 4)
    if e.get("kind") == "marker":
        return Line2D([], [], color=color, linestyle="None", marker=e.get("marker") or "o",
                      markersize=e.get("markersize") or 4)
    return Patch(facecolor=color, edgecolor=e.get("edgecolor") or "none")



def _control(a, b, curve):
    """The control point of a line bent by `curve` (a fraction of its length, + to the left of its direction)."""
    if not curve:
        return None
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    return ((a[0] + b[0]) / 2 + dy / n * curve * n, (a[1] + b[1]) / 2 - dx / n * curve * n)


def _tip(sh, to, ux, uy, L, kind, col, width) -> float:
    """Draw an arrowhead at `to` pointing along (ux, uy); how far the line should stop short of it."""
    if L <= 0:
        return 0.0
    wx, wy = -uy * L * 0.38, ux * L * 0.38
    base = pymupdf.Point(to.x - ux * L, to.y - uy * L)
    if kind == "bar":
        sh.draw_line(pymupdf.Point(to.x + wx * 1.2, to.y + wy * 1.2), pymupdf.Point(to.x - wx * 1.2, to.y - wy * 1.2))
        sh.finish(color=col, width=width)
        return 0.0
    if kind == "dot":
        sh.draw_circle(to, L * 0.3)
        sh.finish(color=None, fill=col, width=0)
        return L * 0.3
    if kind == "open":
        sh.draw_polyline([base + (wx, wy), to, base - (wx, wy)])
        sh.finish(color=col, width=width, closePath=False, lineJoin=1)
        return 0.0
    if kind == "stealth":
        notch = pymupdf.Point(to.x - ux * L * 0.65, to.y - uy * L * 0.65)
        sh.draw_polyline([to, base + (wx * 1.1, wy * 1.1), notch, base - (wx * 1.1, wy * 1.1), to])
        sh.finish(color=None, fill=col, closePath=True, width=0)
        return L * 0.6
    sh.draw_polyline([to, base + (wx, wy), base - (wx, wy), to])
    sh.finish(color=None, fill=col, closePath=True, width=0)
    return L * 0.75


def _stroke(page, pts, curved, closed, p, col, fill):
    """A line, curve or polyline through `pts` (page points) with p's arrowheads, dashes, width and opacity.
    curved: pts are (start, control, end) of a bend, or (with closed/smooth handled by the caller) ignored."""
    L = (p.get("head") or 1.6) * MM
    tip = p.get("tip") or "triangle"
    sh = page.new_shape()
    pts = list(pts)

    def unit(frm, to):
        dx, dy = to.x - frm.x, to.y - frm.y
        n = math.hypot(dx, dy) or 1.0
        return dx / n, dy / n

    if p.get("arrow") and not closed and len(pts) >= 2:
        if p["arrow"] in ("end", "both"):
            ux, uy = unit(pts[-2], pts[-1])
            cut = _tip(sh, pts[-1], ux, uy, L, tip, col, p["width"])
            pts[-1] = pymupdf.Point(pts[-1].x - ux * cut, pts[-1].y - uy * cut)
        if p["arrow"] in ("start", "both"):
            ux, uy = unit(pts[1], pts[0])
            cut = _tip(sh, pts[0], ux, uy, L, tip, col, p["width"])
            pts[0] = pymupdf.Point(pts[0].x - ux * cut, pts[0].y - uy * cut)
    if curved and len(pts) == 3:
        s0, q, e0 = pts
        sh.draw_bezier(s0, s0 + (q - s0) * (2 / 3), e0 + (q - e0) * (2 / 3), e0)
    elif p.get("smooth") and len(pts) > 2:
        for a, c1, c2, b in _catmull(pts, closed):
            sh.draw_bezier(a, c1, c2, b)
    else:
        sh.draw_polyline(pts + ([pts[0]] if closed else []))
    sh.finish(color=col, fill=fill, width=p["width"] if col is not None else 0, closePath=closed,
              dashes=_dashes(p.get("dash"), p["width"]), lineCap=1 if p.get("dash") == "dotted" else 0, lineJoin=1,
              fill_opacity=p.get("opacity", 1.0), stroke_opacity=p.get("opacity", 1.0))
    sh.commit()


def _catmull(pts, closed):
    """Bezier segments of a smooth curve through the points (Catmull-Rom)."""
    n = len(pts)
    out = []
    for i in range(n if closed else n - 1):
        p0 = pts[(i - 1) % n] if (closed or i > 0) else pts[i]
        p1, p2 = pts[i], pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if (closed or i + 2 < n) else p2
        out.append((p1, p1 + (p2 - p0) * (1 / 6), p2 - (p3 - p1) * (1 / 6), p2))
    return out


def _path_piece(oid: str, e, c=lambda v: v) -> Piece:
    p = e.props
    xs, ys = [q[0] for q in p["points"]], [q[1] for q in p["points"]]
    x0, y0 = min(xs), min(ys)
    box = (x0, y0, max(xs) - x0, max(ys) - y0)
    pad = max(p["head"] if p["arrow"] else 0, p["width"] / MM) + (2.0 if p["smooth"] else 0.3)
    rel = [((q[0] - x0) * MM, (q[1] - y0) * MM) for q in p["points"]]
    key = _h("path", rel, {k: p.get(k) for k in ("closed", "smooth", "stroke", "fill", "width", "dash", "arrow", "tip",
                                               "head", "opacity")}, c(p["stroke"]), c(p["fill"]))

    def draw(page, ox, oy):
        pts = [pymupdf.Point(ox + a, oy + b) for a, b in rel]
        _stroke(page, pts, False, p["closed"], p, rgb(c(p["stroke"])), rgb(c(p["fill"])) if p["fill"] else None)
    return Piece(oid, "path", box, key, draw, pad=pad)


def _zoom_lines(reg, tgt):
    """The two lines from a zoom box to its magnified view: from the box's sides facing the view."""
    rx, ry, rw, rh = reg
    tx, ty, tw, th = tgt
    dx = (tx + tw / 2) - (rx + rw / 2)
    dy = (ty + th / 2) - (ry + rh / 2)
    if abs(dx) * th >= abs(dy) * tw:                    # side by side
        if dx > 0:
            return [((rx + rw, ry), (tx, ty)), ((rx + rw, ry + rh), (tx, ty + th))]
        return [((rx, ry), (tx + tw, ty)), ((rx, ry + rh), (tx + tw, ty + th))]
    if dy > 0:
        return [((rx, ry + rh), (tx, ty)), ((rx + rw, ry + rh), (tx + tw, ty))]
    return [((rx, ry), (tx, ty + th)), ((rx + rw, ry), (tx + tw, ty + th))]


def _picture_props(obj):
    """The settings of a picture: a fig.image Element, an inset of an Image, or an Image in a layout."""
    if isinstance(obj, api.Element):
        if obj.kind == "image":
            return obj.props
        if obj.kind == "inset" and isinstance(obj.node, api.Image):
            return obj.node.props
        return None
    if isinstance(obj, api.Image):
        return obj.props
    return None


def _sync_zooms(fig, project):
    """Zoom boxes with sync: crop a magnified picture of the same file to the box (before anything is drawn)."""
    for e in fig.elements:
        if e.kind != "zoom" or not e.props.get("sync"):
            continue
        sp, tp = _picture_props(e.source), _picture_props(e.target)
        if not sp or not tp or Path(project / sp["source"]).resolve() != Path(project / tp["source"]).resolve():
            continue
        l, t, r, b = sp.get("crop") or (0, 0, 0, 0)
        rx, ry, rw, rh = e.props["region"]
        fx0, fx1 = l + rx * (1 - l - r), l + (rx + rw) * (1 - l - r)
        fy0, fy1 = t + ry * (1 - t - b), t + (ry + rh) * (1 - t - b)
        tp["crop"] = (round(fx0, 5), round(fy0, 5), round(1 - fx1, 5), round(1 - fy1, 5))


def _text_artists(fig) -> dict:
    """The texts of a panel by a stable key: ax0.xlabel, ax0.ytick3, ax1.legend2, fig.suptitle, ..."""
    out = {}
    for i, ax in enumerate(fig.axes):
        k = f"ax{i}"
        for name, t in (("xlabel", ax.xaxis.label), ("ylabel", ax.yaxis.label), ("title", ax.title),
                        ("ltitle", getattr(ax, "_left_title", None)), ("rtitle", getattr(ax, "_right_title", None))):
            if t is not None:
                out[f"{k}.{name}"] = t
        for j, t in enumerate(ax.get_xticklabels()):
            out[f"{k}.xtick{j}"] = t
        for j, t in enumerate(ax.get_yticklabels()):
            out[f"{k}.ytick{j}"] = t
        lg = ax.get_legend()
        if lg is not None:
            for j, t in enumerate(lg.get_texts()):
                out[f"{k}.legend{j}"] = t
            out[f"{k}.legendtitle"] = lg.get_title()
        for j, t in enumerate(ax.texts):
            out[f"{k}.text{j}"] = t
    for j, t in enumerate(fig.texts):
        out[f"fig.text{j}"] = t
    for name in ("_suptitle", "_supxlabel", "_supylabel"):
        t = getattr(fig, name, None)
        if t is not None:
            out[f"fig.{name.strip('_')}"] = t
    for n, lg in enumerate(fig.legends):
        for j, t in enumerate(lg.get_texts()):
            out[f"fig.legend{n}.{j}"] = t
    return out


def _set_texts(fig, texts, ticks: bool) -> bool:
    """Put the editor's retyped texts in place (tick labels after a first draw, when they exist)."""
    if not texts:
        return False
    arts = _text_artists(fig)
    changed = False
    if not ticks:
        for k, v in texts.items():
            if "tick" not in k and k in arts:
                arts[k].set_text(v)
                changed = True
        return changed
    for i, ax in enumerate(fig.axes):
        for axis, which in (("x", ax.xaxis), ("y", ax.yaxis)):
            keys = {int(k.rsplit("tick", 1)[1]): v for k, v in texts.items()
                    if k.startswith(f"ax{i}.{axis}tick") and k.rsplit("tick", 1)[1].isdigit()}
            if not keys:
                continue
            locs = which.get_ticklocs()
            labels = [t.get_text() for t in which.get_ticklabels()]
            for j, v in keys.items():
                if j < len(labels):
                    labels[j] = v
            (ax.set_xticks if axis == "x" else ax.set_yticks)(locs, labels)
            changed = True
    return changed


_TEXT_PROPS = {"size": "fontsize", "color": "color", "weight": "fontweight", "style": "fontstyle"}


def _set_text_style(fig, styles, ticks: bool) -> bool:
    """Give texts the editor restyled their size, color, weight and slant (tick labels after a first draw)."""
    if not styles:
        return False
    arts = _text_artists(fig)
    changed = False
    for k, st in styles.items():
        if ("tick" in k) != ticks or k not in arts or not isinstance(st, dict):
            continue
        t = arts[k]
        for name, v in st.items():
            if name in _TEXT_PROPS and v is not None:
                getattr(t, "set_" + _TEXT_PROPS[name])(v)
                changed = True
    return changed


def _is_bold(weight) -> bool:
    if isinstance(weight, (int, float)):
        return weight >= 600
    return str(weight) in ("semibold", "demibold", "demi", "bold", "heavy", "extra bold", "black")


def _measure_texts(fig, h) -> list:
    """Where the panel's texts are: [{key, text, box (x, y, w, h mm from the panel's top-left), size, rotation,
    color, bold, italic}]."""
    import matplotlib.colors as mcolors
    r = fig.canvas.get_renderer()
    out = []
    for k, t in _text_artists(fig).items():
        try:
            if not t.get_visible() or not t.get_text().strip():
                continue
            b = t.get_window_extent(r)
        except Exception:                                # noqa: BLE001
            continue
        if b.width <= 0:
            continue
        d = fig.dpi
        try:
            color = mcolors.to_hex(t.get_color()).upper()
        except ValueError:
            color = None
        out.append({"key": k, "text": t.get_text(), "size": t.get_fontsize(), "rotation": t.get_rotation(),
                    "color": color, "bold": _is_bold(t.get_fontweight()), "italic": t.get_fontstyle() in ("italic", "oblique"),
                    "box": [round(b.x0 / d * 25.4, 3), round(h - b.y1 / d * 25.4, 3), round(b.width / d * 25.4, 3),
                            round(b.height / d * 25.4, 3)]})
    return out[:400]


_FONT_NAMES: dict = {}


def _svg_without_css(data: bytes) -> bytes:
    """An SVG with its <style> sheet's class rules (".st0{fill:#4D4D4D;}", as Illustrator writes them) and its style=""
    attributes turned into presentation attributes (fill="#4D4D4D"), which MuPDF draws: it ignores the sheet, so such
    shapes came out black."""
    text = data.decode("utf-8", errors="replace")
    rules: dict[str, dict] = {}
    for sheet in re.findall(r"<style[^>]*>(.*?)</style>", text, re.S):
        sheet = re.sub(r"/\*.*?\*/|<!\[CDATA\[|\]\]>", "", sheet, flags=re.S)
        for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", sheet):
            decl = _css_decls(body)
            for s_ in sel.split(","):
                s_ = s_.strip()
                if re.fullmatch(r"\.[\w-]+", s_):
                    rules.setdefault(s_[1:], {}).update(decl)
    if not rules and "style=" not in text:
        return data

    def tag(m):
        name, attrs, end = m.group(1), m.group(2) or "", m.group(3)
        if name.lower() in ("style", "svg:style") or not attrs:
            return m.group(0)
        props: dict = {}
        cls = re.search(r'\sclass\s*=\s*"([^"]*)"', attrs)
        for c in (cls.group(1).split() if cls else []):
            props.update(rules.get(c, {}))
        st = re.search(r'\sstyle\s*=\s*"([^"]*)"', attrs)
        if st:
            props.update(_css_decls(st.group(1)))           # inline style wins over the sheet
            attrs = attrs.replace(st.group(0), "")
        if not props:
            return m.group(0)
        for k in props:                                     # the sheet wins over presentation attributes
            attrs = re.sub(rf'\s{re.escape(k)}\s*=\s*"[^"]*"', "", attrs)
        attrs += "".join(f' {k}="{v}"' for k, v in props.items())
        return f"<{name}{attrs}{end}>"

    text = re.sub(r"<([A-Za-z][\w:.-]*)(\s[^<>]*?)?(/?)>", tag, text)
    return text.encode("utf-8")


def _css_decls(body: str) -> dict:
    out = {}
    for d in body.split(";"):
        if ":" in d:
            k, v = d.split(":", 1)
            k, v = k.strip().lower(), v.strip().replace('"', "'")
            if k and v and k != "enable-background" and not v.endswith("!important"):
                out[k] = v
    return out


_LETTER = re.compile(r"^\(?[A-Za-z]\)?\.?$")
_SYMBOL_FONT = re.compile(r"symbol|dingbat|math|stix|^cm(sy|mi|ex|r)\d|^msam|^msbm", re.I)
# metric-compatible stand-ins for one another: text set in any of them looks the same in print
_SANS_CLONES = {"arial", "helvetica", "helveticaneue", "liberationsans", "nimbussans", "nimbussansl",
                "texgyreheros", "arimo"}


def _font_family(font: str) -> str:
    """'ABCDEF+Arial-BoldMT' -> 'Arial'."""
    f = re.split(r"[-,]", font.split("+")[-1])[0]
    return re.sub(r"(PSMT|MT|PS)$", "", re.sub(r"(Bold|Italic|Oblique|Regular)+(MT)?$", "", f)) or f


def _same_font(font: str, style_font: str | None) -> bool:
    a = re.sub(r"[^a-z]", "", _font_family(font).lower())
    b = re.sub(r"[^a-z]", "", (style_font or "").lower())
    return not b or a.startswith(b) or b.startswith(a) or (a in _SANS_CLONES and b in _SANS_CLONES)


def _is_script(span, spans) -> bool:
    """A superscript or subscript: matplotlib draws them at 0.7 of the text before them with the baseline raised or
    lowered, so Agg$^{+}$'s "+" at 4.9 pt beside 7 pt is not small text the way a 4.9 pt label is. Measured along
    the text's own direction, so rotated tick labels count too."""
    s = span["size"]
    if len(span["text"].strip()) > 4:
        return False
    dx, dy = span["dir"]
    sx, sy = span["origin"]
    for o in spans:
        S = o["size"]
        if o is span or abs(s - 0.7 * S) > 0.02 * S or abs(o["dir"][0] - dx) + abs(o["dir"][1] - dy) > 0.02:
            continue
        ox, oy = o["origin"]
        along = (sx - ox) * dx + (sy - oy) * dy              # from the start of the text before it
        across = -(sx - ox) * dy + (sy - oy) * dx            # its baseline raised or lowered
        if 0 < along <= (len(o["text"]) + 1) * S and 0.1 * S < abs(across) < 0.8 * S:
            return True
    return False


def _math_rc(family) -> dict:
    """Mathtext in the text's own font, so **bold**, ^{2} and _{i} match the words around them."""
    key = tuple(family)
    name = _FONT_NAMES.get(key)
    if name is None:
        from matplotlib import font_manager
        try:
            path = font_manager.findfont(font_manager.FontProperties(family=list(family)), fallback_to_default=True)
            # the name matplotlib knows it by: Aptos Display's files say "Aptos" (see officefonts.py)
            name = officefonts.family_of(path) or font_manager.get_font(path).family_name
        except Exception:                                # noqa: BLE001
            name = "DejaVu Sans"
        _FONT_NAMES[key] = name
    rc = {"mathtext.fontset": "custom", "mathtext.rm": name, "mathtext.it": f"{name}:italic",
          "mathtext.bf": f"{name}:bold", "mathtext.sf": name}
    if "mathtext.bfit" in matplotlib.rcParams:
        rc["mathtext.bfit"] = f"{name}:italic:bold"
    return rc


_MARKUP = re.compile(r"(\$[^$]*\$)|\*\*(.+?)\*\*|\*(.+?)\*|\^\{(.+?)\}|_\{(.+?)\}")


def _runs(text: str) -> list:
    """Markup as runs [(text, kind)]: plain, math, bold, italic, sup, sub."""
    out, at = [], 0
    for m in _MARKUP.finditer(text):
        if m.start() > at:
            out.append((text[at:m.start()], "plain"))
        kind = ["math", "bold", "italic", "sup", "sub"][next(i for i in range(5) if m.group(i + 1) is not None)]
        out.append((m.group(m.lastindex), kind))
        at = m.end()
    if at < len(text):
        out.append((text[at:], "plain"))
    return out


def _mt(s: str) -> str:
    """Text written inside mathtext."""
    s = s.replace("\\", r"\backslash ")
    for ch in "{}_^$%#&":
        s = s.replace(ch, "\\" + ch)
    # a hyphen, not math's spaced minus sign ("multi-panel" in bold)
    return s.replace(" ", r"\ ").replace("-", r"\text{-}")


def _as_mathtext(runs, bold_base: bool) -> str:
    rm = r"\mathbf" if bold_base else r"\mathrm"
    out = []
    for t, kind in runs:
        if kind == "plain":
            out.append(t.replace("$", r"\$"))
        elif kind == "math":
            out.append(t if t.startswith("$") else f"${t}$")
        elif kind == "bold":
            out.append(rf"$\mathbf{{{_mt(t)}}}$")
        elif kind == "italic":
            out.append(rf"$\mathit{{{_mt(t)}}}$" if not bold_base else rf"$\mathbfit{{{_mt(t)}}}$"
                       if "mathtext.bfit" in matplotlib.rcParams else rf"$\mathit{{{_mt(t)}}}$")
        elif kind == "sup":
            out.append(rf"$^{{{rm}{{{_mt(t)}}}}}$")
        else:
            out.append(rf"$_{{{rm}{{{_mt(t)}}}}}$")
    return "".join(out)


def _rich(text: str, bold_base: bool, width, fig, props) -> str:
    """The string matplotlib draws for a text with markup, its lines wrapped at `width` mm."""
    runs = _runs(text)
    plain = all(k in ("plain", "math") for _, k in runs)
    if not width:
        return text if plain else _as_mathtext(runs, bold_base)
    r = fig.canvas.get_renderer()

    def measure(line_runs) -> float:
        t = fig.text(0, 0, _as_mathtext(line_runs, bold_base), **props)
        w = t.get_window_extent(r).width / fig.dpi * 25.4
        t.remove()
        return w

    lines = []
    for para in text.split("\n"):
        words = []                                       # words as lists of runs
        cur = []
        for t, kind in _runs(para):
            if kind in ("plain", "bold", "italic"):
                parts = t.split(" ")
                for i, w in enumerate(parts):
                    if i > 0:
                        words.append(cur)
                        cur = []
                    if w:
                        cur.append((w, kind))
            else:
                cur.append((t, kind))
        words.append(cur)
        words = [w for w in words if w]
        line = []
        for w in words:
            trial = line + ([(" ", "plain")] if line else []) + w
            if line and measure(trial) > width:
                lines.append(line)
                line = list(w)
            else:
                line = trial
        lines.append(line)
    return "\n".join(_as_mathtext(l, bold_base) for l in lines)


_BULLET = re.compile(r"^( *)([-•]) +(.*)$")
_BULLET_MD = re.compile(r"^( *)([-•*+]) +(.*)$")
_NUMBERED = re.compile(r"^( *)(\d{1,3})[.)] +(.*)$")
_HEADING = re.compile(r"^(#{1,3}) +(.*)$")


def is_flowing(text: str) -> bool:
    """Text that a text box lays out as blocks: bullets, numbered items or headings."""
    return any(_BULLET.match(ln) or _NUMBERED.match(ln) or _HEADING.match(ln) for ln in text.split("\n"))


def _blocks(text: str, markdown: bool) -> list[dict]:
    """Text as blocks: {kind: para | item | heading | gap, text, level, marker}. In Markdown (a source file) the
    lines of a paragraph or an item join with spaces; otherwise each line is a line of its own."""
    out = []
    bullet = _BULLET_MD if markdown else _BULLET
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            if out and out[-1]["kind"] != "gap":
                out.append({"kind": "gap"})
            continue
        m = _HEADING.match(line.strip())
        if m:
            out.append({"kind": "heading", "level": len(m.group(1)), "text": m.group(2).strip()})
            continue
        m = bullet.match(line) or _NUMBERED.match(line)
        if m:
            indent, mark, body = m.groups()
            numbered = mark[0].isdigit()
            out.append({"kind": "item", "level": min(len(indent) // 2, 2), "text": body.strip(),
                        "marker": f"{mark}." if numbered else ("•" if len(indent) < 2 else "–")})
            continue
        if markdown and out and out[-1]["kind"] in ("para", "item"):
            out[-1]["text"] += " " + line.strip()          # the paragraph or item goes on
            continue
        out.append({"kind": "para", "text": line.strip() if markdown else line, "level": 0})
    while out and out[-1]["kind"] == "gap":
        out.pop()
    return out


def _words(text: str) -> list:
    """Markup split into words, each a list of runs, for wrapping."""
    words, cur = [], []
    for t, kind in _runs(text):
        if kind in ("plain", "bold", "italic"):
            parts = t.split(" ")
            for i, w in enumerate(parts):
                if i > 0:
                    words.append(cur)
                    cur = []
                if w:
                    cur.append((w, kind))
        else:
            cur.append((t, kind))
    words.append(cur)
    return [w for w in words if w]


def _wrap(words, width_mm: float, measure) -> list:
    lines, line = [], []
    for w in words:
        trial = line + ([(" ", "plain")] if line else []) + w
        if line and measure(trial) > width_mm:
            lines.append(line)
            line = list(w)
        else:
            line = trial
    lines.append(line)
    return lines


HEADING_SCALE = {1: 1.5, 2: 1.25, 3: 1.0}
LINE_PITCH = 1.25                 # baseline to baseline, in text sizes
GAP = 0.6                         # a paragraph's space, in lines
ITEM_GAP = 0.2                    # between list items, in lines


def _flow(blocks, size: float, bold: bool, width_mm: float, fig, props) -> list[dict]:
    """Lay blocks out in a box `width_mm` wide: lines [{text (mathtext), size, bold, x (mm), y (baseline, pt from
    the top), marker, mx}], and the height (pt) as the last entry's "end"."""
    r = fig.canvas.get_renderer()
    cache: dict = {}

    def measure(runs, s, b) -> float:
        key = (tuple(runs), s, b)
        if key not in cache:
            t = fig.text(0, 0, _as_mathtext(runs, b), **{**props, "fontsize": s,
                                                          "fontweight": "bold" if b else props["fontweight"]})
            cache[key] = t.get_window_extent(r).width / fig.dpi * 25.4
            t.remove()
        return cache[key]

    out, y, first = [], 0.0, True
    for b in blocks:
        if b["kind"] == "gap":
            y += GAP * LINE_PITCH * size
            continue
        s, bb = size, bold
        if b["kind"] == "heading":
            s, bb = size * HEADING_SCALE[b["level"]], True
            if not first:
                y += 0.35 * LINE_PITCH * size
        indent = hang = 0.0
        if b["kind"] == "item":
            if not first and out and out[-1].get("item"):
                y += ITEM_GAP * LINE_PITCH * size
            em = s * 25.4 / 72                              # mm
            indent = b["level"] * 1.2 * em
            hang = (measure([("00.", "plain")], s, bb) if b["marker"][0].isdigit() else measure([("•", "plain")], s, bb)) \
                + 0.45 * em
        lines = _wrap(_words(b["text"]), max(width_mm - indent - hang, 1.0), lambda rr: measure(rr, s, bb))
        for k, ln in enumerate(lines):
            y += s * 1.0 if (k or out) else s * 0.8          # down to this line's baseline
            out.append({"text": _as_mathtext(ln, bb), "size": s, "bold": bb, "x": indent + hang, "y": y,
                        "marker": b.get("marker") if k == 0 else None, "mx": indent, "item": b["kind"] == "item"})
            y += s * (LINE_PITCH - 1.0)
        first = False
        if b["kind"] == "heading":
            y += 0.15 * LINE_PITCH * size
    return out


def _gs():
    return shutil.which("gs") or next((p for p in ("/opt/homebrew/bin/gs", "/usr/local/bin/gs") if Path(p).exists()), None)


def _ghostscript(src: str, dest: str, fmt: str, cmyk: bool, outline: bool, dpi: int = 1200):
    """Convert with Ghostscript. `dpi`: the resolution of anything it has to rasterize (EPS has no transparency),
    and pictures keep their pixels (no downsampling)."""
    gs = _gs()
    if gs is None:
        raise RuntimeError("EPS, CMYK PDF and outlined text need Ghostscript: brew install ghostscript")
    args = [gs, "-dNOPAUSE", "-dBATCH", "-dSAFER", "-dQUIET", f"-r{int(dpi)}",
            "-dDownsampleColorImages=false", "-dDownsampleGrayImages=false", "-dDownsampleMonoImages=false",
            "-sDEVICE=" + ("eps2write" if fmt == "eps" else "pdfwrite"), f"-sOutputFile={dest}"]
    if cmyk:
        args += ["-sColorConversionStrategy=CMYK", "-dProcessColorModel=/DeviceCMYK"]
    if outline:
        args += ["-dNoOutputFonts"]
    r = subprocess.run(args + [src], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("Ghostscript failed: " + (r.stderr or r.stdout).strip()[-400:])


def _to_cmyk(im):
    """A picture in CMYK for print, through the system's generic CMYK profile when there is one."""
    from PIL import ImageCms
    prof = Path("/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc")
    im = im.convert("RGB")
    if prof.exists():
        try:
            srgb = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(im, srgb, str(prof), outputMode="CMYK")
        except Exception:                                # noqa: BLE001
            pass
    return im.convert("CMYK")



def _color_artists(fig) -> dict:
    """The colored marks of a panel by a stable key: ax0.line1, ax0.coll0 (scatters, fills), ax0.patch3 (bars)."""
    out = {}
    for i, ax in enumerate(fig.axes):
        if hasattr(ax, "_colorbar"):
            continue
        for j, a in enumerate(ax.lines):
            out[f"ax{i}.line{j}"] = a
        for j, a in enumerate(ax.collections):
            out[f"ax{i}.coll{j}"] = a
        for j, a in enumerate(ax.patches):
            out[f"ax{i}.patch{j}"] = a
    return out


def _artist_color(a):
    """The single color of a mark as #RRGGBB, or None (color-mapped, invisible)."""
    from matplotlib.collections import Collection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    try:
        if isinstance(a, Line2D):
            line = a.get_linestyle() not in ("None", "", " ")
            marker = a.get_marker() not in (None, "None", "", " ")
            if not a.get_visible() or not (line or marker):
                return None
            if line:
                return mcolors.to_hex(a.get_color())
            # markers only (error-bar caps, dots): the color they are drawn in
            unfilled = str(a.get_marker()) in "_|x+1234" or a.get_markerfacecolor() in ("none", "None")
            return mcolors.to_hex(a.get_markeredgecolor() if unfilled else a.get_markerfacecolor())
        if isinstance(a, Collection):
            if a.get_array() is not None and len(a.get_array()) > 1:
                return None                              # color-mapped
            fc = a.get_facecolor()
            if len(fc) == 0 or all(c[3] == 0 for c in fc):
                fc = a.get_edgecolor()
            if len(fc) == 0:
                return None
            first = mcolors.to_hex(fc[0])
            return first if all(mcolors.to_hex(c) == first for c in fc) else None
        if isinstance(a, Patch):
            fc = a.get_facecolor()
            return mcolors.to_hex(fc if fc[3] > 0 else a.get_edgecolor())
    except (ValueError, TypeError, IndexError):
        return None
    return None


def _set_colors(fig, colors):
    """Recolor marks the editor recolored (and the legend's swatches of the same color)."""
    if not colors:
        return
    from matplotlib.collections import Collection
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    arts = _color_artists(fig)
    swaps = {}
    for k, c in colors.items():
        a = arts.get(k)
        if a is None:
            continue
        old = _artist_color(a)
        if isinstance(a, Line2D):
            a.set_color(c)
            a.set_markeredgecolor(c)
            a.set_markerfacecolor(c)
        elif isinstance(a, Collection):
            a.set_facecolor(c)
            if a.get_linewidths() is not None and len(a.get_edgecolor()):
                a.set_edgecolor(c)
        elif isinstance(a, Patch):
            a.set_facecolor(c)
        if old:
            swaps[old] = c
    legends = [ax.get_legend() for ax in fig.axes if ax.get_legend() is not None] + list(fig.legends)
    for lg in legends:
        for h in lg.legend_handles:
            hc = _artist_color(h)
            if hc in swaps:
                if isinstance(h, Line2D):
                    h.set_color(swaps[hc])
                    h.set_markerfacecolor(swaps[hc])
                    h.set_markeredgecolor(swaps[hc])
                elif isinstance(h, (Collection, Patch)):
                    h.set_facecolor(swaps[hc])


def _measure_marks(fig, h) -> list:
    """The panel's colored marks: [{key, color, box (mm in the panel)}]."""
    r = fig.canvas.get_renderer()
    out = []
    for k, a in _color_artists(fig).items():
        c = _artist_color(a)
        if not c:
            continue
        try:
            b = a.get_window_extent(r)
        except Exception:                                # noqa: BLE001
            continue
        if not (b.width > 0 or b.height > 0) or not all(map(math.isfinite, (b.x0, b.y0, b.x1, b.y1))):
            continue
        d = fig.dpi
        out.append({"key": k, "color": c.upper(),
                    "box": [round(b.x0 / d * 25.4, 3), round(h - b.y1 / d * 25.4, 3), round(b.width / d * 25.4, 3),
                            round(b.height / d * 25.4, 3)]})
    return out[:600]


# a picture's pixel size, for scale bars: lengths in metres
UNITS_M = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "µm": 1e-6, "nm": 1e-9, "Å": 1e-10, "pm": 1e-12, "in": 0.0254}
_UNIT_NAMES = {"um": "µm", "μm": "µm", "micron": "µm", "microns": "µm", "micrometer": "µm", "micrometre": "µm",
               "\\u00b5m": "µm", "nanometer": "nm", "nanometre": "nm", "millimeter": "mm", "millimetre": "mm",
               "centimeter": "cm", "centimetre": "cm", "meter": "m", "metre": "m", "a": "Å", "angstrom": "Å",
               "ångström": "Å", "inch": "in"}


def _unit_key(unit: str) -> str:
    u = (unit or "").strip()
    return _UNIT_NAMES.get(u.lower(), u.replace("μ", "µ")) if u not in UNITS_M else u


def pixel_size_of(path) -> tuple[float, str] | None:
    """The size of one pixel of a picture, in metres, from its metadata, and where it came from: an OME-TIFF's
    PhysicalSizeX, an ImageJ TIFF's resolution and unit, or a TIFF, PNG or JPEG resolution too fine to be a print
    resolution (pixels under 2.5 µm; 300 dpi is a print resolution, not the size of what was imaged). None if none."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            tags = getattr(im, "tag_v2", None) or {}
            desc = tags.get(270) or ""
            if isinstance(desc, bytes):
                desc = desc.decode("utf-8", "replace")
            if "PhysicalSizeX=" in desc:                     # OME-XML
                m = re.search(r'PhysicalSizeX="([0-9.eE+-]+)"', desc)
                u = re.search(r'PhysicalSizeXUnit="([^"]+)"', desc)
                f = UNITS_M.get(_unit_key(u.group(1) if u else "µm"))
                if m and f and float(m.group(1)) > 0:
                    return float(m.group(1)) * f, "OME-TIFF"
            xres = tags.get(282)
            xres = float(xres) if xres else 0.0
            if desc.startswith("ImageJ=") and xres > 0:
                u = re.search(r"^unit=(.+)$", desc, re.M)
                f = UNITS_M.get(_unit_key(u.group(1).strip() if u else ""))
                if f:
                    return f / xres, "ImageJ"
            per_m = 0.0
            if xres > 0 and tags.get(296) in (2, 3):         # ResolutionUnit: inch, centimetre
                per_m = xres / (0.0254 if tags.get(296) == 2 else 0.01)
            elif im.format in ("PNG", "JPEG") and im.info.get("dpi"):
                per_m = float(im.info["dpi"][0]) / 0.0254
            if per_m > 1 / 2.5e-6:
                return 1 / per_m, f"{im.format} resolution"
    except Exception:                                        # noqa: BLE001
        return None
    return None


# color vision deficiencies (Machado et al. 2009, severity 1), applied in linear RGB
_CVD = {
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "tritanopia": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
    # printed in black and white: the luminance, as the View menu's grayscale preview shows it
    "grayscale": ((0.2126, 0.7152, 0.0722), (0.2126, 0.7152, 0.0722), (0.2126, 0.7152, 0.0722)),
}


def _lin(v):
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lab(rgb_lin):
    r, g, b = rgb_lin
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116   # noqa: E731
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _de(a, b) -> float:
    return math.dist(_lab(a), _lab(b))


def _seen_as(color: str, kind: str):
    lin = tuple(_lin(v) for v in mcolors.to_rgb(color))
    m = _CVD[kind]
    return tuple(min(1.0, max(0.0, sum(m[i][j] * lin[j] for j in range(3)))) for i in range(3))


def _colorful(c: str) -> bool:
    r, g, b = mcolors.to_rgb(c)
    return max(r, g, b) - min(r, g, b) > 0.12


def _clashes(colors) -> list:
    """Pairs of colors that most people tell apart but some can't, or that print alike in grayscale:
    [(a, b, [deficiencies, and "grayscale"])]."""
    cs = sorted({c.upper() for c in colors if c and _colorful(c)})
    out = []
    for i, a in enumerate(cs):
        for b in cs[i + 1:]:
            la, lb = tuple(_lin(v) for v in mcolors.to_rgb(a)), tuple(_lin(v) for v in mcolors.to_rgb(b))
            if _de(la, lb) < 20:
                continue
            kinds = [k for k in _CVD if _de(_seen_as(a, k), _seen_as(b, k)) < 9]
            if kinds:
                out.append((a, b, kinds))
    return out


def _color_vision(infos, fig) -> list:
    """Preflight: colors in a panel (or among the annotations) that look alike to color-blind readers (a warning),
    or only when printed in grayscale (a note: many color-blind-safe palettes have colors of the same lightness)."""
    def seen(kinds):
        cvd = [k for k in kinds if k != "grayscale"]
        return cvd, (", ".join(cvd) + (" and in grayscale" if "grayscale" in kinds else "")) if cvd else "in grayscale"

    warn = []
    for info in infos:
        marks = info.get("marks") or []
        if not marks:
            continue
        clashes = sorted(_clashes(m["color"] for m in marks), key=lambda c: c[2] == ["grayscale"])
        for a, b, kinds in clashes[:3]:
            cvd, how = seen(kinds)
            if cvd:
                warn.append({"level": "warning", "oid": info["oid"], "colors": [a, b],
                             "message": f"{info.get('kind')} {info['oid']}: {a} and {b} look alike with {how}; use a "
                                        f"color-blind-safe palette or tell them apart another way"})
            else:
                warn.append({"level": "info", "oid": info["oid"], "colors": [a, b],
                             "message": f"{info.get('kind')} {info['oid']}: {a} and {b} look alike in grayscale; if "
                                        f"the figure may be printed in black and white, tell them apart another way "
                                        f"(lightness, markers, line styles or labels)"})
    notes = [e["props"].get(k) for e in infos if e["kind"] in ("text", "line", "rect", "ellipse", "path", "bracket")
             for k in ("color", "stroke", "fill") if isinstance(e["props"].get(k), str)]
    for a, b, kinds in sorted(_clashes(notes), key=lambda c: c[2] == ["grayscale"])[:3]:
        cvd, how = seen(kinds)
        warn.append({"level": "warning" if cvd else "info", "oid": None, "colors": [a, b],
                     "message": f"annotations in {a} and {b} look alike {'with ' if cvd else ''}{how}"})
    return warn
