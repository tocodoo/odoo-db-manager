import SwiftUI

struct LogLine: Identifiable, Equatable {
    let id: Int
    let text: String
    let level: Level

    enum Level { case info, warning, error, debug, plain }

    init(id: Int, text: String) {
        self.id = id
        self.text = text
        if text.contains(" ERROR ") || text.contains(" CRITICAL ") || text.hasPrefix("Traceback") {
            level = .error
        } else if text.contains(" WARNING ") {
            level = .warning
        } else if text.contains(" INFO ") {
            level = .info
        } else if text.contains(" DEBUG ") {
            level = .debug
        } else {
            level = .plain
        }
    }
}

struct LogsTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    @ViewState private var lines: [LogLine] = []
    @ViewState private var filter = ""
    @ViewState private var errorsOnly = false
    @ViewState private var follow = true
    @ViewState private var lastSize: UInt64 = 0
    @ViewState private var nextID = 0
    /// Bytes after the last newline: a line still being written.
    @ViewState private var partial = ""

    private var visible: [LogLine] {
        var l = lines
        if errorsOnly {
            var keep: [LogLine] = []
            var inError = false
            for line in l {
                if line.level != .plain { inError = line.level == .error }
                if inError || line.level == .error { keep.append(line) }
            }
            l = keep
        }
        if !filter.isEmpty { l = l.filter { $0.text.localizedCaseInsensitiveContains(filter) } }
        return l
    }

    var body: some View {
        let proc = store.process(for: info.name)
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                TextField("Filter", text: $filter).textFieldStyle(.roundedBorder).frame(maxWidth: 240)
                Toggle("Errors only", isOn: $errorsOnly).toggleStyle(.checkbox)
                Toggle("Follow", isOn: $follow).toggleStyle(.checkbox)
                Spacer()
                let errs = lines.filter { $0.level == .error && !$0.text.hasPrefix(" ") }.count
                if errs > 0 {
                    Chip(text: "\(errs) error\(errs == 1 ? "" : "s")", symbol: "xmark.octagon", tint: .red)
                }
                Button { reveal(info.log.path) } label: { Image(systemName: "folder") }
                    .help("Reveal the log file").disabled(!FileManager.default.fileExists(atPath: info.log.path))
                Button { copyToPasteboard(visible.map(\.text).joined(separator: "\n")) } label: { Image(systemName: "doc.on.doc") }
                    .help("Copy the visible lines")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)

            if proc?.mode == "terminal" && lines.isEmpty {
                ContentUnavailableView {
                    Label("Running in the terminal", systemImage: "terminal")
                } description: {
                    Text("Its output lives in your terminal app. Restart it here to capture the logs.")
                } actions: {
                    Button("Restart in Background") {
                        Task {
                            await store.perform("Stop \(info.name)", db: info.name, args: ["stop", info.name]) { _ in "Stopped" }
                            store.start(info.name)
                        }
                    }
                }
            } else if lines.isEmpty {
                ContentUnavailableView("No log yet", systemImage: "text.alignleft",
                                       description: Text("Logs appear here when the database is started from this app or by an agent."))
            } else {
                logView
            }
        }
        .task(id: info.log.path) { await poll() }
    }

    /// One native text view for the whole log, so a selection can span any number of lines.
    private var logView: some View {
        LogTextView(lines: visible, follow: follow)
            .background(Color(nsColor: .textBackgroundColor))
    }

    private func poll() async {
        lines = []
        lastSize = 0
        partial = ""
        while !Task.isCancelled {
            await read()
            try? await Task.sleep(for: .seconds(1))
        }
    }

    /// Reads only what was appended since the last poll. Each line keeps its id, so the list
    /// appends rows instead of rebuilding 3000 of them every second.
    private func read() async {
        let path = info.log.path
        let from = lastSize
        let chunk: (size: UInt64, data: Data, fresh: Bool, cut: Bool)? = await Task.detached(priority: .utility) {
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
                  let size = attrs[.size] as? UInt64 else { return nil }
            if size == from { return (size, Data(), false, false) }
            let window: UInt64 = 600_000
            // Restart from the end of the file when it is new, was truncated, or grew too much.
            let start = (size < from || from == 0 || size - from > window) ? (size > window ? size - window : 0) : from
            guard let h = FileHandle(forReadingAtPath: path) else { return nil }
            defer { try? h.close() }
            try? h.seek(toOffset: start)
            let data = (try? h.read(upToCount: Int(size - start))) ?? Data()
            return (size, data, start != from, start != from && start > 0)
        }.value
        guard let chunk else {
            if !lines.isEmpty || lastSize != 0 { lines = []; lastSize = 0; partial = "" }
            return
        }
        guard chunk.size != lastSize else { return }
        lastSize = chunk.size
        var parts = ((chunk.fresh ? "" : partial) + String(decoding: chunk.data, as: UTF8.self)).components(separatedBy: "\n")
        if chunk.cut, !parts.isEmpty { parts.removeFirst() }
        partial = parts.popLast() ?? ""
        var id = nextID
        let added = parts.map { text -> LogLine in
            defer { id += 1 }
            return LogLine(id: id, text: text)
        }
        nextID = id
        var all = chunk.fresh ? added : lines + added
        if all.count > 3000 { all.removeFirst(all.count - 3000) }
        lines = all
    }
}

