import AppKit
import UniformTypeIdentifiers

/// The window: figures on the left, the canvas with its rulers in the middle (the code, preflight and terminals in
/// the drawer under it), the inspector on the right. Every edit goes to the engine, which changes the code and
/// renders the figure again.
@MainActor
final class MainController: NSObject, NSWindowDelegate, NSMenuItemValidation {
    let window: NSWindow
    let engine = Engine()
    let canvas = CanvasView()
    let inspector = Inspector()
    let navigator = Navigator()
    let code = CodePane()
    let captionPane = CaptionPane()
    let problems = ProblemsView()
    let history = HistoryView()
    let terminal = TerminalPanel()
    private(set) var drawer: BottomDrawer!
    /// The inspector, or the BioCartoon search in its place.
    let rightHost = NSView()
    var biocartoonPane: BioCartoonPane?
    private let hRuler = RulerView(horizontal: true)
    private let vRuler = RulerView(horizontal: false)
    private let pill = Pill()
    private let status = NSTextField(labelWithString: "")
    private let zoomLabel = NSTextField(labelWithString: "")
    private let toolControl = NSSegmentedControl()
    private var gridButton: NSButton?
    private var guidesButton: NSButton?
    private var root = GrabSplitView()
    private var middle = GrabSplitView()

    private(set) var project: String?
    private(set) var current: String?
    private(set) var figure: FigureData?
    private(set) var figures: [[String: Any]] = []
    private(set) var journals: [String: [String: Any]] = [:]
    /// Poster sizes (Figure(poster=...)): key -> {name, size [w, h] mm, body, min_font, margin, rank}.
    private(set) var posters: [String: [String: Any]] = [:]
    /// Poster templates for New Figure, in order: {key, poster, landscape, name, use, size [w, h] mm as hung}.
    private(set) var posterTemplates: [[String: Any]] = []
    private var state: [String: Any] = [:]
    var alignTo = "selection"
    /// The canvas shows the figure's dark version.
    private(set) var darkPreview = false
    private(set) var python: String?
    /// The server the open project is on (nil: a project on this Mac), and the one the engine was started for.
    private(set) var remote: RemoteProject?
    /// What the last remote project's Python lacked (the tunnel's needs_packages event), for openRemote.
    private var remoteNeeds: [String: Any]?
    private var engineFor: RemoteProject?
    /// The engine started answered its first request; before that, its stopping means its Python can't run it.
    private var engineAnswered = false
    private var queue: Task<Void, Never>?
    private var nudgeTotal = CGPoint.zero
    private var nudgeWork: DispatchWorkItem?
    private(set) var lastPx: Double = 12
    /// The look copied by Copy Style, for Paste Style.
    private var copiedStyle: [String: Any]?
    static let pasteType = NSPasteboard.PasteboardType("org.pachterlab.fignimble.code")
    private var drawerHeight: CGFloat = 260
    var busyPill: Pill { pill }

    override init() {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        super.init()
        window.title = "Fignimble"
        window.delegate = self
        window.minSize = NSSize(width: 900, height: 560)
        window.setFrameAutosaveName("FignimbleMain")
        build()
    }

    // MARK: layout

    private func build() {
        canvas.main = self; inspector.main = self; navigator.main = self; code.main = self; captionPane.main = self; problems.main = self; history.main = self
        hRuler.canvas = canvas; vRuler.canvas = canvas
        drawer = BottomDrawer(caption: captionPane, code: code, problems: problems, history: history, terminal: terminal)
        drawer.onShowTerminal = { [weak self] in self?.showTerminal(nil) }
        drawer.onNewTerminal = { [weak self] in self?.newTerminal(nil) }
        drawer.onHide = { [weak self] in self?.setDrawer(visible: false) }
        terminal.onHide = { [weak self] in self?.drawer.show(.code) }
        canvas.onCursor = { [weak self] p in self?.cursorMoved(p) }
        canvas.onViewChange = { [weak self] in self?.viewChanged() }

        // the canvas area: a tool bar, the rulers, the canvas, a status line
        let area = NSView()
        area.wantsLayer = true
        Theme.layerColors(area) { $0.layer?.backgroundColor = Theme.barBackground.cgColor }
        let bar = toolbar()
        let corner = NSView(); corner.wantsLayer = true
        Theme.layerColors(corner) { $0.layer?.backgroundColor = Theme.barBackground.cgColor }
        status.font = Theme.font(10.5); status.textColor = Theme.ink(0.55); status.lineBreakMode = .byTruncatingMiddle
        // a long path gives way rather than widening the window
        status.setContentCompressionResistancePriority(.init(100), for: .horizontal)
        pill.defaultEstimate = 1.5
        for v in [bar, corner, hRuler, vRuler, canvas, status, pill] as [NSView] { v.translatesAutoresizingMaskIntoConstraints = false; area.addSubview(v) }
        let t = RulerView.thickness
        NSLayoutConstraint.activate([
            bar.leadingAnchor.constraint(equalTo: area.leadingAnchor), bar.trailingAnchor.constraint(equalTo: area.trailingAnchor),
            bar.topAnchor.constraint(equalTo: area.topAnchor), bar.heightAnchor.constraint(equalToConstant: 36),
            corner.leadingAnchor.constraint(equalTo: area.leadingAnchor), corner.topAnchor.constraint(equalTo: bar.bottomAnchor),
            corner.widthAnchor.constraint(equalToConstant: t), corner.heightAnchor.constraint(equalToConstant: t),
            hRuler.leadingAnchor.constraint(equalTo: corner.trailingAnchor), hRuler.trailingAnchor.constraint(equalTo: area.trailingAnchor),
            hRuler.topAnchor.constraint(equalTo: bar.bottomAnchor), hRuler.heightAnchor.constraint(equalToConstant: t),
            vRuler.leadingAnchor.constraint(equalTo: area.leadingAnchor), vRuler.topAnchor.constraint(equalTo: corner.bottomAnchor),
            vRuler.widthAnchor.constraint(equalToConstant: t), vRuler.bottomAnchor.constraint(equalTo: status.topAnchor),
            canvas.leadingAnchor.constraint(equalTo: vRuler.trailingAnchor), canvas.trailingAnchor.constraint(equalTo: area.trailingAnchor),
            canvas.topAnchor.constraint(equalTo: hRuler.bottomAnchor), canvas.bottomAnchor.constraint(equalTo: status.topAnchor),
            status.leadingAnchor.constraint(equalTo: area.leadingAnchor, constant: 8), status.trailingAnchor.constraint(equalTo: area.trailingAnchor, constant: -8),
            status.bottomAnchor.constraint(equalTo: area.bottomAnchor, constant: -3), status.heightAnchor.constraint(equalToConstant: 16),
            pill.centerXAnchor.constraint(equalTo: canvas.centerXAnchor), pill.topAnchor.constraint(equalTo: canvas.topAnchor, constant: 12),
            pill.widthAnchor.constraint(lessThanOrEqualTo: canvas.widthAnchor, multiplier: 0.8),
        ])

        middle.isVertical = false; middle.dividerStyle = .thin; middle.collapsesLastPane = true
        middle.addArrangedSubview(area); middle.addArrangedSubview(drawer)
        middle.setHoldingPriority(.init(260), forSubviewAt: 0); middle.setHoldingPriority(.init(270), forSubviewAt: 1)
        root.isVertical = true; root.dividerStyle = .thin
        inspector.translatesAutoresizingMaskIntoConstraints = false
        rightHost.addSubview(inspector)
        NSLayoutConstraint.activate([
            inspector.leadingAnchor.constraint(equalTo: rightHost.leadingAnchor), inspector.trailingAnchor.constraint(equalTo: rightHost.trailingAnchor),
            inspector.topAnchor.constraint(equalTo: rightHost.topAnchor), inspector.bottomAnchor.constraint(equalTo: rightHost.bottomAnchor),
        ])
        root.addArrangedSubview(navigator); root.addArrangedSubview(middle); root.addArrangedSubview(rightHost)
        root.setHoldingPriority(.init(270), forSubviewAt: 0); root.setHoldingPriority(.init(250), forSubviewAt: 1); root.setHoldingPriority(.init(270), forSubviewAt: 2)
        window.contentView = root
        DispatchQueue.main.async { [self] in
            root.setPosition(210, ofDividerAt: 0)
            root.setPosition(root.bounds.width - 270, ofDividerAt: 1)
            middle.setPosition(middle.bounds.height - drawerHeight, ofDividerAt: 0)
        }
        inspector.update()
    }

