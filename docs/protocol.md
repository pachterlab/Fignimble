# Engine protocol

The app (the macOS app, or the Windows and Linux editor in `apps/desktop`) starts `python -m fignimble.rpc` (with
`PYTHONPATH` at `engine/`) and talks to it in JSON lines, as Manimble
does: a request `{"id": 7, "cmd": "figure", "name": "fig1"}`, a response `{"id": 7, "result": {...}}` or
`{"id": 7, "error": "..."}`, and events with no id. The engine renders in a separate worker process
(`fignimble.worker`), restarted if a panel's code crashes it or runs over 180 s.

Coordinates are millimeters from the figure's top-left corner, y down.

## Commands

| cmd | arguments | result |
|---|---|---|
| `open` | `project` (folder) | `{ok, project}`; writes the agent instruction files the project lacks |
| `close` | | `{ok}`; removes them again |
| `new_project` | `dir`, `journal?` | `{ok, project, figure}` |
| `requirements` | | `{python, engine: [{requirement, why}], project: [...], file}`: packages Fignimble and the project's requirements.txt need that the Python lacks |
| `install` | `requirements` | `{ok, output}`: pip installs them into the engine's Python and restarts the renderer |
| `state` | | `{project, figures: [{name, file, thumb, error, warnings}], canUndo, canRedo, undoLabel, redoLabel, panels}` |
| `figure` | `name`, `pxPerMM?` (sprite resolution), `dark?` (show dark versions from now on) | a render (below) |
| `edit` | `figure`, `op`, op arguments, `pxPerMM?` | `{ok, error?, figure, select?}`: the render after the edit, and the new elements to select |
| `write_source` | `file`, `text`, `figure?` | `{ok, figure?}`; one undo step |
| `undo`, `redo` | `figure?` | `{ok, label, figure}` or `{ok: false, error}` |
| `export` | `figure`, `path`, `format?` (pdf, eps, svg, png, tiff, jpg, pptx), `dpi?`, `journal?`, `cmyk?`, `outline?`, `dark?`, `bleed?` (mm, PDF), `marks?` (crop marks, PDF) | `{ok, path, warnings, resolution: {journal, kind, need, dpi?, pictures?, ok, notes}}`; the app's Copy Figure exports a PDF and a PNG this way for the pasteboard. PDF, PNG, TIFF and SVG record their provenance |
| `new_figure` | `name`, `width`, `height`; or `name`, `poster` (a0, a1, ...), `landscape?` | `{ok, name}`; a poster starts from a template |
| `new_panel` | `figure`, `name`, `x, y, w, h` | as `edit`, plus `file` (the new panel file) |
| `import_file` | `figure`, `path`, `x`, `y`, `w?`, `label?` | as `edit`; pictures are copied into `images/`, `.py` files become panels, figure files of the project are placed whole (`fig.figure`); `label: null` gives no letter |
| `journals` | | the journal presets (sizes, fonts, labels, `dpi: {photo, combination, line}`) |
| `posters` | | the poster presets (`name`, `size: [w, h]` mm, `body`, `min_font`, `title`, `heading`, `labels`, `margin`, `rank`) |
| `poster_templates` | | `{templates: [{key, poster, landscape, name, use, size: [w, h]}]}`: the templates New Figure offers, in order, sized as hung |
| `export_table` | `figure`, `oid`, `path`, `title?` | `{ok, path}`: a table element as a Word file |
| `remote_hosts` | | `{hosts}` from `~/.ssh/config` |
| `remote_list` | `host`, `path?` | `{ok, host, path, entries: [{name, dir}]}`: folders and .py files there |
| `import_remote` | `figure`, `host`, `path`, `x`, `y` | as `import_file`, plus `file`: the panel code copied into `panels/` |
| `update_remote` | `file`, `figure?` | as `write_source`: the panel code fetched again from where its first line says |

`import_remote` and `update_remote` also take `text`, the panel's code when it was fetched already (the tunnel
fetches it from the Mac).

### A project on an SSH server

For a project on a server the app starts `python -m fignimble.tunnel --host H --path FOLDER --mirror LOCAL --python
PY` instead. The tunnel takes the same requests. It starts `fignimble.rpc` on the server over ssh, with
`FIGNIMBLE_REMOTE=1`, and keeps a mirror of the project's code, tables and pictures in LOCAL
(`engine/fignimble/tunnel.py`). The app sends `open` with LOCAL as the project, and every path it sends or gets is
in the mirror. In remote mode the engine also answers these commands, which only the tunnel uses
(`engine/fignimble/mirror.py`):

