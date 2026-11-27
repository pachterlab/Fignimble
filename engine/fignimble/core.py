"""The engine behind the editor: renders figures in a worker process, turns canvas edits into source edits, keeps
the undo history, and watches the project for changes made by an agent or another editor.

The undo model is Manimble's (github.com/pachterlab/Manimble, engine/manimble/core.py): every edit stores each file
before and after; undoing an edit when the file has changed since (an agent wrote to it) puts back only that edit's
own lines, by a three-way merge, and refuses when the same lines changed.
"""

from __future__ import annotations

import ast
import difflib
import re
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import remote, srcedit, tables, templates
from .render import SKIP_DIRS, is_figure_source, is_table_source, outside_stats, project_walk
from .srcedit import REMOVE, EditError, Raw, Source

ENGINE_ROOT = str(Path(__file__).resolve().parent.parent)
GEOMETRY = {"panel": ("x", "y"), "image": ("x", "y"), "text": ("x", "y"), "rect": ("x", "y"),
            "ellipse": ("x", "y"), "line": ("x1", "y1", "x2", "y2"), "bracket": ("x1", "x2", "y"),
            "layout": ("x", "y"), "legend": ("x", "y"), "figure": ("x", "y"), "qr": ("x", "y"), "table": ("x", "y")}
METHOD_KIND = {"panel": "panel", "image": "image", "text": "text", "line": "line", "arrow": "line", "rect": "rect",
               "ellipse": "ellipse", "bracket": "bracket", "layout": "layout", "legend": "legend", "path": "path",
               "figure": "figure", "qr": "qr", "table": "table"}
BOXED = ("panel", "image", "rect", "ellipse", "layout")
TABLE_FILES = (".csv", ".tsv")
# what can be done to a Table's page (its title, cells and notes lay themselves out)
TABLE_OPS = {"set", "move", "resize", "delete", "table_rows", "fit_figure", "style", "paste_style", "labels"}
# a Table's parts: which of the Table(...) call's arguments each one's settings are
TABLE_ARGS = {"title": {"text": "title"}, "notes": {"text": "notes"},
              "cells": {k: k for k in ("rows", "w", "header", "decimals", "align", "grid", "size", "font", "color",
                                       "title", "notes")}}
INSET_KEYS = {"x", "y", "w", "h", "label", "group", "name"}


RECORD_AFTER = 3.0                 # seconds the project must stay unchanged before exports/ and the lock are written


def log(*a):
    print("[fignimble]", *a, file=sys.stderr, flush=True)


