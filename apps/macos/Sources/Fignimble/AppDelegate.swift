import AppKit

@main
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var controller: MainController!
    private var launchURL: URL?

    static func main() {
        // run by ssh as its SSH_ASKPASS program: one question in a dialog, then exit (SSHLogin.swift)
        if ProcessInfo.processInfo.environment["FIGNIMBLE_ASKPASS"] != nil { SSH.askpassMain() }
        // writing to an engine that has just exited raises SIGPIPE, which would end the app: the write throws instead
        signal(SIGPIPE, SIG_IGN)
        let app = NSApplication.shared
        let d = AppDelegate()
        app.delegate = d
        app.setActivationPolicy(.regular)
        app.run()
    }

    func applicationDidFinishLaunching(_ n: Notification) {
        NSWindow.allowsAutomaticWindowTabbing = false
        MainController.applyAppearance()
        OfficeFonts.register()          // Aptos, Calibri, ... in the font menu, before it is built
        controller = MainController()
        buildMenu()
        controller.showWindow()
        NSApp.activate(ignoringOtherApps: true)
        controller.start(open: launchURL ?? commandLineProject())
    }

    func application(_ app: NSApplication, open urls: [URL]) {
        guard let c = controller else { launchURL = urls.first; return }
        if let u = urls.first { c.openProject(at: u) }
    }

    /// `Fignimble.app/Contents/MacOS/Fignimble <project or figure.py>`
    private func commandLineProject() -> URL? {
        for a in CommandLine.arguments.dropFirst() where !a.hasPrefix("-") {
            let u = URL(fileURLWithPath: (a as NSString).expandingTildeInPath,
                        relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)).standardizedFileURL
            if FileManager.default.fileExists(atPath: u.path) { return u }
        }
        return nil
    }

    /// File ▸ Open Recent: the projects opened lately (filled in each time it opens).
    private func recentMenu() -> NSMenuItem {
        let i = NSMenuItem(title: "Open Recent", action: nil, keyEquivalent: "")
        let m = NSMenu(title: "Open Recent")
        m.delegate = self
        i.submenu = m
        return i
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        if menu.title == "Journal Convention", let c = controller {
            menu.removeAllItems()
            c.fillJournalMenu(menu) { k in
                let it = NSMenuItem(title: c.journalName(k), action: #selector(MainController.labelJournal(_:)), keyEquivalent: "")
                it.representedObject = k; return [it]
            }
            return
        }
        guard menu.title == "Open Recent" else { return }
        menu.removeAllItems()
        let recent = MainController.recentProjects
        let names = recent.map { ($0 as NSString).lastPathComponent }
        for p in recent {
            let name = (p as NSString).lastPathComponent
            let it: NSMenuItem
            if let r = RemoteProject(recentKey: p) {             // a project on an SSH server
                it = NSMenuItem(title: r.title, action: #selector(MainController.openRecent(_:)), keyEquivalent: "")
                it.toolTip = "\(r.host):\(r.path)"
                it.image = NSImage(systemSymbolName: "server.rack", accessibilityDescription: "SSH server")
            } else {
                // the same folder name twice: say where each is
                let title = names.filter { $0 == name }.count > 1 ? "\(name) — \((p as NSString).deletingLastPathComponent.replacingOccurrences(of: NSHomeDirectory(), with: "~"))" : name
                it = NSMenuItem(title: title, action: #selector(MainController.openRecent(_:)), keyEquivalent: "")
                it.toolTip = p
                it.image = NSWorkspace.shared.icon(forFile: p); it.image?.size = NSSize(width: 16, height: 16)
            }
            it.representedObject = p
            menu.addItem(it)
        }
        if recent.isEmpty { menu.addItem(NSMenuItem(title: "No Recent Projects", action: nil, keyEquivalent: "")) }
        menu.addItem(.separator())
        let clear = NSMenuItem(title: "Clear Menu", action: recent.isEmpty ? nil : #selector(MainController.clearRecent(_:)), keyEquivalent: "")
        menu.addItem(clear)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        controller.confirmClose() ? .terminateNow : .terminateCancel
    }
    func applicationWillTerminate(_ n: Notification) { controller.finish() }

    /// The About panel: name, version (from the bundle), license and authors (as Manimble's).
    @objc func showAbout(_ sender: Any?) {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "X.Y.Z"
        let para = NSMutableParagraphStyle(); para.alignment = .center
        let credits = NSAttributedString(
            string: "License: BSD-2\nDeveloped by Joseph Rich and Lior Pachter. 2026",
            attributes: [.font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                         .foregroundColor: NSColor.labelColor, .paragraphStyle: para])
        NSApp.orderFrontStandardAboutPanel(options: [
            .applicationName: "Fignimble",
            .applicationVersion: version,
            .version: "",                                   // no "(build)" suffix
            .credits: credits,
            NSApplication.AboutPanelOptionKey(rawValue: "Copyright"): "",
        ])
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: menus

    private func buildMenu() {
        let main = NSMenu()
        func menu(_ title: String, _ items: [NSMenuItem]) {
            let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }
            let top = NSMenuItem(title: title, action: nil, keyEquivalent: ""); top.submenu = m
            main.addItem(top)
        }
        func item(_ t: String, _ s: Selector?, _ k: String = "", _ mods: NSEvent.ModifierFlags = .command, tag: Int = 0, to target: AnyObject? = nil) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: s, keyEquivalent: k)
            i.keyEquivalentModifierMask = mods; i.tag = tag
            i.target = target ?? (s.map { MainController.instancesRespond(to: $0) } == true ? controller : nil)
            return i
        }
        func sub(_ t: String, _ items: [NSMenuItem]) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: nil, keyEquivalent: ""); let m = NSMenu(title: t); items.forEach { m.addItem($0) }; i.submenu = m; return i
        }
        let sep = { NSMenuItem.separator() }

        menu("Fignimble", [item("About Fignimble", #selector(showAbout(_:)), to: self), sep(),
                        item("Install Command Line Tool…", #selector(MainController.installCommandLineTool(_:))),
                        item("Check Packages…", #selector(MainController.checkPackagesNow(_:))), sep(),
                        item("Hide Fignimble", #selector(NSApplication.hide(_:)), "h", to: NSApp),
                        item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option], to: NSApp), sep(),
                        item("Quit Fignimble", #selector(NSApplication.terminate(_:)), "q", to: NSApp)])
        menu("File", [item("New Project…", #selector(MainController.newProject(_:)), "n", [.command, .shift]),
                      item("New Figure…", #selector(MainController.newFigure(_:)), "n"),
                      item("New Table…", #selector(MainController.newTable(_:)), "t", [.command, .shift]),
                      item("Open Project…", #selector(MainController.openProject(_:)), "o"),
                      item("Open Project on SSH Server…", #selector(MainController.openRemoteProject(_:)), "o", [.command, .shift]),
                      recentMenu(), sep(),
                      item("Apply Code Changes", #selector(MainController.saveCode(_:)), "s"),
                      item("Show Project in Finder", #selector(MainController.revealProject(_:))), sep(),
                      item("Export Figure…", #selector(MainController.exportFigure(_:)), "e"),
                      item("Export All Figures as PDF…", #selector(MainController.exportAll(_:)), "e", [.command, .shift]),
                      item("Export Table…", #selector(MainController.exportTable(_:))),
                      item("Export Source Data…", #selector(MainController.exportSourceData(_:))), sep(),
                      item("Check Reproducibility…", #selector(MainController.checkReproducibility(_:))),
                      item("Tag Version…", #selector(MainController.tagVersion(_:))),
                      item("Compare with Version…", #selector(MainController.compareWithVersion(_:))), sep(),
                      item("Close", #selector(NSWindow.performClose(_:)), "w", to: nil)])
        menu("Edit", [item("Undo", #selector(MainController.undoEdit(_:)), "z"),
                      item("Redo", #selector(MainController.redoEdit(_:)), "z", [.command, .shift]), sep(),
                      item("Cut", #selector(NSText.cut(_:)), "x", to: nil), item("Copy", #selector(NSText.copy(_:)), "c", to: nil),
                      item("Paste", #selector(NSText.paste(_:)), "v", to: nil),
                      item("Copy Figure as Picture", #selector(MainController.copyFigure(_:)), "c", [.command, .shift]),
                      item("Copy Style", #selector(MainController.copyStyle(_:)), "c", [.command, .option]),
                      item("Paste Style", #selector(MainController.pasteStyle(_:)), "v", [.command, .option]),
                      item("Duplicate", #selector(MainController.duplicate(_:)), "d"),
                      item("Delete    ⌫", #selector(MainController.deleteSelected(_:))),
                      item("Select All", #selector(NSText.selectAll(_:)), "a", to: nil)])
        menu("Insert", [item("New Panel from Code…", #selector(MainController.newPanel(_:)), "p", [.command, .shift]),
                        item("Panel from Existing Code…", #selector(MainController.insertPanelCode(_:))),
                        item("Picture…", #selector(MainController.insertImage(_:)), "i", [.command, .shift]),
                        item("Figure…", #selector(MainController.insertFigure(_:))),
                        item("QR Code…", #selector(MainController.insertQR(_:))),
                        item("BioCartoon Icons…", #selector(MainController.showBioCartoon(_:)), "b", [.command, .shift]),
                        item("Table…", #selector(MainController.insertGridTable(_:))),
                        item("Table from CSV…", #selector(MainController.insertTable(_:))),
                        item("Panel from SSH Server…", #selector(MainController.insertRemotePanel(_:))),
                        item("Scale Bar on Picture…", #selector(MainController.addScaleBar(_:))),
                        item("Zoom Box", #selector(MainController.addZoom(_:))), sep()]
             + Tool.allCases.dropFirst().map { t in
                 let i = item("\(t.title)    \(t.key.uppercased())", #selector(MenuTools.pick(_:)), to: MenuTools.shared); i.representedObject = t.rawValue; return i })
        menu("Object", [sub("Arrange", [item("Bring to Front", #selector(MainController.bringToFront(_:)), "]", [.command, .shift]),
                                        item("Bring Forward", #selector(MainController.bringForward(_:)), "]"),
                                        item("Send Backward", #selector(MainController.sendBackward(_:)), "["),
                                        item("Send to Back", #selector(MainController.sendToBack(_:)), "[", [.command, .shift])]),
                        sub("Align", [item("Left Edges", #selector(MainController.alignLeft(_:))), item("Centers", #selector(MainController.alignCenter(_:))),
                                      item("Right Edges", #selector(MainController.alignRight(_:))), sep(),
                                      item("Tops", #selector(MainController.alignTop(_:))), item("Middles", #selector(MainController.alignMiddle(_:))),
                                      item("Bottoms", #selector(MainController.alignBottom(_:)))]),
                        sub("Distribute", [item("Horizontally", #selector(MainController.distributeH(_:))), item("Vertically", #selector(MainController.distributeV(_:)))]),
                        sub("Match Size", [item("Width", #selector(MainController.matchWidth(_:))), item("Height", #selector(MainController.matchHeight(_:)))]),
                        sep(),
                        item("Group", #selector(MainController.group(_:)), "g"), item("Ungroup", #selector(MainController.ungroup(_:)), "g", [.command, .shift]),
                        item("Unlink Label from Panel", #selector(MainController.toggleSelectedLabelLink(_:))),
                        sep(), item("Rotate 90° Left", #selector(MainController.rotateLeft(_:))),
                        item("Rotate 90° Right", #selector(MainController.rotateRight(_:))),
                        item("Flip Horizontally", #selector(MainController.flipHorizontal(_:))),
                        item("Flip Vertically", #selector(MainController.flipVertical(_:))),
                        item("Crop Picture", #selector(MainController.cropPicture(_:)), "k", [.command, .option]),
                        sep(), item("Lock", #selector(MainController.toggleLock(_:)), "2"),
                        item("Hide", #selector(MainController.toggleHide(_:)), "3"),
                        sep(), item("Make Layout", #selector(MainController.makeLayout(_:)), "l"),
                        item("Release Layout", #selector(MainController.releaseSelectedLayout(_:)), "l", [.command, .shift]),
                        sep(), item("Edit Code", #selector(MainController.editCode(_:)), "k")])
        // filled when it opens (menuNeedsUpdate), from the engine's journals
        let journalConvention = NSMenuItem(title: "Journal Convention", action: nil, keyEquivalent: "")
        journalConvention.submenu = NSMenu(title: "Journal Convention"); journalConvention.submenu?.delegate = self
        menu("Labels", Inspector.labelFormats.enumerated().map { i, f in item("Style  \(f.0)  \(f.0 == "a" ? "b c" : "…")".replacingOccurrences(of: "  …", with: ""), #selector(MainController.labelFormat(_:)), tag: i) }
             + [item("No Letters", #selector(MainController.labelsNone(_:))), sep(), journalConvention, sep(),
                item("Labels Outside Panels (above left)", #selector(MainController.labelsOutside(_:))),
                item("Labels at Panel Corners", #selector(MainController.labelsInside(_:))), sep(),
                item("Line Up Labels in Rows and Columns", #selector(MainController.toggleLabelAlign(_:))),
                item("Letter in Code Order (not Reading Order)", #selector(MainController.toggleLabelOrder(_:)))])
        menu("View", [item("Zoom In", #selector(MainController.zoomIn(_:)), "+"), item("Zoom Out", #selector(MainController.zoomOut(_:)), "-"),
                      item("Fit Figure", #selector(MainController.zoomFit(_:)), "0"), item("Actual Size", #selector(MainController.zoomActual(_:)), "1"), sep(),
                      item("Grid Lines", #selector(MainController.toggleGrid(_:)), "'"),
                      sub("Grid Spacing", [(5, "0.5 mm"), (10, "1 mm"), (20, "2 mm"), (50, "5 mm"), (100, "10 mm"), (127, "0.5 in (12.7 mm)"), (254, "1 in (25.4 mm)")]
                            .map { item($0.1, #selector(MainController.setGridStep(_:)), tag: $0.0) }),
                      item("Snap to Grid", #selector(MainController.toggleSnapGrid(_:)), "'", [.command, .shift]), sep(),
                      item("Guides", #selector(MainController.toggleGuides(_:)), ";"),
                      item("Add Vertical Guide", #selector(MainController.addVerticalGuide(_:))),
                      item("Add Horizontal Guide", #selector(MainController.addHorizontalGuide(_:))),
                      item("Clear Guides", #selector(MainController.clearGuides(_:))),
                      item("Smart Guides", #selector(MainController.toggleSmartGuides(_:)), "u"), sep(),
                      item("Dark Version of Figure", #selector(MainController.toggleDarkPreview(_:)), "d", [.command, .option]),
                      sub("Color Vision", [item("Normal", #selector(MainController.setVision(_:)), tag: 0),
                                            item("Deuteranopia (green-weak, most common)", #selector(MainController.setVision(_:)), tag: 1),
                                            item("Protanopia (red-weak)", #selector(MainController.setVision(_:)), tag: 2),
                                            item("Tritanopia (blue-weak)", #selector(MainController.setVision(_:)), tag: 3),
                                            item("Grayscale (printed in black and white)", #selector(MainController.setVision(_:)), tag: 4)]),
                      sub("Appearance", [item("System", #selector(MainController.setAppearance(_:)), tag: 0),
                                         item("Light", #selector(MainController.setAppearance(_:)), tag: 1),
                                         item("Dark", #selector(MainController.setAppearance(_:)), tag: 2)]), sep(),
                      item("Caption", #selector(MainController.showCaption(_:)), "j", [.command, .option]),
                      item("Code", #selector(MainController.showCodePane(_:)), "e", [.command, .option]),
                      item("Preflight", #selector(MainController.showProblems(_:)), "p", [.command, .option]),
                      item("History", #selector(MainController.showHistory(_:)), "h", [.command, .option, .shift]),
                      item("Terminal", #selector(MainController.showTerminal(_:)), "t", [.command, .option]),
                      item("New Terminal", #selector(MainController.newTerminal(_:)), "t", [.command, .option, .shift]), sep(),
                      item("Hide Figures and Layers", #selector(MainController.toggleSidebar(_:)), "1", [.command, .option]),
                      item("Hide Inspector", #selector(MainController.toggleInspector(_:)), "i", [.command, .option]), sep(),
                      item("Render Again", #selector(MainController.refreshFigure(_:)), "r")])
        let w = NSMenu(title: "Window")
        w.addItem(item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m", to: nil))
        let wi = NSMenuItem(title: "Window", action: nil, keyEquivalent: ""); wi.submenu = w; main.addItem(wi)
        NSApp.windowsMenu = w
        NSApp.mainMenu = main
    }
}

/// Insert ▸ tools: their one-letter shortcuts work only while the canvas has the keyboard, so typing in a field or the
/// terminal is never taken for a tool.
@MainActor
final class MenuTools: NSObject, NSMenuItemValidation {
    static let shared = MenuTools()
    @objc func pick(_ item: NSMenuItem) {
        guard let w = NSApp.keyWindow, let c = (w.delegate as? MainController)?.canvas,
              let t = Tool(rawValue: item.representedObject as? String ?? "") else { return }
        c.tool = t
        w.makeFirstResponder(c)
    }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        guard let c = (NSApp.keyWindow?.delegate as? MainController)?.canvas else { return false }
        return c.figure != nil && NSApp.keyWindow?.firstResponder === c
    }
}
