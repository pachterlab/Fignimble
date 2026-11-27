import AppKit

/// Reproducibility (engine/fignimble/repro.py): the engine keeps exports/ and fignimble.lock up to date by itself;
/// these are the File menu's ways to check the project against them, name a version (a git tag) and compare with one.
extension MainController {

    /// File ▸ Check Reproducibility…: exports/ and recipe outputs against fignimble.lock, each figure drawn again.
    @objc func checkReproducibility(_ sender: Any?) {
        guard project != nil else { return }
        if code.dirty { code.save() }
        enqueue { [weak self] in guard let self else { return }
            self.busy("Checking the project against fignimble.lock…")
            do {
                _ = try await self.engine.json("record", [:])
                let r = try await self.engine.json("repro_check", [:])
                self.idle()
                self.showReport(title: (r["ok"] as? Bool == true) ? "The exports match the project"
                                                                   : "Some of the project is out of date",
                                text: Self.checkText(r), folder: nil)
            } catch { self.idle(); self.toast(error.localizedDescription, error: true) }
        }
    }

    /// File ▸ Tag Version…: name this version (exports/ and the lock committed, the commit tagged fignimble/NAME).
    @objc func tagVersion(_ sender: Any?) {
        guard project != nil else { return }
        if code.dirty { code.save() }
        let a = NSAlert(); a.messageText = "Tag this version"
        a.informativeText = "Fignimble writes exports/ and fignimble.lock, checks them, commits them, and tags the commit fignimble/NAME, to compare with later (File ▸ Compare with Version…). Commit the project's other files first. Needs git."
        let name = NSTextField(string: ""), msg = NSTextField(string: "")
        name.placeholderString = "submitted"; msg.placeholderString = "As submitted to the journal"
        for f in [name, msg] { f.widthAnchor.constraint(equalToConstant: 260).isActive = true }
        let grid = NSGridView(views: [[NSTextField(labelWithString: "Name"), name],
                                      [NSTextField(labelWithString: "Message"), msg]])
        grid.frame = NSRect(x: 0, y: 0, width: 340, height: 56)
        a.accessoryView = grid; a.addButton(withTitle: "Tag"); a.addButton(withTitle: "Cancel")
        a.window.initialFirstResponder = name
        a.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .alertFirstButtonReturn else { return }
            let n = name.stringValue.trimmingCharacters(in: .whitespaces)
            guard !n.isEmpty else { return }
            var args: [String: Any] = ["name": n]
            if !msg.stringValue.isEmpty { args["message"] = msg.stringValue }
            self.enqueue { [weak self] in guard let self else { return }
                self.busy("Tagging \(n)…")
                do {
                    let res = try await self.engine.json("tag", args)
                    self.idle()
                    if res["ok"] as? Bool == true {
                        let commit = String((res["commit"] as? String ?? "").prefix(10))
                        self.showReport(title: "Tagged \(commit) as \(res["tag"] as? String ?? n)",
                                        text: ((res["committed"] as? String) != nil ? "exports/ and fignimble.lock were committed first.\n\n" : "")
                                            + "Share it with:\n    \(res["push"] as? String ?? "")", folder: nil)
                    } else {
                        self.showReport(title: "Couldn't tag \(n)", text: res["error"] as? String ?? "", folder: nil)
                    }
                } catch { self.idle(); self.toast(error.localizedDescription, error: true) }
            }
        }
    }

    /// File ▸ Compare with Version…: a version against the project now: recipes and files, tables' and panels'
    /// values, and how the figures look, with pictures of the differences.
    @objc func compareWithVersion(_ sender: Any?) {
        guard project != nil else { return }
        if code.dirty { code.save() }
        enqueue { [weak self] in guard let self else { return }
            do {
                let t = try await self.engine.json("tags", [:])
                let tags = (t["tags"] as? [[String: Any]]) ?? []
                if tags.isEmpty {
                    self.showReport(title: "No versions to compare with",
                                    text: (t["error"] as? String) ?? "Make one with File ▸ Tag Version…", folder: nil)
                    return
                }
                let a = NSAlert(); a.messageText = "Compare with a version"
                a.informativeText = "The project now is compared with the version you choose: what changed in how things are made, in the values tables and panels show, and in how the figures look."
                let pop = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 320, height: 26))
                for v in tags {
                    let date = String((v["date"] as? String ?? "").prefix(10))
                    pop.addItem(withTitle: "\(v["name"] as? String ?? "")    \(date)")
                }
                a.accessoryView = pop; a.addButton(withTitle: "Compare"); a.addButton(withTitle: "Cancel")
                guard a.runModal() == .alertFirstButtonReturn,
                      let ref = tags[pop.indexOfSelectedItem]["ref"] as? String else { return }
                self.busy("Comparing with \(tags[pop.indexOfSelectedItem]["name"] as? String ?? ref)…")
                let d = try await self.engine.json("diff", ["a": ref])
                self.idle()
                if d["ok"] as? Bool == false {
                    self.showReport(title: "Couldn't compare", text: d["error"] as? String ?? "", folder: nil)
                    return
                }
                let same = d["same"] as? Bool == true
                self.showReport(title: same ? "The same as \(d["a"] as? String ?? "")"
                                            : "Changed since \(d["a"] as? String ?? "")",
                                text: Self.diffText(d), folder: same ? nil : d["differences"] as? String)
            } catch { self.idle(); self.toast(error.localizedDescription, error: true) }
        }
    }

    /// The engine brought exports/ and the lock up to date by itself: say so only when something went wrong.
    func recorded(_ e: [String: Any]) {
        if let errs = e["errors"] as? [String: Any], let first = errs.first {
            toast("exports/ not updated for \(first.key): \(first.value)", error: true)
        }
    }

    /// A report in a sheet: monospaced text to scroll, and a button that shows the pictures of differences.
    func showReport(title: String, text: String, folder: String?) {
        let a = NSAlert(); a.messageText = title
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 620, height: 320))
        scroll.hasVerticalScroller = true; scroll.hasHorizontalScroller = true; scroll.borderType = .bezelBorder
        let tv = NSTextView(frame: scroll.bounds)
        tv.isEditable = false; tv.isSelectable = true
        tv.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
        tv.string = text
        tv.isHorizontallyResizable = true
        tv.textContainer?.widthTracksTextView = false
        tv.textContainer?.containerSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        tv.autoresizingMask = [.height]
        scroll.documentView = tv
        a.accessoryView = scroll
        a.addButton(withTitle: "OK")
        if folder != nil { a.addButton(withTitle: "Show Pictures of Differences") }
        a.beginSheetModal(for: window) { r in
            if r == .alertSecondButtonReturn, let f = folder {
                NSWorkspace.shared.open(URL(fileURLWithPath: f))
            }
        }
    }

    static func checkText(_ r: [String: Any]) -> String {
        var out: [String] = []
        for p in (r["problems"] as? [String]) ?? [] { out.append("!  fignimble.toml: \(p)") }
        let order = ["lock": 0, "export": 1, "input": 2, "output": 3, "recipe": 4]
        let icon = ["ok": " ", "info": "·", "warning": "?", "error": "!"]
        var items = (r["items"] as? [[String: Any]]) ?? []
        items.sort { (order[$0["kind"] as? String ?? ""] ?? 9, $0["path"] as? String ?? "")
                   < (order[$1["kind"] as? String ?? ""] ?? 9, $1["path"] as? String ?? "") }
        var dataFolders: [String: (n: Int, state: String, level: String, detail: String, mixed: Bool)] = [:]
        for it in items where (it["path"] as? String ?? "").hasPrefix("exports/data/") {
            let folder = ((it["path"] as? String ?? "") as NSString).deletingLastPathComponent
            let s = it["state"] as? String ?? "", l = it["level"] as? String ?? "", d = it["detail"] as? String ?? ""
            if var g = dataFolders[folder] {
                g.n += 1; g.mixed = g.mixed || g.state != s || g.detail != d; dataFolders[folder] = g
            } else { dataFolders[folder] = (1, s, l, d, false) }
        }
        var shown = Set<String>()
        for it in items {
            let path = it["path"] as? String ?? ""
            let folder = (path as NSString).deletingLastPathComponent
            if let g = dataFolders[folder], !g.mixed {
                if g.level != "ok" && !shown.contains(folder) {
                    shown.insert(folder)
                    out.append("\(icon[g.level] ?? " ")  \(g.state.padding(toLength: 10, withPad: " ", startingAt: 0)) \(folder)/ (\(g.n) files)  \(g.detail)")
                }
                continue
            }
            let level = it["level"] as? String ?? "ok", state = it["state"] as? String ?? ""
            let detail = it["detail"] as? String ?? ""
            out.append("\(icon[level] ?? " ")  \(state.padding(toLength: 10, withPad: " ", startingAt: 0)) \(path)\(detail.isEmpty ? "" : "  \(detail)")")
        }
        let summary = ((r["summary"] as? [String: Int]) ?? [:]).sorted { $0.key < $1.key }.map { "\($0.value) \($0.key)" }
        out.append("")
        out.append(summary.joined(separator: ", "))
        out.append("")
        out.append("! out of date or missing   ? the project doesn't say how this was made   · worth knowing")
        return out.joined(separator: "\n")
    }

    static func diffText(_ d: [String: Any]) -> String {
        var out = ["\(d["a"] as? String ?? "")  ->  \(d["b"] as? String ?? "")"]
        let how = (d["how"] as? [[String: Any]]) ?? []
        if !how.isEmpty {
            out += ["", "How things are made:"]
            for h in how {
                let detail = (h["detail"] as? String).map { "  (\($0))" } ?? ""
                out.append("  \((h["change"] as? String ?? "").padding(toLength: 8, withPad: " ", startingAt: 0)) \(h["what"] as? String ?? "") \(h["name"] as? String ?? "")\(detail)")
            }
        }
        for (title, key) in [("What is shown (tables, panels' values):", "shown"), ("How figures look:", "looks")] {
            let part = (d[key] as? [[String: Any]]) ?? []
            let changed = part.filter { ($0["verdict"] as? String) != "exact" }
            out += ["", title]
            if changed.isEmpty { out.append("  all \(part.count) the same"); continue }
            for it in changed {
                var extra: [String] = []
                if let p = it["panels"] as? [String], !p.isEmpty { extra.append("panel\(p.count > 1 ? "s" : "") \(p.joined(separator: ", "))") }
                if let b = it["because"] as? [String], !b.isEmpty { extra.append("made from changed \(b.prefix(4).joined(separator: ", "))") }
                out.append("  \((it["verdict"] as? String ?? "").padding(toLength: 17, withPad: " ", startingAt: 0)) \(it["path"] as? String ?? "")  \(it["detail"] as? String ?? "")\(extra.isEmpty ? "" : "; " + extra.joined(separator: "; "))")
            }
            let same = part.count - changed.count
            if same > 0 { out.append("  (\(same) more the same)") }
        }
        return out.joined(separator: "\n")
    }
}
