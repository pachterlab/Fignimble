# Projects on an SSH server

When a project's data and Python environment are on a Linux server, Fignimble can edit it from your Mac (or, with
the [Windows and Linux editor](../getting-started/windows-linux.md#what-is-different), from Linux, when the server
takes your ssh key). The figures
are drawn on the server, by its Python, next to its data. The window on the Mac works as it does for a local project.

**File ▸ Open Project on SSH Server…** (<kbd>⇧</kbd><kbd>⌘</kbd><kbd>O</kbd>) asks for three things:

- **Server**: a `Host` from `~/.ssh/config`, or `user@server`.
- **Folder**: the project's folder there. **List** shows the folder's contents, and double-clicking a folder goes into
  it. **Open** opens the folder that is listed.
- **Python there**: the Python that draws the figures. It needs Python 3.10 or newer with matplotlib, pymupdf and
  pillow, plus whatever the panels' code imports. Give the full path of an environment's Python, for example
  `~/miniconda3/envs/analysis/bin/python`, because ssh doesn't run your shell's start-up files. The field remembers
  the last value for each server. If that Python lacks one of Fignimble's packages, Fignimble says which, and
  **Install** installs it there with pip.

The project then appears under **File ▸ Open Recent** with the other projects.

## How it works

Fignimble starts its engine on the server over one ssh connection. The first time, and again after each Fignimble
update, it copies the engine (under 1 MB) to `~/.cache/fignimble/engine/` there. Nothing is installed with pip, and
nothing outside that folder changes.

The Mac keeps a mirror of the project's code, tables and pictures in `~/Library/Caches/Fignimble/remote/`. Fignimble
keeps the mirror and the server in step:

- Canvas edits are written into the code on the server. The mirror has the new code before the canvas updates.
- Changes made on the server, by you or an agent in the terminal, reach the mirror within a second or so.
- Files changed in the mirror, from the code pane or an editor opened with **Open in…**, go to the server within a
  second.
- If a file changed on both sides, the server's version wins. The Mac's copy is saved in the `.conflicts` folder
  next to the mirror.

Data files (anything that isn't code, a table, a note or a picture, or that is larger than 8 MB) stay on the server.
Panels read them there, as they always have.

The terminal tabs are ssh sessions in the project's folder on the server. A picture you place from the Mac is copied into the project's `images/` folder on the server. Exports are
written on the server and then copied to wherever you chose on the Mac.

## Logging in

Fignimble logs in to a server once and keeps that connection open for four hours after its last use. Listing
folders, fetching panels, the project's engine and the terminal tabs all go through it, so none of them ask again.

If the server asks for something while logging in, such as a password, a two-factor code or a key's passphrase,
Fignimble shows ssh's question in a dialog. Nothing you type is stored. A server that takes your key logs you in
without asking.

After you log in with a password, Fignimble offers to use a key for that server from now on. It adds your public key
(`~/.ssh/id_ed25519.pub`, or another one you have) to `~/.ssh/authorized_keys` there. If you don't have a key, it
makes one first, without a passphrase.

## When the connection drops

ssh checks the connection every 10 seconds. When it drops (the network changed, the Mac slept, the server
restarted), Fignimble says so within about 30 seconds, and **Reconnect** opens the project again at the same figure.
Edits that had not reached the server yet are still in the mirror, and are sent when you reconnect.

## Fonts

The server draws the text, using its own fonts. Many Linux servers don't have Arial or Helvetica, so matplotlib
substitutes another font without saying so. If the text on the canvas looks wrong, install the fonts on the
server, or put `.ttf` files in `~/.fonts` there and delete `~/.cache/matplotlib`.
