"""Terminals in the project folder, with the `fignimble` command on their PATH: the system's own terminal (Windows'
console or Windows Terminal; on Linux $TERMINAL or the first of the usual ones), optionally running a coding agent."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

from . import locate

AGENTS = [("Claude Code", "claude"), ("Codex", "codex"), ("Gemini CLI", "gemini")]
LINUX_TERMINALS = [  # (command, how to run a command in it)
    ("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]),
    ("kitty", []), ("alacritty", ["-e"]), ("wezterm", ["start", "--"]), ("tilix", ["-e"]), ("mate-terminal", ["-x"]),
    ("lxterminal", ["-e"]), ("terminator", ["-x"]), ("foot", []), ("xterm", ["-e"]),
]


def environment() -> dict:
    env = dict(os.environ)
    b = locate.bin_dir()
    if b:
        env["PATH"] = b + os.pathsep + env.get("PATH", "")
    if env.get("FIGNIMBLE_PYTHON") is None and getattr(locate, "engine_python", None):
        env["FIGNIMBLE_PYTHON"] = locate.engine_python
    return env


def open_terminal(folder: str, command: str | None = None) -> str | None:
    """Open a terminal in `folder`, running `command` and staying open after it. None, or why it couldn't."""
    env = environment()
    try:
        if locate.WINDOWS:
            args = ["cmd.exe", "/k", command] if command else ["cmd.exe", "/k"]
            subprocess.Popen(args, cwd=folder, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)
            return None
        if locate.MACOS:
            subprocess.Popen(["open", "-a", "Terminal", folder], env=env)
            return None
        shell = env.get("SHELL") or "/bin/bash"
        inner = [shell, "-c", f"{command}; exec {shlex.quote(shell)}"] if command else [shell]
        cands = []
        if env.get("TERMINAL"):
            cands.append((env["TERMINAL"], ["-e"]))
        cands += LINUX_TERMINALS
        for name, run in cands:
            exe = shutil.which(name.split()[0])
            if exe:
                subprocess.Popen([exe] + run + inner, cwd=folder, env=env, start_new_session=True)
                return None
        return "No terminal program found; set $TERMINAL to the one you use."
    except OSError as exc:
        return str(exc)
