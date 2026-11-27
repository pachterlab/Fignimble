"""Reproducibility: how each figure, table and picture of a project was made, and whether it still is.

Three files hold it, all at the project's top, all meant for git:

- `fignimble.toml` (written by people): besides `[project]`, the environments recipes run in (`[env.NAME]`), data the
  repository doesn't hold (`[[input]]`, with where to fetch it and its checksum) and recipes for files made outside
  Fignimble (`[[recipe]]`: outputs, the command that makes them, its inputs and environment, how to compare a
  remake; or `manual = "..."` for what no command makes).
- `exports/` (written by Fignimble each time the project changes): a PDF of every figure, a CSV of every table, and
  under `exports/data/` the values each panel plots, one CSV per panel (or per table a panel draws).
- `fignimble.lock` (written by Fignimble): what each export and each recipe output was made from, by SHA-256.

`record` writes exports/ and the lock; `check` compares them with the project as it is, running nothing but
figure code; `reproduce` runs recipes again and compares what they make with what the project holds; `tag` names a
version in git, and `diff` compares two versions, or a version and the project as it is.

Paths in a recipe are relative to its `cwd` (itself relative to the project, `.` by default); paths in the lock are
relative to the project, with `/` between folders.
"""

from __future__ import annotations

import csv
import datetime as _dt
import fnmatch
import glob as _glob
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

EXPORTS = "exports"
LOCK = "fignimble.lock"
LOCK_VERSION = 1
TAG_PREFIX = "fignimble/"
DIGEST_DPI = 96                   # figures are compared as pictures at this resolution
PICTURE_DPI = 150                 # pictures in exports/ at most this, as printed ([exports] picture_dpi)
PIXEL_STEP = 16                   # a pixel differs when a channel moves by more than this (of 255)
DEFAULT_COMPARE = {"rtol": 1e-6, "atol": 1e-9, "pixels": 0.001}

# verdicts of reproduce and diff, for one output
EXACT, WITHIN, DIFFERS = "exact", "within-tolerance", "differs"


# --- small helpers ---

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rel(project: Path, p) -> str:
    """A path as the lock writes it: relative to the project (../ when outside it), with / between folders."""
    return Path(os.path.relpath(Path(p).resolve(), project)).as_posix()


def _abs(project: Path, rel: str) -> Path:
    return (project / rel).resolve()


