"""The render worker: a long-lived process that runs figure code, so matplotlib is imported once.

JSON lines on stdin/stdout: {"id", "cmd": "render"|"export"|"source_data"|"record"|"repro_check"|"tag"|"diff"|"ping", ...} -> {"id", "result"} or {"id", "error"}.
User code that prints goes to stderr (the engine's log), never into the protocol.
"""

from __future__ import annotations

import json
import os
import sys
import traceback


def main():
    out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr
    from .render import Renderer
    renderers: dict[tuple, Renderer] = {}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        try:
            cmd = req["cmd"]
            if cmd == "ping":
                res = {"ok": True}
            else:
                key = (req["project"], req.get("cache"))
                r = renderers.get(key)
                if r is None:
                    r = renderers[key] = Renderer(req["project"], req.get("cache"))
                if cmd == "render":
                    res = r.render(req["file"], px_per_mm=req.get("pxPerMM", 12.0), dark=bool(req.get("dark")))
                elif cmd == "export":
                    res = r.export(req["file"], req["path"], req.get("format"), dpi=int(req.get("dpi", 600)),
                                   dark=bool(req.get("dark")), cmyk=bool(req.get("cmyk")),
                                   outline=bool(req.get("outline")), journal=req.get("journal"),
                                   bleed=float(req.get("bleed") or 0), marks=bool(req.get("marks")))
                elif cmd == "source_data":
                    from . import sourcedata
                    res = sourcedata.export(r, req["file"], req["path"])
                elif cmd == "record":
                    from . import repro
                    res = repro.record(r, force=bool(req.get("force")))
                elif cmd == "repro_check":
                    from . import repro
                    res = repro.check(r.project, r)
                elif cmd == "tag":
                    from . import repro
                    res = repro.tag(r.project, req["name"], req.get("message"), bool(req.get("force")), renderer=r)
                elif cmd == "diff":
                    from . import repro
                    res = repro.diff(r.project, req.get("a"), req.get("b"), renderer=r)
                else:
                    raise ValueError(f"unknown worker command {cmd}")
            out.write(json.dumps({"id": req.get("id"), "result": res}) + "\n")
        except BaseException as exc:                       # noqa: BLE001
            traceback.print_exc()
            out.write(json.dumps({"id": req.get("id"), "error": f"{type(exc).__name__}: {exc}"}) + "\n")


if __name__ == "__main__":
    main()