| cmd | arguments | result |
|---|---|---|
| `manifest` | | `{files: {path: [mtime_ns, size]}}`: the project files the mirror keeps |
| `read_files` | `paths` (relative, or in the staging folder) | `{files: {path: {data (base64), stat} \| null}}` |
| `write_file` | `path`, `data`, `expect` (`[mtime_ns, size]` or null) | `{ok, stat}`, or `{ok: false, conflict, stat}` when the file isn't as expected |
| `delete_file` | `path`, `expect` | `{ok}` or `{ok: false, conflict, stat}` |
| `upload` | `name`, `data` | `{path}`: a file from the Mac, in the staging folder |
| `staging_path` | `name` | `{path}`: where an export goes before the tunnel fetches it |

Each response also has `files: {path: base64}`, the pictures in the `.fignimble` cache that it names and that this
connection hasn't sent yet. The tunnel writes them into the mirror before passing the response on.

The app logs in to the server first and leaves a shared ssh connection open (`apps/macos/Sources/Fignimble/SSHLogin.swift`).
`FIGNIMBLE_SSH_SHARE` names the folder of its socket, and every ssh the engine runs goes through it
(`remote.ssh_command`). When the engine can't start on the server, the tunnel answers every request with that error
until the app closes it. If the server's Python lacks the engine's packages, the error is preceded by the event
`{event: "needs_packages", host, python, missing, pip}`, where `pip` lists what to install (empty when the Python
is older than 3.10). When the connection drops, the tunnel exits with status 3 at once.

## Edit operations

`set {oid, props}` (any arguments of the element's call; a label's `text`), `move {oids, dx, dy, labels?: "all"}`,
`resize {oid, box}`, `endpoints {oid, x1, y1, x2, y2}`, `delete {oids}`, `duplicate {oids, dx, dy}`,
`order {oids, how: front|back|forward|backward}`, `align {oids, how, to: selection|key|figure, key?}`,
`distribute {oids, axis: h|v, gap?}`, `match {oids, what: width|height|both}`, `group {oids}`, `ungroup {oids}`,
`insert {kind, props}`, `figure {props}`, `style {props}`, `labels {props}`, `guide_add {x | y}`,
`guide_move {index, value}`, `guide_delete {index}`, `divider {oid (a layout), index (of its dividers), delta mm}`,
`make_layout {oids, gap?}`, `release_layout {oid}`, `panel_text {oid, key, text, old}` (retype a text a panel draws),
`panel_text_style {oid, key, text, props: {size?, color?, weight?, style?}}` (written into the panel's code when the
text is the literal of one `ax.set_xlabel(...)`, `ax.text(...)`…, else `text_style=` on the panel), `panel_text_reset
{oid, key}`, `panel_color {oid, keys, color, old}`.

A panel inside a layout has a `parent` (the layout's oid, its own oid being `<layout>.<n>`); moving it moves the
layout. A layout lists its `children` and its `dividers` (`{axis: col|row, index, at, from, to, sizes, editable}`).
An inset has `host`, `inner` (panel or image) and `item` (its panel's settings); `set` sends its x, y, w, h, label
to the inset call and the rest to the panel's. `figure` and `export` take `dark` (the figure's dark version).

An edit that would overwrite a value the code computes (`x=a.right + 2`) is refused with the line that controls it.

## A render

```
{figure, file, size: [w, h], background, journal?, figureSite, figureProps,
 style: {props, site}, labelStyle: {props, site, preset},
 elements: [{oid, kind, method, box: [x, y, w, h], props, site: {file, line}, group, sprite: {file, x, y, w, h},
             code?: {file, line}, error?}],
 labels: [{oid: "L<panel oid>", panel, text, auto, box, sprite}],
 guides: [{x, y}], warnings: [{level: error|warning|info, oid, message}], thumb, pxPerMM}
```

Each sprite is a transparent PNG of one element at `pxPerMM`; drawing them in order (elements, then labels) on the
figure's background reproduces the figure.

## Events

`changed` (a file of the project changed outside an edit: render again), `rendered` (`figure`: a background render,
for its thumbnail).