    private func toolbar() -> NSView {
        let bar = NSView(); bar.wantsLayer = true
        Theme.layerColors(bar) { $0.layer?.backgroundColor = Theme.barBackground.cgColor }
        toolControl.segmentCount = Tool.allCases.count
        toolControl.trackingMode = .selectOne
        for (i, t) in Tool.allCases.enumerated() {
            toolControl.setImage(NSImage(systemSymbolName: t.symbol, accessibilityDescription: t.title), forSegment: i)
            toolControl.setToolTip("\(t.title) (\(t.key.uppercased()))", forSegment: i)
            toolControl.setWidth(30, forSegment: i)
        }
        toolControl.selectedSegment = 0; toolControl.target = self; toolControl.action = #selector(toolPicked)
        toolControl.controlSize = .regular; toolControl.segmentStyle = .texturedRounded
        func b(_ sym: String, _ tip: String, _ sel: Selector) -> NSButton {
            let x = NSButton(image: NSImage(systemSymbolName: sym, accessibilityDescription: tip) ?? NSImage(), target: self, action: sel)
            x.bezelStyle = .texturedRounded; x.toolTip = tip; return x
        }
        func sep() -> NSView { let v = NSBox(); v.boxType = .separator; v.widthAnchor.constraint(equalToConstant: 1).isActive = true; v.heightAnchor.constraint(equalToConstant: 18).isActive = true; return v }
        let panel = NSButton(title: "Panel", image: NSImage(systemSymbolName: "chart.xyaxis.line", accessibilityDescription: nil)!, target: self, action: #selector(newPanel(_:)))
        panel.bezelStyle = .texturedRounded; panel.toolTip = "New panel drawn by Python code (⇧⌘P)"
        let image = NSButton(title: "Picture", image: NSImage(systemSymbolName: "photo", accessibilityDescription: nil)!, target: self, action: #selector(insertImage(_:)))
        image.bezelStyle = .texturedRounded; image.toolTip = "Place a picture: PNG, TIFF, JPEG, PDF or SVG (⇧⌘I)"
        let logo = BioCartoonCLI.monoLogo.copy() as! NSImage
        logo.size = NSSize(width: 16, height: 16)
        let icons = NSButton(title: "BioCartoon", image: logo, target: self, action: #selector(showBioCartoon(_:)))
        icons.bezelStyle = .texturedRounded; icons.toolTip = "Search BioCartoon's science icons and drag them onto the figure (⇧⌘B)"
        let aligns: [(String, String, Selector)] = [("align.horizontal.left", "Align left edges", #selector(alignLeft(_:))),
                                                    ("align.horizontal.center", "Align centers", #selector(alignCenter(_:))),
                                                    ("align.horizontal.right", "Align right edges", #selector(alignRight(_:))),
                                                    ("align.vertical.top", "Align tops", #selector(alignTop(_:))),
                                                    ("align.vertical.center", "Align middles", #selector(alignMiddle(_:))),
                                                    ("align.vertical.bottom", "Align bottoms", #selector(alignBottom(_:))),
                                                    ("distribute.horizontal.center", "Distribute horizontally", #selector(distributeH(_:))),
                                                    ("distribute.vertical.center", "Distribute vertically", #selector(distributeV(_:)))]
        let alignStack = NSStackView(views: aligns.map { b($0.0, $0.1, $0.2) } + [b("rectangle.split.2x2", "Make a layout of the selected panels (⌘L): rows and columns, plot areas lined up", #selector(makeLayout(_:)))])
        alignStack.spacing = 2
        zoomLabel.font = Theme.mono(10.5); zoomLabel.textColor = Theme.ink(0.6); zoomLabel.alignment = .center
        zoomLabel.widthAnchor.constraint(equalToConstant: 46).isActive = true
        let spacer = NSView(); spacer.setContentHuggingPriority(.init(1), for: .horizontal)
        let alignSep = sep()
        let zoom = [b("minus.magnifyingglass", "Zoom out (⌘-)", #selector(zoomOut(_:))), zoomLabel,
                    b("plus.magnifyingglass", "Zoom in (⌘+)", #selector(zoomIn(_:))),
                    b("arrow.up.left.and.down.right.magnifyingglass", "Fit the figure (⌘0)", #selector(zoomFit(_:)))]
        let zoomSep = sep()
        let gridButton = b("grid", "Grid lines (⌘')", #selector(toggleGrid(_:)))
        let guidesButton = b("ruler", "Guides: show or hide them (drag one out of a ruler to add it)", #selector(toggleGuides(_:)))
        for x in [gridButton, guidesButton] { x.setButtonType(.pushOnPushOff) }
        gridButton.state = canvas.showGrid ? .on : .off; guidesButton.state = canvas.showGuides ? .on : .off
        self.gridButton = gridButton; self.guidesButton = guidesButton
        let aids = [gridButton, guidesButton]
        let extras = [b("moon", "Dark version of the figure, for talks (⌥⌘D)", #selector(toggleDarkPreview(_:))),
                      b("chevron.left.forwardslash.chevron.right", "The code of the figure and of what is selected (⌥⌘E)", #selector(showCodePane(_:))),
                      b("terminal", "Terminal for a coding agent (⌥⌘T)", #selector(showTerminal(_:))),
                      b("square.and.arrow.up", "Export the figure (⌘E)", #selector(exportFigure(_:)))]
        let row = NSStackView(views: [toolControl, sep(), panel, image, icons, alignSep, alignStack, spacer] + zoom + aids + [zoomSep] + extras)
        row.spacing = 8; row.alignment = .centerY
        // on a narrow screen (a 13" MacBook Air) what the menus also offer steps aside, so the window fits:
        // the align buttons first, then zoom, then dark/code/terminal/export
        for v in [alignSep, alignStack] { row.setVisibilityPriority(.init(rawValue: 300), for: v) }
        for v in zoom + [zoomSep] + aids { row.setVisibilityPriority(.init(rawValue: 500), for: v) }
        for v in extras { row.setVisibilityPriority(.init(rawValue: 700), for: v) }
        row.setClippingResistancePriority(.defaultLow, for: .horizontal)     // so it may be narrower than all of them
        row.translatesAutoresizingMaskIntoConstraints = false
        bar.addSubview(row)
        NSLayoutConstraint.activate([
            row.leadingAnchor.constraint(equalTo: bar.leadingAnchor, constant: 10), row.trailingAnchor.constraint(equalTo: bar.trailingAnchor, constant: -10),
            row.centerYAnchor.constraint(equalTo: bar.centerYAnchor),
        ])
        return bar
    }

    func showWindow() {
        // the first launch: most of the screen, not a fixed 1440 x 900 (more than a 13" MacBook Air shows)
        let firstLaunch = UserDefaults.standard.string(forKey: "NSWindow Frame FignimbleMain") == nil
        if firstLaunch, let vis = (NSScreen.main ?? NSScreen.screens.first)?.visibleFrame {
            let w = min(1440, vis.width * 0.94), h = min(900, vis.height * 0.94)
            window.setFrame(NSRect(x: vis.midX - w / 2, y: vis.midY - h / 2, width: w, height: h), display: false)
        }
        fitWindowToScreen()
        window.makeKeyAndOrderFront(nil)
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.fitWindowToScreen() }
        }
    }

    /// The screen that shows most of the window (a frame saved on a display since unplugged: the main one).
    private var bestScreen: NSScreen? {
        let f = window.frame
        let area = { (s: NSScreen) -> CGFloat in let r = s.frame.intersection(f); return r.isNull ? 0 : r.width * r.height }
        return NSScreen.screens.max { area($0) < area($1) }.flatMap { area($0) > 0 ? $0 : nil } ?? NSScreen.main ?? NSScreen.screens.first
    }

    /// Keep the whole window, title bar included, inside the visible part of its screen (below the menu bar, above
    /// the Dock), whatever size was saved, and let it be made as small as that screen needs.
    func fitWindowToScreen() {
        guard let vis = bestScreen?.visibleFrame else { return }
        window.minSize = NSSize(width: min(900, vis.width), height: min(560, vis.height))
        var f = window.frame
        f.size.width = min(f.width, vis.width); f.size.height = min(f.height, vis.height)
        f.origin.x = min(max(f.minX, vis.minX), vis.maxX - f.width)
        f.origin.y = min(max(f.minY, vis.minY), vis.maxY - f.height)
        if f != window.frame { window.setFrame(f, display: true) }
    }

    // MARK: engine

    func start(open url: URL?) {
        Task {
            let found = await Task.detached { Engine.findPython() }.value
            guard let py = found else { missingPython(then: url); return }
            python = py
            TerminalSession.python = py
            do { try engine.start(python: py) } catch { alert("Couldn't start the engine", error.localizedDescription); return }
            engineFor = nil
            engineAnswered = false
            engine.onEvent = { [weak self] e in self?.event(e) }
            engine.onExit = { [weak self] code in
                guard let self else { return }
                if self.engineFor != nil && self.remote == nil { return }      // still connecting: openRemote says why
                if let r = self.remote { self.connectionLost(r); return }
                if !self.engineAnswered { self.engineWontStart(py, code: code, then: url); return }
                self.alert("The Fignimble engine stopped", "Exit status \(code). The end of its log (~/Library/Logs/fignimble.log):\n\n" + Engine.logTail())
            }
            runShotsIfAsked()                           // its scenes wait for what they need
            let j = try? await engine.json("journals")
            guard engine.isRunning else { return }      // stopped before answering: onExit offers another Python
            engineAnswered = true
            if let j { journals = j.compactMapValues { $0 as? [String: Any] } }
            if let p = try? await engine.json("posters") { posters = p.compactMapValues { $0 as? [String: Any] } }
            if let t = try? await engine.json("poster_templates") { posterTemplates = t["templates"] as? [[String: Any]] ?? [] }
            // a project only when one was opened with the app (a figure file or folder double-clicked, dropped on
            // its icon, or given on the command line); otherwise it starts empty: File ▸ Open Recent has the last ones
            if let u = url { openProject(at: u) }
            else { inspector.update(); status.stringValue = "Open a project (⌘O), one of File ▸ Open Recent, or make a new one (⇧⌘N)." }
        }
    }

    /// The engine ended before its first answer: that Python passes the import check but can't run it (an ipython,
    /// a wrapper that doesn't pass on its input, ...). It is forgotten if it was the one chosen, so the next launch
    /// doesn't try it again, and the choice of a Python is offered.
    private func engineWontStart(_ py: String, code: Int32, then url: URL?) {
        if UserDefaults.standard.string(forKey: "python") == py { UserDefaults.standard.removeObject(forKey: "python") }
        let tail = Engine.logTail(6)
        missingPython(then: url, because: "\(py) passed the check for matplotlib, pymupdf, pillow and numpy, but the Fignimble engine stopped as it started (exit status \(code))."
                      + (tail.isEmpty ? "" : " The end of its log (~/Library/Logs/fignimble.log):\n\n" + tail))
    }

    /// No Python that can render: offer to set one up (setup_python.sh, a minute or two), or to choose one.
    /// `because`: why the Python found can't be used, said first.
    private func missingPython(then url: URL?, because: String? = nil) {
        let a = NSAlert()
        a.messageText = because == nil ? "Fignimble needs to set up Python" : "That Python can't run Fignimble"
        a.informativeText = (because.map { $0 + "\n\n" } ?? "") + "Fignimble draws figures with Python (matplotlib and PyMuPDF). Set Up Python downloads Python 3.11 and those packages into ~/Library/Application Support/Fignimble (about 260 MB, usually under a minute). Nothing else on your Mac changes and no password is needed.\n\nOr choose a Python of your own (3.10 or later) that has matplotlib, pymupdf, pillow and numpy installed."
        a.addButton(withTitle: "Set Up Python")
        a.addButton(withTitle: "Choose a Python…")
        a.addButton(withTitle: "Quit")
        // FIGNIMBLE_TEST_SETUP: a headless check of the setup, as if Set Up Python were pressed
        let test = ProcessInfo.processInfo.environment["FIGNIMBLE_TEST_SETUP"] != nil
        switch test ? .alertFirstButtonReturn : a.runModal() {
        case .alertFirstButtonReturn:
            PythonSetup.run(over: window) { [weak self] ok in
                guard let self else { return }
                guard ok else { if !test { self.missingPython(then: url) }; return }
                // start with the Python just made; if even that can't render, say so rather than offer the setup again
                if Engine.canImportEngineNeeds(PythonSetup.python) {
                    UserDefaults.standard.set(PythonSetup.python, forKey: "python")
                    self.start(open: url)
                } else {
                    self.alert("Python was set up but can't draw figures",
                               "\(PythonSetup.python) isn't Python 3.10 or later with matplotlib, pymupdf, pillow and numpy. The log is ~/Library/Logs/fignimble.log.")
                }
            }
        case .alertSecondButtonReturn:
            let o = NSOpenPanel()
            o.message = "A python3 with matplotlib, pymupdf, pillow and numpy (for example a conda or venv's bin/python3)"
            o.canChooseFiles = true; o.treatsFilePackagesAsDirectories = true; o.showsHiddenFiles = true
            if o.runModal() == .OK, let u = o.url {
                if Engine.canImportEngineNeeds(u.path) {
                    UserDefaults.standard.set(u.path, forKey: "python")
                    start(open: url)
                } else {
                    let b = NSAlert(); b.messageText = "That isn't Python 3.10 or later with matplotlib, pymupdf, pillow and numpy"
                    b.informativeText = u.path; b.runModal()
                    missingPython(then: url)
                }
            } else { missingPython(then: url) }
        default: NSApp.terminate(nil)
        }
    }

    private func event(_ e: [String: Any]) {
        switch e["event"] as? String {
        case "changed":
            code.reloadIfChanged()
            refresh()
        case "rendered":
            Task { await refreshState() }
        case "recorded":                                // exports/ and fignimble.lock brought up to date (repro.py)
            recorded(e)
        case "needs_packages":                          // a server's Python lacks the engine's packages (tunnel.py)
            remoteNeeds = e
        default: break
        }
    }

    /// Until the engine work queued so far is done.
    func waitForQueue() async { await queue?.value }

    /// Run engine work one piece at a time, in order.
    func enqueue(_ work: @escaping @MainActor () async -> Void) {
        let prev = queue
        queue = Task { @MainActor in
            await prev?.value
            await work()
        }
    }

    /// The engine for a project here (nil) or on a server: the running one, or a new one in its place.
    /// The connection to a project's server closed (the tunnel exits when it does): Reconnect opens it again, with
    /// the same figure; edits that hadn't gone across are still in the mirror and go then.
    private func connectionLost(_ r: RemoteProject) {
        let a = NSAlert()
        a.messageText = "The connection to \(r.host) closed"
        a.informativeText = "Edits that hadn't reached \(r.host) yet are kept on this Mac and sent when you reconnect.\n\n"
            + Engine.logTail(4)
        a.addButton(withTitle: "Reconnect"); a.addButton(withTitle: "Close")
        let fig = current
        a.beginSheetModal(for: window) { [weak self] resp in
            guard resp == .alertFirstButtonReturn, let self else { return }
            self.openRemote(r, figure: fig)
        }
    }

    private func useEngine(for target: RemoteProject?) -> Bool {
        if engineFor == target && engine.isRunning { return true }
        guard let py = python else { return false }
        engine.shutdown(wait: engineFor == nil ? 2 : 15)
        project = nil; remote = nil
        do { try engine.start(python: py, remote: target) } catch {
            alert("Couldn't start the engine", error.localizedDescription); return false
        }
        engineFor = target
        TerminalSession.remote = target
        return true
    }

    func openProject(at url: URL) {
        var dir = url, fig: String?
        var isDir: ObjCBool = false
        FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir)
        if !isDir.boolValue { dir = url.deletingLastPathComponent(); fig = url.deletingPathExtension().lastPathComponent }
        if code.dirty { code.save() }
        enqueue { [self] in
            guard useEngine(for: nil) else { return }
            let r: [String: Any]
            do {
                r = try await engine.json("open", ["project": dir.path])
                project = r["project"] as? String ?? dir.path
            } catch { alert("Couldn't open \(dir.lastPathComponent)", error.localizedDescription); return }
            // the engine opens the project a folder is in (a panels/ folder, a panel file: up to its fignimble.toml)
            let root = URL(fileURLWithPath: project ?? dir.path)
            if root.path != dir.path { fig = nil }
            remote = nil
            MainController.noteRecent(root.path)
            NSDocumentController.shared.noteNewRecentDocumentURL(root)
            window.representedURL = root
            await opened(title: root.lastPathComponent, figure: fig)
            checkMarker(r)
        }
    }

    /// A project on an SSH server: the engine started there (over ssh, by fignimble.tunnel), the project's files
    /// mirrored here.
    func openRemote(_ r: RemoteProject, figure fig: String? = nil) {
        if code.dirty { code.save() }
        enqueue { [self] in
            busy("Logging in to \(r.host)…")
            do { try await logIn(r.host) } catch {
                idle()
                if case SSH.Failure.canceled = error { return }
                alert("Couldn't connect to \(r.host)", error.localizedDescription)
                return
            }
            busy("Connecting to \(r.host)…")
            remoteNeeds = nil
            guard useEngine(for: r) else { idle(); return }
            var opening: [String: Any] = [:]
            do {
                let res = try await engine.json("open", ["project": r.mirror])
                project = res["project"] as? String ?? r.mirror
                opening = res
            } catch {
                idle()
                engine.shutdown(wait: 2)                  // the tunnel that couldn't start the engine there
                engineFor = nil
                if let needs = remoteNeeds { remoteNeeds = nil; missingRemotePackages(needs, for: r, error: error.localizedDescription); return }
                alert("Couldn't open \(r.path) on \(r.host)", error.localizedDescription + "\n\n" + Engine.logTail(6))
                return
            }
            idle()
            remote = r
            MainController.noteRecent(r.recentKey)
            window.representedURL = nil
            await opened(title: r.title, figure: fig)
            checkMarker(opening)
        }
    }

    /// The server's Python lacks the engine's packages: install them there with pip, or choose another Python.
    private func missingRemotePackages(_ needs: [String: Any], for r: RemoteProject, error: String) {
        let pip = needs["pip"] as? [String] ?? []
        let a = NSAlert()
        a.messageText = pip.isEmpty ? "\(r.python) on \(r.host) is too old" : "\(r.python) on \(r.host) doesn't have \(pip.joined(separator: ", "))"
        a.informativeText = error
        if !pip.isEmpty { a.addButton(withTitle: "Install") }
        a.addButton(withTitle: "Choose Another Python…")
        a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { [weak self] resp in
            guard let self else { return }
            let choice = resp.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue - (pip.isEmpty ? -1 : 0)
            if choice == 0 {
                self.enqueue { [weak self] in
                    guard let self, await self.installRemotePackages(pip, python: r.python, host: r.host) else { return }
                    self.openRemote(r)
                }
            } else if choice == 1 {
                let d = RemoteDialog(main: self, mode: .project, prefill: r)
                d.onOpenProject = { [weak self] p in self?.openRemote(p) }
                d.begin()
            }
        }
    }

    /// After a project opened: its figures, the first (or `fig`) on the canvas.
    private func opened(title: String, figure fig: String?) async {
        window.title = "Fignimble — " + title
        current = nil; figure = nil
        canvas.selection = []
        code.clear()
        await refreshState()
        if terminal.hasStarted, let p = project { terminal.start(in: p, original: nil) }
        let name = fig ?? (figures.first?["name"] as? String)
        if let n = name { await load(n) } else { canvas.setFigure(nil); inspector.update(); status.stringValue = "No figures yet: File ▸ New Figure (⌘N)." }
        fitWindowToScreen()
        explainProjectOnce()
        checkPackages(asked: false)
    }

    /// A project's fignimble.toml, which says at a glance that Fignimble made the folder and which version: offered
    /// (once per project) for folders of figures made before projects had one, and a warning when it names a newer
    /// Fignimble than this one.
    func checkMarker(_ r: [String: Any]) {
        guard let p = project, ProcessInfo.processInfo.environment["FIGNIMBLE_SHOTS"] == nil else { return }
        if r["newer"] as? Bool == true {
            toast("This project was made with Fignimble \(r["madeWith"] as? String ?? "") and this is \(r["version"] as? String ?? "an older one"): update Fignimble if something doesn't draw", error: true)
            return
        }
        guard r["marker"] as? Bool == false, r["looksLikeProject"] as? Bool == true else { return }
        var seen = UserDefaults.standard.stringArray(forKey: "markerOffered") ?? []
        guard !seen.contains(p) else { return }
        seen.append(p)
        UserDefaults.standard.set(Array(seen.suffix(100)), forKey: "markerOffered")
        enqueue { [weak self] in
            guard let self else { return }
            let a = NSAlert()
            a.messageText = "Mark this folder as a Fignimble project?"
            a.informativeText = "Fignimble can add a small file, fignimble.toml, to \((p as NSString).lastPathComponent). It tells anyone "
                + "looking at the folder (or its repository) that its figures are made with Fignimble, and which version. "
                + "Fignimble and the fignimble command also use it to find the project from any folder inside it."
            a.addButton(withTitle: "Add fignimble.toml"); a.addButton(withTitle: "Not Now")
            guard await a.beginSheetModal(for: self.window) == .alertFirstButtonReturn else { return }
            if (try? await self.engine.json("add_marker")) != nil { self.toast("Added fignimble.toml") }
            else { self.toast("Couldn't add fignimble.toml", error: true) }
        }
    }

    @objc func checkPackagesNow(_ sender: Any?) { checkPackages(asked: true) }

    /// What Fignimble (engine/requirements.txt) and the project (its requirements.txt) need that the Python lacks:
    /// offered for install with pip, once per project and set of packages unless asked from the menu.
    func checkPackages(asked: Bool) {
        enqueue { [weak self] in
            guard let self, let r = try? await self.engine.json("requirements") else { return }
            let rows = ((r["engine"] as? [[String: Any]]) ?? []) + ((r["project"] as? [[String: Any]]) ?? [])
            let reqs = rows.compactMap { $0["requirement"] as? String }
            guard !reqs.isEmpty else { if asked { self.toast("Everything Fignimble and this project need is installed") }; return }
            let key = (self.project ?? "") + "|" + reqs.joined(separator: ",")
            var seen = UserDefaults.standard.stringArray(forKey: "packagesOffered") ?? []
            if !asked && (seen.contains(key) || ProcessInfo.processInfo.environment["FIGNIMBLE_SHOTS"] != nil) { return }
            seen.append(key)
            UserDefaults.standard.set(Array(seen.suffix(50)), forKey: "packagesOffered")
            let python = r["python"] as? String ?? "Fignimble's Python"
            let list = rows.map { row -> String in
                let why = row["why"] as? String ?? ""
                return "• \(row["requirement"] as? String ?? "")" + (why == "not installed" ? "" : " (\(why))")
            }.joined(separator: "\n")
            let a = NSAlert()
            a.messageText = "Install the packages this needs?"
            let fromProject = !((r["project"] as? [Any]) ?? []).isEmpty
            a.informativeText = (fromProject ? "This project's requirements.txt and Fignimble list packages" : "Fignimble needs packages")
                + " that its Python doesn't have:\n\n\(list)\n\nInstall installs them with pip into \((python as NSString).abbreviatingWithTildeInPath)."
            a.addButton(withTitle: "Install"); a.addButton(withTitle: "Not Now")
            guard await a.beginSheetModal(for: self.window) == .alertFirstButtonReturn else { return }
            self.busy("Installing \(reqs.count == 1 ? reqs[0] : "\(reqs.count) packages")…")
            let res = (try? await self.engine.json("install", ["requirements": reqs])) ?? ["ok": false, "output": "the engine stopped"]
            self.idle()
            if res["ok"] as? Bool == true {
                self.toast("Installed \(reqs.joined(separator: ", "))")
                await self.refreshState()
                if let n = self.current { await self.load(n) }       // the engine renders again with them
            } else {
                self.alert("The packages couldn't be installed", res["output"] as? String ?? "")
            }
        }
    }

    /// The first time a project is opened: where its code is, and that edits go straight into it.
    private func explainProjectOnce() {
        guard let p = project, ProcessInfo.processInfo.environment["FIGNIMBLE_SHOTS"] == nil else { return }
        var seen = UserDefaults.standard.stringArray(forKey: "explainedProjects") ?? []
        guard !seen.contains(p) else { return }
        seen.append(p)
        UserDefaults.standard.set(seen, forKey: "explainedProjects")
        let fig = figure.map { ($0.file as NSString).lastPathComponent } ?? "the figure files"
        let a = NSAlert()
        a.messageText = "Your figures are Python code"
        a.informativeText = "This figure is \(fig) in\n\((p as NSString).abbreviatingWithTildeInPath)\n\n"
            + "Everything you do here (moving, resizing, retyping, styling) is written into that code straight away; there is "
            + "nothing to save. The Code tab (⌥⌘E) shows the code of whatever you select: a panel's own drawing code, or the "
            + "figure's line that places it. Agents in the terminal edit the same files."
        a.addButton(withTitle: "Got It")
        a.addButton(withTitle: "Show in Finder")
        a.beginSheetModal(for: window) { r in
            if r == .alertSecondButtonReturn { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: self.figure?.file ?? p)]) }
        }
    }

    func refreshState() async {
        guard project != nil, let s = try? await engine.json("state") else { return }
        state = s
        let before = figures.firstIndex { $0["name"] as? String == current }
        figures = s["figures"] as? [[String: Any]] ?? []
        // the figure on screen is gone (deleted, cut, a New Figure undone): the one next to it in the list instead
        if let c = current, !figures.contains(where: { $0["name"] as? String == c }) {
            current = nil; figure = nil; canvas.selection = []
            if let n = (figures.isEmpty ? nil : figures[min(before ?? 0, figures.count - 1)])?["name"] as? String {
                enqueue { [self] in await load(n) }
            } else {
                canvas.setFigure(nil); navigator.setLayers(nil, selection: []); inspector.update(); code.clear()
                status.stringValue = "No figures yet: File ▸ New Figure (⌘N)."
            }
        }
        history.set(done: s["history"] as? [String] ?? [], undone: s["future"] as? [String] ?? [])
        navigator.setFigures(figures, current: current)
        var files: [String] = []
        if let f = figure?.file, !f.isEmpty { files.append(f) }
        if let p = project { files.append(p + "/style.py") }
        files += (s["panels"] as? [String] ?? []).map { project! + "/" + $0 }
        code.setFiles(files.filter { FileManager.default.fileExists(atPath: $0) })
    }

    private func load(_ name: String) async {
        busy("Rendering \(name)…")
        do {
            let r = try await engine.json("figure", ["name": name, "pxPerMM": canvas.wantedPxPerMM, "dark": darkPreview])
            lastPx = canvas.wantedPxPerMM
            let switched = current != name
            current = name
            apply(r)
            if switched, let f = figure, !f.file.isEmpty { code.open(f.file, line: nil) }
            idle()
        } catch { idle(); toast(error.localizedDescription, error: true) }
        await refreshState()
    }

    func loadFigure(_ name: String) async { await load(name) }

    /// Show a figure from the list.
    func show(figure name: String) {
        guard name != current else { return }
        if code.dirty { code.save() }
        canvas.selection = []
        enqueue { [self] in await load(name) }
    }

    /// Render the figure on screen again (its files changed, or the zoom needs sharper pictures).
    func refresh(force: Bool = false) {
        guard let name = current else { Task { await refreshState() }; return }
        enqueue { [self] in await load(name) }
    }

    func apply(_ r: [String: Any]) {
        let f = FigureData(r)
        figure = f
        canvas.setFigure(f)
        navigator.setLayers(f, selection: canvas.selection)
        problems.set(f.warnings)
        captionPane.set(f.caption, figure: f.name, table: f.isTable)
        drawer.rebuildStrip()
        inspector.update()
        hRuler.needsDisplay = true; vRuler.needsDisplay = true
        if let err = f.error {
            status.stringValue = "The figure's code doesn't run: " + (err.split(separator: "\n").last.map(String.init) ?? "")
        } else {
            let n = f.warnings.filter { $0.level != "info" }.count
            let where_ = (f.file as NSString).abbreviatingWithTildeInPath
            status.stringValue = where_ + "  ·  \(mm(Double(f.size.width))) × \(mm(Double(f.size.height))) mm" + (f.journal.map { "  ·  \($0["name"] as? String ?? "")" } ?? "")
                + (n > 0 ? "  ·  \(n) preflight warning\(n == 1 ? "" : "s")" : "")
        }
        updateZoomLabel()
    }

    /// An edit of the figure on screen: `args` for the engine's `edit` (op and its arguments).
    func edit(_ args: [String: Any]) {
        guard let name = current else { return }
        var a = args
        a["figure"] = name
        a["pxPerMM"] = lastPx
        enqueue { [self] in
            busy(nil)
            do {
                let r = try await engine.json("edit", a)
                if let f = r["figure"] as? [String: Any] { apply(f) }
                if r["ok"] as? Bool == true {
                    if let sel = r["select"] as? [String], !sel.isEmpty { canvas.selection = sel; navigator.selectLayers(sel) }
                    idle()
                } else {
                    canvas.clearPending()
                    toast(r["error"] as? String ?? "That didn't work", error: true)
                }
            } catch { canvas.clearPending(); toast(error.localizedDescription, error: true) }
            code.reloadIfChanged()
            await refreshState()
        }
    }

    func writeSource(_ path: String, _ text: String, done: @escaping () -> Void) {
        enqueue { [self] in
            busy("Applying the code…")
            do {
                var a: [String: Any] = ["file": path, "text": text, "pxPerMM": lastPx]
                if let c = current { a["figure"] = c }
                let r = try await engine.json("write_source", a)
                if let f = r["figure"] as? [String: Any] { apply(f) }
                idle()
                done()
            } catch { toast(error.localizedDescription, error: true) }
            await refreshState()
        }
    }

    // MARK: messages

    private var busyShown = false
    func busy(_ text: String?) {
        busyShown = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.35) { [weak self] in
            guard let self, self.busyShown else { return }
            self.pill.show(text ?? "Rendering…", symbol: nil, tint: nil, spinning: true, cancellable: false)
        }
    }
    func idle() { busyShown = false; pill.hide() }
    func toast(_ text: String, error: Bool = false) {
        busyShown = false
        pill.show(text, symbol: error ? "exclamationmark.triangle.fill" : "checkmark.circle.fill", tint: error ? .systemOrange : .systemGreen)
        let t = text
        DispatchQueue.main.asyncAfter(deadline: .now() + (error ? 6 : 2.5)) { [weak self] in
            guard let self, self.pill.label.stringValue == t, !self.pill.hovered else { return }
            self.pill.hide()
        }
    }
    func alert(_ title: String, _ text: String) {
        let a = NSAlert(); a.messageText = title; a.informativeText = text; a.addCopyButton()
        a.beginSheetModal(for: window)
    }

    private func cursorMoved(_ p: CGPoint?) {
        hRuler.cursor = p; vRuler.cursor = p
    }

    func hint(for e: Element?) {
        guard let e = e, let f = figure else { return }
        var s = e.title(label: f.labelText(e.oid))
        if e.isLabel { s += e.detached ? "  ·  unlinked from its panel: drag moves only it, double-click links it again"
                                       : "  ·  drag moves every label, ⌥-drag this one, double-click unlinks it from its panel" }
        else if e.kind == "panel" { s += "  ·  double-click for its code" }
        else if e.kind == "text" { s += "  ·  double-click to retype" }
        if let l = e.line { s += "  ·  line \(l)" }
        status.stringValue = s
    }

    private func viewChanged() {
        hRuler.needsDisplay = true; vRuler.needsDisplay = true
        updateZoomLabel()
        // sharper sprites when zoomed in far enough
        let want = canvas.wantedPxPerMM
        if want > lastPx * 1.4 || want < lastPx / 2.5 {
            lastPx = want
            NSObject.cancelPreviousPerformRequests(withTarget: self, selector: #selector(zoomRefresh), object: nil)
            perform(#selector(zoomRefresh), with: nil, afterDelay: 0.4)
        }
    }
    @objc private func zoomRefresh() { refresh() }
    private func updateZoomLabel() {
        zoomLabel.stringValue = "\(Int((canvas.scale / (72 / 25.4) * 100).rounded()))%"
    }

    // MARK: selection

    func selectionChanged() {
        navigator.selectLayers(canvas.selection)
        inspector.update()
        hRuler.needsDisplay = true; vRuler.needsDisplay = true
        showCodeOfSelection()
    }

    /// The Code tab, when it is up, shows the code of what is selected: a panel's own code (at its function, with a
    /// link to where the figure places it), the line of the figure that makes anything else, or the figure's file.
    private func showCodeOfSelection() {
        guard drawer.page == .code, !middle.isSubviewCollapsed(drawer), !code.dirty, let fig = figure else { return }
        guard let oid = canvas.selection.first, let picked = fig.element(oid) else {
            if !fig.file.isEmpty, code.path != fig.file { code.open(fig.file, line: nil) }
            return
        }
        let e = picked.isLabel ? (fig.element(picked.panelOid ?? "") ?? picked) : picked
        let place = e.file.flatMap { f in e.line.map { (f, $0) } }
        let drawsCode = e.kind == "panel" || (e.kind == "inset" && e.inner == "panel")
        if drawsCode, let f = e.codeFile, f.hasSuffix(".py") {
            code.open(f, line: e.codeLine, placedBy: place)
        } else if let (f, l) = place {
            code.open(f, line: l)
        }
    }

    func select(_ oids: [String]) {
        canvas.selection = oids.flatMap { canvas.withGroup($0) }.reduce(into: [String]()) { if !$0.contains($1) { $0.append($1) } }
    }

    func updateInspector() { inspector.update() }

    func toolChanged() {
        toolControl.selectedSegment = Tool.allCases.firstIndex(of: canvas.tool) ?? 0
        if canvas.tool != .select { status.stringValue = "\(canvas.tool.title): " + (canvas.tool == .text ? "click where the text goes" : "drag on the figure (⌥ keeps the tool, ⇧ constrains)") }
    }
    @objc private func toolPicked() { canvas.tool = Tool.allCases[toolControl.selectedSegment]; window.makeFirstResponder(canvas) }

    // MARK: code

    func showCode(of e: Element) {
        setDrawer(visible: true)
        drawer.show(.code)
        if e.kind == "panel" || e.kind == "inset" || e.kind == "figure" || (e.kind == "text" && e.s("source") != nil),
           let f = e.codeFile { code.open(f, line: e.codeLine) }
        else if let f = e.file { code.open(f, line: e.line) }
    }

    func promptText(_ title: String, _ value: String, _ done: @escaping (String) -> Void) {
        let a = NSAlert(); a.messageText = title
        let f = NSTextField(string: value); f.frame = NSRect(x: 0, y: 0, width: 240, height: 22)
        a.accessoryView = f; a.addButton(withTitle: "OK"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { r in if r == .alertFirstButtonReturn { done(f.stringValue) } }
        DispatchQueue.main.async { a.window.makeFirstResponder(f) }
    }

    // MARK: edits on the selection

    private var sel: [String] { canvas.selection }
    private var selElements: [String] { sel.filter { !($0.hasPrefix("L")) } }

    func nudge(dx: Double, dy: Double) {
        guard !sel.isEmpty else { return }
        nudgeTotal.x += dx; nudgeTotal.y += dy
        canvas.showNudge(sel, nudgeTotal)
        nudgeWork?.cancel()
        let oids = sel
        let w = DispatchWorkItem { [weak self] in
            guard let self else { return }
            let d = self.nudgeTotal
            self.nudgeTotal = .zero
            self.edit(["op": "move", "oids": oids, "dx": d.x, "dy": d.y])
        }
        nudgeWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: w)
    }

    func order(_ how: String) { if !selElements.isEmpty { edit(["op": "order", "oids": selElements, "how": how]) } }
    func align(_ how: String, to: String? = nil) {
        guard !selElements.isEmpty else { return }
        let t = to ?? (selElements.count == 1 ? "figure" : alignTo)
        edit(["op": "align", "oids": selElements, "how": how, "to": t, "key": selElements[0]])
    }
    func distribute(_ axis: String, gap: Double? = nil) {
        var a: [String: Any] = ["op": "distribute", "oids": selElements, "axis": axis]
        if let g = gap { a["gap"] = g }
        edit(a)
    }
    func match(_ what: String) { edit(["op": "match", "oids": selElements, "what": what]) }

    @objc func deleteSelected(_ sender: Any?) { if !sel.isEmpty { let s = sel; canvas.selection = []; edit(["op": "delete", "oids": s]) } }
    @objc func delete(_ sender: Any?) { deleteSelected(sender) }
    @objc func duplicate(_ sender: Any?) { if !selElements.isEmpty { edit(["op": "duplicate", "oids": selElements, "dx": 4, "dy": 4]) } }
    /// Copy: the elements' code on the pasteboard (with the figure they came from); with nothing selected, the
    /// whole figure as a picture.
    @objc func copy(_ sender: Any?) { if selElements.isEmpty { copyFigure(sender) } else { copySelection(then: nil) } }

    /// Edit ▸ Copy Figure as Picture: the whole figure (its dark version when that is shown) on the pasteboard as
    /// vector PDF and as a 600 dpi PNG, for Word, PowerPoint, Keynote, Illustrator, mail and chat.
    @objc func copyFigure(_ sender: Any?) {
        guard let name = current else { return }
        let dark = darkPreview
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("fignimble-copy-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let pdf = dir.appendingPathComponent(name + ".pdf"), png = dir.appendingPathComponent(name + ".png")
        enqueue { [self] in
            defer { try? FileManager.default.removeItem(at: dir) }
            busy("Copying \(name)…")
            do {
                for (u, fmt) in [(pdf, "pdf"), (png, "png")] {
                    _ = try await engine.json("export", ["figure": name, "path": u.path, "format": fmt, "dpi": 600, "dark": dark])
                }
                guard let pdfData = try? Data(contentsOf: pdf), let pngData = try? Data(contentsOf: png) else {
                    toast("Couldn't copy \(name)", error: true); return
                }
                let item = NSPasteboardItem()
                item.setData(pdfData, forType: .pdf)
                item.setData(pngData, forType: .png)
                if let tiff = NSBitmapImageRep(data: pngData)?.tiffRepresentation(using: .lzw, factor: 0) { item.setData(tiff, forType: .tiff) }
                let pb = NSPasteboard.general
                pb.clearContents()
                pb.writeObjects([item])
                toast("Copied \(name)\(dark ? " (dark version)" : "") as a picture")
            } catch { toast(error.localizedDescription, error: true) }
        }
    }

    private func copySelection(then: (() -> Void)?) {
        guard let name = current, !selElements.isEmpty else { return }
        let oids = selElements
        enqueue { [self] in
            do {
                let r = try await engine.json("copy", ["figure": name, "oids": oids])
                let code = r["code"] as? [String] ?? []
                let payload = try JSONSerialization.data(withJSONObject: ["figure": name, "project": project ?? "", "code": code])
                let pb = NSPasteboard.general
                pb.clearContents()
                pb.setData(payload, forType: MainController.pasteType)
                pb.setString(code.joined(separator: "\n").replacingOccurrences(of: "\u{00a7}FIG\u{00a7}.", with: "fig."), forType: .string)
                then?()
            } catch { toast(error.localizedDescription, error: true) }
        }
    }

    @objc func cut(_ sender: Any?) {
        let s = selElements
        copySelection { [weak self] in self?.canvas.selection = []; self?.edit(["op": "delete", "oids": s]) }
    }

    /// Paste: elements copied from Fignimble (into this figure, shifted 4 mm, or another one where they were), or a
    /// picture from another app, saved into images/.
    @objc func paste(_ sender: Any?) {
        guard let name = current, let f = figure else { return }
        let pb = NSPasteboard.general
        if let d = pb.data(forType: MainController.pasteType),
           let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let code = obj["code"] as? [String] {
            let same = obj["figure"] as? String == name && obj["project"] as? String == project
            enqueue { [self] in
                let r = (try? await engine.json("paste", ["figure": name, "code": code, "dx": same ? 4 : 0, "dy": same ? 4 : 0, "pxPerMM": lastPx])) ?? [:]
                if let fig = r["figure"] as? [String: Any] { apply(fig) }
                if let sel = r["select"] as? [String], !sel.isEmpty { canvas.selection = sel }
                if r["ok"] as? Bool != true { toast(r["error"] as? String ?? "Couldn't paste", error: true) }
                await refreshState()
            }
            return
        }
        if let img = NSImage(pasteboard: pb), let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("fignimble-paste-\(UUID().uuidString).png")
            try? png.write(to: tmp)
            let w = min(60.0, Double(f.size.width) - 10)
            enqueue { [self] in
                let r = (try? await engine.json("paste_image", ["figure": name, "path": tmp.path, "x": 5, "y": 5, "w": w, "pxPerMM": lastPx])) ?? [:]
                if let fig = r["figure"] as? [String: Any] { apply(fig) }
                if let sel = r["select"] as? [String] { canvas.selection = sel }
                if r["ok"] as? Bool != true { toast(r["error"] as? String ?? "Couldn't paste the picture", error: true) }
                await refreshState()
            }
        }
    }

    /// Copy Style (the eyedropper): the colors, line and text settings of the selected element.
    @objc func copyStyle(_ sender: Any?) {
        guard let oid = sel.first, let e = figure?.element(oid) else { return }
        let keys = ["color", "stroke", "fill", "width", "dash", "opacity", "size", "weight", "style", "font", "tip", "head", "radius", "arrow"]
        copiedStyle = e.props.filter { keys.contains($0.key) }
        toast("Copied the look of \(e.title(label: figure?.labelText(oid)))")
    }
    @objc func pasteStyle(_ sender: Any?) {
        guard let st = copiedStyle, !selElements.isEmpty else { return }
        edit(["op": "paste_style", "oids": selElements, "props": st])
    }

    func rotate(by degrees: Double) { if !selElements.isEmpty { edit(["op": "rotate", "oids": selElements, "by": degrees]) } }
    func flip(_ axis: String) { if !selElements.isEmpty { edit(["op": "flip", "oids": selElements, "axis": axis]) } }
    @objc func rotateLeft(_ sender: Any?) { rotate(by: 90) }
    @objc func rotateRight(_ sender: Any?) { rotate(by: -90) }
    @objc func flipHorizontal(_ sender: Any?) { flip("h") }
    @objc func flipVertical(_ sender: Any?) { flip("v") }
    @objc func cropPicture(_ sender: Any?) { if let oid = sel.first { canvas.beginCrop(oid) } }
    @objc func toggleLock(_ sender: Any?) {
        guard let oid = sel.first, let e = figure?.element(oid) else { return }
        edit(["op": "set", "oid": oid, "props": ["locked": !e.locked]])
    }
    @objc func toggleHide(_ sender: Any?) {
        guard let oid = sel.first, let e = figure?.element(oid) else { return }
        edit(["op": "set", "oid": oid, "props": ["hidden": !e.hidden]])
    }
    func setLayer(_ oid: String, hidden: Bool? = nil, locked: Bool? = nil) {
        var p: [String: Any] = [:]
        if let h = hidden { p["hidden"] = h }
        if let l = locked { p["locked"] = l }
        edit(["op": "set", "oid": oid, "props": p])
    }

    func replacePicture(_ oid: String) {
        let o = NSOpenPanel(); o.allowedContentTypes = [.png, .jpeg, .tiff, .pdf, .svg, .gif, .bmp, .webP]
        o.message = "The picture to show in its place (copied into the project's images folder)"
        o.beginSheetModal(for: window) { [weak self] r in
            guard r == .OK, let u = o.url else { return }
            self?.edit(["op": "replace_source", "oid": oid, "path": u.path])
        }
    }

    @objc func addScaleBar(_ sender: Any?) {
        guard let oid = sel.first, let e = figure?.element(oid), e.kind == "image" || (e.kind == "inset" && e.inner == "image") else {
            toast("Select a picture (PNG, TIFF or JPEG) for the scale bar", error: true); return
        }
        let a = NSAlert(); a.messageText = "Scale bar"
        a.informativeText = "How long the bar is, and how big one pixel of the original picture is. Leave the pixel size empty to read it from the picture's metadata (OME-TIFF, ImageJ); preflight warns if there is none."
        let len = NSTextField(string: "50"), unit = NSTextField(string: "µm"), px = NSTextField(string: "")
        px.placeholderString = "from file"
        for f in [len, unit, px] { f.widthAnchor.constraint(equalToConstant: 80).isActive = true }
        let grid = NSGridView(views: [[NSTextField(labelWithString: "Length"), len], [NSTextField(labelWithString: "Unit"), unit],
                                      [NSTextField(labelWithString: "One pixel is"), px]])
        grid.frame = NSRect(x: 0, y: 0, width: 220, height: 90)
        a.accessoryView = grid; a.addButton(withTitle: "Add"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { [weak self] r in
            guard r == .alertFirstButtonReturn else { return }
            var props: [String: Any] = ["length": Double(len.stringValue) ?? 50, "unit": unit.stringValue]
            if let d = Double(px.stringValue.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: ".")) { props["pixel_size"] = d }
            self?.edit(["op": "add_scalebar", "oid": oid, "props": props])
        }
    }

    /// Unlink a panel's label from it (each then moves on its own), or link it again: `oid` is the label or panel.
    func toggleLabelLink(_ oid: String) {
        guard let f = figure, let e = f.element(oid) else { return }
        guard let label = e.isLabel ? e : f.label(of: e.oid) else { return }
        edit(["op": "label_detach", "oid": label.oid, "detach": !label.detached])
        toast(label.detached ? "Label \(label.text ?? "") moves with its panel again"
                             : "Label \(label.text ?? "") unlinked: the panel and its label now move separately (double-click the label to link it again)")
    }
    @objc func toggleSelectedLabelLink(_ sender: Any?) { if let oid = sel.first { toggleLabelLink(oid) } }
    /// The label the selection's first item is or has.
    private var selectedLabel: Element? {
        guard let f = figure, let oid = sel.first, let e = f.element(oid) else { return nil }
        return e.isLabel ? e : f.label(of: e.oid)
    }

    @objc func addZoom(_ sender: Any?) {
        guard !selElements.isEmpty else { toast("Select a picture (or a panel, then the one that magnifies it)", error: true); return }
        edit(["op": "add_zoom", "oids": Array(selElements.prefix(2))])
    }

    /// Undo or redo to a step of the history: `n` steps left done.
    func undoTo(_ n: Int) {
        guard let name = current else { return }
        enqueue { [self] in
            let r = (try? await engine.json("undo_to", ["n": n, "figure": name, "pxPerMM": lastPx])) ?? [:]
            if let f = r["figure"] as? [String: Any] { apply(f) }
            if r["ok"] as? Bool != true, let e = r["error"] as? String { toast(e, error: true) }
            code.reloadIfChanged()
            await refreshState()
        }
    }
    /// Recolor one mark of a panel with the color picker; the color is written once it settles.
    private var markColor: (oid: String, mark: PanelMark)?
    private var markColorWork: DispatchWorkItem?
    func recolorMark(_ oid: String, _ mark: PanelMark) {
        markColor = (oid, mark)
        let p = NSColorPanel.shared
        p.setTarget(nil); p.setAction(nil)
        p.color = NSColor(hex: mark.color) ?? .black
        p.setTarget(self); p.setAction(#selector(markColorChanged(_:)))
        p.makeKeyAndOrderFront(nil)
        toast("Pick a color for \(mark.key.replacingOccurrences(of: "ax0.", with: "")) (\(mark.color))")
    }
    @objc private func markColorChanged(_ sender: NSColorPanel) {
        guard let (oid, mark) = markColor else { return }
        let c = sender.color.hex
        markColorWork?.cancel()
        let w = DispatchWorkItem { [weak self] in
            self?.edit(["op": "panel_color", "oid": oid, "keys": [mark.key], "color": c, "old": mark.color])
            self?.markColor = (oid, PanelMark(key: mark.key, color: c, box: mark.box))
        }
        markColorWork = w
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.45, execute: w)
    }

    @objc func setVision(_ sender: NSMenuItem) {
        canvas.vision = ["normal", "deuteranopia", "protanopia", "tritanopia", "grayscale"][sender.tag]
        if canvas.vision != "normal" { toast("The figure as seen with \(canvas.vision) (only on screen)") }
    }

    @objc func showHistory(_ sender: Any?) { setDrawer(visible: true); drawer.show(.history) }
    @objc func selectAll(_ sender: Any?) { canvas.selection = figure?.elements.map(\.oid) ?? [] }
    @objc func group(_ sender: Any?) { if selElements.count > 1 { edit(["op": "group", "oids": selElements]) } }
    @objc func ungroup(_ sender: Any?) { if !selElements.isEmpty { edit(["op": "ungroup", "oids": selElements]) } }
    @objc func bringToFront(_ sender: Any?) { order("front") }
    @objc func bringForward(_ sender: Any?) { order("forward") }
    @objc func sendBackward(_ sender: Any?) { order("backward") }
    @objc func sendToBack(_ sender: Any?) { order("back") }
    @objc func alignLeft(_ sender: Any?) { align("left") }
    @objc func alignCenter(_ sender: Any?) { align("hcenter") }
    @objc func alignRight(_ sender: Any?) { align("right") }
    @objc func alignTop(_ sender: Any?) { align("top") }
    @objc func alignMiddle(_ sender: Any?) { align("vcenter") }
    @objc func alignBottom(_ sender: Any?) { align("bottom") }
    @objc func distributeH(_ sender: Any?) { distribute("h") }
    @objc func distributeV(_ sender: Any?) { distribute("v") }
    @objc func matchWidth(_ sender: Any?) { match("width") }
    @objc func matchHeight(_ sender: Any?) { match("height") }
    @objc func makeLayout(_ sender: Any?) {
        let free = sel.compactMap { figure?.element($0) }.filter { ($0.kind == "panel" || $0.kind == "image") && $0.parentOid == nil }
        guard free.count >= 2 else { toast("Select two or more panels placed by hand", error: true); return }
        edit(["op": "make_layout", "oids": free.map(\.oid), "gap": 4])
    }
    func releaseLayout(_ oid: String) { edit(["op": "release_layout", "oid": oid]) }
    @objc func releaseSelectedLayout(_ sender: Any?) {
        if let oid = sel.first, let top = figure?.element(oid)?.top, figure?.element(top)?.kind == "layout" { releaseLayout(top) }
    }

    @objc func toggleDarkPreview(_ sender: Any?) {
        darkPreview.toggle()
        refresh()
        toast(darkPreview ? "Showing the figure's dark version (for talks); exports stay light unless you ask" : "Showing the figure")
    }

    @objc func setAppearance(_ sender: NSMenuItem) {
        let names = ["system", "light", "dark"]
        UserDefaults.standard.set(names[sender.tag], forKey: "appearance")
        MainController.applyAppearance()
    }
    static func applyAppearance() {
        switch UserDefaults.standard.string(forKey: "appearance") {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: NSApp.appearance = nil
        }
    }

    @objc func editCode(_ sender: Any?) {
        if let oid = sel.first, let e = figure?.element(oid) { showCode(of: e) }
        else if let f = figure { setDrawer(visible: true); drawer.show(.code); code.open(f.file, line: nil) }
    }

    @objc func labelFormat(_ sender: NSMenuItem) {
        let p = Inspector.labelFormats[sender.tag]
        var props: [String: Any] = ["format": p.1, "case": p.2]
        if figure?.labelStyle["numbering"] as? String == "none" { props["numbering"] = "alpha" }     // letters again
        edit(["op": "labels", "props": props])
    }
    @objc func labelJournal(_ sender: NSMenuItem) {
        guard let k = sender.representedObject as? String, let lab = journals[k]?["labels"] as? [String: Any] else { return }
        edit(["op": "labels", "props": lab])
    }
    @objc func toggleLabelAlign(_ sender: Any?) {
        edit(["op": "labels", "props": ["align": !((figure?.labelStyle["align"] as? Bool) ?? true)]])
    }
    @objc func toggleLabelOrder(_ sender: Any?) {
        edit(["op": "labels", "props": ["order": (figure?.labelStyle["order"] as? String) == "source" ? "reading" : "source"]])
    }
    @objc func labelsOutside(_ sender: Any?) {
        // labels in the gutter above-left of each panel
        let size = num(figure?.labelStyle["size"] ?? 8)
        edit(["op": "labels", "props": ["dx": -(size * 0.35 + 0.5), "dy": -(size * 0.35 + 0.5)]])
    }
    @objc func labelsInside(_ sender: Any?) { edit(["op": "labels", "props": ["dx": 0, "dy": 0]]) }

    // MARK: undo

    /// Typing in a terminal: Undo and Redo are the shell's business (⌘Z there never changes the figure).
    private var inTerminal: Bool { (window.firstResponder as? NSView)?.isDescendant(of: terminal) ?? false }

    @objc func undoEdit(_ sender: Any?) {
        if inTerminal { return }
        if let tv = window.firstResponder as? NSTextView, tv.isEditable { tv.undoManager?.undo(); return }
        guard let name = current ?? figures.first?["name"] as? String else { return }
        enqueue { [self] in
            let r = (try? await engine.json("undo", ["figure": name, "pxPerMM": lastPx])) ?? [:]
            if let f = r["figure"] as? [String: Any] { apply(f) }
            if r["ok"] as? Bool != true, let e = r["error"] as? String { toast(e, error: true) }
            else if let l = r["label"] as? String { toast("Undid \(l)") }
            code.reloadIfChanged()
            await refreshState()
        }
    }
    @objc func redoEdit(_ sender: Any?) {
        if inTerminal { return }
        if let tv = window.firstResponder as? NSTextView, tv.isEditable { tv.undoManager?.redo(); return }
        guard let name = current else { return }
        enqueue { [self] in
            let r = (try? await engine.json("redo", ["figure": name, "pxPerMM": lastPx])) ?? [:]
            if let f = r["figure"] as? [String: Any] { apply(f) }
            if r["ok"] as? Bool != true, let e = r["error"] as? String { toast(e, error: true) }
            code.reloadIfChanged()
            await refreshState()
        }
    }

    // MARK: files

    // MARK: recent projects

    /// Projects opened lately, newest first (File ▸ Open Recent, and the empty window's buttons).
    static var recentProjects: [String] {
        let d = UserDefaults.standard
        // before Open Recent there was only the last project
        let r = d.stringArray(forKey: "recentProjects") ?? d.string(forKey: "lastProject").map { [$0] } ?? []
        return r.filter { $0.hasPrefix("ssh:") || FileManager.default.fileExists(atPath: $0) }
    }
    static func noteRecent(_ path: String) {
        var r = (UserDefaults.standard.stringArray(forKey: "recentProjects") ?? []).filter { $0 != path }
        r.insert(path, at: 0)
        UserDefaults.standard.set(Array(r.prefix(12)), forKey: "recentProjects")
    }
    @objc func openRecent(_ sender: NSMenuItem) {
        guard let p = sender.representedObject as? String else { return }
        if let r = RemoteProject(recentKey: p) { openRemote(r); return }
        guard FileManager.default.fileExists(atPath: p) else { toast("\(p) is no longer there", error: true); return }
        openProject(at: URL(fileURLWithPath: p))            // which applies unsaved code first, as Open Project does
    }
    @objc func clearRecent(_ sender: Any?) { UserDefaults.standard.removeObject(forKey: "recentProjects") }

    @objc func openProject(_ sender: Any?) {
        let p = NSOpenPanel()
        p.canChooseDirectories = true; p.canChooseFiles = true; p.allowedContentTypes = [.pythonScript, .folder]
        p.message = "Choose a Fignimble project folder (or a figure's .py file)"
        p.beginSheetModal(for: window) { [weak self] r in if r == .OK, let u = p.url { self?.openProject(at: u) } }
    }

    @objc func newProject(_ sender: Any?) {
        let p = NSSavePanel()
        p.message = "A new folder for the project's figures, panels and pictures"
        p.nameFieldStringValue = "fignimble"            // the convention: paper/fignimble/, marked by its fignimble.toml
        let journal = NSPopUpButton(frame: .zero, pullsDown: false)
        journal.menu?.addItem(withTitle: "No journal", action: nil, keyEquivalent: "")
        journal.menu?.addItem(.separator())
        if let menu = journal.menu {
            fillJournalMenu(menu) { k in
                let it = NSMenuItem(title: journalName(k), action: nil, keyEquivalent: ""); it.representedObject = k; return [it]
            }
        }
        if let i = journal.itemArray.firstIndex(where: { $0.representedObject as? String == "nature" }) { journal.selectItem(at: i) }
        let acc = NSStackView(views: [NSTextField(labelWithString: "Style for:"), journal]); acc.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        p.accessoryView = acc
        p.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .OK, let u = p.url else { return }
            let j = journal.selectedItem?.representedObject as? String ?? ""
            Task { @MainActor in
                do {
                    var a: [String: Any] = ["dir": u.path]
                    if !j.isEmpty { a["journal"] = j }
                    _ = try await self.engine.json("new_project", a)
                    self.openProject(at: u)
                } catch { self.alert("Couldn't make the project", error.localizedDescription) }
            }
        }
    }

    /// Units a new figure's size can be typed in: name, millimeters in one.
    static let sizeUnits: [(String, Double)] = [("mm", 1), ("cm", 10), ("in", 25.4), ("ft", 304.8)]

    @objc func newFigure(_ sender: Any?) {
        guard project != nil else { newProject(sender); return }
        let a = NSAlert(); a.messageText = "New figure"
        a.informativeText = "A figure file in the project. Its size in millimeters, centimeters, inches or feet (the code keeps millimeters)."
        let name = NSTextField(string: "fig\(figures.count + 1)")
        let units = MainController.sizeUnits
        var unit = units.firstIndex { $0.0 == UserDefaults.standard.string(forKey: "newFigureUnit") } ?? 0
        let show = { (v: Double) -> String in mm(v / units[unit].1) }
        let w = NSTextField(string: show(Double(figure?.size.width ?? 183))), h = NSTextField(string: show(120))
        for f in [name, w, h] { f.widthAnchor.constraint(equalToConstant: f === name ? 140 : 60).isActive = true }
        let unitMenu = NSPopUpButton(frame: .zero, pullsDown: false)
        unitMenu.addItems(withTitles: ["millimeters (mm)", "centimeters (cm)", "inches (in)", "feet (ft)"])
        unitMenu.selectItem(at: unit)
        // a figure of any size, or a poster from a template of a usual conference size (title, sections, a figure, a
        // QR code); the template sets how it hangs, and Landscape turns it
        let templates = posterTemplates
        let kind = NSPopUpButton(frame: .zero, pullsDown: false)
        kind.addItem(withTitle: "Figure (any size)")
        kind.menu?.addItem(.separator())
        for t in templates {
            kind.addItem(withTitle: "Poster: " + (t["name"] as? String ?? ""))
            kind.lastItem?.toolTip = (t["use"] as? String).map { $0.prefix(1).uppercased() + $0.dropFirst() + ". Check your conference's instructions." }
            kind.lastItem?.representedObject = t
        }
        kind.widthAnchor.constraint(equalToConstant: 210).isActive = true
        let landscape = NSButton(checkboxWithTitle: "Landscape", target: nil, action: nil)
        let template = { kind.selectedItem?.representedObject as? [String: Any] }
        let posterSize = { () -> (Double, Double)? in
            guard let t = template(), let k = t["poster"] as? String, let s = self.posters[k]?["size"] as? [Any], s.count == 2 else { return nil }
            let (a, b) = (num(s[0]), num(s[1]))
            return landscape.state == .on ? (max(a, b) == a ? (a, b) : (b, a)) : (min(a, b) == a ? (a, b) : (b, a))
        }
        var lastTemplate: String?
        let showKind = {
            // a template just picked hangs its usual way
            if let t = template(), let k = t["key"] as? String, k != lastTemplate {
                lastTemplate = k
                landscape.state = (t["landscape"] as? Bool ?? false) ? .on : .off
            }
            let p = posterSize()
            for f in [w, h] { f.isEnabled = p == nil }
            landscape.isEnabled = p != nil
            if let (pw, ph) = p {
                w.stringValue = show(pw); h.stringValue = show(ph)
                if name.stringValue.hasPrefix("fig") { name.stringValue = self.figures.contains { $0["name"] as? String == "poster" } ? "poster2" : "poster" }
            }
        }
        let change = Action { s in
            if (s as? NSPopUpButton) === unitMenu {
                // the size typed stays the same size, in the new unit
                let old = units[unit].1
                unit = unitMenu.indexOfSelectedItem
                for f in [w, h] { if let v = Double(f.stringValue.replacingOccurrences(of: ",", with: ".")) { f.stringValue = mm(v * old / units[unit].1) } }
            }
            showKind()
        }
        for c in [unitMenu, kind, landscape] as [NSControl] { c.target = change; c.action = #selector(Action.fire(_:)) }
        landscape.isEnabled = false
        let grid = NSGridView(views: [[NSTextField(labelWithString: "Name"), name], [NSTextField(labelWithString: "Kind"), kind],
                                      [NSView(), landscape], [NSTextField(labelWithString: "Width"), w],
                                      [NSTextField(labelWithString: "Height"), h], [NSTextField(labelWithString: "Units"), unitMenu]])
        grid.frame = NSRect(x: 0, y: 0, width: 280, height: 190)
        a.accessoryView = grid; a.addButton(withTitle: "Create"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { [weak self] r in
            _ = change
            guard let self, r == .alertFirstButtonReturn else { return }
            UserDefaults.standard.set(units[unit].0, forKey: "newFigureUnit")
            let k = units[unit].1
            let num = { (f: NSTextField, d: Double) -> Double in (Double(f.stringValue.replacingOccurrences(of: ",", with: ".")) ?? d / k) * k }
            let width = (num(w, 183) * 100).rounded() / 100, height = (num(h, 120) * 100).rounded() / 100
            var args: [String: Any] = ["name": name.stringValue, "width": width, "height": height]
            if let t = template() { args = ["name": name.stringValue, "poster": t["poster"] as? String ?? "a0", "landscape": landscape.state == .on] }
            self.enqueue { [weak self] in guard let self else { return }
                do {
                    let res = try await self.engine.json("new_figure", args)
                    if let n = res["name"] as? String { await self.refreshState(); await self.load(n) }
                    else if let e = res["error"] as? String { self.toast(e, error: true) }
                } catch { self.toast(error.localizedDescription, error: true) }
            }
        }
    }

    @objc func newPanel(_ sender: Any?) {
        guard let name = current, let f = figure else { return }
        let a = NSAlert(); a.messageText = "New panel"
        a.informativeText = "Makes panels/<name>.py with a draw(fig) function to edit (or to ask an agent to write), and places it on the figure."
        let field = NSTextField(string: "panel"); field.frame = NSRect(x: 0, y: 0, width: 200, height: 22)
        a.accessoryView = field; a.addButton(withTitle: "Create"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .alertFirstButtonReturn else { return }
            let stem = field.stringValue.replacingOccurrences(of: " ", with: "_")
            let c = CGPoint(x: f.size.width / 2 - 30, y: f.size.height / 2 - 22)
            self.enqueue { [weak self] in guard let self else { return }
                do {
                    let r = try await engine.json("new_panel", ["figure": name, "name": stem, "x": Double(c.x), "y": Double(c.y), "w": 60, "h": 45, "pxPerMM": lastPx])
                    if let fig = r["figure"] as? [String: Any] { apply(fig) }
                    if let sel = r["select"] as? [String] { canvas.selection = sel }
                    if let file = r["file"] as? String { setDrawer(visible: true); drawer.show(.code); await refreshState(); code.open(file, line: 1) }
                    if r["ok"] as? Bool == false { toast(r["error"] as? String ?? "", error: true) }
                } catch { toast(error.localizedDescription, error: true) }
            }
        }
    }

    @objc func insertPanelCode(_ sender: Any?) {
        guard let p = project else { return }
        let o = NSOpenPanel(); o.allowedContentTypes = [.pythonScript]; o.directoryURL = URL(fileURLWithPath: p + "/panels")
        o.message = "A Python file with a draw(fig) function"
        o.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .OK, let u = o.url, let f = self.figure else { return }
            self.importFiles([u], at: CGPoint(x: f.size.width / 2 - 30, y: f.size.height / 2 - 22))
        }
    }

    /// Insert ▸ Figure…: another figure of the project, placed whole and drawn from its code.
    @objc func insertFigure(_ sender: Any?) {
        guard let p = project, let name = current else { return }
        let others = figures.compactMap { $0["name"] as? String }.filter { $0 != name }
        guard !others.isEmpty else { toast("The project has no other figure to place: File ▸ New Figure makes one", error: true); return }
        let a = NSAlert(); a.messageText = "Place a figure"
        a.informativeText = "The figure is drawn from its code every time, so it stays as that figure is: change it there and it changes here. Scaling it scales its text and lines."
        let pick = NSPopUpButton(frame: NSRect(x: 0, y: 0, width: 200, height: 26), pullsDown: false)
        pick.addItems(withTitles: others)
        a.accessoryView = pick; a.addButton(withTitle: "Place"); a.addButton(withTitle: "Cancel")
        a.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .alertFirstButtonReturn, let n = pick.titleOfSelectedItem else { return }
            let m = num(self.figure?.poster?["margin"])
            self.importFiles([URL(fileURLWithPath: p + "/" + n + ".py")], at: CGPoint(x: max(5, m), y: max(5, m)))
        }
    }

    /// Insert ▸ QR Code…: a QR code for a link, in the figure's lower right corner.
    @objc func insertQR(_ sender: Any?) {
        guard let f = figure else { return }
        promptText("Link for the QR code", "https://") { [weak self] t in
            guard let self, !t.trimmingCharacters(in: .whitespaces).isEmpty else { return }
            let w = f.poster != nil ? 60.0 : 25.0
            let m = max(num(f.poster?["margin"]), 5)
            self.edit(["op": "insert", "kind": "qr", "props": ["data": t, "x": max(0, (Double(f.size.width) - w - m).rounded()),
                                                              "y": max(0, (Double(f.size.height) - w - m).rounded()), "w": w]])
        }
    }

    @objc func insertImage(_ sender: Any?) {
        guard figure != nil else { return }
        let o = NSOpenPanel(); o.allowsMultipleSelection = true
        o.allowedContentTypes = [.png, .jpeg, .tiff, .pdf, .svg, .gif, .bmp, .webP]
        o.message = "Pictures to place (copied into the project's images folder)"
        o.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .OK, let f = self.figure else { return }
            self.importFiles(o.urls, at: CGPoint(x: 5, y: 5 + 0 * f.size.height))
        }
    }

    func importFiles(_ urls: [URL], at p: CGPoint) {
        guard let name = current else { return }
        for (i, u) in urls.enumerated() {
            enqueue { [self] in
                do {
                    let r = try await engine.json("import_file", ["figure": name, "path": u.path, "x": Double(p.x) + Double(i) * 5, "y": Double(p.y) + Double(i) * 5, "pxPerMM": lastPx])
                    if let fig = r["figure"] as? [String: Any] { apply(fig) }
                    if let sel = r["select"] as? [String] { canvas.selection = sel }
                    if r["ok"] as? Bool == false { toast(r["error"] as? String ?? "", error: true) }
                } catch { toast(error.localizedDescription, error: true) }
                await refreshState()
            }
        }
    }

    /// A picture file placed at (x, y), w mm wide (it stays in images/ when it is there already); `lettered` false
    /// gives it no panel letter; `created`: the file was made for this (a BioCartoon icon), so undo removes it too.
    func placePicture(_ path: String, x: Double, y: Double, w: Double, lettered: Bool = true, created: Bool = false) {
        guard let name = current else { return }
        enqueue { [self] in
            do {
                var a: [String: Any] = ["figure": name, "path": path, "x": x, "y": y, "w": w, "pxPerMM": lastPx]
                if !lettered { a["label"] = NSNull() }
                if created { a["created"] = true }
                let r = try await engine.json("import_file", a)
                if let fig = r["figure"] as? [String: Any] { apply(fig) }
                if let sel = r["select"] as? [String] { canvas.selection = sel }
                if r["ok"] as? Bool == false { toast(r["error"] as? String ?? "", error: true) }
            } catch { toast(error.localizedDescription, error: true) }
            await refreshState()
        }
    }

    /// The resolutions (dpi) a journal asks for: photo, combination, line (the usual standard when none is named).
    func journalDPI(_ key: String?) -> (photo: Int, combination: Int, line: Int) {
        guard let k = key, let d = journals[k]?["dpi"] as? [String: Any] else { return (300, 600, 1200) }
        let n = { (s: String, v: Int) in (d[s] as? NSNumber)?.intValue ?? v }
        return (n("photo", 300), n("combination", 600), n("line", 1200))
    }

    @objc func exportFigure(_ sender: Any?) {
        guard let name = current, let f = figure else { return }
        let p = NSSavePanel()
        let format = NSPopUpButton(frame: .zero, pullsDown: false)
        var formats = ["pdf", "eps", "tiff", "svg", "png", "jpg", "pptx"]
        var titles = ["PDF (vector)", "EPS (vector)", "TIFF", "SVG (vector)", "PNG", "JPEG", "PowerPoint (.pptx)"]
        if f.isTableFigure {                // a table on its own: also as cells people can edit
            formats += ["docx", "xlsx", "csv", "tsv"]
            titles += ["Word table (.docx)", "Excel (.xlsx)", "CSV", "TSV"]
        }
        format.addItems(withTitles: titles)
        // any resolution, typed or picked; the journal's minimum is applied by the engine
        let dpis = [300, 500, 600, 1000, 1200, 2400]
        let dpi = NSComboBox()
        dpi.addItems(withObjectValues: dpis.map { "\($0)" }); dpi.stringValue = "600"; dpi.numberOfVisibleItems = 6
        dpi.widthAnchor.constraint(equalToConstant: 70).isActive = true
        let dpiValue = { () -> Int in max(72, min(4800, Int(dpi.stringValue.filter(\.isNumber)) ?? 600)) }
        // the journal whose resolutions the export meets: the style's, or another
        let styleJournal = f.style["journal"] as? String
        let journal = NSPopUpButton(frame: .zero, pullsDown: false)
        journal.menu?.addItem(withTitle: "General (300/600/1200 dpi)", action: nil, keyEquivalent: "")
        journal.menu?.addItem(.separator())
        if let menu = journal.menu {
            fillJournalMenu(menu) { k in
                let it = NSMenuItem(title: journalName(k), action: nil, keyEquivalent: ""); it.representedObject = k; return [it]
            }
        }
        // General unless asked: a journal's rules are picked here when the figure goes to that journal
        _ = styleJournal
        journal.selectItem(at: 0)
        let key = { journal.selectedItem?.representedObject as? String }
        let note = NSTextField(wrappingLabelWithString: "")
        note.font = Theme.font(10.5); note.textColor = .secondaryLabelColor; note.preferredMaxLayoutWidth = 520
        // pictures make it a combination figure (the engine checks the pixels themselves)
        let pictures = f.elements.contains { e in
            let src = (e.kind == "inset" ? e.item["source"] : e.props["source"]) as? String ?? ""
            return (e.kind == "image" || (e.kind == "inset" && e.inner == "image")) && !src.lowercased().hasSuffix(".svg") && !src.lowercased().hasSuffix(".pdf")
        }
        let cmykBox = NSButton(checkboxWithTitle: "CMYK", target: nil, action: nil)
        cmykBox.toolTip = "Colors converted for print (PDF, EPS, TIFF, JPEG; PDF and EPS through Ghostscript)"
        let outlineBox = NSButton(checkboxWithTitle: "Text as outlines", target: nil, action: nil)
        outlineBox.toolTip = "Text turned into shapes, for journals that ask for it (PDF, EPS, SVG)"
        let darkBox = NSButton(checkboxWithTitle: "Dark version", target: nil, action: nil)
        darkBox.state = darkPreview ? .on : .off
        darkBox.toolTip = "White text and lines on black, for slides"
        // for a print shop: the page past the figure's edges, and where to cut
        let bleed = NSTextField(string: f.poster != nil ? "3" : "0")
        bleed.widthAnchor.constraint(equalToConstant: 36).isActive = true
        bleed.toolTip = "Millimeters printed past each edge, so trimming leaves no white line (PDF)"
        let marksBox = NSButton(checkboxWithTitle: "Crop marks", target: nil, action: nil)
        marksBox.toolTip = "Lines outside the figure showing where to cut it (PDF)"
        let poster = f.poster != nil
        let refresh = { (pickDPI: Bool) in
            let fmt = formats[format.indexOfSelectedItem]
            let d = self.journalDPI(key())
            let raster = ["tiff", "png", "jpg"].contains(fmt)
            let tableFile = ["docx", "xlsx", "csv", "tsv"].contains(fmt)
            for v in [dpi, journal, cmykBox, outlineBox, darkBox] as [NSControl] { v.isEnabled = !tableFile }
            for v in [bleed, marksBox] as [NSControl] { v.isEnabled = fmt == "pdf" }
            if pickDPI {
                dpi.stringValue = poster && key() == nil ? "\(raster ? 300 : (fmt == "pptx" ? 200 : 600))"
                    : "\(raster ? (pictures ? d.combination : d.line) : (fmt == "pptx" ? 300 : 600))"
            }
            p.allowedContentTypes = [UTType(filenameExtension: fmt) ?? .data]
            p.nameFieldStringValue = name + "." + fmt
            if tableFile {
                note.stringValue = fmt == "docx" ? "The table as a real Word table, in the figure's font: what journals want for tables."
                    : fmt == "xlsx" ? "The table as an Excel sheet: numbers stay numbers, rounded as on the figure."
                    : "The table's cells as \(fmt.uppercased()) text, rounded as on the figure."
                return
            }
            if fmt == "pptx" {
                note.stringValue = poster
                    ? "A slide the poster's size (PowerPoint's largest is 56 inches a side; a larger poster is scaled to it), the poster filling it: a PNG at this resolution, with a vector copy PowerPoint 2019 and later show instead."
                    : "A 16:9 slide with the figure on it: at its real size when it fits, else scaled to fit. The picture is a PNG at this resolution, with a vector copy PowerPoint 2019 and later show instead; the caption becomes its alt text."
                return
            }
            if poster && key() == nil {
                note.stringValue = "Posters are printed at 150 to 300 dpi at full size: pictures below 150 dpi are reported, and TIFF, PNG and JPEG are written at 300 dpi at most (more would be hundreds of megapixels). PDF stays vector; give a print shop a PDF with the bleed and crop marks they ask for. The file is measured once written and records what it was made from."
                return
            }
            let who = key().map { self.journalName($0) } ?? "General"
            let need = "\(who): \(d.line) dpi for line art, \(d.combination) for figures with pictures, \(d.photo) for the pictures in a vector file." + (key().map { " " + self.journalChecked($0) } ?? "")
            note.stringValue = need + (raster
                ? " This figure \(pictures ? "has pictures" : "is line art"): a lower setting is raised to \(pictures ? d.combination : d.line) dpi (except PNG)."
                : " Vector stays sharp at any size; the resolution is that of what panels draw as pixels (heatmaps, dense scatters)\(fmt == "eps" ? " and of what EPS has to rasterize" : ""), at least \(d.photo) dpi; pictures placed from files keep their own pixels, and those below \(d.photo) dpi are reported.")
                + " The file is measured once written."
        }
        let fmtAction = Action { _ in refresh(true) }
        format.target = fmtAction; format.action = #selector(Action.fire(_:))
        journal.target = fmtAction; journal.action = #selector(Action.fire(_:))
        // two rows, so the options fit the save panel's width whatever the journal's name
        journal.widthAnchor.constraint(lessThanOrEqualToConstant: 260).isActive = true
        let top = NSStackView(views: [NSTextField(labelWithString: "Format:"), format, NSTextField(labelWithString: "For:"), journal])
        let boxes = NSStackView(views: [NSTextField(labelWithString: "Resolution:"), dpi, NSTextField(labelWithString: "dpi"),
                                        NSView(), cmykBox, outlineBox, darkBox])
        let print = NSStackView(views: [NSTextField(labelWithString: "Bleed:"), bleed, NSTextField(labelWithString: "mm"), marksBox])
        let acc = NSStackView(views: [top, boxes, print, note]); acc.orientation = .vertical; acc.alignment = .leading
        acc.edgeInsets = NSEdgeInsets(top: 8, left: 8, bottom: 8, right: 8)
        p.accessoryView = acc
        refresh(true)
        if let proj = project { p.directoryURL = URL(fileURLWithPath: proj) }
        p.beginSheetModal(for: window) { [weak self] r in
            _ = fmtAction
            guard let self, r == .OK, let u = p.url else { return }
            let fmt = formats[format.indexOfSelectedItem], d = dpiValue()
            let dark = darkBox.state == .on, cmyk = cmykBox.state == .on, outline = outlineBox.state == .on
            var args: [String: Any] = ["figure": name, "path": u.path, "format": fmt, "dpi": d, "dark": dark, "cmyk": cmyk, "outline": outline]
            if fmt == "pdf" {
                args["bleed"] = max(0, min(25, Double(bleed.stringValue.replacingOccurrences(of: ",", with: ".")) ?? 0))
                args["marks"] = marksBox.state == .on
            }
            if let j = key() { args["journal"] = j }
            self.enqueue { [weak self] in guard let self else { return }
                busy("Exporting…")
                do {
                    let r = try await self.engine.json("export", args)
                    let w = r["warnings"] as? [String] ?? []
                    let rz = r["resolution"] as? [String: Any] ?? [:]
                    var said = "Exported \(u.lastPathComponent)"
                    if ["docx", "xlsx", "csv", "tsv"].contains(fmt) { toast(said); return }
                    if let got = rz["dpi"] as? NSNumber { said += " at \(got.intValue) dpi" }
                    else if let pic = rz["pictures"] as? NSNumber { said += ": vector, pictures at \(pic.intValue) dpi or more" }
                    else if fmt != "svg" { said += ": vector" }
                    if let n = (rz["notes"] as? [String])?.first { said += " (\(n))" }
                    toast(w.isEmpty ? said : said + ". " + w.joined(separator: "; "), error: !w.isEmpty)
                } catch { toast(error.localizedDescription, error: true) }
            }
        }
    }

    @objc func exportAll(_ sender: Any?) {
        guard project != nil else { return }
        let o = NSOpenPanel(); o.canChooseDirectories = true; o.canChooseFiles = false; o.canCreateDirectories = true
        o.message = "A folder for a PDF of every figure"
        o.beginSheetModal(for: window) { [weak self] r in
            guard let self, r == .OK, let dir = o.url else { return }
            let names = self.figures.compactMap { $0["name"] as? String }
            self.enqueue { [weak self] in guard let self else { return }
                busy("Exporting \(names.count) figures…")
                var failed: [String] = []
                for n in names {
                    do { _ = try await engine.json("export", ["figure": n, "path": dir.appendingPathComponent(n + ".pdf").path, "format": "pdf"]) }
                    catch { failed.append(n) }
                }
                toast(failed.isEmpty ? "Exported \(names.count) figures" : "Couldn't export \(failed.joined(separator: ", "))", error: !failed.isEmpty)
            }
        }
    }

    @objc func revealProject(_ sender: Any?) { if let p = project { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: p)]) } }
    @objc func saveCode(_ sender: Any?) { code.save() }

    // MARK: view

    @objc func zoomIn(_ sender: Any?) { canvas.zoom(by: 1.25) }
    @objc func zoomOut(_ sender: Any?) { canvas.zoom(by: 0.8) }
    @objc func zoomFit(_ sender: Any?) { canvas.fit() }
    @objc func zoomActual(_ sender: Any?) { canvas.actualSize() }
    @objc func toggleGrid(_ sender: Any?) { canvas.showGrid.toggle() }
    /// The tool bar's grid and guides buttons, pressed or not as the canvas shows them.
    func syncAidButtons() { gridButton?.state = canvas.showGrid ? .on : .off; guidesButton?.state = canvas.showGuides ? .on : .off }
    @objc func toggleSnapGrid(_ sender: Any?) { canvas.snapToGrid.toggle() }
    @objc func toggleSmartGuides(_ sender: Any?) { canvas.smartGuides.toggle() }
    @objc func toggleGuides(_ sender: Any?) {
        canvas.showGuides.toggle()
        if canvas.showGuides && (figure?.guides.isEmpty ?? true) { toast("No guides yet: View ▸ Add Vertical Guide, or drag one out of a ruler") }
    }
    @objc func setGridStep(_ sender: NSMenuItem) {
        canvas.gridStep = Double(sender.tag) / 10
        canvas.showGrid = true
    }
    @objc func addVerticalGuide(_ sender: Any?) {
        guard let f = figure else { return }
        canvas.showGuides = true
        edit(["op": "guide_add", "x": (Double(f.size.width) / 2 * 10).rounded() / 10])
        toast("Guide added at the middle: drag it to place it, or back into the ruler to remove it")
    }
    @objc func addHorizontalGuide(_ sender: Any?) {
        guard let f = figure else { return }
        canvas.showGuides = true
        edit(["op": "guide_add", "y": (Double(f.size.height) / 2 * 10).rounded() / 10])
        toast("Guide added at the middle: drag it to place it, or back into the ruler to remove it")
    }
    @objc func clearGuides(_ sender: Any?) { edit(["op": "guide_clear"]) }
    @objc func toggleSidebar(_ sender: Any?) { navigator.isHidden.toggle() }
    @objc func toggleInspector(_ sender: Any?) { rightHost.isHidden.toggle() }
    @objc func refreshFigure(_ sender: Any?) { refresh(force: true) }
    @objc func showCodePane(_ sender: Any?) {
        if !drawer.isHidden && drawer.page == .code && !middle.isSubviewCollapsed(drawer) { setDrawer(visible: false); return }
        setDrawer(visible: true); drawer.show(.code)
        showCodeOfSelection()
    }
    @objc func showProblems(_ sender: Any?) { setDrawer(visible: true); drawer.show(.problems) }
    @objc func showCaption(_ sender: Any?) {
        setDrawer(visible: true); drawer.show(.caption)
        window.makeFirstResponder(captionPane.text)
    }

    @objc func showTerminal(_ sender: Any?) {
        if drawer.page == .terminal && !middle.isSubviewCollapsed(drawer) && sender != nil { setDrawer(visible: false); return }
        setDrawer(visible: true)
        if !terminal.hasStarted, let p = project { terminal.start(in: p, original: nil) }
        drawer.show(.terminal)
        terminal.focus()
    }
    @objc func newTerminal(_ sender: Any?) {
        guard let p = project else { return }
        setDrawer(visible: true)
        if terminal.hasStarted { terminal.addTab() } else { terminal.start(in: p, original: nil) }
        drawer.show(.terminal)
    }

    private func setDrawer(visible: Bool) {
        let collapsed = middle.isSubviewCollapsed(drawer)
        if visible && collapsed {
            middle.setPosition(middle.bounds.height - drawerHeight, ofDividerAt: 0)
        } else if !visible && !collapsed {
            drawerHeight = max(140, drawer.frame.height)
            middle.setPosition(middle.bounds.height, ofDividerAt: 0)
        }
    }

    // MARK: menus

    func contextMenu() -> NSMenu {
        let m = NSMenu()
        func item(_ t: String, _ s: Selector) { m.addItem(withTitle: t, action: s, keyEquivalent: "").target = self }
        if !sel.isEmpty {
            item("Edit Code", #selector(editCode(_:)))
            m.addItem(.separator())
            item("Bring to Front", #selector(bringToFront(_:))); item("Bring Forward", #selector(bringForward(_:)))
            item("Send Backward", #selector(sendBackward(_:))); item("Send to Back", #selector(sendToBack(_:)))
            m.addItem(.separator())
            if selElements.count > 1 { item("Group", #selector(group(_:))); item("Make Layout", #selector(makeLayout(_:))) }
            if sel.first.flatMap({ figure?.element($0)?.top }).flatMap({ figure?.element($0) })?.kind == "layout" {
                item("Release Layout", #selector(releaseSelectedLayout(_:)))
            }
            if figure.map({ f in selElements.contains { f.element($0)?.group != nil } }) == true { item("Ungroup", #selector(ungroup(_:))) }
            if sel.count <= 2, let l = selectedLabel {
                item(l.detached ? "Link Label to Panel" : "Unlink Label from Panel", #selector(toggleSelectedLabelLink(_:)))
            }
            item("Duplicate", #selector(duplicate(_:)))
            item("Delete", #selector(deleteSelected(_:)))
            if sel.count == 1, figure?.element(sel[0])?.kind == "table" { item("Export Table…", #selector(exportTable(_:))) }
            if sel.count == 1, let e = figure?.element(sel[0]), remoteOrigin(of: e) != nil { item("Update from Server", #selector(updateFromServer(_:))) }
            let bc = bioCartoonMenuItems()
            if !bc.isEmpty { m.addItem(.separator()); bc.forEach { m.addItem($0) } }
        } else {
            item("New Panel…", #selector(newPanel(_:))); item("Place Picture…", #selector(insertImage(_:)))
            m.addItem(.separator())
            item("Edit the Figure's Code", #selector(editCode(_:)))
        }
        return m
    }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch item.action {
        case #selector(undoEdit(_:)):
            if inTerminal { item.title = "Undo"; return false }
            if let tv = window.firstResponder as? NSTextView, tv.isEditable { item.title = "Undo"; return tv.undoManager?.canUndo ?? false }
            item.title = (state["undoLabel"] as? String).map { "Undo \($0)" } ?? "Undo"
            return state["canUndo"] as? Bool ?? false
        case #selector(redoEdit(_:)):
            if inTerminal { item.title = "Redo"; return false }
            if let tv = window.firstResponder as? NSTextView, tv.isEditable { item.title = "Redo"; return tv.undoManager?.canRedo ?? false }
            item.title = (state["redoLabel"] as? String).map { "Redo \($0)" } ?? "Redo"
            return state["canRedo"] as? Bool ?? false
        case #selector(toggleDarkPreview(_:)): item.state = darkPreview ? .on : .off; return figure != nil
        case #selector(setVision(_:)):
            item.state = canvas.vision == ["normal", "deuteranopia", "protanopia", "tritanopia", "grayscale"][item.tag] ? .on : .off
            return figure != nil
        case #selector(setAppearance(_:)):
            item.state = (UserDefaults.standard.string(forKey: "appearance") ?? "system") == ["system", "light", "dark"][item.tag] ? .on : .off
        case #selector(makeLayout(_:)):
            return sel.compactMap { figure?.element($0) }.filter { ($0.kind == "panel" || $0.kind == "image") && $0.parentOid == nil }.count >= 2
        case #selector(releaseSelectedLayout(_:)):
            return sel.first.flatMap { figure?.element($0)?.top }.flatMap { figure?.element($0) }?.kind == "layout"
        case #selector(toggleGrid(_:)): item.state = canvas.showGrid ? .on : .off
        case #selector(toggleSnapGrid(_:)): item.state = canvas.snapToGrid ? .on : .off
        case #selector(toggleSmartGuides(_:)): item.state = canvas.smartGuides ? .on : .off
        case #selector(toggleGuides(_:)): item.state = canvas.showGuides ? .on : .off
        case #selector(setGridStep(_:)): item.state = abs(canvas.gridStep - Double(item.tag) / 10) < 1e-6 ? .on : .off
        case #selector(addVerticalGuide(_:)), #selector(addHorizontalGuide(_:)): return figure != nil
        case #selector(clearGuides(_:)): return !(figure?.guides.isEmpty ?? true)
        case #selector(toggleSidebar(_:)): item.title = navigator.isHidden ? "Show Figures and Layers" : "Hide Figures and Layers"
        case #selector(toggleInspector(_:)): item.title = rightHost.isHidden ? "Show Inspector" : "Hide Inspector"
        case #selector(labelsNone(_:)): item.state = figure?.labelStyle["numbering"] as? String == "none" ? .on : .off; return figure != nil
        case #selector(exportSourceData(_:)): return figure != nil && figure?.isTableFigure != true
        case #selector(exportTable(_:)): return (sel.count == 1 && figure?.element(sel[0])?.kind == "table") || figure?.tableCells != nil
        case #selector(insertTable(_:)): return figure != nil && figure?.isTable == false
        case #selector(insertRemotePanel(_:)): return figure != nil
        case #selector(newTable(_:)): return project != nil
        case #selector(checkReproducibility(_:)), #selector(tagVersion(_:)), #selector(compareWithVersion(_:)):
            return project != nil
        case #selector(toggleLabelAlign(_:)): item.state = (figure?.labelStyle["align"] as? Bool ?? true) ? .on : .off; return figure != nil
        case #selector(toggleLabelOrder(_:)): item.state = (figure?.labelStyle["order"] as? String) == "source" ? .on : .off; return figure != nil
        case #selector(labelFormat(_:)):
            let p = Inspector.labelFormats[item.tag]
            item.state = (figure?.labelStyle["format"] as? String == p.1 && figure?.labelStyle["case"] as? String == p.2
                          && figure?.labelStyle["numbering"] as? String != "none") ? .on : .off
            return figure != nil
        case #selector(copy(_:)): return figure != nil
        case #selector(deleteSelected(_:)), #selector(delete(_:)), #selector(editCode(_:)):
            return !sel.isEmpty || item.action == #selector(editCode(_:)) && figure != nil
        case #selector(duplicate(_:)), #selector(bringToFront(_:)), #selector(bringForward(_:)), #selector(sendBackward(_:)), #selector(sendToBack(_:)),
             #selector(alignLeft(_:)), #selector(alignCenter(_:)), #selector(alignRight(_:)), #selector(alignTop(_:)), #selector(alignMiddle(_:)), #selector(alignBottom(_:)),
             #selector(ungroup(_:)):
            return !selElements.isEmpty
        case #selector(group(_:)), #selector(matchWidth(_:)), #selector(matchHeight(_:)), #selector(distributeH(_:)), #selector(distributeV(_:)):
            return selElements.count > 1
        case #selector(paste(_:)):
            let pb = NSPasteboard.general
            return figure != nil && (pb.data(forType: MainController.pasteType) != nil || NSImage.canInit(with: pb))
        case #selector(cut(_:)), #selector(copyStyle(_:)): return !selElements.isEmpty
        case #selector(pasteStyle(_:)): return copiedStyle != nil && !selElements.isEmpty
        case #selector(rotateLeft(_:)), #selector(rotateRight(_:)), #selector(toggleLock(_:)), #selector(toggleHide(_:)):
            return !selElements.isEmpty
        case #selector(flipHorizontal(_:)), #selector(flipVertical(_:)), #selector(cropPicture(_:)), #selector(addScaleBar(_:)):
            return sel.first.flatMap { figure?.element($0) }.map { $0.kind == "image" || ($0.kind == "inset" && $0.inner == "image") } ?? false
        case #selector(addZoom(_:)): return !selElements.isEmpty
        case #selector(toggleSelectedLabelLink(_:)):
            item.title = selectedLabel?.detached == true ? "Link Label to Panel" : "Unlink Label from Panel"
            return selectedLabel != nil
        case #selector(selectAll(_:)): return figure != nil
        case #selector(insertFigure(_:)): return figure != nil && figures.count > 1
        case #selector(newPanel(_:)), #selector(insertImage(_:)), #selector(insertPanelCode(_:)), #selector(exportFigure(_:)), #selector(insertQR(_:)),
             #selector(labelsOutside(_:)), #selector(labelsInside(_:)), #selector(labelJournal(_:)), #selector(refreshFigure(_:)),
             #selector(showBioCartoon(_:)), #selector(copyFigure(_:)):
            return figure != nil
        case #selector(insertGridTable(_:)): return figure != nil && figure?.isTable == false
        case #selector(newFigure(_:)), #selector(exportAll(_:)), #selector(revealProject(_:)), #selector(showTerminal(_:)), #selector(newTerminal(_:)):
            return project != nil
        case #selector(saveCode(_:)): return code.dirty
        default: break
        }
        return true
    }

    // MARK: window

    func windowShouldClose(_ sender: NSWindow) -> Bool { confirmClose() }

    func confirmClose() -> Bool {
        if code.dirty {
            let a = NSAlert(); a.messageText = "Apply your unsaved code changes first?"
            a.addButton(withTitle: "Apply"); a.addButton(withTitle: "Discard"); a.addButton(withTitle: "Cancel")
            switch a.runModal() {
            case .alertFirstButtonReturn: code.save()
            case .alertSecondButtonReturn: break
            default: return false
            }
        }
        let running = terminal.runningCommands
        if !running.isEmpty {
            let a = NSAlert(); a.messageText = "Stop \(running.joined(separator: ", ")) in the terminal?"
            a.informativeText = "Closing Fignimble ends the terminals."
            a.addButton(withTitle: "Close"); a.addButton(withTitle: "Cancel")
            if a.runModal() != .alertFirstButtonReturn { return false }
        }
        return true
    }

    func windowWillClose(_ n: Notification) { finish(); NSApp.terminate(nil) }

    func finish() {
        terminal.terminateAll()
        // the engine removes the agent instruction files it wrote into the project when its input ends
        engine.finishInput()
        let deadline = Date().addingTimeInterval(2)
        while engine.isRunning && Date() < deadline { usleep(50_000) }
        engine.stop()
    }
}
