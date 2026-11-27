"""JSON lines over stdin/stdout, used by the Fignimble app (the protocol is Manimble's; see docs/protocol.md).

Request : {"id": 1, "cmd": "figure", ...args}
Response: {"id": 1, "result": {...}}  or  {"id": 1, "error": "..."}
Event   : {"event": "changed"}        (pushed, no id)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True          # nothing written into the app bundle, whose signature it would break

from . import mirror, remote, templates
from .core import Session

_out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
sys.stdout = sys.stderr
_wlock = threading.Lock()


def send(obj):
    line = json.dumps(obj)
    with _wlock:
        _out.write(line + "\n")


def main():
    python = os.environ.get("FIGNIMBLE_PYTHON") or sys.executable
    app = {"s": None}
    far = mirror.Remote() if mirror.enabled() else None      # on a server, for the tunnel (mirror.py)

    def need() -> Session:
        if app["s"] is None:
            raise RuntimeError("no project open")
        return app["s"]

    def open_project(a):
        old = app["s"]
        if old:
            old.close()
        where = os.path.expanduser(a["project"])
        if far and not os.path.isabs(where):           # a server folder as typed: from the home folder
            where = os.path.join(os.path.expanduser("~"), where)
        s = Session(str(templates.project_root(where)), python)
        s.on_event = send
        app["s"] = s
        return {"ok": True, "project": str(s.project), **templates.marker_state(s.project)}

    def add_marker(a):
        p = templates.write_marker(need().project)
        return {"ok": True, "path": str(p)}

    def requirements(a):
        from . import requirements as reqs
        return reqs.missing(str(app["s"].project) if app["s"] else a.get("project"))

    def install(a):
        from . import requirements as reqs
        res = reqs.install(list(a.get("requirements") or []))
        if res["ok"] and app["s"]:
            app["s"].restart_renderer()
        return res

    def remote_list(a):
        try:
            return {"ok": True, **remote.list_dir(a["host"], a.get("path") or "")}
        except remote.RemoteError as exc:
            return {"ok": False, "error": str(exc)}

    def project():
        return app["s"].project if app["s"] else None

    def close(a):
        if app["s"]:
            app["s"].close()
            app["s"] = None
        return {"ok": True}

    handlers = {
        "open": open_project,
        "close": close,
        "new_project": lambda a: templates.new_project(a["dir"], a.get("journal")),
        "add_marker": add_marker,
        "requirements": requirements,
        "install": install,
        "state": lambda a: need().api_state(),
        "figure": lambda a: need().api_figure(a),
        "edit": lambda a: need().edit(a),
        "write_source": lambda a: need().write_source(a),
        "undo": lambda a: need().undo(a),
        "undo_to": lambda a: need().undo_to(a),
        "copy": lambda a: need().copy(a),
        "paste": lambda a: need().paste(a),
        "paste_image": lambda a: need().paste_image(a),
        "redo": lambda a: need().redo_(a),
        "export": lambda a: need().export(a),
        "export_table": lambda a: need().export_table(a),
        "export_source_data": lambda a: need().export_source_data(a),
        "record": lambda a: need().record_now(a),
        "repro_check": lambda a: need().repro_check(a),
        "tags": lambda a: need().tags(a),
        "tag": lambda a: need().tag(a),
        "diff": lambda a: need().diff(a),
        "remote_hosts": lambda a: {"hosts": __import__("fignimble.remote", fromlist=["hosts"]).hosts()},
        "remote_list": remote_list,
        "import_remote": lambda a: need().import_remote(a),
        "update_remote": lambda a: need().update_remote(a),
        "new_figure": lambda a: need().new_figure(a),
        "new_panel": lambda a: need().new_panel(a),
        "copy_figure": lambda a: need().copy_figure(a),
        "paste_figure": lambda a: need().paste_figure(a),
        "delete_figure": lambda a: need().delete_figure(a),
        "new_table": lambda a: need().new_table(a),
        "insert_table": lambda a: need().insert_table(a),
        "import_file": lambda a: need().import_file(a),
        # in their order (by publisher), which a JSON object doesn't keep for the app
        "journals": lambda a: {k: {**j, "rank": i} for i, (k, j) in
                               enumerate(__import__("fignimble.journals", fromlist=["JOURNALS"]).JOURNALS.items())},
        "posters": lambda a: {k: {**p, "size": list(p["size"]), "rank": i} for i, (k, p) in
                              enumerate(__import__("fignimble.journals", fromlist=["POSTERS"]).POSTERS.items())},
        # in the order offered, with their sizes as hung
        "poster_templates": lambda a: {"templates": [
            {**t, "size": list(__import__("fignimble.journals", fromlist=["poster_size"]).poster_size(t["poster"], t["landscape"]))}
            for t in __import__("fignimble.journals", fromlist=["POSTER_TEMPLATES"]).POSTER_TEMPLATES]},
    }
    if far:
        handlers.update({
            "manifest": lambda a: mirror.manifest(need().project),
            "read_files": lambda a: far.read_files(project(), a),
            "write_file": lambda a: far.write_file(need().project, a),
            "delete_file": lambda a: far.delete_file(need().project, a),
            "upload": far.upload,
            "staging_path": far.staging_path,
        })
    pool = ThreadPoolExecutor(max_workers=6)

    def run(req):
        try:
            res = handlers[req["cmd"]](req)
            msg = {"id": req["id"], "result": res}
            if far:
                msg["files"] = far.attach(res, app["s"].cache if app["s"] else None)
            send(msg)
        except BaseException as exc:  # noqa: BLE001
            traceback.print_exc()
            send({"id": req.get("id"), "error": str(exc)})

    for line in sys.stdin:
        line = line.strip()
        if line:
            req = json.loads(line)
            if req.get("cmd") in ("open", "close"):     # order matters: finish before later requests
                run(req)
            else:
                pool.submit(run, req)
    if app["s"]:
        app["s"].close()
    if far:
        far.close()


if __name__ == "__main__":
    main()