/// Read-only, selectable NSTextView showing the log lines. Appends (and trims from the top)
/// incrementally instead of re-rendering the whole text every poll.
private struct LogTextView: NSViewRepresentable {
    var lines: [LogLine]
    var follow: Bool

    final class Coordinator {
        var ids: [Int] = []
        var lengths: [Int] = []
    }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        scroll.drawsBackground = false
        scroll.hasVerticalScroller = true
        let tv = scroll.documentView as! NSTextView
        tv.isEditable = false
        tv.isSelectable = true
        tv.isRichText = false
        tv.drawsBackground = false
        tv.textContainerInset = NSSize(width: 10, height: 8)
        tv.isAutomaticLinkDetectionEnabled = false
        tv.usesFindBar = true
        tv.isIncrementalSearchingEnabled = true
        tv.layoutManager?.allowsNonContiguousLayout = true
        update(tv, context: context, scroll: true)
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }
        update(tv, context: context, scroll: false)
    }

    private func update(_ tv: NSTextView, context: Context, scroll force: Bool) {
        let c = context.coordinator
        guard let storage = tv.textStorage else { return }
        let newIDs = lines.map(\.id)
        guard newIDs != c.ids else { return }

        // Common case: some lines dropped from the top and/or new ones appended at the bottom.
        var dropped = -1
        if let first = newIDs.first, let k = c.ids.firstIndex(of: first) {
            let kept = c.ids.count - k
            if kept <= newIDs.count, Array(c.ids[k...]) == Array(newIDs.prefix(kept)) { dropped = k }
        } else if newIDs.isEmpty == false, c.ids.isEmpty {
            dropped = 0
        }

        storage.beginEditing()
        if dropped >= 0 {
            let cut = c.lengths.prefix(dropped).reduce(0, +)
            if cut > 0 { storage.deleteCharacters(in: NSRange(location: 0, length: cut)) }
            c.ids.removeFirst(dropped)
            c.lengths.removeFirst(dropped)
            for line in lines.dropFirst(c.ids.count) { append(line, to: storage, c) }
        } else {
            storage.setAttributedString(NSAttributedString())
            c.ids = []
            c.lengths = []
            for line in lines { append(line, to: storage, c) }
        }
        storage.endEditing()

        // Follow the tail, unless the user is in the middle of selecting something.
        if force || (follow && tv.selectedRange().length == 0) {
            tv.scrollToEndOfDocument(nil)
        }
    }

    private func append(_ line: LogLine, to storage: NSTextStorage, _ c: Coordinator) {
        let color: NSColor = switch line.level {
        case .error: .systemRed
        case .warning: .systemOrange
        case .debug: .secondaryLabelColor
        case .info, .plain: .labelColor
        }
        var attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular),
            .foregroundColor: color,
        ]
        if line.level == .error { attrs[.backgroundColor] = NSColor.systemRed.withAlphaComponent(0.06) }
        let text = line.text + "\n"
        storage.append(NSAttributedString(string: text, attributes: attrs))
        c.ids.append(line.id)
        c.lengths.append((text as NSString).length)
    }
}
