"""Panels from an SSH server: list a folder there and fetch a panel's code into the project.

ssh runs here without asking anything (BatchMode). The app logs in first (SSHLogin.swift), showing what ssh asks
(a password, a code) in a dialog, and leaves a shared connection open in FIGNIMBLE_SSH_SHARE that these commands
use; without it the server must take your key or an ssh-agent's. Hosts come from ~/.ssh/config.
FIGNIMBLE_SSH replaces the command (the tests use a stand-in).
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
HEADER = "# Fetched from {host}:{path} by Fignimble (right-click the panel > Update from Server fetches it again)\n"


class RemoteError(Exception):
    pass


def _ssh():
    return shlex.split(os.environ.get("FIGNIMBLE_SSH", "ssh"))


def ssh_command() -> list[str]:
    """ssh and its options, through the app's shared connection to the server when there is one."""
    share = os.environ.get("FIGNIMBLE_SSH_SHARE") if os.name != "nt" else None    # Windows' ssh has no ControlMaster
    opts = ["-o", f"ControlPath={share}/%C", "-o", "ControlMaster=auto", "-o", "ControlPersist=4h"] if share else []
    return _ssh() + OPTIONS + opts


def hosts() -> list[str]:
    """The hosts named in ~/.ssh/config (and the files it includes), without wildcards."""
    out, seen = [], set()

    def read(path: Path, depth=0):
        if depth > 4 or not path.exists():
            return
        for line in path.read_text(errors="replace").splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            key, val = parts[0].lower(), parts[1]
            if key == "host":
                for h in val.split():
                    if not any(c in h for c in "*?!") and h not in seen:
                        seen.add(h)
                        out.append(h)
            elif key == "include":
                for pat in val.split():
                    base = Path(os.path.expanduser(pat))
                    if not base.is_absolute():
                        base = Path.home() / ".ssh" / base
                    for f in sorted(base.parent.glob(base.name)):
                        read(f, depth + 1)

    read(Path.home() / ".ssh" / "config")
    return out


def _run(args, timeout=40) -> bytes:
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RemoteError(f"{args[0]} isn't installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RemoteError("the server didn't answer in time") from exc
    if r.returncode != 0:
        err = (r.stderr or r.stdout).decode(errors="replace").strip().splitlines()
        msg = err[-1] if err else f"exit status {r.returncode}"
        if "Permission denied" in msg or "password" in msg.lower():
            msg += " (the login to the server has ended: try again, and Fignimble asks for what the server needs)"
        raise RemoteError(msg)
    return r.stdout


def _quote(path: str) -> str:
    """A remote path for the server's shell: quoted, with a leading ~ left for it to expand."""
    if path == "~":
        return "~"
    if path.startswith("~/"):
        return "~/" + shlex.quote(path[2:])
    return shlex.quote(path)


def _check_host(host: str):
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        raise RemoteError(f"{host!r} isn't a host name")


def list_dir(host: str, path: str = "") -> dict:
    """{path: the folder's absolute path, entries: [{name, dir}]}: its folders and .py files (the home folder when
    `path` is empty)."""
    _check_host(host)
    script = f"cd {_quote(path) if path else '~'} && pwd && ls -1Ap"
    out = _run(ssh_command() + [host, "--", script]).decode(errors="replace").splitlines()
    if not out:
        raise RemoteError("no answer from the server")
    entries = []
    for name in out[1:]:
        if name.endswith("/"):
            entries.append({"name": name[:-1], "dir": True})
        elif name.endswith(".py"):
            entries.append({"name": name, "dir": False})
    entries.sort(key=lambda e: (not e["dir"], e["name"].startswith("."), e["name"].lower()))
    return {"host": host, "path": out[0].strip(), "entries": entries}


def fetch(host: str, path: str, dest: Path) -> Path:
    """Copy `path` on `host` to the local file `dest`."""
    _check_host(host)
    data = _run(ssh_command() + [host, "--", f"cat -- {_quote(path)}"], timeout=120)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def with_header(text: str, host: str, path: str) -> str:
    """The panel's code with a first line saying where it came from (replacing an older one)."""
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("# Fetched from "):
        lines = lines[1:]
    return HEADER.format(host=host, path=path) + "".join(lines)


def origin(text: str) -> tuple[str, str] | None:
    """(host, path) of a panel fetched from a server, from its first line."""
    first = text.split("\n", 1)[0]
    if not first.startswith("# Fetched from ") or " by Fignimble" not in first:
        return None
    where = first[len("# Fetched from "):first.index(" by Fignimble")]
    host, _, path = where.partition(":")
    return (host, path) if host and path else None
