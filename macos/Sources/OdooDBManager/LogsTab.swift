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
    @State private var lines: [LogLine] = []
    @State private var filter = ""
    @State private var errorsOnly = false
    @State private var follow = true
    @State private var lastSize: UInt64 = 0
    @State private var nextID = 0
    /// Bytes after the last newline: a line still being written.
    @State private var partial = ""

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

    private var logView: some View {
        ScrollViewReader { proxy in
            ScrollView([.vertical]) {
                LazyVStack(alignment: .leading, spacing: 1) {
                    ForEach(visible) { line in
                        Text(line.text)
                            .font(.system(size: 11, design: .monospaced))
                            .foregroundStyle(color(line.level))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 1)
                            .background(line.level == .error ? Color.red.opacity(0.06) : .clear)
                            .id(line.id)
                    }
                }
                .textSelection(.enabled)
                .padding(.vertical, 8)
            }
            .background(Color(nsColor: .textBackgroundColor))
            .onChange(of: lines.last?.id) { _, id in
                guard follow, let id else { return }
                proxy.scrollTo(id, anchor: .bottom)
            }
            .onAppear { if let id = visible.last?.id { proxy.scrollTo(id, anchor: .bottom) } }
        }
    }

    private func color(_ level: LogLine.Level) -> Color {
        switch level {
        case .error: .red
        case .warning: .orange
        case .debug: .secondary
        case .info, .plain: .primary
        }
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