class WorkerProc:
    """The render worker process (fignimble.worker): one request at a time, restarted if it dies or hangs."""

    def __init__(self, python: str):
        self.python = python
        self.proc: subprocess.Popen | None = None
        self.lines: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.n = 0

    def _start(self):
        env = dict(os.environ, PYTHONPATH=ENGINE_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""),
                   PYTHONUNBUFFERED="1", MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([self.python, "-u", "-m", "fignimble.worker"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=sys.stderr, env=env, text=True, bufsize=1)
        self.lines = queue.Queue()
        q, out = self.lines, self.proc.stdout

        def pump():
            for ln in out:
                q.put(ln)
            q.put(None)
        threading.Thread(target=pump, daemon=True).start()

    def request(self, req: dict, timeout: float = 180) -> dict:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.n += 1
            req = dict(req, id=self.n)
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.proc = None
                raise RuntimeError("the render worker stopped; try again") from None
            deadline = time.time() + timeout
            while True:
                try:
                    ln = self.lines.get(timeout=max(0.05, deadline - time.time()))
                except queue.Empty:
                    self.kill()
                    raise RuntimeError(f"rendering took longer than {timeout:.0f} s and was stopped "
                                       "(a panel's code may loop forever)") from None
                if ln is None:
                    self.proc = None
                    raise RuntimeError("the render worker crashed (see ~/Library/Logs/fignimble.log)")
                msg = json.loads(ln)
                if msg.get("id") != req["id"]:
                    continue
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def kill(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except Exception:                            # noqa: BLE001
                self.proc.kill()


class UndoConflict(Exception):
    """Undo or redo would overwrite lines changed since (by an agent, another editor): the files it names."""


def _merge3(base: bytes, ours: bytes, theirs: bytes) -> bytes | None:
    """`ours` (the file as it is now) with the changes that take `base` to `theirs` (what undo or redo wants)
    applied line by line; None when a change meets lines that changed since `base`. (From Manimble.)"""
    b, o, t = (x.decode("utf-8").splitlines(keepends=True) for x in (base, ours, theirs))
    ops = difflib.SequenceMatcher(None, b, o, autojunk=False).get_opcodes()
    mine = [op for op in ops if op[0] != "equal"]
    equal = [op for op in ops if op[0] == "equal"]

    def where(i: int) -> int | None:
        for _, i1, i2, j1, _ in equal:
            if i1 <= i < i2:
                return j1 + i - i1
        return len(o) if i == len(b) and not any(i1 == len(b) for _, i1, _, _, _ in mine) else None

    out, at = [], 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, t, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if any(m1 < max(i2, i1 + 1) and i1 < max(m2, m1 + 1) for _, m1, m2, _, _ in mine):
            return None
        start = where(i1)
        if start is None or start < at:
            return None
        if any(where(i) != start + i - i1 for i in range(i1, i2)):
            return None
        out += o[at:start] + t[j1:j2]
        at = start + (i2 - i1)
    out += o[at:]
    return "".join(out).encode("utf-8")


class Plan:
    """Patches to several files, made from one parse of each, applied together as one undo step."""

    def __init__(self):
        self.sources: dict[str, Source] = {}
        self.patches: dict[str, list] = {}
        self.inserted: dict[str, list[int]] = {}       # file -> lines (after the edit) where new statements start
        self.new_files: list[Path] = []                 # files made for this edit (a copied picture): undone with it
        self.raw: dict[str, tuple[str, str]] = {}       # files that aren't Python (a table's CSV): (before, after)

    def source(self, path: str) -> Source:
        if path not in self.sources:
            self.sources[path] = Source(Path(path).read_text(encoding="utf-8"), path)
        return self.sources[path]

    def add(self, path: str, patches: list):
        self.source(path)
        lst = self.patches.setdefault(path, [])
        for p in patches:
            lst[:] = [q for q in lst if (q[0], q[1]) != (p[0], p[1]) or q[0] == q[1]]
            lst.append(p)

    def texts(self) -> dict[str, tuple[str, str]]:
        out = {}
        for path, patches in self.patches.items():
            src = self.sources[path]
            new = srcedit.apply(src.text, patches)
            if new != src.text:
                try:
                    ast.parse(new)
                except SyntaxError as exc:
                    raise EditError(f"the edit would break {Path(path).name} (line {exc.lineno}: {exc.msg}); "
                                    "nothing was changed") from None
                out[path] = (src.text, new)
        for path, (before, after) in self.raw.items():
            if before != after:
                out[path] = (before, after)
        return out


class Session:
    def __init__(self, project: str, python: str | None = None):
        self.project = Path(project).resolve()
        if not self.project.is_dir():
            raise FileNotFoundError(f"no folder {self.project}")
        self.cache = self.project / ".fignimble"
        self.cache.mkdir(exist_ok=True)
        self.worker = WorkerProc(python or sys.executable)
        self.history: list[dict] = []
        self.redo: list[dict] = []
        self.renders: dict[str, tuple] = {}            # figure -> (fingerprint, pxPerMM, result)
        self.render_lock = threading.Lock()
        self.edit_lock = threading.RLock()
        self.on_event = None
        self.closed = False
        self.current: str | None = None
        self.dark = False                              # the editor shows the figures' dark versions
        # files outside the project each figure read when last drawn (a picture an analysis writes, panel code kept
        # with it): watched with the project's own, so that when one changes the figure is drawn again
        self.outside: dict[str, list[str]] = {}
        self._sig = self.fingerprint()
        # exports/ and fignimble.lock (repro.py), brought up to date by a worker of their own a moment after the
        # project stops changing, so that the editor's renders never wait for them
        self.python = python or sys.executable
        self.recorder: WorkerProc | None = None
        self.record_due: float | None = time.time()      # when to record next (None: nothing to record)
        self.recording = False
        self.last_record: dict | None = None
        self.agent_files = templates.write_agent_files(self.project)
        threading.Thread(target=self._watch, daemon=True).start()

    def close(self):
        self.closed = True
        templates.remove_agent_files(self.project, self.agent_files)
        self.worker.close()
        if self.recorder:
            self.recorder.close()

    def emit(self, ev: dict):
        if self.on_event:
            self.on_event(ev)

    # --- files ---

    def fingerprint(self) -> str:
        items = []
        for dirpath, dirs, files in project_walk(self.project):
            for f in files:
                if f in templates.AGENT_FILES:
                    continue
                try:
                    st = os.stat(os.path.join(dirpath, f))
                except OSError:
                    continue
                items.append((dirpath, f, st.st_mtime_ns, st.st_size))
        items.sort()
        items += outside_stats(sorted({f for fs in self.outside.values() for f in fs}))
        return str(hash(tuple(items)))

    def figures(self) -> list[dict]:
        out = []
        for p in sorted(self.project.glob("*.py"), key=lambda p: _natural(p.stem)):
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if is_figure_source(text):
                out.append({"name": p.stem, "file": str(p), "kind": "table" if is_table_source(text) else "figure"})
        return out

    def figure_file(self, name: str) -> str:
        p = self.project / f"{name}.py"
        if not p.exists():
            raise EditError(f"there is no figure {name}")
        return str(p)

    def panel_files(self) -> list[str]:
        out = []
        for p in sorted(self.project.rglob("*.py")):
            rel = p.relative_to(self.project)
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not is_figure_source(t) and "def " in t and p.name != "style.py":
                out.append(str(rel))
        return out

    def _watch(self):
        """Notice edits made outside the editor (an agent, a text editor) and re-render."""
        last_bg = 0.0
        while not self.closed:
            time.sleep(0.8)
            try:
                sig = self.fingerprint()
            except Exception:                            # noqa: BLE001
                continue
            if sig != self._sig:
                self._sig = sig
                self.record_due = time.time() + RECORD_AFTER
                self.emit({"event": "changed"})
            elif self.record_due is not None and time.time() >= self.record_due and not self.recording:
                self.record_due = None
                if self.records():
                    self.recording = True
                    threading.Thread(target=self._record, daemon=True).start()
            elif time.time() - last_bg > 1.5:
                last_bg = time.time()
                self._render_background()

    # --- exports/ and the lock ---

    def records(self) -> bool:
        """Whether this project keeps exports/ and a lock up to date: a marked project (fignimble.toml) that
        doesn't turn it off ([exports] on_save = false)."""
        if not (self.project / templates.MARKER).is_file():
            return False
        from . import repro
        try:
            return bool(repro.load_config(self.project).exports.get("on_save", True))
        except Exception:                                # noqa: BLE001
            return True

    def _record(self):
        try:
            if self.recorder is None:
                self.recorder = WorkerProc(self.python)
            res = self.recorder.request({"cmd": "record", "project": str(self.project), "cache": str(self.cache)},
                                        timeout=1800)
            self.last_record = {**res, "at": time.time()}
            if res.get("written") or res.get("removed") or res.get("errors"):
                log("recorded", len(res.get("written", [])), "written,", len(res.get("removed", [])), "removed",
                    ("; errors: " + ", ".join(res["errors"])) if res.get("errors") else "")
            self.emit({"event": "recorded", "written": res.get("written", []), "removed": res.get("removed", []),
                       "errors": res.get("errors", {})})
        except Exception as exc:                          # noqa: BLE001
            log("record", exc)
            self.last_record = {"error": str(exc), "at": time.time()}
        finally:
            self.recording = False

    def record_now(self, a: dict | None = None) -> dict:
        """Bring exports/ and the lock up to date now (File ▸ Save, a tag): the record's report."""
        if self.recorder is None:
            self.recorder = WorkerProc(self.python)
        req = {"cmd": "record", "project": str(self.project), "cache": str(self.cache),
               "force": bool((a or {}).get("force"))}
        res = self.recorder.request(req, timeout=1800)
        self.last_record = {**res, "at": time.time()}
        return res

    def repro_check(self, a: dict | None = None) -> dict:
        """The project against its lock (repro.check), each figure drawn again by the recorder's worker."""
        if self.recorder is None:
            self.recorder = WorkerProc(self.python)
        return self.recorder.request({"cmd": "repro_check", "project": str(self.project), "cache": str(self.cache)},
                                     timeout=1800)

    def tags(self, a: dict | None = None) -> dict:
        from . import repro
        try:
            return {"ok": True, "tags": repro.tags(self.project)}
        except repro.GitError as exc:
            return {"ok": False, "error": str(exc), "tags": []}

    def tag(self, a: dict) -> dict:
        """Name this version: {name, message?, force?} -> repro.tag's result, or {ok: false, error}."""
        if self.recorder is None:
            self.recorder = WorkerProc(self.python)
        try:
            res = self.recorder.request({"cmd": "tag", "project": str(self.project), "cache": str(self.cache),
                                         "name": a["name"], "message": a.get("message"),
                                         "force": bool(a.get("force"))}, timeout=1800)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc).removeprefix("GitError: ")}
        return {"ok": True, **res}

    def diff(self, a: dict) -> dict:
        """Compare versions: {a?, b?} -> repro.diff's result, or {ok: false, error}."""
        if self.recorder is None:
            self.recorder = WorkerProc(self.python)
        try:
            res = self.recorder.request({"cmd": "diff", "project": str(self.project), "cache": str(self.cache),
                                         "a": a.get("a"), "b": a.get("b")}, timeout=1800)
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc).removeprefix("GitError: ")}
        return {"ok": True, **res}

    def _render_background(self):
        """Keep the other figures' thumbnails current while nothing else happens."""
        for f in self.figures():
            if self.closed or f["name"] == self.current:
                continue
            got = self.renders.get(f["name"])
            if got and got[0] == self._sig:
                continue
            if self.render_lock.locked():
                return
            try:
                self.render(f["name"], (got[1] if got else 4.0))
                self.emit({"event": "rendered", "figure": f["name"]})
            except Exception as exc:                      # noqa: BLE001
                log("background render", f["name"], exc)
            return

    # --- rendering ---

    def render(self, name: str, px: float | None = None, force: bool = False, dark: bool | None = None) -> dict:
        """A figure's render, from the cache when nothing changed. `dark`: its dark version (default: the version
        the editor shows)."""
        px = float(px or 12.0)
        dark = self.dark if dark is None else bool(dark)
        with self.render_lock:
            sig = self.fingerprint()
            got = self.renders.get(name)
            if got and not force and got[0] == sig and got[1] == px and got[2].get("dark", False) == dark:
                return got[2]
            res = self.worker.request({"cmd": "render", "project": str(self.project), "cache": str(self.cache),
                                       "file": self.figure_file(name), "pxPerMM": px, "dark": dark})
            outside = list(res.get("outside") or []) if not res.get("error") else self.outside.get(name, [])
            if outside != self.outside.get(name, []):
                # the files watched outside the project changed: they join the signature, without that change
                # itself counting as an edit (unless something else changed while the figure was drawn)
                if outside:
                    self.outside[name] = outside
                else:
                    self.outside.pop(name, None)
                new = self.fingerprint()
                if self._sig == sig:
                    self._sig = new
                sig = new
            self.renders[name] = (sig, px, res)
            return res

    def restart_renderer(self):
        """After packages were installed: a fresh worker that can import them, and every figure rendered again."""
        with self.render_lock:
            with self.worker.lock:
                self.worker.kill()
            self.renders.clear()

    def api_state(self) -> dict:
        figs = []
        for f in self.figures():
            got = self.renders.get(f["name"])
            r = got[2] if got else {}
            figs.append({**f, "thumb": r.get("thumb"), "error": r.get("error"),
                         "warnings": len([w for w in r.get("warnings", []) if w["level"] != "info"])})
        return {"project": str(self.project), "figures": figs, "canUndo": bool(self.history),
                "canRedo": bool(self.redo), "undoLabel": self.history[-1]["label"] if self.history else None,
                "redoLabel": self.redo[-1]["label"] if self.redo else None, "panels": self.panel_files(),
                "history": [e["label"] for e in self.history], "future": [e["label"] for e in reversed(self.redo)],
                "agentFiles": self.agent_files}

    def api_figure(self, a: dict) -> dict:
        self.current = a["name"]
        if "dark" in a:
            self.dark = bool(a["dark"])
        return self.render(a["name"], a.get("pxPerMM"))

    # --- undo ---

    def _write(self, texts: dict[str, tuple[str, str]], label: str):
        written = {}
        for path, (before, after) in texts.items():
            Path(path).write_text(after, encoding="utf-8")
            written[path] = (before.encode(), after.encode())
        if written:
            self.history.append({"label": label, "files": written})
            self.redo.clear()
            self._sig = self.fingerprint()

    def _restore(self, written: dict, which: int) -> dict:
        plan, clash = {}, []
        for path, pair in written.items():
            target, expected = pair[which], pair[1 - which]
            try:
                current = Path(path).read_bytes()
            except FileNotFoundError:
                current = None
            if current in (expected, target):
                plan[path] = target
                continue
            merged = None
            if None not in (target, expected, current):
                try:
                    merged = _merge3(expected, current, target)
                    if merged is not None and path.endswith(".py"):
                        ast.parse(merged)
                except (UnicodeDecodeError, SyntaxError, ValueError):
                    merged = None
            if merged is None:
                clash.append(Path(path).name)
            else:
                plan[path] = merged
        if clash:
            raise UndoConflict(clash)
        for path, data in plan.items():
            if data is None:
                Path(path).unlink(missing_ok=True)
            else:
                Path(path).write_bytes(data)
        self._sig = self.fingerprint()
        return plan

    def undo(self, a: dict) -> dict:
        with self.edit_lock:
            if not self.history:
                return {"ok": False, "error": "nothing to undo"}
            e = self.history.pop()
            try:
                self._restore(e["files"], 0)
            except UndoConflict as exc:
                return {"ok": False, "error": f"Can't undo {e['label']}: {', '.join(exc.args[0])} changed since on the "
                                              "same lines (an agent or another editor), so it was left as it is."}
            self.redo.append(e)
            return {"ok": True, "label": e["label"], **self._after(a.get("figure"), a.get("pxPerMM"))}

    def undo_to(self, a: dict) -> dict:
        """Undo until `n` steps of history are left (redo, the other way)."""
        n = int(a["n"])
        last = {}
        while len(self.history) > n:
            last = self.undo({"figure": None})
            if not last.get("ok"):
                return {**last, **self._after(a.get("figure"), a.get("pxPerMM"))}
        while len(self.history) < n and self.redo:
            last = self.redo_({"figure": None})
            if not last.get("ok"):
                return {**last, **self._after(a.get("figure"), a.get("pxPerMM"))}
        return {"ok": True, **self._after(a.get("figure"), a.get("pxPerMM"))}

    def redo_(self, a: dict) -> dict:
        with self.edit_lock:
            if not self.redo:
                return {"ok": False, "error": "nothing to redo"}
            e = self.redo.pop()
            try:
                self._restore(e["files"], 1)
            except UndoConflict as exc:
                return {"ok": False, "error": f"Can't redo {e['label']}: {', '.join(exc.args[0])} changed since."}
            self.history.append(e)
            return {"ok": True, "label": e["label"], **self._after(a.get("figure"), a.get("pxPerMM"))}

    def _after(self, name: str | None, px=None, select_lines: dict | None = None) -> dict:
        if not name:
            return {}
        try:
            fig = self.render(name, px)
        except Exception as exc:                         # noqa: BLE001
            return {"renderError": str(exc)}
        out = {"figure": fig}
        if select_lines:
            sel = []
            for info in fig.get("elements", []):
                s = info.get("site") or {}
                if s.get("line") in select_lines.get(s.get("file"), []):
                    sel.append(info["oid"])
            out["select"] = sel
        return out

    # --- editing source directly ---

    def write_source(self, a: dict) -> dict:
        """The code editor saved a file."""
        path = str(Path(a["file"]).resolve())
        if not path.startswith(str(self.project) + os.sep):
            return {"ok": False, "error": "that file is not in the project"}
        with self.edit_lock:
            before = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
            if before != a["text"]:
                self._write({path: (before, a["text"])}, f"Edit {Path(path).name}")
            return {"ok": True, **self._after(a.get("figure"), a.get("pxPerMM"))}

    # --- canvas edits ---

    def edit(self, a: dict) -> dict:
        name = a["figure"]
        with self.edit_lock:
            data = self.render(name, a.get("pxPerMM"))
            if data.get("error"):
                return {"ok": False, "error": "the figure's code doesn't run; fix it first:\n" + data["error"]}
            plan = Plan()
            op = a["op"]
            try:
                if _is_table(data) and op not in TABLE_OPS:
                    raise EditError("a table's file holds its title, cells and notes, laid out for you; to draw on "
                                    "a table or put it with other things, place it in a figure (Insert ▸ Figure…)")
                label = getattr(self, f"_op_{op}")(plan, data, a)
                texts = plan.texts()
            except (EditError, KeyError, ValueError) as exc:
                return {"ok": False, "error": str(exc).strip("'\"")}
            made = [Path(p) for p in plan.new_files + list(a.get("_new_files") or [])]
            if not texts:
                return {"ok": True, "unchanged": True, "figure": data}
            self._write(texts, label)
            for p in made:                              # undo takes them away again, redo brings them back
                if p.exists():
                    self.history[-1]["files"][str(p)] = (None, p.read_bytes())
            # where the new statements are now, to select what they make
            lines = {}
            for path, ins in plan.inserted.items():
                lines[path] = ins
            if a.get("then"):
                # a follow-up edit that needs this one rendered first (the page fitted to a table just typed
                # into): one undo step with it
                n = len(self.history)
                more = self.edit({**a["then"], "figure": name, "pxPerMM": a.get("pxPerMM")})
                if more.get("ok") and len(self.history) == n + 1:
                    extra = self.history.pop()
                    for path, (before, after) in extra["files"].items():
                        old = self.history[-1]["files"].get(path)
                        self.history[-1]["files"][path] = (old[0] if old else before, after)
            res = {"ok": True, **self._after(name, a.get("pxPerMM"), lines or None)}
            fig = res.get("figure", {})
            if fig.get("error"):
                # the edit broke the figure (it shouldn't): take it back
                e = self.history.pop()
                self._restore(e["files"], 0)
                return {"ok": False, "error": "that edit made the figure fail, so it was undone:\n" + fig["error"],
                        **self._after(name, a.get("pxPerMM"))}
            return res

    # helpers

    @staticmethod
    def _info(data: dict, oid: str) -> dict:
        for i in data["elements"] + data["labels"]:
            if i["oid"] == str(oid):
                return i
        raise EditError(f"no element {oid} (the figure changed; try again)")

    @staticmethod
    def _who(data: dict, info: dict) -> str:
        if info["kind"] == "label":
            return f"label {info['text']}"
        lab = next((l["text"] for l in data["labels"] if l["panel"] == info["oid"]), None)
        if lab:
            return f"panel {lab}"
        if info["kind"] == "text":
            return f"text “{info['props']['text'][:20]}”"
        return f"the {info['method']}"

    def _call(self, plan: Plan, data: dict, info: dict):
        site = info.get("site")
        if not site or not site.get("file"):
            raise EditError(f"can't tell which code made {self._who(data, info)}")
        src = plan.source(site["file"])
        call = src.find_call(site, info["method"])
        if call is None:
            raise EditError(f"can't find the code of {self._who(data, info)} ({Path(site['file']).name} line "
                            f"{site['line']}); was the file changed? Try again")
        if info["method"] != "Table" and \
                sum(1 for e in data["elements"] if e.get("site") == site and e["method"] == info["method"]) > 1:
            raise EditError(f"{self._who(data, info)} is made by a loop ({Path(site['file']).name} line {site['line']}), "
                            "together with others; change it in the code")
        return src, call

    def _set(self, plan: Plan, data: dict, info: dict, updates: dict):
        if info.get("method") == "Table":                  # a part of a Table: its own argument of the Table(...)
            names = TABLE_ARGS.get(info["props"].get("role"), {})
            updates = {names[k]: (REMOVE if names[k] in ("title", "notes") and v in ("", None) else v)
                       for k, v in updates.items() if k in names}
            if not updates:
                return                                     # where it goes and how it looks follow from the table
        src, call = self._call(plan, data, info)
        plan.add(src.path, srcedit.set_args(src, call, info["method"], updates, self._who(data, info)))

    def _top(self, data, oids) -> list[str]:
        """The elements themselves for panels inside a layout: the layout (moving one moves it)."""
        out = []
        for oid in oids:
            info = self._info(data, oid)
            top = info.get("parent") or info["oid"]
            if top not in out:
                out.append(top)
        return out

    def _host_box(self, data, info):
        h = info.get("host")
        if h is None:
            raise EditError("can't find the panel this inset is in")
        return self._info(data, h)["box"]

    def _panel_of(self, data, oid):
        info = self._info(data, oid)
        return self._info(data, info["panel"]) if info["kind"] == "label" else info

    # ops: each adds patches to the plan and returns the undo label

    def _op_set(self, plan, data, a):
        """Change arguments of one element: {oid, props}."""
        info = self._info(data, a["oid"])
        props = {k: (REMOVE if (v is None and k in ("label_dx", "label_dy", "label_x", "label_y", "group", "name", "crop", "font", "flip",
                                                    "width", "texts", "colors", "text_style", "height", "fit"))
                     or (v is False and k in ("locked", "hidden")) else v)
                 for k, v in a["props"].items()}
        if info["kind"] == "label":
            panel = self._info(data, info["panel"])
            upd = {}
            if "text" in props:
                upd["label"] = props["text"] if props["text"] not in ("", None) else "auto"
            self._set(plan, data, panel, upd)
            return "Change Label"
        if info["kind"] == "inset":
            own = {k: v for k, v in props.items() if k in INSET_KEYS}
            item = {k: v for k, v in props.items() if k not in INSET_KEYS}
            if own:
                self._set(plan, data, info, own)
            if item:
                self._set(plan, data, {**info, "site": info["itemSite"], "method": info["itemMethod"]}, item)
            return "Change Inset"
        if info["kind"] == "legend" and info.get("parent"):
            raise EditError("this legend is collected from the layout's panels; change the panels' legends, or "
                            "the layout's guides")
        self._set(plan, data, info, props)
        return "Change " + ", ".join(sorted(props))[:40]

    def _op_move(self, plan, data, a):
        """Move elements by (dx, dy) mm: {oids, dx, dy, labels?: "all"}."""
        dx, dy = float(a.get("dx", 0)), float(a.get("dy", 0))
        moved_all_labels = False
        labels = [o for o in a["oids"] if self._info(data, o)["kind"] == "label"]
        for oid in labels + self._top(data, [o for o in a["oids"] if o not in labels]):
            info = self._info(data, oid)
            if info["kind"] == "inset":
                hx, hy, hw, hh = self._host_box(data, info)
                p = info["props"]
                upd = {k: p[k] + d / size for k, d, size in (("x", dx, hw), ("y", dy, hh)) if d and size}
                if upd:
                    self._set(plan, data, info, {k: round(v, 4) for k, v in upd.items()})
                continue
            if info["kind"] == "path":
                self._set(plan, data, info, {"points": [(round(x + dx, 2), round(y + dy, 2))
                                                        for x, y in info["props"]["points"]]})
                continue
            if info["kind"] == "zoom":
                sx, sy, sw, sh = self._info(data, info["source"])["box"] if info.get("source") else (0, 0, 1, 1)
                sw, sh = (info.get("sourceBox") or [sx, sy, sw, sh])[2:]
                rx, ry, rw, rh = info["props"]["region"]
                self._set(plan, data, info, {"region": (round(rx + dx / sw, 4), round(ry + dy / sh, 4), rw, rh)})
                continue
            if info["kind"] == "scalebar":
                bx, by = info["anchor"]
                d = info["dest"]
                self._set(plan, data, info, {"x": round((bx + dx - d[0]) / d[2], 4),
                                             "y": round((by + dy - d[1]) / d[3], 4)})
                continue
            if info["kind"] == "label" and info.get("detached"):
                # unhooked from its panel: it has a place of its own
                panel = self._info(data, info["panel"])
                x, y = info["box"][:2]
                self._set(plan, data, panel, {"label_x": round(x + dx, 2), "label_y": round(y + dy, 2)})
                continue
            if info["kind"] == "label":
                if a.get("labels") == "all":
                    if not moved_all_labels:
                        self._label_style(plan, data, {"dx": data["labelStyle"]["props"]["dx"] + dx,
                                                       "dy": data["labelStyle"]["props"]["dy"] + dy})
                        moved_all_labels = True
                    continue
                panel = self._info(data, info["panel"])
                p = panel["props"]
                upd = {k: (p.get(k) or 0) + d for k, d in (("label_dx", dx), ("label_dy", dy)) if d}
                self._set(plan, data, panel, {k: (REMOVE if abs(v) < 0.005 else v) for k, v in upd.items()})
                continue
            p = info["props"]
            # only the coordinates that change, so a computed y doesn't stop a sideways move
            upd = {k: p[k] + (dy if k.startswith("y") else dx) for k in GEOMETRY.get(info["kind"], ())
                   if (dy if k.startswith("y") else dx)}
            if upd:
                self._set(plan, data, info, upd)
        return "Move Labels" if moved_all_labels else "Move"

    def _op_resize(self, plan, data, a):
        """Give an element a new box: {oid, box: [x, y, w, h], keepAspect?}."""
        info = self._info(data, a["oid"])
        x, y, w, h = (float(v) for v in a["box"])
        k = info["kind"]
        if info.get("parent"):
            raise EditError("a panel in a layout gets its size from the layout: drag the gaps between panels, "
                            "or resize the layout's frame")
        if k == "inset":
            hx, hy, hw, hh = self._host_box(data, info)
            self._set(plan, data, info, {"x": round((x - hx) / hw, 4), "y": round((y - hy) / hh, 4),
                                         "w": round(w / hw, 4), "h": round(h / hh, 4)})
            return "Resize"
        if k == "zoom":
            sx, sy, sw, sh = info["sourceBox"]
            self._set(plan, data, info, {"region": (round((x - sx) / sw, 4), round((y - sy) / sh, 4),
                                                    round(w / sw, 4), round(h / sh, 4))})
            return "Resize Zoom"
        if k == "path":
            ox, oy, ow, oh = info["box"]
            pts = [(round(x + (px - ox) * (w / ow if ow else 1), 2), round(y + (py - oy) * (h / oh if oh else 1), 2))
                   for px, py in info["props"]["points"]]
            self._set(plan, data, info, {"points": pts})
            return "Resize"
        if k in ("image", "figure") and info["props"].get("h") is None and not a.get("free"):
            self._set(plan, data, info, {"x": x, "y": y, "w": w})
        elif k == "figure" and info["props"].get("w") is None:
            self._set(plan, data, info, {"x": x, "y": y, "h": h})
        elif k == "figure":
            self._set(plan, data, info, {"x": x, "y": y, "w": w, "h": h})
        elif k == "qr":
            self._set(plan, data, info, {"x": x, "y": y, "w": round(max(w, h), 2)})
        elif k in BOXED:
            self._set(plan, data, info, {"x": x, "y": y, "w": w, "h": h})
        elif k == "text":
            ox, oy, ow, oh = info["box"]
            size = info["props"].get("size") or data["style"]["props"]["size"]
            ratio = h / oh if oh > 0 else 1
            align = info["props"].get("align", "left")
            ax = {"left": x, "center": x + w / 2, "right": x + w}.get(align, x)
            self._set(plan, data, info, {"size": round(size * ratio * 4) / 4, "x": ax, "y": y})
        elif k == "bracket":
            self._set(plan, data, info, {"x1": x, "x2": x + w})
        elif k == "table":
            self._set(plan, data, info, {"x": x, "y": y, "w": round(w, 2)})
        else:
            raise EditError(f"a {k} can't be resized that way")
        return "Resize"

    def _op_endpoints(self, plan, data, a):
        info = self._info(data, a["oid"])
        self._set(plan, data, info, {k: float(a[k]) for k in ("x1", "y1", "x2", "y2") if k in a})
        return "Move Line End"

    def _op_delete(self, plan, data, a):
        stmts = {}
        for oid in a["oids"]:
            info = self._info(data, oid)
            if info["kind"] == "label":
                self._set(plan, data, self._info(data, info["panel"]), {"label": None})
                continue
            if info.get("method") == "Table":
                role = info["props"].get("role")
                if role == "cells":
                    raise EditError("a table's cells are the table: delete its file to delete the table, or its "
                                    "rows and columns from its cells")
                src, call = self._call(plan, data, info)
                plan.add(src.path, srcedit.set_args(src, call, "Table", {role: REMOVE}, f"the table's {role}"))
                continue
            if info.get("parent"):
                parent = self._info(data, info["parent"])
                if info["kind"] == "legend":
                    self._set(plan, data, parent, {"guides": REMOVE})
                    continue
                if any(self._info(data, o)["oid"] == parent["oid"] for o in a["oids"]):
                    continue
                raise EditError(f"{self._who(data, info)} is part of a layout; take it out of the layout's code "
                                f"({Path(parent['site']['file']).name} line {parent['site']['line']}), or delete the "
                                "whole layout")
            src, call = self._call(plan, data, info)
            st = src.statement(call)
            used = srcedit.names_used_after(src, st)
            if used:
                n, ln = used[0]
                raise EditError(f"{self._who(data, info)} is used by later code ({n}, line {ln}); delete that first")
            stmts[(src.path, st.lineno)] = (src, st)
        for (path, _), (src, st) in stmts.items():
            a0, b0 = src.stmt_span(st)
            plan.add(path, [(a0, b0, "")])
        return "Delete"

    def _op_duplicate(self, plan, data, a):
        """Copies of elements, offset by (dx, dy): {oids, dx?, dy?}."""
        dx, dy = float(a.get("dx", 4)), float(a.get("dy", 4))
        per_file = {}
        for oid in self._top(data, [o for o in a["oids"] if self._info(data, o)["kind"] != "label"]):
            info = self._info(data, oid)
            if info["kind"] == "label":
                continue
            src, call = self._call(plan, data, info)
            st = src.statement(call)
            text = src.seg(call)
            copy = Source(text, src.path)
            ccall = next(n for n in ast.walk(copy.tree) if isinstance(n, ast.Call))
            p = info["props"]
            upd = {k: p[k] + (dy if k.startswith("y") else dx) for k in GEOMETRY.get(info["kind"], ())}
            if info["kind"] == "inset":
                hx, hy, hw, hh = self._host_box(data, info)
                upd = {"x": round(p["x"] + dx / hw, 4), "y": round(p["y"] + dy / hh, 4)}
            if p.get("name"):
                upd["name"] = REMOVE
            new = srcedit.apply(text, srcedit.set_args(copy, ccall, info["method"], upd, self._who(data, info)))
            per_file.setdefault(src.path, []).append((src, st, src.indent(st) + new + "\n"))
        for path, items in per_file.items():
            src = items[0][0]
            # after the last statement of the selection, in selection order
            last = max(items, key=lambda t: t[1].end_lineno)[1]
            _, end = src.stmt_span(last)
            block = "".join(t[2] for t in sorted(items, key=lambda t: t[1].lineno))
            plan.add(path, [(end, end, block)])
            first = src.text[:end].count("\n") + 1
            plan.inserted[path] = list(range(first, first + block.count("\n")))
        return "Duplicate"

    def _element_stmts(self, src: Source, data: dict):
        """The statements of this file that make elements, by block."""
        out = {}
        for info in data["elements"]:
            s = info.get("site") or {}
            if s.get("file") != src.path or info.get("parent"):
                continue
            call = src.find_call(s, info["method"])
            if call is None:
                continue
            try:
                st = src.statement(call)
            except EditError:
                continue
            out[id(st)] = (st, info["oid"])
        return out

    def _op_order(self, plan, data, a):
        """Stacking order: {oids, how: front|back|forward|backward}. The order of the calls is the drawing order."""
        how = a["how"]
        files = {}
        for oid in self._top(data, a["oids"]):
            info = self._info(data, oid)
            if info["kind"] == "label":
                continue
            src, call = self._call(plan, data, info)
            files.setdefault(src.path, (src, set()))[1].add(id(src.statement(call)))
        for path, (src, chosen) in files.items():
            stm = self._element_stmts(src, data)
            first = stm[next(iter(chosen))][0]
            block = src.block_of(first)
            order = [st for st in block if id(st) in stm]
            if not all(any(id(st) == c for st in order) for c in chosen):
                raise EditError("those elements are made in different parts of the code; reorder them there")
            sel = [st for st in order if id(st) in chosen]
            rest = [st for st in order if id(st) not in chosen]
            if how == "front":
                new = rest + sel
            elif how == "back":
                new = sel + rest
            else:
                new = list(order)
                rng = range(len(new) - 2, -1, -1) if how == "forward" else range(1, len(new))
                for i in rng:
                    j = i + 1 if how == "forward" else i - 1
                    if id(new[i]) in chosen and id(new[j]) not in chosen:
                        new[i], new[j] = new[j], new[i]
            if [id(s) for s in new] == [id(s) for s in order]:
                continue
            texts = {}
            for st in order:
                a0, b0 = src.stmt_span(st)
                texts[id(st)] = src.text[a0:b0]
            plan.add(path, [(*src.stmt_span(old), texts[id(nw)]) for old, nw in zip(order, new)])
        return {"front": "Bring to Front", "back": "Send to Back", "forward": "Bring Forward",
                "backward": "Send Backward"}[how]

    def _boxes(self, data, oids):
        out = []
        for oid in self._top(data, [o for o in oids if self._info(data, o)["kind"] != "label"]):
            info = self._info(data, oid)
            if info["kind"] != "label":
                out.append((oid, info["box"]))
        return out

    def _op_align(self, plan, data, a):
        """{oids, how: left|hcenter|right|top|vcenter|bottom, to: selection|figure|key, key?}"""
        how, to = a["how"], a.get("to", "selection")
        boxes = self._boxes(data, a["oids"])
        if not boxes:
            raise EditError("nothing to align")
        if to == "figure":
            X0, Y0, X1, Y1 = 0, 0, data["size"][0], data["size"][1]
        elif to == "key" and a.get("key") is not None:
            kx, ky, kw, kh = self._info(data, a["key"])["box"]
            X0, Y0, X1, Y1 = kx, ky, kx + kw, ky + kh
        else:
            if len(boxes) < 2:
                raise EditError("select two or more elements to align them to each other (or align to the figure)")
            X0 = min(b[0] for _, b in boxes)
            Y0 = min(b[1] for _, b in boxes)
            X1 = max(b[0] + b[2] for _, b in boxes)
            Y1 = max(b[1] + b[3] for _, b in boxes)
        for oid, (x, y, w, h) in boxes:
            dx = dy = 0.0
            if how == "left":
                dx = X0 - x
            elif how == "right":
                dx = X1 - (x + w)
            elif how == "hcenter":
                dx = (X0 + X1) / 2 - (x + w / 2)
            elif how == "top":
                dy = Y0 - y
            elif how == "bottom":
                dy = Y1 - (y + h)
            elif how == "vcenter":
                dy = (Y0 + Y1) / 2 - (y + h / 2)
            if abs(dx) > 1e-6 or abs(dy) > 1e-6:
                self._op_move(plan, data, {"oids": [oid], "dx": dx, "dy": dy})
        return "Align"

    def _op_distribute(self, plan, data, a):
        """Equal gaps between the elements: {oids, axis: h|v, gap?} (a fixed gap in mm, if given)."""
        h = a.get("axis", "h") == "h"
        boxes = sorted(self._boxes(data, a["oids"]), key=lambda t: t[1][0] if h else t[1][1])
        if len(boxes) < 2 or (len(boxes) < 3 and a.get("gap") is None):
            raise EditError("select three or more elements to distribute (or give a gap)")
        i0, i1 = (0, 2) if h else (1, 3)
        start = boxes[0][1][i0]
        if a.get("gap") is not None:
            gap = float(a["gap"])
        else:
            span = boxes[-1][1][i0] + boxes[-1][1][i1] - start
            gap = (span - sum(b[i1] for _, b in boxes)) / (len(boxes) - 1)
        at = start
        for oid, b in boxes:
            d = at - b[i0]
            if abs(d) > 1e-6:
                self._op_move(plan, data, {"oids": [oid], "dx": d if h else 0, "dy": 0 if h else d})
            at += b[i1] + gap
        return "Distribute"

    def _op_match(self, plan, data, a):
        """Make the elements as wide / high as the first: {oids, what: width|height|both}."""
        boxes = self._boxes(data, a["oids"])
        if len(boxes) < 2:
            raise EditError("select the element to match first, then the others")
        _, (kx, ky, kw, kh) = boxes[0]
        for oid, (x, y, w, h) in boxes[1:]:
            info = self._info(data, oid)
            if info["kind"] not in BOXED:
                continue
            nw = kw if a["what"] in ("width", "both") else w
            nh = kh if a["what"] in ("height", "both") else h
            if info["kind"] == "image" and info["props"].get("h") is None:
                if a["what"] == "height":
                    nw = kh * (w / h if h else 1)
                self._set(plan, data, info, {"w": nw})
            else:
                self._set(plan, data, info, {"w": nw, "h": nh})
        return "Match Size"

    def _op_divider(self, plan, data, a):
        """Drag a gap between two rows or columns of a layout: {oid (the layout), index (its dividers'), delta mm}.
        The tracks on either side change size; the call that sets them gets the new relative sizes."""
        lay = self._info(data, a["oid"])
        if lay["kind"] != "layout":
            lay = self._info(data, lay.get("parent") or a["oid"])
        d = lay["dividers"][int(a["index"])]
        if not d.get("editable"):
            raise EditError("these rows or columns are made with | / + ; give them sizes with Row(..., widths=[...]), "
                            "Column(..., heights=[...]) or Grid(...) to drag them")
        sizes = list(d["sizes"])
        i, delta = d["index"], float(a["delta"])
        lo = 3.0
        delta = max(lo - sizes[i], min(delta, sizes[i + 1] - lo))
        sizes[i] += delta
        sizes[i + 1] -= delta
        small = min(sizes)
        weights = [round(v / small, 2) for v in sizes]
        site, method = d["site"], d["method"]
        src = plan.source(site["file"])
        call = src.find_call(site, method)
        if call is None:
            raise EditError("can't find the code of this layout; was the file changed? Try again")
        plan.add(src.path, srcedit.set_args(src, call, method, {d["param"]: weights}, "the layout"))
        return "Resize Layout Tracks"

    def _op_make_layout(self, plan, data, a):
        """Put free panels and pictures into a patchwork layout: {oids, gap?}. Each becomes a Panel(...) or Image(...)
        named after its file, and fig.layout(Grid(..., ncol=n), x, y, w, h) takes the place of the first, in their
        reading order, filling the box they covered."""
        from .render import _reading_order
        items = [self._info(data, o) for o in self._top(data, a["oids"])]
        items = [i for i in items if i["kind"] in ("panel", "image") and not i.get("parent")]
        if len(items) < 2:
            raise EditError("select two or more panels or pictures (not already in a layout) to lay out")
        ordered = _reading_order([(i, i["oid"], i["box"]) for i in items])
        rows, last = [], None
        for i, _, b in ordered:                          # rows as the reading order found them
            if last is None or b[1] - last[1] >= min(8.0, 0.4 * max(last[3], 1)):
                rows.append([])
                last = b
            rows[-1].append(i)
        ncol = max(len(r) for r in rows)
        x0 = min(i["box"][0] for i in items)
        y0 = min(i["box"][1] for i in items)
        x1 = max(i["box"][0] + i["box"][2] for i in items)
        y1 = max(i["box"][1] + i["box"][3] for i in items)
        path = data["file"]
        src = plan.source(path)
        var = srcedit.figure_var(src)
        taken = {n.id for n in ast.walk(src.tree) if isinstance(n, ast.Name)}
        defs, names, first = [], [], None
        for i, _, _ in ordered:
            fsrc, call = self._call(plan, data, i)
            if fsrc.path != path:
                raise EditError("those panels are made in another file")
            st = fsrc.statement(call)
            if srcedit.names_used_after(fsrc, st):
                raise EditError(f"{self._who(data, i)} is used by later code; lay it out in the code")
            text = fsrc.seg(call)
            one = Source(text, path)
            c1 = next(n for n in ast.walk(one.tree) if isinstance(n, ast.Call))
            drop = {k: REMOVE for k in ("x", "y", "w", "h", "group")}
            patches = srcedit.set_args(one, c1, i["method"], drop, self._who(data, i))
            patches.append((one.start(c1.func), one.end(c1.func), "Panel" if i["kind"] == "panel" else "Image"))
            text = srcedit.apply(text, patches)
            stem = Path(str(i["props"].get("source", "panel"))).stem
            base = "".join(ch if ch.isalnum() else "_" for ch in stem).strip("_").lower() or "panel"
            if base[0].isdigit():
                base = "p" + base
            name, n = base, 1
            while name in taken:
                n += 1
                name = f"{base}{n}"
            taken.add(name)
            names.append(name)
            defs.append(f"{name} = {text}")
            a0, b0 = fsrc.stmt_span(st)
            plan.add(path, [(a0, b0, "")])
            first = a0 if first is None else min(first, a0)
        gap = float(a.get("gap", 4))
        lay = (f"{var}.layout(Grid({', '.join(names)}, ncol={ncol}), x={srcedit.fmt(x0)}, y={srcedit.fmt(y0)}, "
               f"w={srcedit.fmt(x1 - x0)}, h={srcedit.fmt(y1 - y0)}, gap={srcedit.fmt(gap)})")
        block = "\n".join(defs + [lay]) + "\n"
        plan.add(path, [(first, first, block)])
        if "Grid" not in src.text and not re.search(r"^\s*from\s+fignimble\s+import\s+\*", src.text, re.M):
            raise EditError("this file doesn't import Panel and Grid (add `from fignimble import *`)")
        line = src.text[:first].count("\n") + 1
        plan.inserted[path] = list(range(line, line + len(defs) + 1))
        return "Make Layout"

    def _op_release_layout(self, plan, data, a):
        """Take the panels out of a layout: each becomes a fig.panel(...) or fig.image(...) where it is now. {oid}"""
        lay = self._info(data, a["oid"])
        if lay["kind"] != "layout":
            lay = self._info(data, lay.get("parent") or a["oid"])
        src, call = self._call(plan, data, lay)
        var = srcedit.figure_var(src)
        st = src.statement(call)
        lines = []
        node_names = set()
        for oid in lay.get("children", []):
            ch = self._info(data, oid)
            if ch["kind"] == "legend":
                continue
            csrc = plan.source(ch["site"]["file"]) if ch.get("site") else None
            ccall = csrc.find_call(ch["site"], ch["method"]) if csrc else None
            if ccall is None:
                raise EditError(f"can't find the code of {self._who(data, ch)}")
            text = csrc.seg(ccall)
            one = Source(text, csrc.path)
            c1 = next(n for n in ast.walk(one.tree) if isinstance(n, ast.Call))
            x, y, w, h = ch["box"]
            meth = "panel" if ch["kind"] == "panel" else "image"
            patches = srcedit.set_args(one, c1, ch["method"], {"x": round(x, 2), "y": round(y, 2), "w": round(w, 2),
                                                              "h": round(h, 2)})
            patches.append((one.start(c1.func), one.end(c1.func), f"{var}.{meth}"))
            lines.append(src.indent(st) + srcedit.apply(text, patches))
            try:
                cst = csrc.statement(ccall)
                if isinstance(cst, ast.Assign) and csrc.path == src.path:
                    node_names.update(t.id for t in cst.targets if isinstance(t, ast.Name))
                    used = [n for n in srcedit.names_used_after(csrc, cst) if n[1] != st.lineno and
                            not (st.lineno <= n[1] <= st.end_lineno)]
                    if not used:
                        a0, b0 = csrc.stmt_span(cst)
                        plan.add(csrc.path, [(a0, b0, "")])
            except EditError:
                pass
        a0, b0 = src.stmt_span(st)
        plan.add(src.path, [(a0, b0, "\n".join(lines) + "\n")])
        return "Release Layout"

    def _name_of(self, plan, data, info) -> str:
        """The variable a panel or picture is in, naming its line (`cells = fig.image(...)`) if it has none."""
        if info["kind"] == "label":
            info = self._info(data, info["panel"])
        src, call = self._call(plan, data, info)
        st = src.statement(call)
        if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
            return st.targets[0].id
        if not isinstance(st, ast.Expr):
            raise EditError(f"{self._who(data, info)} needs a name in the code first")
        stem = Path(str((info.get("item") or info["props"]).get("source", info["kind"]))).stem
        base = "".join(ch if ch.isalnum() else "_" for ch in stem).strip("_").lower() or info["kind"]
        if base[0].isdigit():
            base = "p" + base
        taken = {n.id for n in ast.walk(src.tree) if isinstance(n, ast.Name)}
        name, k = base, 1
        while name in taken:
            k += 1
            name = f"{base}{k}"
        at = src.start(st)
        plan.add(src.path, [(at, at, f"{name} = ")])
        return name

    def _append(self, plan, data, line: str):
        """A statement after the figure file's last element."""
        src, end, ind = self._insert_point(plan, data)
        plan.add(src.path, [(end, end, ind + line + "\n")])
        plan.inserted.setdefault(src.path, []).append(src.text[:end].count("\n") + 1)

    def _op_add_scalebar(self, plan, data, a):
        """A scale bar on a picture: {oid, props: {length, unit, pixel_size, ...}}."""
        info = self._info(data, a["oid"])
        if info["kind"] not in ("image", "inset"):
            raise EditError("select a picture for the scale bar")
        name = self._name_of(plan, data, info)
        var = srcedit.figure_var(plan.source(data["file"]))
        props = {"length": 50, "unit": "µm", **a.get("props", {})}      # no pixel_size: the picture's metadata
        self._append(plan, data, srcedit.call_text(var, "scalebar", [srcedit.Raw(name)], props))
        return "Add Scale Bar"

    def _op_add_zoom(self, plan, data, a):
        """A zoom box: {oids: [source, target]} joins two panels; {oids: [picture]} also makes the magnified view, an
        inset of the same picture in the picture's corner."""
        oids = [o for o in a["oids"] if not o.startswith("L")]
        if not oids:
            raise EditError("select a picture (or a panel and the one that magnifies it)")
        src_info = self._info(data, oids[0])
        sname = self._name_of(plan, data, src_info)
        var = srcedit.figure_var(plan.source(data["file"]))
        if len(oids) >= 2:
            tname = self._name_of(plan, data, self._info(data, oids[1]))
        else:
            if src_info["kind"] != "image":
                raise EditError("select the panel and the one that magnifies it, or one picture")
            tname = sname + "_zoom"
            item = srcedit.call_text("", "Image", [src_info["props"]["source"]], {})[1:]
            self._append(plan, data, f"{tname} = {var}.inset({item}, within={sname}, x=0.6, y=0.6, w=0.38, h=0.38)")
        self._append(plan, data, srcedit.call_text(var, "zoom", [srcedit.Raw(sname), srcedit.Raw(tname)],
                                                   {"region": (0.2, 0.2, 0.25, 0.25), "color": "#F2C14E"}))
        return "Add Zoom"

    def _op_points(self, plan, data, a):
        """A path's points: {oid, points}."""
        info = self._info(data, a["oid"])
        self._set(plan, data, info, {"points": [(round(float(x), 2), round(float(y), 2)) for x, y in a["points"]]})
        return "Edit Path"

    def _picture_call(self, data, info):
        """The info whose call holds a picture's settings (an inset's are its Image's)."""
        if info["kind"] == "inset":
            return {**info, "site": info["itemSite"], "method": info["itemMethod"], "props": info["item"]}
        return info

    def _op_crop(self, plan, data, a):
        """Crop a picture to the part of it now in `box` (mm): {oid, box}. Beyond its edges uncrops."""
        info = self._info(data, a["oid"])
        pic = self._picture_call(data, info)
        p = pic["props"]
        if info["kind"] not in ("image", "inset") or (info["kind"] == "inset" and info.get("inner") != "image"):
            raise EditError("only pictures can be cropped (panels draw what their code draws)")
        if p.get("rotation") or p.get("flip"):
            raise EditError("crop a picture before turning or flipping it (set its rotation to 0 first)")
        x, y, w, h = info["box"]
        l, t, r, b = p.get("crop") or (0, 0, 0, 0)
        fw, fh = w / max(1 - l - r, 1e-6), h / max(1 - t - b, 1e-6)
        fx, fy = x - l * fw, y - t * fh
        nx, ny, nw, nh = (float(v) for v in a["box"])
        nl = min(max((nx - fx) / fw, 0), 0.95)
        nt = min(max((ny - fy) / fh, 0), 0.95)
        nr = min(max(1 - (nx + nw - fx) / fw, 0), 0.95 - nl)
        nb = min(max(1 - (ny + nh - fy) / fh, 0), 0.95 - nt)
        crop = tuple(round(v, 4) for v in (nl, nt, nr, nb))
        upd = {"crop": REMOVE if not any(crop) else crop}
        if info["kind"] == "image" and not info.get("parent"):
            vx, vy = fx + nl * fw, fy + nt * fh
            upd.update(x=round(vx, 2), y=round(vy, 2), w=round(fw * (1 - nl - nr), 2))
            if p.get("h") is not None:
                upd["h"] = round(fh * (1 - nt - nb), 2)
        self._set(plan, data, pic, upd)
        return "Crop"

    def _op_rotate(self, plan, data, a):
        """Turn elements by `by` degrees (counterclockwise): {oids, by}."""
        for oid in self._top(data, a["oids"]):
            info = self._info(data, oid)
            pic = self._picture_call(data, info)
            if pic["kind"] not in ("image", "inset", "panel", "text"):
                continue
            cur = (pic["props"].get("rotation") or 0)
            new = (cur + float(a["by"])) % 360
            self._set(plan, data, pic, {"rotation": REMOVE if new == 0 else round(new, 2)})
        return "Rotate"

    def _op_flip(self, plan, data, a):
        """Mirror pictures: {oids, axis: h|v}. Flipping twice undoes it."""
        for oid in self._top(data, a["oids"]):
            pic = self._picture_call(data, self._info(data, oid))
            if pic["kind"] not in ("image", "inset") or (pic.get("inner") not in (None, "image")):
                continue
            cur = pic["props"].get("flip")
            new = None if cur == a["axis"] else (a["axis"] if not cur else None)
            if cur and cur != a["axis"]:                 # h then v is a half turn
                rot = ((pic["props"].get("rotation") or 0) + 180) % 360
                self._set(plan, data, pic, {"flip": REMOVE, "rotation": REMOVE if rot == 0 else rot})
                continue
            self._set(plan, data, pic, {"flip": REMOVE if new is None else new})
        return "Flip"

    def _op_panel_text(self, plan, data, a):
        """Retype a text a panel's code draws: {oid, key, text, old}. A string literal in the panel's code that is
        the old text is changed; otherwise the panel gets a texts={key: text} override."""
        info = self._info(data, a["oid"])
        if not (info["kind"] == "panel" or (info["kind"] == "inset" and info.get("inner") == "panel")):
            raise EditError("only the texts of a panel drawn by code can be retyped this way")
        item = self._picture_call(data, info) if info["kind"] == "inset" else info
        props = item["props"]
        old, new = a.get("old"), a["text"]
        src = props.get("source")
        if old and isinstance(src, str) and src.endswith(".py") and not src.startswith("<"):
            path = str((self.project / src).resolve())
            if Path(path).exists() and not (props.get("texts") or {}).get(a["key"]):
                code = plan.source(path)
                hits = [n for n in ast.walk(code.tree) if isinstance(n, ast.Constant) and n.value == old]
                if len(hits) == 1:
                    n = hits[0]
                    plan.add(path, [(code.start(n), code.end(n), srcedit.fmt(new))])
                    return "Retype in Panel"
        texts = dict(props.get("texts") or {})
        texts[a["key"]] = new
        self._set(plan, data, item, {"texts": texts})
        return "Retype in Panel"

    # the calls that draw a text given as their first argument (or `label`/`s`/`t`), whose fontsize, color,
    # fontweight and fontstyle a restyle can set where the code is
    _TEXT_CALLS = {"set_xlabel", "set_ylabel", "set_title", "xlabel", "ylabel", "title", "text", "annotate",
                   "suptitle", "supxlabel", "supylabel", "figtext", "set_label_text"}
    _TEXT_KW = {"size": "fontsize", "color": "color", "weight": "fontweight", "style": "fontstyle"}

    def _op_panel_text_style(self, plan, data, a):
        """Restyle a text a panel's code draws: {oid, key, text, props: {size?, color?, weight?, style?}} (None
        removes one). When the text is a string literal of one ax.set_xlabel(...), ax.text(...) etc. in the panel's
        code, that call gets fontsize=, color=, fontweight=, fontstyle=; otherwise the panel gets
        text_style={key: {...}}."""
        info = self._info(data, a["oid"])
        if not (info["kind"] == "panel" or (info["kind"] == "inset" and info.get("inner") == "panel")):
            raise EditError("only the texts of a panel drawn by code can be restyled this way")
        item = self._picture_call(data, info) if info["kind"] == "inset" else info
        props, key, upd = item["props"], a["key"], dict(a.get("props") or {})
        text, src = a.get("text"), props.get("source")
        styles = dict(props.get("text_style") or {})
        if text and key not in styles and not (props.get("texts") or {}).get(key) and isinstance(src, str) \
                and src.endswith(".py") and not src.startswith("<"):
            path = str((self.project / src).resolve())
            if Path(path).exists():
                code = plan.source(path)
                calls = []
                for n in ast.walk(code.tree):
                    if isinstance(n, ast.Call) and isinstance(n.func, (ast.Attribute, ast.Name)) \
                            and (n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id) in self._TEXT_CALLS:
                        given = [x for x in n.args[:3]] + [k.value for k in n.keywords if k.arg in ("label", "s", "t")]
                        if any(isinstance(x, ast.Constant) and x.value == text for x in given):
                            calls.append(n)
                if len(calls) == 1:
                    try:
                        plan.add(path, srcedit.set_args(code, calls[0], "", {
                            self._TEXT_KW[k]: (srcedit.REMOVE if v is None else v) for k, v in upd.items()
                            if k in self._TEXT_KW}, what="this text"))
                        return "Restyle Text in Panel"
                    except EditError:
                        pass                     # a size the code computes: the panel's override wins over it
        st = dict(styles.get(key) or {})
        for k, v in upd.items():
            if k in self._TEXT_KW:
                if v is None:
                    st.pop(k, None)
                else:
                    st[k] = v
        if st:
            styles[key] = st
        else:
            styles.pop(key, None)
        self._set(plan, data, item, {"text_style": styles or srcedit.REMOVE})
        return "Restyle Text in Panel"

    def _op_panel_text_reset(self, plan, data, a):
        """Drop the editor's retyping and restyling of one text of a panel: {oid, key}."""
        info = self._info(data, a["oid"])
        item = self._picture_call(data, info) if info["kind"] == "inset" else info
        props, key = item["props"], a["key"]
        upd = {}
        for name in ("texts", "text_style"):
            d = dict(props.get(name) or {})
            if key in d:
                d.pop(key)
                upd[name] = d or srcedit.REMOVE
        if not upd:
            raise EditError("this text is as the panel's code draws it")
        self._set(plan, data, item, upd)
        return "Reset Text in Panel"

    def _op_panel_color(self, plan, data, a):
        """Recolor marks a panel's code draws: {oid, keys, color, old}. When the old color is written once in the
        panel's code, that is changed (every mark drawn with it follows); otherwise the panel gets colors={key: new}
        for the marks given."""
        import matplotlib.colors as mcolors
        info = self._info(data, a["oid"])
        if not (info["kind"] == "panel" or (info["kind"] == "inset" and info.get("inner") == "panel")):
            raise EditError("only the marks of a panel drawn by code can be recolored this way")
        item = self._picture_call(data, info) if info["kind"] == "inset" else info
        props = item["props"]
        new, old, keys = a["color"], (a.get("old") or "").upper(), list(a["keys"])
        src = props.get("source")
        overridden = set(props.get("colors") or {}) & set(keys)
        if old and isinstance(src, str) and src.endswith(".py") and not overridden:
            path = str((self.project / src).resolve())
            if Path(path).exists():
                code = plan.source(path)
                hits = []
                for n in ast.walk(code.tree):
                    if isinstance(n, ast.Constant) and isinstance(n.value, str):
                        try:
                            if mcolors.to_hex(n.value).upper() == old:
                                hits.append(n)
                        except ValueError:
                            pass
                if len(hits) == 1:
                    n = hits[0]
                    plan.add(path, [(code.start(n), code.end(n), srcedit.fmt(new))])
                    return "Recolor in Panel"
        colors = dict(props.get("colors") or {})
        for k in keys:
            colors[k] = new
        self._set(plan, data, item, {"colors": colors})
        return "Recolor in Panel"

    def _op_paste_style(self, plan, data, a):
        """Give elements another's look (the eyedropper): {oids, props}; each takes the settings its kind has."""
        for oid in self._top(data, a["oids"]):
            info = self._info(data, oid)
            if info["kind"] == "label":
                continue
            params = set(srcedit.PARAMS.get(info["method"], []))
            upd = {k: v for k, v in a["props"].items() if k in params}
            if upd:
                self._set(plan, data, info, upd)
        return "Paste Style"

    def _op_replace_source(self, plan, data, a):
        """Show another picture file in a picture's place: {oid, path} (copied into images/ if it is elsewhere)."""
        info = self._info(data, a["oid"])
        pic = self._picture_call(data, info)
        if pic["kind"] not in ("image", "inset") and info.get("kind") != "image":
            raise EditError("only a picture's file can be replaced")
        rel = self._bring_in(Path(a["path"]), plan.new_files)
        self._set(plan, data, pic, {"source": str(rel)})
        return "Replace Picture"

    def _bring_in(self, src: Path, made: list | None = None) -> Path:
        """A file in the project: itself, or a copy in panels/, tables/ or images/ (added to `made`)."""
        src = src.resolve()
        if not src.exists():
            raise EditError(f"no file {src}")
        if str(src).startswith(str(self.project) + os.sep):
            return src.relative_to(self.project)
        dest_dir = self.project / ("panels" if src.suffix == ".py" else
                                   "tables" if src.suffix.lower() in TABLE_FILES else "images")
        dest_dir.mkdir(exist_ok=True)
        dest = dest_dir / src.name
        n = 1
        while dest.exists() and dest.read_bytes() != src.read_bytes():
            n += 1
            dest = dest_dir / f"{src.stem}-{n}{src.suffix}"
        if not dest.exists():
            shutil.copy2(src, dest)
            if made is not None:
                made.append(dest)
        return dest.relative_to(self.project)

    def _op_label_detach(self, plan, data, a):
        """Unhook a panel's label from it, or hook it on again: {oid (the panel or its label), detach: bool}. An
        unhooked label stays where it is when the panel moves (label_x/label_y); hooked on again, it keeps its
        place as an offset from the panel's corner (label_dx/label_dy)."""
        info = self._info(data, a["oid"])
        if info["kind"] != "label":
            info = next((l for l in data["labels"] if l["panel"] == info["oid"]), None)
            if info is None:
                raise EditError("that has no panel letter")
        panel = self._info(data, info["panel"])
        x, y = info["box"][:2]
        detach = bool(a.get("detach", not info.get("detached")))
        if detach:
            self._set(plan, data, panel, {"label_x": round(x, 2), "label_y": round(y, 2),
                                          "label_dx": REMOVE, "label_dy": REMOVE})
            return "Unlink Label"
        lp = data["labelStyle"]["props"]
        bx, by = panel["box"][:2]
        dx, dy = x - (bx + lp["dx"]), y - (by + lp["dy"])
        self._set(plan, data, panel, {"label_x": REMOVE, "label_y": REMOVE,
                                      "label_dx": REMOVE if abs(dx) < 0.005 else round(dx, 2),
                                      "label_dy": REMOVE if abs(dy) < 0.005 else round(dy, 2)})
        return "Link Label"

    def _op_caption(self, plan, data, a):
        """The figure's caption: {text} (empty: none). Written as fig.caption(...) after the figure's code."""
        text = str(a.get("text") or "").rstrip()
        cap = data.get("caption") or {}
        site = cap.get("site")
        lit = srcedit.text_literal(text)
        if site and site.get("file"):
            src = plan.source(site["file"])
            call = src.find_call(site, "caption")
            if call is None:
                raise EditError("can't find the figure's fig.caption(...) call")
            if not text:
                st = src.statement(call)
                a0, b0 = src.stmt_span(st)
                plan.add(src.path, [(a0, b0, "")])
                return "Remove Caption"
            if call.args:
                node = call.args[0]
                if not srcedit.is_literal(node):
                    raise EditError(f"the caption is computed by the code ({src.seg(node)}); change it there")
                plan.add(src.path, [(src.start(node), src.end(node), lit)])
            else:
                plan.add(src.path, srcedit.set_args(src, call, "caption", {"text": srcedit.Raw(lit)}, "the caption"))
            return "Edit Caption"
        if not text:
            return "Edit Caption"
        src = plan.source(data["file"])
        var = srcedit.figure_var(src)
        end = len(src.text)
        prefix = "" if src.text.endswith("\n") else "\n"
        plan.add(src.path, [(end, end, prefix + f"{var}.caption({lit})\n")])
        return "Edit Caption"

    def _op_table_rows(self, plan, data, a):
        """New cells for a table: {oid, rows: [[str]]}. A table from a file gets the file written again (in its
        own delimiter); one written in the code gets its rows= replaced."""
        info = self._info(data, a["oid"])
        if info["kind"] != "table":
            raise EditError("only a table has cells")
        if info.get("fromCode"):
            raise EditError(f"this table's cells come from {info['fromCode']}() in the code; change them there, or "
                            "in the data it reads")
        rows = [[str(c) for c in r] for r in a["rows"]]
        if not rows or not rows[0]:
            raise EditError("a table needs at least one cell")
        src_file = info["props"].get("source")
        if src_file:
            path = (self.project / src_file).resolve()
            if not str(path).startswith(str(self.project) + os.sep):
                raise EditError("that table's file is outside the project")
            plan.raw[str(path)] = (path.read_text(encoding="utf-8-sig") if path.exists() else "",
                                   tables.write_delimited(rows, "\t" if path.suffix.lower() in (".tsv", ".txt") else ","))
        else:
            self._set(plan, data, info, {"rows": [[_cell(c) for c in r] for r in rows]})
        return "Edit Table"

    def _op_fit_figure(self, plan, data, a):
        """Make the figure the size of what is on it, with a margin: {margin?} (mm)."""
        if _is_table(data):
            return "Fit Figure"                            # a table's page always fits it
        m = float(a.get("margin", 3))
        boxes = [e["box"] for e in data["elements"] + data["labels"]
                 if e.get("box") and e["kind"] not in ("zoom",) and not e.get("parent") and e["box"][2] > 0]
        if not boxes:
            raise EditError("nothing on the figure to fit it to")
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return self._op_figure(plan, data, {"props": {"width": round(x1 + m, 1), "height": round(y1 + m, 1)}})

    def _op_group(self, plan, data, a):
        used = {e["props"].get("group") for e in data["elements"]}
        n = 1
        while f"g{n}" in used:
            n += 1
        for oid in self._top(data, a["oids"]):
            info = self._info(data, oid)
            if info["kind"] != "label":
                self._set(plan, data, info, {"group": f"g{n}"})
        return "Group"

    def _op_ungroup(self, plan, data, a):
        for oid in self._top(data, a["oids"]):
            info = self._info(data, oid)
            if info["kind"] != "label" and info["props"].get("group"):
                self._set(plan, data, info, {"group": REMOVE})
        return "Ungroup"

    def _insert_point(self, plan, data):
        """Where a new element's statement goes: after the figure file's last element statement (module level)."""
        path = data["file"]
        src = plan.source(path)
        stm = self._element_stmts(src, data)
        top = [st for st, _ in stm.values() if st in src.tree.body]
        if top:
            last = max(top, key=lambda s: s.end_lineno)
        else:
            fs = data.get("figureSite") or {}
            call = src.find_call(fs, "Figure") if fs else None
            last = src.statement(call) if call is not None else src.tree.body[-1]
        _, end = src.stmt_span(last)
        return src, end, src.indent(last)

    def _op_insert(self, plan, data, a):
        """A new element: {kind, props}. kind: panel, image, text, line, arrow, rect, ellipse, bracket."""
        kind, props = a["kind"], dict(a.get("props", {}))
        if "points" in props:
            props["points"] = [tuple(q) for q in props["points"]]
        src, end, ind = self._insert_point(plan, data)
        var = srcedit.figure_var(src)
        first = {"panel": "source", "image": "source", "text": "text", "table": "source", "figure": "source",
                 "qr": "data"}.get(kind)
        args = [props.pop(first)] if first else []
        if kind in ("line", "arrow"):
            args = [props.pop(k) for k in ("x1", "y1", "x2", "y2")]
        if kind == "bracket":
            args = [props.pop(k) for k in ("x1", "x2", "y")]
        line = ind + srcedit.call_text(var, kind, args, props) + "\n"
        prefix = "" if src.text[:end].endswith("\n") or not src.text[:end] else "\n"
        plan.add(src.path, [(end, end, prefix + line)])
        plan.inserted[src.path] = [src.text[:end].count("\n") + 1 + (1 if prefix else 0)]
        return f"Insert {kind.title()}"

    def _figure_call(self, plan, data):
        fs = data.get("figureSite")
        src = plan.source(fs["file"])
        call = src.find_call(fs, "Figure")
        if call is None:
            raise EditError("can't find the Figure(...) call")
        return src, call

    def _op_figure(self, plan, data, a):
        """Figure settings: {props: {width, height, background, poster, landscape, margin}}. None takes width,
        height, poster, landscape or margin out of the call (a poster's size then comes from its preset)."""
        src, call = self._figure_call(plan, data)
        props = {k: (REMOVE if v is None and k in ("width", "height", "poster", "landscape", "margin") else v)
                 for k, v in a["props"].items()}
        if props.get("landscape") is False:
            props["landscape"] = REMOVE
        plan.add(src.path, srcedit.set_args(src, call, "Figure", props, "the figure"))
        return "Figure Size" if {"width", "height"} & set(a["props"]) else "Figure Settings"

    def _label_style(self, plan, data, props: dict):
        ls = data["labelStyle"]
        site = ls.get("site")
        if site:
            src = plan.source(site["file"])
            call = src.find_call(site, ("Labels", "Labels.journal"))
            if call is not None:
                method = srcedit.callee(call)
                plan.add(src.path, srcedit.set_args(src, call, method, props, "the labels"))
                return
        # no Labels(...) of its own: give the style (or the figure) one
        cur = {k: v for k, v in ls["props"].items()}
        changed = {**{k: v for k, v in cur.items() if k in props}, **props}
        st = data["style"].get("site")
        target = None
        if st:
            src = plan.source(st["file"])
            call = src.find_call(st, ("Style", "Style.journal"))
            if call is not None:
                target = (src, call, srcedit.callee(call))
        if target is None:
            src, call = self._figure_call(plan, data)
            target = (src, call, "Figure")
        src, call, method = target
        ref = srcedit.class_ref(src, "Labels")
        if ls.get("preset"):
            text = f'{ref}.journal({srcedit.fmt(ls["preset"])}' + "".join(
                f", {k}={srcedit.fmt(v)}" for k, v in changed.items()) + ")"
        else:
            defaults = {"format": "{}", "case": "lower", "numbering": "alpha", "font": None, "size": 8,
                        "weight": "bold", "style": "normal", "color": "#000000", "dx": 0.0, "dy": 0.0,
                        "order": "reading", "align": True}
            keep = {k: v for k, v in cur.items() if defaults.get(k, object()) != v}
            keep.update(props)
            text = f"{ref}(" + ", ".join(f"{k}={srcedit.fmt(v)}" for k, v in keep.items()) + ")"
        patches = srcedit.set_args(src, call, method, {"labels": Raw(text)}, "the style")
        plan.add(src.path, patches + srcedit.ensure_import(src, ref))

    def _op_labels(self, plan, data, a):
        """Label style: {props: {format, case, numbering, font, size, weight, style, color, dx, dy, order, align}}."""
        self._label_style(plan, data, a["props"])
        return "Label Style"

    def _op_style(self, plan, data, a):
        """Shared style: {props: {font, size, linewidth, despine, min_font, min_dpi}}."""
        st = data["style"].get("site")
        if st:
            src = plan.source(st["file"])
            call = src.find_call(st, ("Style", "Style.journal"))
            if call is not None:
                plan.add(src.path, srcedit.set_args(src, call, srcedit.callee(call), a["props"], "the style"))
                return "Style"
        src, call = self._figure_call(plan, data)
        ref = srcedit.class_ref(src, "Style")
        text = f"{ref}(" + ", ".join(f"{k}={srcedit.fmt(v)}" for k, v in a["props"].items()) + ")"
        plan.add(src.path, srcedit.set_args(src, call, "Figure", {"style": Raw(text)}, "the figure")
                 + srcedit.ensure_import(src, ref))
        return "Style"

    def _op_guide_add(self, plan, data, a):
        src = plan.source(data["file"])
        var = srcedit.figure_var(src)
        k = "x" if a.get("x") is not None else "y"
        end = len(src.text)
        prefix = "" if src.text.endswith("\n") else "\n"
        plan.add(src.path, [(end, end, prefix + f"{var}.guide({k}={srcedit.fmt(float(a[k]))})\n")])
        return "Add Guide"

    def _guide_call(self, plan, data, i):
        g = data["guides"][int(i)]
        src = plan.source(g["site"]["file"])
        call = src.find_call(g["site"], "guide")
        if call is None:
            raise EditError("can't find that guide's code")
        return src, call, g

    def _op_guide_move(self, plan, data, a):
        src, call, g = self._guide_call(plan, data, a["index"])
        k = "x" if g["x"] is not None else "y"
        plan.add(src.path, srcedit.set_args(src, call, "guide", {k: float(a["value"])}, "the guide"))
        return "Move Guide"

    def _op_guide_delete(self, plan, data, a):
        src, call, g = self._guide_call(plan, data, a["index"])
        st = src.statement(call)
        a0, b0 = src.stmt_span(st)
        plan.add(src.path, [(a0, b0, "")])
        return "Delete Guide"

    def _op_guide_clear(self, plan, data, a):
        """Remove every guide of the figure."""
        if not data["guides"]:
            raise EditError("the figure has no guides")
        spans = {}
        for i in range(len(data["guides"])):
            src, call, g = self._guide_call(plan, data, i)
            spans.setdefault(src.path, set()).add(src.stmt_span(src.statement(call)))
        for path, ss in spans.items():
            plan.add(path, [(a0, b0, "") for a0, b0 in sorted(ss)])
        return "Clear Guides"

    # --- clipboard ---

    def copy(self, a: dict) -> dict:
        """The code of elements, to paste into this or another figure: {figure, oids} -> {code: [statements]}.
        A layout brings the Panel(...) lines it uses."""
        data = self.render(a["figure"], a.get("pxPerMM"))
        plan = Plan()
        out, seen = [], set()
        for oid in self._top(data, [o for o in a["oids"] if not o.startswith("L")]):
            info = self._info(data, oid)
            if info.get("method") == "Table":
                return {"ok": False, "error": "a table is copied whole: place its file in a figure with "
                                              "Insert ▸ Figure…"}
            src, call = self._call(plan, data, info)
            st = src.statement(call)
            var = srcedit.figure_var(src)
            if info["kind"] == "layout":
                used = {n.id for n in ast.walk(st) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                for d in src.tree.body:
                    if isinstance(d, ast.Assign) and d.lineno < st.lineno and isinstance(d.value, ast.Call) \
                            and any(isinstance(t, ast.Name) and t.id in used for t in d.targets) and d.lineno not in seen:
                        seen.add(d.lineno)
                        out.append(src.text[slice(*src.stmt_span(d))].strip("\n"))
            if st.lineno in seen:
                continue
            seen.add(st.lineno)
            text = src.text[slice(*src.stmt_span(st))].strip("\n")
            out.append(re.sub(rf"\b{re.escape(var)}\.", "\u00a7FIG\u00a7.", text))
        return {"ok": True, "code": out}

    def paste(self, a: dict) -> dict:
        """Put copied code into a figure: {figure, code, dx?, dy?}. Names it assigns that the figure already has get
        a number; positions move by (dx, dy)."""
        name = a["figure"]
        with self.edit_lock:
            data = self.render(name, a.get("pxPerMM"))
            if data.get("error"):
                return {"ok": False, "error": "the figure's code doesn't run; fix it first"}
            plan = Plan()
            try:
                src, end, ind = self._insert_point(plan, data)
                var = srcedit.figure_var(src)
                taken = {n.id for n in ast.walk(src.tree) if isinstance(n, ast.Name)}
                block = "\n".join(a["code"]).replace("\u00a7FIG\u00a7.", f"{var}.")
                tree = ast.parse(block)
                renames = {}
                for st in tree.body:
                    if isinstance(st, ast.Assign):
                        for t in st.targets:
                            if isinstance(t, ast.Name) and t.id in taken:
                                k = 2
                                while f"{t.id}{k}" in taken:
                                    k += 1
                                renames[t.id] = f"{t.id}{k}"
                                taken.add(renames[t.id])
                for old, new in renames.items():
                    block = re.sub(rf"\b{re.escape(old)}\b", new, block)
                dx, dy = float(a.get("dx", 0)), float(a.get("dy", 0))
                if dx or dy:
                    one = Source(block)
                    patches = []
                    for st in one.tree.body:
                        call = st.value if isinstance(st, (ast.Expr, ast.Assign)) else None
                        if not isinstance(call, ast.Call):
                            continue
                        method = srcedit.callee(call)
                        kind = METHOD_KIND.get(method)
                        if not kind:
                            continue
                        upd = {}
                        for k in GEOMETRY.get(kind, ()):
                            present, v, lit = srcedit.arg_value(one, call, k, method)
                            if present and lit and isinstance(v, (int, float)):
                                upd[k] = v + (dy if k.startswith("y") else dx)
                        if kind == "path":
                            present, v, lit = srcedit.arg_value(one, call, "points", method)
                            if present and lit:
                                upd["points"] = [(x + dx, y + dy) for x, y in v]
                        if upd:
                            patches += srcedit.set_args(one, call, method, upd)
                    block = srcedit.apply(block, patches)
                missing = _undefined(block, src.text[:end])
                if missing:
                    raise EditError(f"the pasted code uses {', '.join(sorted(missing))}, which this figure doesn't have; "
                                    "copy those panels too")
                text = "".join(ind + ln + "\n" if ln.strip() else "\n" for ln in block.splitlines())
                plan.add(src.path, [(end, end, text)])
                first = src.text[:end].count("\n") + 1
                plan.inserted[src.path] = list(range(first, first + text.count("\n")))
                texts = plan.texts()
            except (EditError, SyntaxError, KeyError, ValueError) as exc:
                return {"ok": False, "error": str(exc)}
            self._write(texts, "Paste")
            return {"ok": True, **self._after(name, a.get("pxPerMM"), plan.inserted)}

    def paste_image(self, a: dict) -> dict:
        """A picture from the clipboard (PNG data in a file the app wrote): saved in images/ and placed."""
        src = Path(a["path"])
        (self.project / "images").mkdir(exist_ok=True)
        n = 1
        while (self.project / "images" / f"pasted-{n}.png").exists():
            n += 1
        dest = self.project / "images" / f"pasted-{n}.png"
        shutil.copy2(src, dest)
        return self.import_file({**a, "path": str(dest), "created": True})

    # --- files the editor adds ---

    def _mark_new_project(self):
        """The first figure or table of a folder opened empty makes it a project: give it its marker. (A folder that
        already had figures but no marker is asked about by the app instead.)"""
        if templates.read_marker(self.project) is None and not self.figures():
            templates.write_marker(self.project)

    def new_figure(self, a: dict) -> dict:
        """A new figure file: {name, width, height, journal?}, or a poster: {name, poster, landscape?}."""
        name = a.get("name") or ("poster" if a.get("poster") else "fig")
        base, n = name, 1
        while (self.project / f"{name}.py").exists():
            n += 1
            name = f"{base}{n}"
        if not name.isidentifier():
            return {"ok": False, "error": f"{name} can't be a figure name: use letters, digits and _"}
        self._mark_new_project()
        if a.get("poster"):
            try:
                text = templates.poster_py(a["poster"], bool(a.get("landscape")),
                                           [f["name"] for f in self.figures()])
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            if not (self.project / "style.py").exists():
                (self.project / "style.py").write_text(templates.style_py(a.get("journal")), encoding="utf-8")
            text = templates.figure_py(float(a.get("width", 183)), float(a.get("height", 120)))
        path = self.project / f"{name}.py"
        with self.edit_lock:
            path.write_text(text, encoding="utf-8")
            self.history.append({"label": "New Figure", "files": {str(path): (None, text.encode())}})
            self.redo.clear()
            self._sig = self.fingerprint()
        return {"ok": True, "name": name}

    def _own_data(self, name: str, text: str) -> list[str]:
        """The data files a figure or table keeps for itself: tables/<name>.csv (or .tsv) when its code reads it.
        A file another figure also reads isn't its own."""
        out = []
        for ext in (".csv", ".tsv"):
            rel = f"tables/{name}{ext}"
            if rel in text and (self.project / rel).exists():
                others = [p for p in self.project.glob("*.py") if p.stem != name
                          and rel in p.read_text(encoding="utf-8", errors="replace")]
                if not others:
                    out.append(rel)
        return out

    def copy_figure(self, a: dict) -> dict:
        """A whole figure or table to put on the pasteboard: {name} -> {ok, name, kind, text, data: {rel: text}}."""
        path = Path(self.figure_file(a["name"]))
        text = path.read_text(encoding="utf-8")
        data = {rel: (self.project / rel).read_text(encoding="utf-8", errors="replace")
                for rel in self._own_data(a["name"], text)}
        return {"ok": True, "name": a["name"], "kind": "table" if is_table_source(text) else "figure",
                "text": text, "data": data}

    def paste_figure(self, a: dict) -> dict:
        """A figure or table copied whole, as a new file of this project: {name, text, data?} -> {ok, name}. It is
        named <name>_copy (or <name> in another project, when that is free); its own data file is copied with it
        under the new name. One undo step."""
        base = re.sub(r"\W", "_", a.get("name") or "fig").strip("_") or "fig"
        if base[0].isdigit():
            base = "fig_" + base
        data = a.get("data") or {}
        taken = lambda n: (self.project / f"{n}.py").exists() or any(
            (self.project / "tables" / f"{n}{Path(r).suffix}").exists() for r in data)
        name, n = base, 1
        if taken(name):
            name = f"{base}_copy"
            while taken(name):
                n += 1
                name = f"{base}_copy{n}"
        text = a["text"]
        files = {}
        for rel, content in data.items():
            new_rel = f"tables/{name}{Path(rel).suffix}"
            text = text.replace(rel, new_rel)
            files[self.project / new_rel] = content
        path = self.project / f"{name}.py"
        files[path] = text
        with self.edit_lock:
            written = {}
            for p, content in files.items():
                p.parent.mkdir(exist_ok=True)
                p.write_text(content, encoding="utf-8")
                written[str(p)] = (None, p.read_bytes())
            self.history.append({"label": "Paste " + ("Table" if is_table_source(text) else "Figure"), "files": written})
            self.redo.clear()
            self._sig = self.fingerprint()
        return {"ok": True, "name": name}

    def delete_figure(self, a: dict) -> dict:
        """A figure or table's file deleted, with the data file it keeps for itself: {name} -> {ok, usedBy}. One undo
        step. `usedBy`: the figures that place it whole (fig.figure), which no longer run."""
        name = a["name"]
        path = Path(self.figure_file(name))
        text = path.read_text(encoding="utf-8", errors="replace")
        kind = "Table" if is_table_source(text) else "Figure"
        used = [p.stem for p in self.project.glob("*.py") if p.stem != name
                and f"{name}.py" in p.read_text(encoding="utf-8", errors="replace")]
        with self.edit_lock:
            written = {}
            for p in [path] + [self.project / rel for rel in self._own_data(name, text)]:
                written[str(p)] = (p.read_bytes(), None)
                p.unlink()
            self.history.append({"label": f"Delete {kind}", "files": written})
            self.redo.clear()
            self.renders.pop(name, None)
            if self.current == name:
                self.current = None
            self._sig = self.fingerprint()
        return {"ok": True, "usedBy": used}

    def new_panel(self, a: dict) -> dict:
        """A new panel file from the template, and the panel on the figure: {figure, name, x, y, w, h}."""
        name = a.get("name") or "panel"
        (self.project / "panels").mkdir(exist_ok=True)
        stem, n = name, 1
        while (self.project / "panels" / f"{stem}.py").exists():
            n += 1
            stem = f"{name}{n}"
        path = self.project / "panels" / f"{stem}.py"
        with self.edit_lock:
            path.write_text(templates.panel_py(stem), encoding="utf-8")
            res = self.edit({"figure": a["figure"], "op": "insert", "kind": "panel", "pxPerMM": a.get("pxPerMM"),
                             "props": {"source": f"panels/{stem}.py", "x": a.get("x", 5), "y": a.get("y", 5),
                                       "w": a.get("w", 60), "h": a.get("h", 45)}})
            if res.get("ok") and self.history:
                self.history[-1]["files"][str(path)] = (None, path.read_bytes())
            return {**res, "file": str(path)}

    def import_file(self, a: dict) -> dict:
        """A dropped or chosen file onto the figure: {figure, path, x, y, w?, label?, created?}. Pictures outside
        the project are copied into its images folder; a .py file becomes a panel. The copy, and the file itself when
        `created` (made just for this, as a BioCartoon icon or a pasted picture), are undone with the insertion."""
        src = Path(a["path"]).resolve()
        made = [src] if a.get("created") else []
        try:
            rel = self._bring_in(src, made)
        except EditError as exc:
            return {"ok": False, "error": str(exc)}
        res = self._import(a, src, rel, made)
        if not res.get("ok"):                          # nothing placed: don't leave the copy behind
            for p in made:
                p.unlink(missing_ok=True)
        return res

    def _import(self, a, src, rel, made) -> dict:
        x, y = float(a.get("x", 5)), float(a.get("y", 5))
        if src.suffix.lower() in TABLE_FILES:
            return self.edit({"figure": a["figure"], "op": "insert", "kind": "table", "pxPerMM": a.get("pxPerMM"),
                              "props": {"source": str(rel), "x": x, "y": y}, "_new_files": made})
        if src.suffix == ".py" and is_figure_source(src.read_text(encoding="utf-8", errors="replace")):
            return self._place_figure(a, rel, x, y, made)
        if src.suffix == ".py":
            text = src.read_text(encoding="utf-8", errors="replace")
            fn = "draw"
            if "def draw(" not in text:
                defs = [n.name for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)]
                if not defs:
                    return {"ok": False, "error": f"{src.name} has no function to draw a panel with"}
                fn = defs[0]
            props = {"source": str(rel), "x": x, "y": y, "w": float(a.get("w", 60)), "h": float(a.get("h", 45))}
            if fn != "draw":
                props["function"] = fn
            return self.edit({"figure": a["figure"], "op": "insert", "kind": "panel", "props": props,
                              "pxPerMM": a.get("pxPerMM"), "_new_files": made})
        props = {"source": str(rel), "x": x, "y": y, "w": float(a.get("w", 60))}
        if "label" in a:                                 # None: no panel letter (an icon)
            props["label"] = a["label"]
        return self.edit({"figure": a["figure"], "op": "insert", "kind": "image", "pxPerMM": a.get("pxPerMM"),
                          "props": props, "_new_files": made})

    def _place_figure(self, a, rel, x, y, made) -> dict:
        """Another figure of the project placed whole (fig.figure): at its own size when it fits, else as wide as
        the room to the right of (x, y)."""
        if rel.parent != Path(".") or rel.stem == a["figure"]:
            return {"ok": False, "error": f"{rel} can't be placed: a figure takes the project's other figure files"
                    if rel.stem != a["figure"] else "a figure can't be placed in itself"}
        outer = self.render(a["figure"], a.get("pxPerMM"))
        inner = self.render(rel.stem, 4.0)
        if inner.get("error"):
            return {"ok": False, "error": f"{rel} doesn't run; fix it first"}
        W, H = inner["size"]
        room = max(outer["size"][0] - x - 5, 20) if not outer.get("error") else W
        props = {"source": str(rel), "x": x, "y": y}
        if a.get("w"):
            props["w"] = float(a["w"])
        elif W > room:
            props["w"] = round(room, 1)
        return self.edit({"figure": a["figure"], "op": "insert", "kind": "figure", "props": props,
                          "pxPerMM": a.get("pxPerMM"), "_new_files": made})

    # --- panels from an SSH server ---

    def remote_list(self, a: dict) -> dict:
        """A folder on a server: {host, path?} -> {ok, host, path, entries: [{name, dir}]} (folders and .py files)."""
        try:
            return {"ok": True, **remote.list_dir(a["host"], a.get("path") or "")}
        except remote.RemoteError as exc:
            return {"ok": False, "error": str(exc)}

    def import_remote(self, a: dict) -> dict:
        """A panel's code from a server, placed on the figure: {figure, host, path, x, y}. It is copied into
        panels/ with a first line saying where from, so Update from Server can fetch it again."""
        host, path = a["host"], a["path"]
        name = Path(path).name
        if not name.endswith(".py"):
            return {"ok": False, "error": f"{name} isn't a Python file"}
        (self.project / "panels").mkdir(exist_ok=True)
        dest = self.project / "panels" / name
        n = 1
        while dest.exists():
            n += 1
            dest = self.project / "panels" / f"{Path(name).stem}-{n}.py"
        text = a.get("text")                          # fetched already (by the tunnel, on the Mac)
        if text is None:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    got = remote.fetch(host, path, Path(tmp) / name)
                except remote.RemoteError as exc:
                    return {"ok": False, "error": f"couldn't fetch {host}:{path}: {exc}"}
                text = got.read_text(encoding="utf-8", errors="replace")
        try:
            ast.parse(text)
        except SyntaxError as exc:
            return {"ok": False, "error": f"{name} isn't valid Python (line {exc.lineno}): {exc.msg}"}
        dest.write_text(remote.with_header(text, host, path), encoding="utf-8")
        res = self.import_file({**a, "path": str(dest), "created": True})
        if not res.get("ok"):
            dest.unlink(missing_ok=True)
        return {**res, "file": str(dest)}

    def update_remote(self, a: dict) -> dict:
        """Fetch a panel file again from the server it came from: {file, figure?} (one undo step)."""
        path = Path(a["file"])
        if not path.is_absolute():
            path = self.project / path
        where = remote.origin(path.read_text(encoding="utf-8", errors="replace")) if path.exists() else None
        if where is None:
            return {"ok": False, "error": f"{path.name} wasn't fetched from a server"}
        text = a.get("text")                          # fetched already (by the tunnel, on the Mac)
        if text is None:
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    got = remote.fetch(where[0], where[1], Path(tmp) / path.name)
                except remote.RemoteError as exc:
                    return {"ok": False, "error": f"couldn't fetch {where[0]}:{where[1]}: {exc}"}
                text = got.read_text(encoding="utf-8", errors="replace")
        return self.write_source({"file": str(path), "text": remote.with_header(text, *where), "figure": a.get("figure"),
                                  "pxPerMM": a.get("pxPerMM")})

    def export_table(self, a: dict) -> dict:
        """A table as a file people edit: {figure, oid?, path, format?, title?} -> {ok, path}. .docx (a Word table
        in the figure's font and the table's size), .xlsx, .csv or .tsv; with its header and decimals, and, for a
        Table, its title above and its notes below. Without `oid`, the figure's only table."""
        data = self.render(a["figure"])
        if data.get("error"):
            return {"ok": False, "error": "the figure's code doesn't run; fix it first"}
        try:
            path = tables.write_rendered(data, a["path"], a.get("format"), a.get("oid"), a.get("title"))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}

    def export_source_data(self, a: dict) -> dict:
        """The values plotted in each panel of a figure, one sheet per panel: {figure, path} -> {ok, path, sheets,
        panels}. See sourcedata.py."""
        with self.render_lock:
            res = self.worker.request({"cmd": "source_data", "project": str(self.project), "cache": str(self.cache),
                                       "file": self.figure_file(a["figure"]), "path": a["path"]}, timeout=600)
        return {"ok": True, **res}

    def new_table(self, a: dict) -> dict:
        """A table of the paper in a file of its own: {name, rows, cols, title?} -> {ok, name}. An m x n grid of
        empty cells with lines between them, kept in tables/<name>.csv, to type into; its page is sized to it."""
        name = re.sub(r"\W", "_", a.get("name") or "table").strip("_") or "table"
        if name[0].isdigit():
            name = "table_" + name
        base, n = name, 1
        while (self.project / f"{name}.py").exists() or (self.project / "tables" / f"{name}.csv").exists():
            n += 1
            name = f"{base}{n}"
        nr, nc = max(1, int(a.get("rows", 4))), max(1, int(a.get("cols", 3)))
        self._mark_new_project()
        rows = [["" for _ in range(nc)] for _ in range(nr)]
        (self.project / "tables").mkdir(exist_ok=True)
        csv_path = self.project / "tables" / f"{name}.csv"
        if not (self.project / "style.py").exists():
            (self.project / "style.py").write_text(templates.style_py(a.get("journal")), encoding="utf-8")
        text = templates.table_py(f"tables/{name}.csv", a.get("title"))
        path = self.project / f"{name}.py"
        with self.edit_lock:
            csv_path.write_text(tables.write_delimited(rows), encoding="utf-8")
            path.write_text(text, encoding="utf-8")
            self.history.append({"label": "New Table", "files": {str(path): (None, text.encode()),
                                                                 str(csv_path): (None, csv_path.read_bytes())}})
            self.redo.clear()
            self._sig = self.fingerprint()
        return {"ok": True, "name": name}

    def insert_table(self, a: dict) -> dict:
        """A table embedded in a figure: {figure, rows, cols, x?, y?} -> the edit's result. An m x n grid of empty
        cells, with lines between them, kept in tables/<figure>_table<N>.csv, to type into on the canvas; undo takes
        the file away with it."""
        nr, nc = max(1, int(a.get("rows", 4))), max(1, int(a.get("cols", 3)))
        (self.project / "tables").mkdir(exist_ok=True)
        n = 1
        while (self.project / "tables" / f"{a['figure']}_table{n}.csv").exists():
            n += 1
        csv_path = self.project / "tables" / f"{a['figure']}_table{n}.csv"
        csv_path.write_text(tables.write_delimited([["" for _ in range(nc)] for _ in range(nr)]), encoding="utf-8")
        res = self.edit({"figure": a["figure"], "op": "insert", "kind": "table", "pxPerMM": a.get("pxPerMM"),
                         "props": {"source": f"tables/{csv_path.name}", "x": float(a.get("x", 5)),
                                   "y": float(a.get("y", 5)), "grid": True}, "_new_files": [csv_path]})
        if not res.get("ok"):
            csv_path.unlink(missing_ok=True)
        return res

    def export(self, a: dict) -> dict:
        fmt = (a.get("format") or Path(a["path"]).suffix.lstrip(".")).lower()
        if fmt in ("docx", "xlsx", "csv", "tsv"):           # a table figure, as cells people can edit
            res = self.export_table({**a, "format": fmt, "oid": a.get("oid")})
            if not res.get("ok"):
                raise RuntimeError(res.get("error"))
            return {**res, "format": fmt, "resolution": {}, "warnings": []}
        with self.render_lock:
            return self.worker.request({"cmd": "export", "project": str(self.project), "cache": str(self.cache),
                                        "file": self.figure_file(a["figure"]), "path": a["path"],
                                        "format": a.get("format"), "dpi": a.get("dpi", 600),
                                        "dark": bool(a.get("dark")), "cmyk": bool(a.get("cmyk")),
                                        "outline": bool(a.get("outline")), "journal": a.get("journal"),
                                        "bleed": float(a.get("bleed") or 0), "marks": bool(a.get("marks"))}, timeout=600)


def _cell(c: str):
    """A typed cell as it goes into rows=: a number when it reads as one, else the text."""
    t = c.strip()
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t) if t and t.lower() not in ("nan", "inf", "-inf", "infinity") else c
    except ValueError:
        return c


def _is_table(data: dict) -> bool:
    """Whether a render is of a Table (a table of the paper in its own file) rather than a figure."""
    return (data.get("figureProps") or {}).get("kind") == "table"


def _natural(s: str):
    import re
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _undefined(block: str, before: str) -> set:
    """Names the block reads that neither it nor the code before it defines (nor Python or fignimble)."""
    import builtins
    import fignimble
    defined = set(dir(builtins)) | set(fignimble.__all__) | {"fignimble"}
    for tree in (ast.parse(before or "\n"), ast.parse(block)):
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                defined.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for al in n.names:
                    defined.add((al.asname or al.name).split(".")[0])
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                defined.add(n.name)
    return {n.id for n in ast.walk(ast.parse(block)) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
            and n.id not in defined}
