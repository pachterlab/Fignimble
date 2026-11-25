"""A project on an SSH server, edited in the app on a Mac.

    python -m fignimble.tunnel --host HOST --path FOLDER --mirror LOCAL_FOLDER [--python PYTHON]

The app starts this in place of fignimble.rpc and talks to it the same way (JSON lines, docs/protocol.md). It runs
the engine on the server over ssh, copying the engine there first (once per version, into
~/.cache/fignimble/engine/), so panel code runs where its data and packages are. The app works on a mirror of the
project in LOCAL_FOLDER, which this keeps in step both ways:

- the server sends the pictures it renders with each response (mirror.py), written into the mirror's .fignimble;
- paths in requests go from the mirror to the server, paths in responses come back;
- after an edit, or when the server says files changed, the files that changed there are fetched; files changed in
  the mirror (the code pane, an editor, a picture the app made) are sent to the server before the next request,
  and within a second anyway. When both sides changed a file, the server's version wins and the Mac's is kept in
  the conflicts folder beside the mirror;
- pictures to place and exports go across as files: uploaded to the server, and fetched to where the app asked.

ssh runs without asking anything (BatchMode): the app logs in first and leaves a shared connection open
(remote.ssh_command), or the server takes a key. When the connection drops this exits (status 3), so the app can say
so at once. FIGNIMBLE_SSH replaces the ssh command (the tests use a stand-in).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import queue
import shlex
import signal
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import Future
from pathlib import Path

from . import mirror, remote

PACKAGE = Path(__file__).resolve().parent
# answered from what the engine already has: no project files change, so nothing to fetch afterwards
READ_ONLY = {"state", "figure", "journals", "requirements", "copy", "export", "export_table", "remote_hosts",
             "remote_list", "manifest", "read_files"}
BATCH = 16 * 1024 * 1024                         # bytes of files fetched per request
NEEDS_ENGINE, NEEDS_PACKAGES = 97, 98
CONNECTION_LOST = 3                              # the exit status when the server went away
READY = "fignimble-remote-ready"
MISSING = "fignimble-missing"
# run by the server's Python: what the engine needs that it lacks (Python 3.6 syntax, so old ones can say so)
CHECK = ('import sys, importlib.util as u; m = [n for n in ("matplotlib", "pymupdf", "PIL") if u.find_spec(n) is None]'
         ' + (["Python 3.10"] if sys.version_info < (3, 10) else []); m and print("' + MISSING + ' " + " ".join(m));'
         ' sys.exit(1 if m else 0)')


def log(*a):
    print("[tunnel]", *a, file=sys.stderr, flush=True)


class TunnelError(Exception):
    pass


def engine_version() -> str:
    """A hash of the engine's files: the folder on the server it goes into."""
    h = hashlib.sha256()
    for p in sorted(PACKAGE.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            h.update(p.relative_to(PACKAGE).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()[:16]


def engine_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(str(PACKAGE), arcname="fignimble",
                filter=lambda t: None if "__pycache__" in t.name or t.name.endswith(".pyc") else t)
    return buf.getvalue()


def ssh_args(host: str, script: str) -> list[str]:
    """ssh running a POSIX shell script on `host` (whatever the login shell there is)."""
    return (remote.ssh_command() + ["-T", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=4",
                                    "-o", "Compression=yes", host, "--", "sh -c " + shlex.quote(script)])


def _sep(path: str) -> str:
    """The separator of a path: the server's are always "/", a Windows mirror's "\\"."""
    return "\\" if os.sep == "\\" and ("\\" in path or path[1:2] == ":") else "/"


def _map(v, a: str, b: str):
    """`v` with paths under folder `a` moved under folder `b` (in strings, lists, dict keys and values), their
    separators made `b`'s (a Windows mirror and a POSIX server)."""
    if isinstance(v, str):
        for s in dict.fromkeys(("/", os.sep)):
            if v == a or v.startswith(a + s):
                rest = v[len(a):]
                return b + (rest.replace(s, _sep(b)) if s != _sep(b) else rest)
        return v
    if isinstance(v, dict):
        return {_map(k, a, b): _map(x, a, b) for k, x in v.items()}
    if isinstance(v, list):
        return [_map(x, a, b) for x in v]
    return v


def _write_atomic(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tunnel-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


class Tunnel:
    def __init__(self, host: str, path: str, local: Path, python: str = "python3"):
        remote._check_host(host)
        self.host, self.path, self.python = host, path, python or "python3"
        local.mkdir(parents=True, exist_ok=True)
        self.L = str(local.resolve())
        self.R: str | None = None                 # the project's folder on the server, once open
        self.open = False
        self.state_file = Path(self.L + ".sync.json")
        self.conflicts = Path(self.L + ".conflicts")
        try:
            self.state: dict = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            self.state = {}                         # relative path -> {"r": server [mtime_ns, size], "l": mirror's}
        self.proc: subprocess.Popen | None = None
        self.out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr
        self.out_lock, self.lock, self.sync_lock = threading.Lock(), threading.Lock(), threading.RLock()
        self.server_lock = threading.Lock()
        self.pending: dict[str, Future] = {}
        self.app_cmds: dict = {}                    # app request id -> (cmd, context)
        self.outbox: queue.Queue = queue.Queue()
        self.n = 0
        self.alive = True
        self.closing = False                        # the app ended the session (so the server going is expected)
        self.needs: dict | None = None              # what the server's Python lacks, when that stopped the start
        self.deliverer: threading.Thread | None = None
        self.remote_size: dict[str, int] = {}       # relative path -> size on the server, from the last manifest

    # --- starting the engine on the server ---

    def launch_script(self, version: str) -> str:
        d = f'"$HOME/.cache/fignimble/engine/{version}"'
        py = remote._quote(self.python)
        return (f'D={d}; [ -f "$D/.ok" ] || exit {NEEDS_ENGINE}; '
                f"{py} -c {shlex.quote(CHECK)} || exit {NEEDS_PACKAGES}; "
                f"echo '{{\"event\": \"{READY}\"}}'; "
                f'cd "$D" && FIGNIMBLE_REMOTE=1 MPLBACKEND=Agg PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 '
                f'PYTHONPATH="$D" exec {py} -u -m fignimble.rpc')

    def install_engine(self, version: str):
        log(f"copying the engine to {self.host} (~/.cache/fignimble/engine/{version})")
        d = f'"$HOME/.cache/fignimble/engine/{version}"'
        script = (f'D={d}; rm -rf "$D.part" && mkdir -p "$D.part" && tar xzf - -C "$D.part" && rm -rf "$D" '
                  f'&& mv "$D.part" "$D" && touch "$D/.ok"')
        try:
            r = subprocess.run(ssh_args(self.host, script), input=engine_tarball(), capture_output=True, timeout=300)
        except subprocess.TimeoutExpired as exc:
            raise TunnelError(f"{self.host} didn't take the engine in time") from exc
        if r.returncode != 0:
            raise TunnelError(f"couldn't copy the engine to {self.host}: "
                              + (r.stderr.decode(errors="replace").strip().splitlines() or ["?"])[-1])

    def start(self):
        version = engine_version()
        for attempt in (1, 2):
            log(f"starting the engine on {self.host} with {self.python}")
            p = subprocess.Popen(ssh_args(self.host, self.launch_script(version)), stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
            first = p.stdout.readline()
            if READY.encode() in first:
                self.proc = p
                return
            code = p.wait()
            if code == NEEDS_ENGINE and attempt == 1:
                self.install_engine(version)
                continue
            if code == NEEDS_PACKAGES:
                said = first.decode(errors="replace").strip()
                if not said.startswith(MISSING):
                    raise TunnelError(f"couldn't run {self.python} on {self.host}; its message is above. Choose the "
                                      "path of a Python there (3.10 or newer).")
                missing = said.split()[1:]
                pip = [{"PIL": "pillow"}.get(n, n) for n in missing if n != "Python 3.10"]
                self.needs = {"host": self.host, "python": self.python, "missing": missing,
                              "pip": [] if "Python 3.10" in missing else pip}
                if "Python 3.10" in missing:
                    raise TunnelError(f"{self.python} on {self.host} is older than Python 3.10. Choose the Python of "
                                      "a newer environment there.")
                raise TunnelError(f"{self.python} on {self.host} doesn't have {', '.join(pip)}. Install "
                                  f"{'it' if len(pip) == 1 else 'them'} there ({self.python} -m pip install "
                                  f"{' '.join(pip)}), or choose the Python of an environment that has the engine's "
                                  "packages (matplotlib, pymupdf and pillow).")
            if code == 255:
                raise TunnelError(f"ssh couldn't connect to {self.host}; its message is above.")
            raise TunnelError(f"the engine on {self.host} didn't start (exit status {code}); its messages are above")
        raise TunnelError("the engine didn't start")

    # --- talking ---

    def emit(self, msg: dict):
        with self.out_lock:
            self.out.write(json.dumps(msg) + "\n")

    def to_server(self, msg: dict):
        line = (json.dumps(msg) + "\n").encode()
        with self.server_lock:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, AttributeError) as exc:
                raise TunnelError(f"the connection to {self.host} is gone") from exc

    def call(self, cmd: str, timeout: float = 900, **a) -> dict:
        """A request of the tunnel's own to the engine on the server, and its result."""
        fut: Future = Future()
        with self.lock:
            self.n += 1
            mid = f"~{self.n}"
            self.pending[mid] = fut
        self.to_server({**a, "id": mid, "cmd": cmd})
        msg = fut.result(timeout=timeout)
        if "error" in msg:
            raise TunnelError(msg["error"])
        return msg.get("result") or {}

    def read_server(self):
        """The engine's answers and events: pictures written at once, the tunnel's own answers handed back, the
        rest queued for the app in order."""
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            mid = msg.get("id")
            with self.lock:
                ctx = self.app_cmds.get(mid) if not isinstance(mid, str) else None
            if ctx and ctx[0] == "open" and isinstance(msg.get("result"), dict) and msg["result"].get("project"):
                self.R = msg["result"]["project"]
                self.open = True
            self.write_pictures(msg.pop("files", None) or {})
            if isinstance(mid, str) and mid.startswith("~"):
                with self.lock:
                    fut = self.pending.pop(mid, None)
                if fut:
                    fut.set_result(msg)
            else:
                self.outbox.put(msg)
        self.alive = False
        with self.lock:
            waiting, self.pending = self.pending, {}
        for fut in waiting.values():
            fut.set_result({"error": f"the connection to {self.host} closed"})
        self.outbox.put(None)
        if not self.closing:
            # the server went away (the network, a sleeping Mac, the server restarting): say so now rather than at
            # the app's next request. Edits not sent yet stay in the mirror and go across when it reconnects
            code = self.proc.wait()
            log(f"the connection to {self.host} closed (ssh exit status {code})")
            if self.deliverer:
                self.deliverer.join(timeout=10)
            os._exit(CONNECTION_LOST)

    def write_pictures(self, files: dict):
        for path, data in files.items():
            if self.R and path.startswith(self.R + "/"):
                try:
                    _write_atomic(Path(self.L + path[len(self.R):]), base64.b64decode(data))
                except OSError as exc:
                    log("couldn't write", path, exc)

    def deliver(self):
        """Responses and events to the app, after fetching what they changed."""
        while (msg := self.outbox.get()) is not None:
            try:
                if "event" in msg:
                    if msg["event"] == "changed":
                        self.pull()
                    self.emit(msg)
                    continue
                with self.lock:
                    cmd, ctx = self.app_cmds.pop(msg.get("id"), (None, None))
                res = msg.get("result")
                if res is not None and self.R:
                    if cmd == "open" or cmd not in READ_ONLY:
                        self.pull()
                    if cmd in ("export", "export_table") and ctx and isinstance(res, dict) and res.get("path"):
                        got = self.call("read_files", paths=[res["path"]])["files"].get(res["path"])
                        if got:
                            _write_atomic(Path(ctx), base64.b64decode(got["data"]))
                            res["path"] = ctx
                        else:
                            msg = {"id": msg["id"], "error": f"couldn't fetch the export from {self.host}"}
                    if "result" in msg:
                        msg["result"] = _map(res, self.R, self.L)
                if cmd == "close":
                    self.open = False
                self.emit(msg)
            except Exception as exc:                  # noqa: BLE001
                log("delivering", exc)
                if "id" in msg:
                    self.emit({"id": msg["id"], "error": str(exc)})

    # --- the mirror ---

    def save_state(self):
        try:
            self.state_file.write_text(json.dumps(self.state))
        except OSError as exc:
            log("couldn't save the mirror's state", exc)

    def _lstat(self, rel: str) -> list[int] | None:
        try:
            st = os.stat(os.path.join(self.L, rel))
            return [st.st_mtime_ns, st.st_size]
        except OSError:
            return None

    def _keep_conflict(self, rel: str):
        src = Path(self.L) / rel
        if src.exists():
            dest = self.conflicts / time.strftime("%Y%m%d-%H%M%S") / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            log(f"{rel} changed on {self.host} and here: the server's version wins; this Mac's is in {dest}")

    def _fetch(self, rels: list[str]) -> set[str]:
        """Fetch files from the server into the mirror -> the ones fetched."""
        done, batch, size = set(), [], 0

        def go(b):
            got = self.call("read_files", paths=b)["files"]
            for rel, f in got.items():
                if f is None:
                    continue
                _write_atomic(Path(self.L) / rel, base64.b64decode(f["data"]))
                self.state[rel] = {"r": f["stat"], "l": self._lstat(rel)}
                done.add(rel)
        for rel in rels:
            n = self.remote_size.get(rel, 0)
            if batch and size + n > BATCH:
                go(batch)
                batch, size = [], 0
            batch.append(rel)
            size += n
        if batch:
            go(batch)
        return done

    def push(self):
        """Send the files changed in the mirror to the server."""
        with self.sync_lock:
            if not (self.open and self.alive):
                return
            here = mirror.scan(Path(self.L))
            changed = False
            for rel, ls in here.items():
                s = self.state.get(rel)
                if s and s["l"] == ls:
                    continue
                changed = True
                data = base64.b64encode((Path(self.L) / rel).read_bytes()).decode()
                r = self.call("write_file", path=rel, data=data, expect=s["r"] if s else None)
                if r.get("ok"):
                    self.state[rel] = {"r": r["stat"], "l": ls}
                elif r.get("stat") is None:            # gone from the server since: this Mac's copy comes back
                    r = self.call("write_file", path=rel, data=data, expect=None)
                    if r.get("ok"):
                        self.state[rel] = {"r": r["stat"], "l": ls}
                else:                                  # changed there too: the server's wins
                    self._keep_conflict(rel)
                    self.remote_size[rel] = r["stat"][1]
                    self._fetch([rel])
            for rel in [r for r in self.state if r not in here]:
                changed = True
                r = self.call("delete_file", path=rel, expect=self.state[rel]["r"])
                del self.state[rel]                    # changed there since: the next pull fetches it again
                if not r.get("ok"):
                    log(f"{rel} was deleted here but changed on {self.host}: kept")
            if changed:
                self.save_state()

    def pull(self):
        """Fetch the files changed on the server (after sending this Mac's first)."""
        with self.sync_lock:
            if not (self.open and self.alive):
                return
            self.push()
            there = self.call("manifest")["files"]
            self.remote_size = {rel: s[1] for rel, s in there.items()}
            need = [rel for rel, rs in there.items() if (self.state.get(rel) or {}).get("r") != rs]
            changed = bool(need)
            self._fetch(need)
            for rel in [r for r in self.state if r not in there]:
                changed = True
                s = self.state.pop(rel)
                if self._lstat(rel) == s["l"]:          # deleted on the server and not changed here
                    (Path(self.L) / rel).unlink(missing_ok=True)
            if changed:
                self.save_state()

    def poll(self):
        """Edits made in the mirror (an editor, the app) go to the server within a second."""
        while self.alive:
            time.sleep(1.0)
            try:
                self.push()
            except Exception as exc:                  # noqa: BLE001
                log("sending changes", exc)

    # --- requests from the app ---

    def local_path(self, p) -> bool:
        return isinstance(p, str) and os.path.isabs(p) and not (p == self.L or p.startswith(self.L + os.sep)
                                                                or p.startswith(self.L + "/"))

    def upload(self, path: str) -> str:
        data = base64.b64encode(Path(path).read_bytes()).decode()
        return self.call("upload", name=Path(path).name, data=data)["path"]

    def request(self, req: dict):
        cmd, mid = req.get("cmd"), req.get("id")
        if cmd == "remote_hosts":                       # this Mac's ~/.ssh/config, not the server's
            self.emit({"id": mid, "result": {"hosts": remote.hosts()}})
            return
        if cmd == "remote_list":
            try:
                res = {"ok": True, **remote.list_dir(req["host"], req.get("path") or "")}
            except remote.RemoteError as exc:
                res = {"ok": False, "error": str(exc)}
            self.emit({"id": mid, "result": res})
            return
        if cmd == "new_project":
            self.emit({"id": mid, "error": "a new project can't be made in a project on a server"})
            return
        ctx = None
        if cmd == "open":
            req["project"] = self.path
        else:
            self.push()
            if self.R:
                req = _map(req, self.L, self.R)
            if cmd in ("import_file", "paste_image") or (cmd == "edit" and req.get("op") == "replace_source"):
                if self.local_path(req.get("path")) and os.path.isfile(req["path"]):
                    req["path"] = self.upload(req["path"])
            elif cmd in ("export", "export_table"):
                ctx = req["path"]                       # where the app wants it
                req["path"] = self.call("staging_path", name=Path(ctx).name)["path"]
            elif cmd in ("import_remote", "update_remote"):
                # the panel's server is reached from this Mac, as the dialog listed it
                if cmd == "import_remote":
                    host, path = req["host"], req["path"]
                else:
                    f = Path(_map(req["file"], self.R, self.L) if os.path.isabs(req["file"]) else Path(self.L) / req["file"])
                    where = remote.origin(f.read_text(encoding="utf-8", errors="replace")) if f.exists() else None
                    if where is None:
                        self.emit({"id": mid, "result": {"ok": False, "error": f"{f.name} wasn't fetched from a server"}})
                        return
                    host, path = where
                try:
                    data = remote._run(remote.ssh_command() + [host, "--", f"cat -- {remote._quote(path)}"],
                                       timeout=120)
                except remote.RemoteError as exc:
                    self.emit({"id": mid, "result": {"ok": False, "error": f"couldn't fetch {host}:{path}: {exc}"}})
                    return
                req["text"] = data.decode("utf-8", errors="replace")
        with self.lock:
            self.app_cmds[mid] = (cmd, ctx)
        self.to_server(req)

    def run(self) -> int:
        try:
            self.start()
        except TunnelError as exc:
            # answered to the app's requests (its `open`) until it closes this, with what the Python lacks first
            log(exc)
            if self.needs:
                self.emit({"event": "needs_packages", **self.needs})
            for line in sys.stdin:
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                self.emit({"id": req.get("id"), "error": str(exc)})
            return 1
        self.deliverer = deliverer = threading.Thread(target=self.deliver, daemon=True)
        threading.Thread(target=self.read_server, daemon=True).start()
        deliverer.start()
        threading.Thread(target=self.poll, daemon=True).start()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            try:
                self.request(req)
            except Exception as exc:                  # noqa: BLE001
                log(req.get("cmd"), exc)
                self.emit({"id": req.get("id"), "error": str(exc)})
            if not self.alive:
                break
        self.closing = True
        try:                                            # the app is done: last edits across, then the engine ends
            self.push()
        except Exception as exc:                      # noqa: BLE001
            log("sending the last changes", exc)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        deliverer.join(timeout=30)
        try:
            return self.proc.wait(timeout=30) or 0
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fignimble.tunnel", description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True)
    ap.add_argument("--path", required=True, help="the project's folder on the server")
    ap.add_argument("--mirror", required=True, help="the local folder the app works on (RemoteProject.mirror)")
    ap.add_argument("--python", default="python3", help="the Python on the server")
    a = ap.parse_args(argv)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    t = Tunnel(a.host, a.path, Path(a.mirror), a.python)
    try:
        sys.exit(t.run())
    finally:
        if t.proc and t.proc.poll() is None:
            t.proc.kill()


if __name__ == "__main__":
    main()
