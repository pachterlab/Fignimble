"""The engine as a child process (`python -m fignimble.rpc`, or `fignimble.tunnel` for a project on an SSH server),
talked to in JSON lines as docs/protocol.md describes. Requests go one at a time, in order, as the macOS app sends
them; answers and events arrive on the Qt main thread."""

from __future__ import annotations

import json
import os
from collections import deque
from typing import Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import locate


class EngineClient(QObject):
    event = Signal(dict)            # {"event": "changed" | "rendered" | "needs_packages", ...}
    exited = Signal(int)            # the engine ended by itself (crashed, or couldn't start)
    busy = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.python: str | None = None
        self.buffer = b""
        self.next_id = 1
        self.waiting: dict[int, tuple] = {}
        self.queue: deque = deque()
        self.in_flight: int | None = None
        self.stopping = False

    # --- the process ---

    def start(self, python: str, tunnel: dict | None = None):
        """Start the engine with `python`; `tunnel`: {host, path, mirror, python} for a project on a server."""
        self.stop()
        self.python = python
        p = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        old = env.value("PYTHONPATH", "")
        env.insert("PYTHONPATH", locate.engine_dir() + (os.pathsep + old if old else ""))
        env.insert("FIGNIMBLE_PYTHON", python)
        env.insert("MPLBACKEND", "Agg")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONUTF8", "1")
        bins = [b for b in (locate.bin_dir(),) if b]
        if bins:
            env.insert("PATH", os.pathsep.join(bins + [env.value("PATH", "")]))
        p.setProcessEnvironment(env)
        p.setStandardErrorFile(str(locate.log_path()), QProcess.OpenModeFlag.Append)
        p.readyReadStandardOutput.connect(self._read)
        p.finished.connect(self._finished)
        p.errorOccurred.connect(self._error)
        args = ["-u", "-m", "fignimble.rpc"]
        if tunnel:
            args = ["-u", "-m", "fignimble.tunnel", "--host", tunnel["host"], "--path", tunnel["path"],
                    "--mirror", tunnel["mirror"], "--python", tunnel.get("python") or "python3"]
        locate.note(f"engine {python} {' '.join(args[2:])}")
        self.stopping = False
        self.buffer = b""
        p.start(python, args)
        self.proc = p

    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def stop(self, wait_ms: int = 3000):
        """End the engine: its input first, so it closes the project (and a tunnel sends its last changes)."""
        p, self.proc = self.proc, None
        self._fail_all("The Fignimble engine stopped")
        if p is None:
            return
        self.stopping = True
        try:
            p.finished.disconnect(self._finished)
        except (RuntimeError, TypeError):
            pass
        if p.state() != QProcess.ProcessState.NotRunning:
            p.closeWriteChannel()
            if not p.waitForFinished(wait_ms):
                p.kill()
                p.waitForFinished(1000)

    def _finished(self, code, _status=None):
        self.proc = None
        self._fail_all("The Fignimble engine stopped")
        if not self.stopping:
            self.exited.emit(int(code))

    def _error(self, err):
        if err == QProcess.ProcessError.FailedToStart and not self.stopping:
            self.proc = None
            self._fail_all(f"Couldn't start {self.python}")
            self.exited.emit(-1)

    def _fail_all(self, why: str):
        waiting, self.waiting = self.waiting, {}
        queued, self.queue = list(self.queue), deque()
        self.in_flight = None
        self.busy.emit(False)
        for _, on_err in [w for w in waiting.values()] + [(q[2], q[3]) for q in queued]:
            if on_err:
                on_err(why)

    # --- requests ---

    def call(self, cmd: str, args: dict | None = None, on_result: Callable | None = None,
             on_error: Callable | None = None):
        """Send `cmd` after the requests before it have their answers; `on_result(result)` or `on_error(text)`."""
        self.queue.append((cmd, dict(args or {}), on_result, on_error))
        self._pump()

    def _pump(self):
        if self.in_flight is not None or not self.queue:
            return
        cmd, args, on_result, on_error = self.queue.popleft()
        if not self.running():
            if on_error:
                on_error("The Fignimble engine isn't running")
            self._pump()
            return
        i = self.next_id
        self.next_id += 1
        self.waiting[i] = (on_result, on_error)
        self.in_flight = i
        self.busy.emit(True)
        self.proc.write((json.dumps({**args, "id": i, "cmd": cmd}) + "\n").encode("utf-8"))

    def _read(self):
        if self.proc is None:
            return
        self.buffer += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                msg = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if "event" in msg and "id" not in msg:
                self.event.emit(msg)
                continue
            got = self.waiting.pop(msg.get("id"), None)
            if msg.get("id") == self.in_flight:
                self.in_flight = None
                self.busy.emit(False)
            if got:
                on_result, on_error = got
                if "error" in msg:
                    if on_error:
                        on_error(str(msg["error"]))
                elif on_result:
                    on_result(msg.get("result") if msg.get("result") is not None else {})
            self._pump()