def _write_atomic(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def parse_duration(text) -> float | None:
    """Seconds in "20 min", "2h", "1h30m", "90 s", "3 days"; None for nothing readable."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    units = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
             "d": 86400, "day": 86400}
    total, found = 0.0, False
    for n, u in re.findall(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)", str(text)):
        u = u.lower().rstrip("s") if len(u) > 1 else u.lower()
        if u not in units:
            return None
        total += float(n) * units[u]
        found = True
    return total if found else None


class Hasher:
    """SHA-256 of files, remembered by (size, modification time) in the cache, so that checking a project with
    gigabytes of data doesn't read it all every time."""

    def __init__(self, cache: Path | None):
        self.path = cache / "hashes.json" if cache else None
        self.known: dict = {}
        self.dirty = False
        if self.path:
            try:
                self.known = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.known = {}

    def __call__(self, p) -> str | None:
        p = Path(p)
        try:
            st = p.stat()
        except OSError:
            return None
        if not p.is_file():
            return None
        key = str(p.resolve())
        got = self.known.get(key)
        if got and got[0] == st.st_size and got[1] == st.st_mtime_ns:
            return got[2]
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.known[key] = [st.st_size, st.st_mtime_ns, digest]
        self.dirty = True
        return digest

    def save(self):
        if self.path and self.dirty:
            try:
                _write_atomic(self.path, json.dumps(self.known).encode())
            except OSError:
                pass
            self.dirty = False


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# --- fignimble.toml ---

@dataclass
class Env:
    name: str
    image: str | None = None          # a container image, best pinned by digest (name@sha256:...)
    dockerfile: str | None = None     # how the image is built (built here when no image is named)
    context: str | None = None        # the build's folder (default: the Dockerfile's)
    conda: str | None = None          # a conda environment or lock file
    python: str | None = None         # requirements.txt, uv.lock, ...
    command: str | None = None        # what runs a command in it here: "conda run -n paper", "uv run --frozen"

    def files(self) -> dict[str, str]:
        return {k: v for k, v in (("dockerfile", self.dockerfile), ("conda", self.conda), ("python", self.python))
                if v}


@dataclass
class Input:
    path: str                         # relative to the project
    fetch: str | None = None
    sha256: str | None = None
    size: str | None = None
    access: str | None = None         # "public", or how to ask for it ("restricted: dbGaP phs000123")


@dataclass
class Recipe:
    name: str
    outputs: list[str]
    run: str | None = None
    cwd: str = "."
    inputs: list[str] = field(default_factory=list)
    env: str | None = None
    data: list[str] = field(default_factory=list)     # the numbers an output shows, compared instead of its pixels
    compare: dict = field(default_factory=dict)
    seed: object = None
    deterministic: bool = True
    resources: dict = field(default_factory=dict)     # time, memory, gpu
    manual: str | None = None                         # made by hand: how
    where: str | None = None                          # "cluster", ...: documented, not run here

    def base(self, project: Path) -> Path:
        return (project / self.cwd).resolve()

    def paths(self, project: Path, which: list[str]) -> list[Path]:
        """Files named by a list of the recipe's paths, globs expanded (a glob matching nothing names nothing)."""
        out = []
        for p in which:
            full = self.base(project) / p
            if any(ch in p for ch in "*?["):
                out += [Path(m).resolve() for m in sorted(_glob.glob(str(full), recursive=True))]
            else:
                out.append(full.resolve())
        return out

    def covers(self, project: Path, path: Path) -> bool:
        path = Path(path).resolve()
        for p in self.outputs:
            full = (self.base(project) / p)
            if any(ch in p for ch in "*?["):
                if fnmatch.fnmatch(str(path), str(Path(os.path.normpath(full)))):
                    return True
            elif full.resolve() == path:
                return True
        return False

    def runnable(self) -> bool:
        return bool(self.run) and not self.manual and not self.where

    def definition(self) -> dict:
        return {"outputs": self.outputs, "run": self.run, "cwd": self.cwd, "inputs": self.inputs, "env": self.env,
                "data": self.data, "manual": self.manual}

    def digest(self, env: Env | None) -> str:
        d = self.definition()
        if env:
            d["env_def"] = {k: v for k, v in env.__dict__.items() if k != "name"}
        return sha256_bytes(json.dumps(d, sort_keys=True).encode())[:16]

    def tolerance(self) -> dict:
        return {**DEFAULT_COMPARE, **{k: v for k, v in self.compare.items() if k in DEFAULT_COMPARE}}


@dataclass
class Config:
    envs: dict[str, Env] = field(default_factory=dict)
    inputs: list[Input] = field(default_factory=list)
    recipes: list[Recipe] = field(default_factory=list)
    exports: dict = field(default_factory=lambda: {"on_save": True, "data": True})
    problems: list[str] = field(default_factory=list)

    def recipe_for(self, project: Path, path: Path) -> Recipe | None:
        return next((r for r in self.recipes if r.covers(project, path)), None)


def _toml(text: str) -> dict:
    try:
        import tomllib
    except ImportError:                       # Python 3.10
        try:
            import tomli as tomllib           # type: ignore
        except ImportError:
            raise RuntimeError("reading recipes in fignimble.toml needs Python 3.11, or `pip install tomli`") from None
    return tomllib.loads(text)


def _strs(v, what: str, problems: list) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return list(v)
    problems.append(f"{what} should be a list of paths")
    return []


def default_name(output: str) -> str:
    """A recipe's name when it gives none: its first output's name (out/umap.png: umap), or the folder of a glob
    (images/*.svg: images)."""
    stem = re.sub(r"[*?\[\]]", "", Path(output).stem).strip("._-")
    return stem or Path(output).parent.name or "recipe"


def load_config(project) -> Config:
    """The reproducibility part of a project's fignimble.toml; what's wrong with it is in `problems`, and the rest
    is still read."""
    from .templates import MARKER
    project = Path(project)
    cfg = Config()
    try:
        text = (project / MARKER).read_text(encoding="utf-8")
    except OSError:
        return cfg
    try:
        raw = _toml(text)
    except Exception as exc:                  # noqa: BLE001
        cfg.problems.append(f"{MARKER} can't be read: {exc}")
        return cfg
    ex = raw.get("exports") or {}
    if isinstance(ex, dict):
        cfg.exports.update({k: ex[k] for k in ("on_save", "data", "picture_dpi") if k in ex})
    for name, e in (raw.get("env") or {}).items():
        if not isinstance(e, dict):
            cfg.problems.append(f"[env.{name}] should be a table")
            continue
        unknown = set(e) - {"image", "dockerfile", "context", "conda", "python", "command"}
        if unknown:
            cfg.problems.append(f"[env.{name}]: unknown key{'s' if len(unknown) > 1 else ''} {', '.join(sorted(unknown))}")
        cfg.envs[name] = Env(name, **{k: str(v) for k, v in e.items() if k not in unknown})
    for i, e in enumerate(raw.get("input") or []):
        if not isinstance(e, dict) or not isinstance(e.get("path"), str):
            cfg.problems.append(f"input {i + 1} needs a path")
            continue
        cfg.inputs.append(Input(e["path"], e.get("fetch"), e.get("sha256"), e.get("size") and str(e["size"]),
                                e.get("access")))
    names = set()
    for i, r in enumerate(raw.get("recipe") or []):
        if not isinstance(r, dict):
            cfg.problems.append(f"recipe {i + 1} should be a table")
            continue
        p = cfg.problems
        outputs = _strs(r.get("outputs"), f"recipe {i + 1}: outputs", p)
        if not outputs:
            p.append(f"recipe {i + 1} names no outputs")
            continue
        name = str(r.get("name") or default_name(outputs[0]))
        if name in names:
            p.append(f"two recipes are called {name}: give one a name")
        names.add(name)
        known = {"name", "outputs", "run", "cwd", "inputs", "env", "data", "compare", "seed", "deterministic",
                 "resources", "manual", "where"}
        if set(r) - known:
            p.append(f"recipe {name}: unknown key{'s' if len(set(r) - known) > 1 else ''} "
                     f"{', '.join(sorted(set(r) - known))}")
        rec = Recipe(name=name, outputs=outputs, run=r.get("run"), cwd=str(r.get("cwd") or "."),
                     inputs=_strs(r.get("inputs"), f"recipe {name}: inputs", p), env=r.get("env"),
                     data=_strs(r.get("data"), f"recipe {name}: data", p),
                     compare=r.get("compare") if isinstance(r.get("compare"), dict) else {}, seed=r.get("seed"),
                     deterministic=bool(r.get("deterministic", True)),
                     resources=r.get("resources") if isinstance(r.get("resources"), dict) else {},
                     manual=r.get("manual"), where=r.get("where"))
        if not rec.run and not rec.manual:
            p.append(f"recipe {name} has neither run (the command) nor manual (how it was made by hand)")
        if rec.env and rec.env not in cfg.envs:
            p.append(f"recipe {name} names env {rec.env!r}, which fignimble.toml doesn't define")
        cfg.recipes.append(rec)
    return cfg


# --- the lock ---

def read_lock(project) -> dict:
    try:
        lock = json.loads((Path(project) / LOCK).read_text(encoding="utf-8"))
        if isinstance(lock, dict):
            lock.setdefault("exports", {})
            lock.setdefault("outputs", {})
            return lock
    except (OSError, ValueError):
        pass
    return {"lock": LOCK_VERSION, "exports": {}, "outputs": {}}


def lock_text(lock: dict) -> str:
    return json.dumps(lock, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_lock(project, lock: dict) -> bool:
    """Write the lock if it changed; whether it did."""
    from . import __version__
    lock = {**lock, "lock": LOCK_VERSION, "fignimble": __version__}
    path = Path(project) / LOCK
    text = lock_text(lock)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    _write_atomic(path, text.encode())
    return True


def git_head(project) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def env_record(project: Path, env: Env | None, hasher: Hasher) -> dict | None:
    """What a lock says about an environment: its image, and a hash of each file that defines it."""
    if env is None:
        return None
    out = {"name": env.name}
    if env.image:
        out["image"] = env.image
    if env.command:
        out["command"] = env.command
    for k, f in env.files().items():
        out[k] = {"path": f, "sha256": hasher(project / f)}
    return out


# --- comparing two files ---

def _rows(data: bytes, delim: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace")), delimiter=delim))


def _float(s: str):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def compare_tables(a: bytes, b: bytes, rtol: float, atol: float, delim: str = ",") -> tuple[str, str]:
    """Cells compared as numbers where both are numbers, within rtol/atol, and as text elsewhere."""
    if a == b:
        return EXACT, "identical"
    ra, rb = _rows(a, delim), _rows(b, delim)
    if len(ra) != len(rb):
        return DIFFERS, f"{len(ra)} rows, now {len(rb)}"
    cells = off = 0
    worst = 0.0
    shape = None
    for i, (x, y) in enumerate(zip(ra, rb)):
        if len(x) != len(y):
            shape = f"row {i + 1} has {len(x)} cells, now {len(y)}"
            break
        for u, v in zip(x, y):
            cells += 1
            if u == v:
                continue
            fu, fv = _float(u), _float(v)
            if fu is None or fv is None:
                off += 1
                continue
            d = abs(fu - fv)
            worst = max(worst, d / max(abs(fu), abs(fv), 1e-300))
            if d > atol + rtol * abs(fu):
                off += 1
    if shape:
        return DIFFERS, shape
    if off:
        return DIFFERS, f"{off} of {cells} cells differ beyond the tolerance (rtol {rtol:g}, atol {atol:g})"
    return WITHIN, f"all {cells} cells within rtol {rtol:g} (largest relative difference {worst:.2g})"


def _raster(path_or_bytes, ext: str):
    """A file as a list of RGB pictures (numpy arrays): a PDF's or SVG's pages drawn at DIGEST_DPI, or a picture."""
    import numpy as np
    ext = ext.lower()
    if ext in (".pdf", ".svg", ".eps"):
        import pymupdf
        if isinstance(path_or_bytes, (bytes, bytearray)):
            doc = pymupdf.open(stream=bytes(path_or_bytes), filetype=ext.lstrip("."))
        else:
            doc = pymupdf.open(str(path_or_bytes))
        out = []
        with doc:
            for page in doc:
                pix = page.get_pixmap(dpi=DIGEST_DPI, alpha=False, colorspace=pymupdf.csRGB)
                out.append(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy())
        return out
    from PIL import Image
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else path_or_bytes
    with Image.open(src) as im:
        frames = []
        try:
            for k in range(getattr(im, "n_frames", 1)):
                im.seek(k)
                frames.append(np.asarray(im.convert("RGB")).copy())
        except EOFError:
            pass
        return frames


def picture_digest(path_or_bytes, ext: str = ".pdf") -> str:
    """A hash of how a file looks (its pages drawn), not of its bytes, which carry dates and ids."""
    h = hashlib.sha256()
    for a in _raster(path_or_bytes, ext):
        h.update(str(a.shape).encode())
        h.update(a.tobytes())
    return h.hexdigest()[:32]


def compare_pictures(a, b, ext: str, pixels: float, highlight: Path | None = None,
                     boxes: dict | None = None, size_mm: tuple | None = None) -> tuple[str, str, list[str]]:
    """(verdict, detail, panels): pages drawn and compared pixel by pixel; at most `pixels` (a fraction) may differ.
    `boxes` ({letter: [x, y, w, h] mm}) with `size_mm` name the panels where pixels differ. `highlight`: a picture
    of the new version with what differs in red."""
    import numpy as np
    pa, pb = _raster(a, ext), _raster(b, ext)
    if len(pa) != len(pb):
        return DIFFERS, f"{len(pa)} pages, now {len(pb)}", []
    total = off = 0
    where: set = set()
    for k, (x, y) in enumerate(zip(pa, pb)):
        if x.shape != y.shape:
            return DIFFERS, f"size {x.shape[1]}x{x.shape[0]} px, now {y.shape[1]}x{y.shape[0]} px", []
        mask = (np.abs(x.astype(np.int16) - y.astype(np.int16)) > PIXEL_STEP).any(axis=2)
        n = int(mask.sum())
        total += mask.size
        off += n
        if n and boxes and size_mm and k == 0:
            sy, sx = mask.shape[0] / size_mm[1], mask.shape[1] / size_mm[0]
            for letter, (bx, by, bw, bh) in boxes.items():
                sub = mask[max(0, int(by * sy)):int((by + bh) * sy) + 1, max(0, int(bx * sx)):int((bx + bw) * sx) + 1]
                if sub.size and sub.sum() > max(3, 0.0005 * sub.size):
                    where.add(letter)
        if n and highlight and k == 0:
            from PIL import Image
            img = (y.astype(np.float32) * 0.35 + 255 * 0.65).astype(np.uint8)
            img[mask] = (220, 30, 30)
            highlight.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(highlight)
    if off == 0:
        return EXACT, "the same, pixel for pixel", []
    frac = off / max(total, 1)
    panels = sorted(where, key=lambda s: (len(s), s))
    if frac <= pixels:
        return WITHIN, f"{frac:.3%} of pixels differ (tolerance {pixels:.3%})", panels
    return DIFFERS, f"{frac:.2%} of pixels differ", panels


PICTURE_EXT = (".pdf", ".svg", ".eps", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp")


def compare_files(a: bytes | None, b: bytes | None, name: str, tol: dict | None = None,
                  highlight: Path | None = None, boxes=None, size_mm=None) -> dict:
    """Two versions of a file, by what kind of file it is: {verdict, detail, panels?}."""
    tol = {**DEFAULT_COMPARE, **(tol or {})}
    if a is None and b is None:
        return {"verdict": "missing", "detail": "in neither"}
    if a is None:
        return {"verdict": "added", "detail": "new"}
    if b is None:
        return {"verdict": "removed", "detail": "gone"}
    if a == b:
        return {"verdict": EXACT, "detail": "identical"}
    ext = Path(name).suffix.lower()
    try:
        if ext in (".csv", ".tsv"):
            v, d = compare_tables(a, b, tol["rtol"], tol["atol"], "\t" if ext == ".tsv" else ",")
            return {"verdict": v, "detail": d}
        if ext in PICTURE_EXT:
            v, d, panels = compare_pictures(a, b, ext, tol["pixels"], highlight, boxes, size_mm)
            out = {"verdict": v, "detail": d}
            if panels:
                out["panels"] = panels
            if highlight and highlight.exists() and v != EXACT:
                out["highlight"] = str(highlight)
            return out
    except Exception as exc:                  # noqa: BLE001
        return {"verdict": DIFFERS, "detail": f"couldn't compare ({type(exc).__name__}: {exc}); the bytes differ"}
    return {"verdict": DIFFERS, "detail": "the bytes differ"}


# --- record: exports/ and the lock ---

def _slug(s: str) -> str:
    return re.sub(r"[^\w.-]+", "-", s).strip("-") or "x"


def _panel_boxes(data: dict) -> dict:
    """{letter: [x, y, w, h]} of a render's lettered elements (mm)."""
    letters = {l["panel"]: l["text"] for l in data.get("labels", [])}
    return {letters[e["oid"]]: [round(v, 2) for v in e["box"]] for e in data.get("elements", [])
            if e.get("oid") in letters and e.get("box")}


def _figure_files(project: Path) -> list[tuple[Path, str]]:
    from .render import is_figure_source, is_table_source
    out = []
    for p in sorted(project.glob("*.py")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if is_figure_source(text):
            out.append((p, "table" if is_table_source(text) else "figure"))
    return out


def record(renderer, force: bool = False, data: bool | None = None, only: list[str] | None = None) -> dict:
    """Bring exports/ and the lock up to date with the project: render each figure, and write its PDF when it looks
    different from the one in exports/ (so that a change that doesn't show leaves git nothing to commit); each
    table's CSV; the values each panel plots when its figure changed (`data`, default the project's setting); and
    what each recipe's outputs, and each picture a figure places, are now. `only`: these figure files.

    -> {written: [...], removed: [...], unchanged: [...], errors: {figure: message}, lock: bool}"""
    from . import tables
    project = renderer.project
    cfg = load_config(project)
    data = cfg.exports.get("data", True) if data is None else data
    lock = read_lock(project)
    hasher = Hasher(renderer.cache)
    ex_dir = project / EXPORTS
    work = renderer.cache / "record"
    work.mkdir(parents=True, exist_ok=True)
    report = {"written": [], "removed": [], "unchanged": [], "errors": {}}
    figures = _figure_files(project)
    if only:
        want = {Path(f).stem for f in only}
        figures = [(p, k) for p, k in figures if p.stem in want]
    seen_exports: set[str] = set()
    pictures: dict[str, set] = {}         # pictures figures place -> the figures
    state_file = work / "state.json"      # per figure: what its panel values were last extracted from
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    def sources() -> dict:
        out = {}
        for f in sorted(renderer.used):
            h = hasher(f)
            if h:
                out[_rel(project, f)] = h
        style = project / "style.py"
        if style.exists():
            out.setdefault("style.py", hasher(style))
        return out

    for path, kind in figures:
        name = path.stem
        try:
            res = renderer.render(str(path), sprites=False)
        except Exception as exc:                   # noqa: BLE001
            res = {"error": f"{type(exc).__name__}: {exc}"}
        if res.get("error"):
            report["errors"][path.name] = res["error"].strip().splitlines()[-1] if res["error"].strip() else "error"
            # what it made before stays, and stays recorded
            seen_exports.update(k for k, v in lock["exports"].items() if v.get("figure") == path.name)
            continue
        src = sources()
        for f in renderer.used:
            if Path(f).suffix.lower() in PICTURE_EXT:
                pictures.setdefault(_rel(project, f), set()).add(path.name)
        tables_here = [e for e in res["elements"] if e["kind"] == "table" and e["props"].get("cells") is not None]
        if kind == "table":
            outs = [(f"{EXPORTS}/{name}.csv", None)]
        else:
            outs = [(f"{EXPORTS}/{name}.{_slug(e['props'].get('name') or f'table{i + 1}')}.csv", e["oid"])
                    for i, e in enumerate(tables_here)]
        for rel, oid in outs:
            tmp = work / Path(rel).name
            try:
                tables.write_rendered(res, tmp, "csv", oid=oid)
            except ValueError as exc:
                report["errors"][path.name] = str(exc)
                continue
            new = tmp.read_bytes()
            seen_exports.add(rel)
            dest = project / rel
            entry = {"figure": path.name, "kind": "table", "sha256": sha256_bytes(new), "sources": src}
            if dest.exists() and dest.read_bytes() == new and not force:
                report["unchanged"].append(rel)
            else:
                _write_atomic(dest, new)
                report["written"].append(rel)
            old = lock["exports"].get(rel, {})
            entry["at"] = old.get("at") if {k: old.get(k) for k in entry} == entry else _now()
            lock["exports"][rel] = entry
        if kind == "table":
            continue
        rel = f"{EXPORTS}/{name}.pdf"
        seen_exports.add(rel)
        tmp = work / f"{name}.pdf"
        try:
            renderer.export(str(path), str(tmp), "pdf")
        except Exception as exc:                   # noqa: BLE001
            report["errors"][path.name] = f"export failed: {type(exc).__name__}: {exc}"
            continue
        src = sources()
        _lighten(tmp, cfg.exports.get("picture_dpi", PICTURE_DPI))
        dig = picture_digest(tmp)
        dest = project / rel
        old = lock["exports"].get(rel, {})
        same = dest.exists() and old.get("picture") == dig and old.get("sha256") == hasher(dest)
        if same and not force:
            report["unchanged"].append(rel)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dest)
            report["written"].append(rel)
        entry = {"figure": path.name, "kind": "figure", "picture": dig, "sha256": hasher(dest), "sources": src,
                 "size": [round(v, 2) for v in res["size"]], "panels": _panel_boxes(res)}
        entry["at"] = old.get("at") if {k: old.get(k) for k in entry} == entry else _now()
        lock["exports"][rel] = entry
        # the values each panel plots, when anything the figure is made from changed (a picture can stay the same
        # while the numbers move: an autoscaled axis without tick labels), or they were never written. Data files
        # a panel reads by itself aren't among the sources; the project's fingerprint covers them.
        prefix = f"{EXPORTS}/data/{name}/"
        had = {k for k in lock["exports"] if k.startswith(prefix)}
        made_from = sources_digest(src)
        key = sha256_bytes(f"{made_from} {renderer.fingerprint()} {dig}".encode())[:24]
        if data and (not same or force or not had or any(not (project / k).exists() for k in had)
                     or state.get(name) != key
                     or any(lock["exports"][k].get("made_from") != made_from for k in had)):
            try:
                written = _record_data(renderer, path, project, prefix, dig, made_from, lock)
                state[name] = key
            except Exception as exc:               # noqa: BLE001
                report["errors"][path.name] = f"panel data: {type(exc).__name__}: {exc}"
                written = None
            if written is not None:
                for k in had - set(written):
                    (project / k).unlink(missing_ok=True)
                    lock["exports"].pop(k, None)
                    report["removed"].append(k)
                for k, changed in written.items():
                    (report["written"] if changed else report["unchanged"]).append(k)
                seen_exports.update(written)
            else:
                seen_exports.update(had)
        elif data:
            seen_exports.update(had)
    # exports of figures that are gone
    if not only:
        for rel in list(lock["exports"]):
            if rel not in seen_exports:
                (project / rel).unlink(missing_ok=True)
                lock["exports"].pop(rel)
                report["removed"].append(rel)
        _prune_empty(ex_dir)
    # recipe outputs and pictures: as they are now, when they changed
    _record_outputs(project, cfg, lock, hasher, pictures, partial=bool(only))
    try:
        _write_atomic(state_file, json.dumps(state).encode())
    except OSError:
        pass
    hasher.save()
    report["lock"] = write_lock(project, lock)
    return report


def _lighten(pdf: Path, dpi):
    """Pictures in an export for exports/ brought down to `dpi` as printed (a record to read and compare, not the file
    to submit: `fignimble export` makes that), so that a micrograph doesn't put megabytes into git each time the
    figure changes. 0 or false keeps them as they are."""
    if not dpi:
        return
    import pymupdf
    dpi = int(dpi)
    with pymupdf.open(str(pdf)) as doc:
        if not doc[0].get_images():
            return
        doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=85, lossy=True, lossless=True)
        doc.save(str(pdf) + ".tmp", garbage=3, deflate=True)
    os.replace(str(pdf) + ".tmp", pdf)


def _prune_empty(d: Path):
    if not d.is_dir():
        return
    for sub in sorted((p for p in d.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            sub.rmdir()
        except OSError:
            pass


def sources_digest(src: dict) -> str:
    """One hash for what an export was made from (its sources' hashes)."""
    return sha256_bytes(json.dumps(src, sort_keys=True).encode())[:16]


def _record_data(renderer, path: Path, project: Path, prefix: str, picture: str, made_from: str, lock: dict) -> dict:
    """Write the values each panel of a figure plots, a CSV per panel (its tables one under another, each under a
    `# title` row, as on the source-data sheet), and their lock entries: {rel: changed}."""
    from . import sourcedata, tables
    out = {}
    for panel in sourcedata.collect(renderer, str(path)):
        blocks = [(t, rows) for t, rows in panel["blocks"] if rows]
        if not blocks:
            continue
        rel = f"{prefix}{_slug(panel['key'])}.csv"
        rows_out: list = []
        for title, rows in blocks:            # stacked as on the source-data sheet: a title, the table, a blank row
            if rows_out:
                rows_out.append([])
            if len(blocks) > 1 or title:
                rows_out.append([f"# {title}" if title else "#"])
            rows_out += [["" if c is None else (repr(c) if isinstance(c, float) else c) for c in r] for r in rows]
        text = tables.write_delimited(rows_out).encode("utf-8")
        dest = project / rel
        changed = not (dest.exists() and dest.read_bytes() == text)
        if changed:
            _write_atomic(dest, text)
        entry = {"figure": path.name, "kind": "panel-data", "panel": panel["panel"], "how": panel["how"],
                 "tables": len(blocks), "sha256": sha256_bytes(text), "picture": picture, "made_from": made_from}
        if panel["notes"]:
            entry["notes"] = panel["notes"]
        old = lock["exports"].get(rel, {})
        entry["at"] = old.get("at") if {k: old.get(k) for k in entry} == entry else _now()
        lock["exports"][rel] = entry
        out[rel] = changed
    return out


def _record_outputs(project: Path, cfg: Config, lock: dict, hasher: Hasher, pictures: dict, partial: bool):
    """Lock entries for recipe outputs and placed pictures whose contents changed since they were recorded (or that
    were never recorded): `observed`, with their inputs as they are now. An output whose contents didn't change
    keeps its entry, so that inputs changed since show as stale."""
    head = None
    keep: set[str] = set()
    for r in cfg.recipes:
        env = cfg.envs.get(r.env) if r.env else None
        for out in r.paths(project, r.outputs):
            rel = _rel(project, out)
            keep.add(rel)
            h = hasher(out)
            if h is None:
                continue
            old = lock["outputs"].get(rel)
            if old and old.get("sha256") == h:
                if rel in pictures:
                    old["figures"] = sorted(pictures[rel])
                continue
            head = head or git_head(project)
            entry = {"recipe": r.name, "sha256": h, "how": "manual" if r.manual else "observed", "at": _now(),
                     "recipe_hash": r.digest(env),
                     "inputs": {_rel(project, i): hasher(i) for i in r.paths(project, r.inputs)},
                     "data": {_rel(project, d): hasher(d) for d in r.paths(project, r.data)}}
            if env:
                entry["env"] = env_record(project, env, hasher)
            if head:
                entry["commit"] = head
            if rel in pictures:
                entry["figures"] = sorted(pictures[rel])
            lock["outputs"][rel] = entry
    for rel, figs in pictures.items():
        if rel in keep:
            continue
        keep.add(rel)
        h = hasher(_abs(project, rel))
        old = lock["outputs"].get(rel)
        if old and old.get("sha256") == h and not old.get("recipe"):
            old["figures"] = sorted(figs)
            continue
        if h:
            lock["outputs"][rel] = {"sha256": h, "how": "observed", "at": _now(), "figures": sorted(figs)}
    if not partial:
        for rel in list(lock["outputs"]):
            if rel not in keep:
                lock["outputs"].pop(rel)


# --- check ---

def _item(path: str, kind: str, state: str, detail: str = "", level: str = "ok", **more) -> dict:
    return {"path": path, "kind": kind, "state": state, "detail": detail, "level": level, **more}


def check(project, renderer=None) -> dict:
    """The project against its lock, without running recipes. With a renderer, each figure is drawn and compared
    with its PDF in exports/ (else exports are judged by whether the files they were made from changed).

    -> {items: [{path, kind, state, detail, level}], problems, summary: {state: n}, ok}
    level: ok | info (worth knowing) | warning (the project doesn't say how this was made) | error (out of date,
    missing, or not what was recorded)"""
    project = Path(project).resolve()
    cfg = load_config(project)
    lock = read_lock(project)
    hasher = Hasher(project / ".fignimble" if (project / ".fignimble").is_dir() else None)
    items = []
    recorded = (project / LOCK).exists()
    if not recorded:
        items.append(_item(LOCK, "lock", "missing", "nothing recorded yet: open the project in Fignimble, or run "
                           "`fignimble record`", "warning"))
    # exports
    figs = {p.name: k for p, k in _figure_files(project)}
    by_figure: dict[str, list] = {}
    for rel, e in lock["exports"].items():
        by_figure.setdefault(e.get("figure"), []).append(rel)
    for name, kind in (figs.items() if recorded else ()):
        stem = Path(name).stem
        expected = f"{EXPORTS}/{stem}.csv" if kind == "table" else f"{EXPORTS}/{stem}.pdf"
        if expected not in lock["exports"] or not (project / expected).exists():
            items.append(_item(expected, "export", "missing", f"no export of {name} recorded", "error"))
    for rel, e in sorted(lock["exports"].items()):
        f = project / rel
        if e.get("figure") not in figs:
            items.append(_item(rel, "export", "orphan", f"{e.get('figure')} is gone", "error"))
            continue
        h = hasher(f)
        if h is None:
            items.append(_item(rel, "export", "missing", "recorded but not on disk", "error"))
            continue
        if h != e.get("sha256"):
            items.append(_item(rel, "export", "edited", "not the file recorded (edited by hand, or written by "
                               "something else)", "error"))
            continue
        if e.get("kind") == "panel-data":
            fig_entry = lock["exports"].get(f"{EXPORTS}/{Path(e['figure']).stem}.pdf", {})
            if fig_entry.get("picture") != e.get("picture") or \
                    sources_digest(fig_entry.get("sources", {})) != e.get("made_from"):
                items.append(_item(rel, "export", "stale", "written before its figure last changed", "error"))
                continue
            srcs = fig_entry.get("sources", {})
        else:
            srcs = e.get("sources", {})
        changed = [s for s, sh in srcs.items() if hasher(_abs(project, s)) != sh]
        if changed and e.get("kind") != "panel-data":
            items.append(_item(rel, "export", "stale", "made from files changed since: " + ", ".join(changed[:6]),
                               "error", changed=changed))
        elif changed:
            items.append(_item(rel, "export", "stale", "its figure changed since", "error", changed=changed))
        else:
            items.append(_item(rel, "export", "current", "", "ok"))
    if renderer is not None and recorded:
        _check_renders(project, renderer, lock, items)
    # inputs
    for inp in cfg.inputs:
        p = project / inp.path
        h = hasher(p)
        if h is None:
            how = (f"fetch it from {inp.fetch}" if inp.fetch else
                   (f"access: {inp.access}" if inp.access else "fignimble.toml doesn't say where to get it"))
            items.append(_item(inp.path, "input", "missing", how, "warning" if inp.fetch or inp.access else "error"))
        elif inp.sha256 and h != inp.sha256.lower():
            items.append(_item(inp.path, "input", "checksum", "its SHA-256 isn't the one fignimble.toml gives",
                               "error"))
        else:
            items.append(_item(inp.path, "input", "ok", "checksum matches" if inp.sha256 else "no checksum given",
                               "ok" if inp.sha256 else "info"))
    # recipe outputs and pictures
    for rel, e in sorted(lock["outputs"].items()):
        p = _abs(project, rel)
        r = cfg.recipe_for(project, p)
        h = hasher(p)
        figures = e.get("figures") or []
        used = f" (in {', '.join(figures)})" if figures else ""
        if h is None:
            items.append(_item(rel, "output", "missing", f"recorded, but not on disk{used}", "error"))
            continue
        if r is None:
            items.append(_item(rel, "output", "no-recipe", f"no recipe says how it was made{used}",
                               "info" if not rel.startswith("../") and Path(rel).parts[0] == "images" else "warning",
                               figures=figures))
            continue
        if r.manual:
            items.append(_item(rel, "output", "manual", f"{r.manual}{used}", "info", recipe=r.name, figures=figures))
            continue
        if h != e.get("sha256"):
            items.append(_item(rel, "output", "unrecorded", f"changed since recorded{used}: open the project or run "
                               "`fignimble record`", "error", recipe=r.name, figures=figures))
            continue
        env = cfg.envs.get(r.env) if r.env else None
        why = [f"recipe {r.name} changed"] if e.get("recipe_hash") and e["recipe_hash"] != r.digest(env) else []
        now_inputs = {_rel(project, i): hasher(i) for i in r.paths(project, r.inputs)}
        for k, v in now_inputs.items():
            if k not in e.get("inputs", {}):
                why.append(f"{k} is a new input")
            elif e["inputs"][k] != v:
                why.append(f"{k} {'is missing' if v is None else 'changed'}")
        if env:
            for k, f in env.files().items():
                rec = ((e.get("env") or {}).get(k) or {})
                if rec.get("sha256") and rec["sha256"] != hasher(project / f):
                    why.append(f"environment {env.name}: {f} changed")
        state = "stale" if why else ("made" if e.get("how") == "made" else "recorded")
        detail = "; ".join(why) if why else ("remade by Fignimble from these inputs" if state == "made" else
                                             "as recorded; not yet remade by `fignimble reproduce`")
        items.append(_item(rel, "output", state, detail + used, "error" if why else "ok", recipe=r.name,
                           figures=figures))
    # recipes whose outputs were never recorded
    for r in cfg.recipes:
        outs = r.paths(project, r.outputs)
        if not outs or all(_rel(project, o) not in lock["outputs"] and not o.exists() for o in outs):
            if not r.manual:
                items.append(_item(r.name, "recipe", "no-output", "none of its outputs exist yet: run "
                                   "`fignimble reproduce`", "warning", recipe=r.name))
    hasher.save()
    summary: dict[str, int] = {}
    for it in items:
        summary[it["state"]] = summary.get(it["state"], 0) + 1
    problems = list(cfg.problems)
    return {"project": str(project), "items": items, "problems": problems, "summary": summary,
            "ok": not problems and not any(it["level"] == "error" for it in items)}


def _check_renders(project: Path, renderer, lock: dict, items: list):
    """Each figure drawn now, compared with its PDF in exports/: replaces the 'current' or 'stale' verdict that
    file hashes gave."""
    work = renderer.cache / "record"
    work.mkdir(parents=True, exist_ok=True)
    by_path = {it["path"]: it for it in items if it["kind"] == "export"}
    for path, kind in _figure_files(project):
        if kind == "table":
            continue
        rel = f"{EXPORTS}/{path.stem}.pdf"
        it = by_path.get(rel)
        if it is None or it["state"] not in ("current", "stale"):
            continue
        tmp = work / f"check-{path.stem}.pdf"
        try:
            renderer.export(str(path), str(tmp), "pdf")
            _lighten(tmp, load_config(project).exports.get("picture_dpi", PICTURE_DPI))
        except Exception as exc:                    # noqa: BLE001
            it.update(state="error", level="error", detail=f"{path.name} doesn't draw: {type(exc).__name__}: {exc}")
            continue
        if picture_digest(tmp) == lock["exports"][rel].get("picture"):
            it.update(state="current", level="ok", detail="drawn again: looks the same")
        else:
            e = lock["exports"][rel]
            v = compare_files((project / rel).read_bytes(), tmp.read_bytes(), rel, None,
                              boxes=e.get("panels"), size_mm=e.get("size"))
            if v["verdict"] in (EXACT, WITHIN):
                it.update(state="current", level="ok", detail=f"drawn again: {v['detail']}")
            else:
                where = f" (panel{'s' if len(v.get('panels', [])) > 1 else ''} {', '.join(v['panels'])})" \
                    if v.get("panels") else ""
                it.update(state="stale", level="error",
                          detail=f"drawn now, it differs from the export: {v['detail']}{where}")
        tmp.unlink(missing_ok=True)


# --- reproduce ---

def _order(recipes: list[Recipe], project: Path) -> list[Recipe]:
    """Recipes in an order in which each runs after the recipes that make its inputs."""
    makes = {}
    for r in recipes:
        for o in r.paths(project, r.outputs):
            makes[str(o)] = r.name
    by = {r.name: r for r in recipes}
    done, order, busy = set(), [], set()

    def visit(r):
        if r.name in done:
            return
        if r.name in busy:
            raise ValueError(f"recipes make each other's inputs in a circle, through {r.name}")
        busy.add(r.name)
        for i in r.paths(project, r.inputs):
            dep = makes.get(str(i))
            if dep and dep != r.name:
                visit(by[dep])
        busy.discard(r.name)
        done.add(r.name)
        order.append(r)
    for r in recipes:
        visit(r)
    return order


def plan(project, only: list[str] | None = None) -> dict:
    """What `reproduce` would run: each recipe, in order, with its command as it runs here and what it needs."""
    project = Path(project).resolve()
    cfg = load_config(project)
    recipes = _order(cfg.recipes, project)
    if only:
        recipes = [r for r in recipes if r.name in only or any(Path(o).name in only for o in r.outputs)]
    out = []
    for r in recipes:
        env = cfg.envs.get(r.env) if r.env else None
        out.append({"recipe": r.name, "run": r.run, "cwd": str(r.base(project)), "env": r.env,
                    "command": _command(project, r, env, dry=True) if r.runnable() else None,
                    "manual": r.manual, "where": r.where, "resources": r.resources,
                    "fetch": [i.fetch for i in cfg.inputs if i.fetch and not (project / i.path).exists()
                              and _abs(project, i.path) in r.paths(project, r.inputs)]})
    return {"project": str(project), "recipes": out, "problems": cfg.problems}


def _mounts(project: Path, r: Recipe) -> list[str]:
    """Folders a container needs, each mounted at its own path: the recipe's folder and those of its inputs and
    outputs that are elsewhere."""
    dirs = {r.base(project)}
    for p in r.paths(project, r.inputs + r.outputs + r.data):
        dirs.add(p.parent)
    keep = []
    for d in sorted(dirs, key=lambda d: len(d.parts)):
        if not any(d == k or k in d.parents for k in keep):
            keep.append(d)
    return [str(d) for d in keep]


def _image_for(project: Path, env: Env, dry: bool) -> str:
    if env.image:
        return env.image
    df = (project / env.dockerfile).resolve()
    tag = f"fignimble-{_slug(project.name).lower()}-{_slug(env.name).lower()}:{sha256_bytes(df.read_bytes())[:12]}" \
        if df.exists() else f"fignimble-{_slug(env.name).lower()}"
    if dry:
        return tag
    ctx = (project / env.context).resolve() if env.context else df.parent
    have = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if have.returncode != 0:
        b = subprocess.run(["docker", "build", "-t", tag, "-f", str(df), str(ctx)], capture_output=True, text=True)
        if b.returncode != 0:
            raise RuntimeError(f"docker build failed:\n{b.stderr[-2000:]}")
    return tag


def _command(project: Path, r: Recipe, env: Env | None, dry: bool = False) -> list[str]:
    if env and (env.image or env.dockerfile):
        image = _image_for(project, env, dry)
        cmd = ["docker", "run", "--rm"]
        for m in _mounts(project, r):
            cmd += ["-v", f"{m}:{m}"]
        cmd += ["-w", str(r.base(project))]
        if sys.platform.startswith("linux") and hasattr(os, "getuid"):
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        if r.seed is not None:
            cmd += ["-e", f"FIGNIMBLE_SEED={r.seed}"]
        return cmd + [image, "sh", "-c", r.run]
    if env and env.command:
        return shlex.split(env.command) + ["sh", "-c", r.run]
    return ["sh", "-c", r.run]


def _have_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


def fetch_input(project: Path, inp: Input) -> str | None:
    """Download an input to its path and check its SHA-256; None when done, else what went wrong."""
    import urllib.request
    dest = project / inp.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.part")
    h = hashlib.sha256()
    try:
        req = urllib.request.Request(inp.fetch, headers={"User-Agent": "fignimble"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            for chunk in iter(lambda: resp.read(1 << 20), b""):
                f.write(chunk)
                h.update(chunk)
    except Exception as exc:                        # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return f"couldn't download {inp.fetch}: {exc}"
    if inp.sha256 and h.hexdigest() != inp.sha256.lower():
        tmp.unlink(missing_ok=True)
        return f"{inp.fetch} doesn't have the SHA-256 fignimble.toml gives"
    os.replace(tmp, dest)
    return None


def reproduce(project, check_only: bool = True, budget: str | None = None, only: list[str] | None = None,
              fetch: bool = True, log=None) -> dict:
    """Run recipes again, in order, and compare what they make with what the project holds.

    check_only: put the project's files back afterwards (what was made is kept in .fignimble/reproduce/); else keep
    what was made, record it in the lock as `made`, and bring exports/ up to date.
    budget: how long all of it may take ("1h"); a recipe that says it takes longer than what is left isn't run.

    -> {results: [{recipe, verdict, detail, outputs: [{path, verdict, detail}], seconds}], summary, ok, kept}"""
    project = Path(project).resolve()
    cfg = load_config(project)
    log = log or (lambda *_: None)
    if cfg.problems:
        return {"project": str(project), "results": [], "problems": cfg.problems, "summary": {}, "ok": False}
    hasher = Hasher(project / ".fignimble")
    lock = read_lock(project)
    left = parse_duration(budget) if budget else None
    started = time.time()
    run_dir = project / ".fignimble" / "reproduce" / time.strftime("%Y%m%d-%H%M%S")
    before_dir, after_dir = run_dir / "before", run_dir / "after"
    recipes = _order(cfg.recipes, project)
    if only:
        recipes = [r for r in recipes if r.name in only or any(Path(o).name in only for o in r.outputs)]
    inputs_by_path = {str(_abs(project, i.path)): i for i in cfg.inputs}
    saved: dict[Path, Path | None] = {}            # file -> its copy from before the run (None: didn't exist)
    results = []
    failed_outputs: set[str] = set()

    def keep_before(p: Path):
        if p in saved:
            return
        if p.exists():
            dest = before_dir / str(len(saved))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            saved[p] = dest
        else:
            saved[p] = None

    try:
        for r in recipes:
            res = {"recipe": r.name, "outputs": [], "seconds": 0.0}
            results.append(res)
            env = cfg.envs.get(r.env) if r.env else None
            if r.manual:
                res.update(verdict="not-run", why="manual", detail=r.manual)
                continue
            if r.where:
                res.update(verdict="not-run", why="resources", detail=f"runs on {r.where}, not here")
                continue
            if not r.run:
                res.update(verdict="not-run", why="manual", detail="no command")
                continue
            ins = r.paths(project, r.inputs)
            missing = []
            for i in ins:
                if i.exists():
                    continue
                inp = inputs_by_path.get(str(i))
                if str(i) in failed_outputs:
                    missing.append(f"{_rel(project, i)} (its recipe failed)")
                elif inp and inp.fetch and fetch:
                    log(f"fetching {inp.path} from {inp.fetch}")
                    err = fetch_input(project, inp)
                    if err:
                        missing.append(err)
                elif inp and inp.access and inp.access != "public":
                    res.update(verdict="not-run", why="restricted", detail=f"{inp.path}: {inp.access}")
                    break
                else:
                    missing.append(_rel(project, i))
            if res.get("verdict"):
                failed_outputs.update(str(o) for o in r.paths(project, r.outputs))
                continue
            if missing:
                res.update(verdict="not-run", why="missing-data", detail="missing: " + "; ".join(missing))
                failed_outputs.update(str(o) for o in r.paths(project, r.outputs))
                continue
            need = parse_duration(r.resources.get("time"))
            if left is not None:
                spent = time.time() - started
                if need is not None and need > left - spent:
                    res.update(verdict="not-run", why="resources",
                               detail=f"says it takes {r.resources.get('time')}; {max(0, left - spent):.0f} s left")
                    failed_outputs.update(str(o) for o in r.paths(project, r.outputs))
                    continue
            if r.resources.get("gpu") and not _have_gpu():
                res.update(verdict="not-run", why="resources", detail="needs a GPU; none here")
                failed_outputs.update(str(o) for o in r.paths(project, r.outputs))
                continue
            if env and (env.image or env.dockerfile) and not shutil.which("docker"):
                res.update(verdict="not-run", why="environment", detail=f"env {env.name} needs Docker; it isn't "
                           "installed")
                failed_outputs.update(str(o) for o in r.paths(project, r.outputs))
                continue
            outs = r.paths(project, r.outputs)
            datas = r.paths(project, r.data)
            for p in outs + datas:
                keep_before(p)
            old = {p: (saved[p].read_bytes() if saved.get(p) else None) for p in outs + datas}
            try:
                cmd = _command(project, r, env)
            except Exception as exc:                # noqa: BLE001
                res.update(verdict="not-run", why="environment", detail=str(exc))
                failed_outputs.update(str(o) for o in outs)
                continue
            log(f"running {r.name}: {r.run}")
            timeout = None
            if left is not None:
                timeout = max(1.0, left - (time.time() - started))
            t0 = time.time()
            envvars = dict(os.environ)
            if r.seed is not None:
                envvars["FIGNIMBLE_SEED"] = str(r.seed)
            try:
                proc = subprocess.run(cmd, cwd=r.base(project), capture_output=True, text=True, timeout=timeout,
                                      env=envvars)
            except subprocess.TimeoutExpired:
                res.update(verdict="not-run", why="resources", detail="ran out of the time budget",
                           seconds=round(time.time() - t0, 1))
                failed_outputs.update(str(o) for o in outs)
                continue
            res["seconds"] = round(time.time() - t0, 1)
            log_file = run_dir / f"{_slug(r.name)}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            log_file.write_text(f"$ {shlex.join(cmd)}\n\n{proc.stdout}\n--- stderr ---\n{proc.stderr}",
                                encoding="utf-8")
            res["log"] = str(log_file)
            if proc.returncode != 0:
                res.update(verdict="failed", detail=f"exit status {proc.returncode}: "
                           + (proc.stderr.strip().splitlines() or ["(no output)"])[-1][:300])
                failed_outputs.update(str(o) for o in outs)
                continue
            tol = r.tolerance()
            data_v = []
            for p in datas:
                new = p.read_bytes() if p.exists() else None
                v = compare_files(old[p], new, p.name, tol)
                data_v.append({"path": _rel(project, p), **v})
                if new is not None:
                    _copy_after(after_dir, project, p)
            for p in outs:
                new = p.read_bytes() if p.exists() else None
                hl = after_dir / "differences" / f"{_slug(_rel(project, p))}.png"
                v = compare_files(old[p], new, p.name, tol, highlight=hl)
                res["outputs"].append({"path": _rel(project, p), **v})
                if new is not None:
                    _copy_after(after_dir, project, p)
            res["data"] = data_v
            res.update(_recipe_verdict(res["outputs"], data_v, r))
            if not check_only:
                head = git_head(project)
                for p in outs:
                    if p.exists():
                        lock["outputs"][_rel(project, p)] = {
                            "recipe": r.name, "sha256": hasher(p), "how": "made", "at": _now(),
                            "recipe_hash": r.digest(env), "seconds": res["seconds"],
                            "inputs": {_rel(project, i): hasher(i) for i in ins},
                            "data": {_rel(project, d): hasher(d) for d in datas},
                            **({"env": env_record(project, env, hasher)} if env else {}),
                            **({"commit": head} if head else {}),
                            **({"figures": lock["outputs"].get(_rel(project, p), {}).get("figures")}
                               if lock["outputs"].get(_rel(project, p), {}).get("figures") else {})}
    finally:
        if check_only:
            for p, copy in saved.items():
                if copy is None:
                    p.unlink(missing_ok=True)
                else:
                    shutil.copy2(copy, p)
    if not check_only:
        write_lock(project, lock)
    hasher.save()
    summary: dict[str, int] = {}
    for res in results:
        k = res["verdict"] + (f": {res['why']}" if res.get("why") else "")
        summary[k] = summary.get(k, 0) + 1
    ok = all(res["verdict"] in (EXACT, WITHIN) for res in results)
    return {"project": str(project), "results": results, "summary": summary, "ok": ok, "problems": [],
            "kept": str(after_dir) if after_dir.exists() else None, "check_only": check_only}


def _copy_after(after_dir: Path, project: Path, p: Path):
    dest = after_dir / _rel(project, p).replace("../", "__/")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(p, dest)


def _recipe_verdict(outputs: list, data: list, r: Recipe) -> dict:
    """A recipe's verdict from its outputs' and data's: data decide when there are data (pictures drawn by another
    version of a plotting library differ in pixels while showing the same numbers)."""
    rank = {EXACT: 0, WITHIN: 1, "added": 2, DIFFERS: 3, "removed": 3, "missing": 3}

    def worst(vs):
        return max((v["verdict"] for v in vs), key=lambda v: rank.get(v, 3), default=EXACT)
    if data:
        w = worst(data)
        pic = worst(outputs)
        detail = "; ".join(f"{v['path']}: {v['detail']}" for v in data)
        if w in (EXACT, WITHIN) and pic not in (EXACT, WITHIN):
            detail += " (the pictures differ in pixels; the numbers they show match)"
        if w == "added":
            return {"verdict": "differs", "detail": "the data weren't there before: " + detail}
        return {"verdict": w if w in (EXACT, WITHIN) else DIFFERS, "detail": detail}
    w = worst(outputs)
    detail = "; ".join(f"{v['path']}: {v['detail']}" for v in outputs)
    if w == "added":
        return {"verdict": DIFFERS, "detail": "nothing to compare with: " + detail}
    if w == DIFFERS and not r.deterministic:
        detail += " (the recipe says it isn't deterministic: give it `data` to compare numbers instead)"
    return {"verdict": w if w in (EXACT, WITHIN) else DIFFERS, "detail": detail}


# --- git: tags and versions ---

class GitError(RuntimeError):
    pass


def _git(project: Path, *args, check: bool = True, binary: bool = False):
    try:
        r = subprocess.run(["git", *args], cwd=project, capture_output=True, text=not binary, timeout=60)
    except FileNotFoundError:
        raise GitError("git isn't installed") from None
    if check and r.returncode != 0:
        err = r.stderr.decode(errors="replace") if binary else r.stderr
        raise GitError(err.strip() or f"git {' '.join(args)} failed")
    return r


def git_root(project: Path) -> Path:
    r = _git(project, "rev-parse", "--show-toplevel", check=False)
    if r.returncode != 0:
        raise GitError(f"{project} isn't in a git repository: versions are git tags (git init, and commit the "
                       "project, first)")
    return Path(r.stdout.strip()).resolve()


def _tag_ref(name: str) -> str:
    return name if name.startswith(TAG_PREFIX) else TAG_PREFIX + name


def tags(project) -> list[dict]:
    """The project's versions, newest first: [{name, ref, commit, date, message}]."""
    project = Path(project).resolve()
    git_root(project)
    r = _git(project, "for-each-ref", "--sort=-creatordate", "--format=%(refname:short)%09%(objectname)%09"
             "%(*objectname)%09%(creatordate:iso-strict)%09%(contents:subject)", f"refs/tags/{TAG_PREFIX}")
    out = []
    for ln in r.stdout.splitlines():
        ref, obj, peeled, date, subj = (ln.split("\t") + [""] * 5)[:5]
        out.append({"name": ref[len(TAG_PREFIX):], "ref": ref, "commit": peeled or obj, "date": date,
                    "message": subj})
    return out


def tag(project, name: str, message: str | None = None, force: bool = False, renderer=None, log=None) -> dict:
    """Name this version of the project: record exports/ and the lock, check them, commit them, and tag the commit
    `fignimble/NAME`. Refuses when the check finds a problem, or when files of the project other than exports/ and
    the lock aren't committed (the tag must name the code the exports were made from), unless `force`."""
    project = Path(project).resolve()
    log = log or (lambda *_: None)
    root = git_root(project)
    if not re.fullmatch(r"[\w.-]+(/[\w.-]+)*", name) or ".." in name:
        raise GitError(f"{name!r} can't be a tag name: use letters, digits, - _ . and /")
    ref = _tag_ref(name)
    if _git(project, "rev-parse", "-q", "--verify", f"refs/tags/{ref}", check=False).returncode == 0:
        raise GitError(f"there is already a version called {name}")
    rel = os.path.relpath(project, root)
    pathspec = "." if rel == "." else rel
    if renderer is not None:
        log("recording exports and the lock")
        record(renderer)
    st = _git(project, "status", "--porcelain", "--untracked-files=all", "--", ".").stdout.splitlines()
    others = [ln[3:] for ln in st if not _ours(ln[3:], rel)]
    others = [o for o in others if not Path(o).name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md")]
    if others and not force:
        raise GitError("commit the project first; these files aren't committed: " + ", ".join(others[:8])
                       + (" ..." if len(others) > 8 else "") + " (or tag anyway with --force)")
    c = check(project, renderer)
    bad = [it for it in c["items"] if it["level"] == "error"] + [{"path": "fignimble.toml", "detail": p}
                                                                 for p in c["problems"]]
    if bad and not force:
        raise GitError("the check found problems:\n" + "\n".join(f"  {b['path']}: {b['detail']}" for b in bad[:12])
                       + "\n(fix them, or tag anyway with --force)")
    paths = [p for p in (f"{pathspec}/{EXPORTS}" if pathspec != "." else EXPORTS,
                         f"{pathspec}/{LOCK}" if pathspec != "." else LOCK)]
    tracked = set(_git(root, "ls-files", "--", *paths).stdout.split())
    paths = [p for p in paths if (root / p).exists() or any(t == p or t.startswith(p + "/") for t in tracked)]
    if paths:
        _git(root, "add", "-A", "--", *paths)
    staged = _git(root, "diff", "--cached", "--name-only", "--", *paths).stdout.split() if paths else []
    committed = None
    if staged:
        _git(root, "commit", "-m", f"Fignimble: exports and lock for version {name}", "--", *paths)
        committed = _git(root, "rev-parse", "HEAD").stdout.strip()
    msg = message or f"Fignimble version {name}"
    body = json.dumps({"project": rel, "summary": c["summary"], "forced": bool(bad or others) and force})
    _git(root, "tag", "-a", ref, "-m", msg, "-m", body)
    commit = _git(root, "rev-parse", "HEAD").stdout.strip()
    return {"tag": ref, "name": name, "commit": commit, "committed": committed, "problems": bad if force else [],
            "uncommitted": others if force else [], "push": f"git push origin {ref}"}


def _ours(path: str, rel: str) -> bool:
    """Whether a path git status gives (relative to the repository) is exports/ or the lock of the project."""
    p = path.strip('"')
    pre = "" if rel == "." else rel.rstrip("/") + "/"
    return p == pre + LOCK or p.startswith(pre + EXPORTS + "/") or p == pre + EXPORTS


class Version:
    """A version of the project's files to compare: a git ref, or the files as they are (ref None)."""

    def __init__(self, project: Path, ref: str | None):
        self.project, self.ref = project, ref
        if ref is not None:
            root = git_root(project)
            self.prefix = "" if root == project else Path(os.path.relpath(project, root)).as_posix() + "/"
            self.root = root
            if _git(project, "rev-parse", "-q", "--verify", f"{ref}^{{commit}}", check=False).returncode != 0:
                raise GitError(f"no version or commit called {ref}")

    @property
    def label(self) -> str:
        if self.ref is None:
            return "the project now"
        return self.ref[len(TAG_PREFIX):] if self.ref.startswith(TAG_PREFIX) else self.ref

    def read(self, rel: str) -> bytes | None:
        if self.ref is None:
            p = self.project / rel
            try:
                return p.read_bytes() if p.is_file() else None
            except OSError:
                return None
        r = _git(self.root, "show", f"{self.ref}:{self.prefix}{rel}", check=False, binary=True)
        return r.stdout if r.returncode == 0 else None

    def exports(self) -> list[str]:
        if self.ref is None:
            d = self.project / EXPORTS
            return sorted(p.relative_to(self.project).as_posix() for p in d.rglob("*") if p.is_file()) \
                if d.is_dir() else []
        r = _git(self.root, "ls-tree", "-r", "--name-only", self.ref, "--", f"{self.prefix}{EXPORTS}", check=False)
        return sorted(ln[len(self.prefix):] for ln in r.stdout.splitlines() if ln)

    def lock(self) -> dict:
        b = self.read(LOCK)
        try:
            got = json.loads(b) if b else {}
        except ValueError:
            got = {}
        got.setdefault("exports", {})
        got.setdefault("outputs", {})
        return got

    def config(self) -> dict:
        b = self.read("fignimble.toml")
        try:
            return _toml(b.decode("utf-8")) if b else {}
        except Exception:                           # noqa: BLE001
            return {}


def _resolve_ref(project: Path, name: str) -> str:
    """A version name (a tag without its prefix), a full tag, or any git ref."""
    t = _tag_ref(name)
    if _git(project, "rev-parse", "-q", "--verify", f"refs/tags/{t}", check=False).returncode == 0:
        return t
    return name


def diff(project, a: str | None = None, b: str | None = None, renderer=None, out_dir: Path | None = None) -> dict:
    """Compare two versions: `a` (default: the newest version) with `b` (default: the project now, after recording
    its exports when a renderer is given).

    -> {a, b, how: [...] (recipes, environments and inputs that changed), shown: [...] (tables and panels' values),
    looks: [...] (figures, with the panels whose pixels changed and a picture of the differences), summary, same}"""
    project = Path(project).resolve()
    git_root(project)
    if a is None:
        ts = tags(project)
        if not ts:
            raise GitError("the project has no versions yet: make one with `fignimble tag NAME`")
        a = ts[0]["ref"]
    va = Version(project, _resolve_ref(project, a))
    if b is None and renderer is not None:
        record(renderer)
    vb = Version(project, _resolve_ref(project, b) if b else None)
    out_dir = out_dir or project / ".fignimble" / "diff" / f"{_slug(va.label)}..{_slug(vb.label)}"
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    la, lb = va.lock(), vb.lock()
    # 1. how things are made
    how = []
    ca, cb = va.config(), vb.config()
    ra = {str(r.get("name") or default_name((r.get("outputs") or ["?"])[0])): r for r in ca.get("recipe") or []}
    rb = {str(r.get("name") or default_name((r.get("outputs") or ["?"])[0])): r for r in cb.get("recipe") or []}
    for n in sorted(set(ra) | set(rb)):
        if n not in ra:
            how.append({"what": "recipe", "name": n, "change": "added"})
        elif n not in rb:
            how.append({"what": "recipe", "name": n, "change": "removed"})
        elif ra[n] != rb[n]:
            keys = sorted(k for k in set(ra[n]) | set(rb[n]) if ra[n].get(k) != rb[n].get(k))
            how.append({"what": "recipe", "name": n, "change": "changed", "fields": keys,
                        "detail": "; ".join(f"{k}: {ra[n].get(k)!r} -> {rb[n].get(k)!r}" for k in keys)})
    ea, eb = ca.get("env") or {}, cb.get("env") or {}
    for n in sorted(set(ea) | set(eb)):
        if ea.get(n) != eb.get(n):
            how.append({"what": "env", "name": n, "change": "added" if n not in ea else
                        ("removed" if n not in eb else "changed"),
                        "detail": f"{ea.get(n)!r} -> {eb.get(n)!r}"})
    srcs_a, srcs_b = _all_sources(la), _all_sources(lb)
    for f in sorted(set(srcs_a) | set(srcs_b)):
        if srcs_a.get(f) != srcs_b.get(f):
            how.append({"what": "file", "name": f, "change": "added" if f not in srcs_a else
                        ("removed" if f not in srcs_b else "changed")})
    # 2. what's shown, and 3. how it looks
    shown, looks = [], []
    for rel in sorted(set(va.exports()) | set(vb.exports())):
        ba, bb = va.read(rel), vb.read(rel)
        e = lb["exports"].get(rel) or la["exports"].get(rel) or {}
        if e.get("kind") == "panel-data":          # made from what its figure was made from
            fig_pdf = f"{EXPORTS}/{Path(e.get('figure', '')).stem}.pdf"
            why = _why(la["exports"].get(fig_pdf, {}), lb["exports"].get(fig_pdf, {}))
        else:
            why = _why(la["exports"].get(rel, {}), lb["exports"].get(rel, {}))
        if rel.endswith(".pdf"):
            v = compare_files(ba, bb, rel, None, highlight=out_dir / f"{Path(rel).stem}.png",
                              boxes=e.get("panels"), size_mm=e.get("size"))
            looks.append({"path": rel, "figure": e.get("figure"), **v, **({"because": why} if why else {})})
        else:
            v = compare_files(ba, bb, rel)
            shown.append({"path": rel, "figure": e.get("figure"), "panel": e.get("panel"), **v,
                          **({"because": why} if why else {})})
    summary: dict[str, int] = {}
    for it in shown + looks:
        summary[it["verdict"]] = summary.get(it["verdict"], 0) + 1
    same = all(it["verdict"] in (EXACT, WITHIN) for it in shown + looks)
    return {"project": str(project), "a": va.label, "b": vb.label, "a_ref": va.ref, "b_ref": vb.ref,
            "how": how, "shown": shown, "looks": looks, "summary": summary, "same": same,
            "differences": str(out_dir) if out_dir.exists() else None}


def _all_sources(lock: dict) -> dict:
    out = {}
    for e in lock.get("exports", {}).values():
        out.update(e.get("sources") or {})
    for rel, e in lock.get("outputs", {}).items():
        out[rel] = e.get("sha256")
        out.update(e.get("inputs") or {})
    return out


def _why(ea: dict, eb: dict) -> list[str]:
    sa, sb = ea.get("sources") or {}, eb.get("sources") or {}
    return sorted(f for f in set(sa) | set(sb) if sa.get(f) != sb.get(f))
