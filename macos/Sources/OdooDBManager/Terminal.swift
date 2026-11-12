import AppKit
import Observation
import SwiftTerm
import SwiftUI

/// What `odoodb terminal <db> --kind … --json` returns: the command, prepared like `start`.
struct TerminalSpec: Decodable {
    var kind: String
    var title: String
    var executable: String
    var args: [String]
    var cwd: String
    var env: [String: String]
    /// Server runs: where the output is also written (Logs and History tabs read it).
    var log: String?
    var url: String?
}

/// A process running in the built-in terminal. Kept by the store, so it survives leaving the page.
@MainActor
@Observable
final class TerminalSession: Identifiable {
    let id = UUID()
    let db: String?
    let kind: String
    let title: String
    var running = true
    var exitCode: Int32?
    @ObservationIgnored let view: OdooTerminalView
    @ObservationIgnored var onExit: ((TerminalSession) -> Void)?

    init(db: String?, kind: String, title: String, executable: String, args: [String], cwd: String?,
         env: [String: String], log: String?) {
        self.db = db
        self.kind = kind
        self.title = title
        view = OdooTerminalView(frame: NSRect(x: 0, y: 0, width: 800, height: 400))
        view.configure(log: log)
        view.onExit = { [weak self] code in
            guard let self else { return }
            self.running = false
            self.exitCode = code
            self.onExit?(self)
        }
        var full = env
        full["TERM"] = full["TERM"] ?? "xterm-256color"
        full["LANG"] = full["LANG"] ?? "en_US.UTF-8"
        view.startProcess(executable: executable, args: args, environment: full.map { "\($0.key)=\($0.value)" },
                          currentDirectory: cwd)
    }

    convenience init(db: String?, spec: TerminalSpec) {
        self.init(db: db, kind: spec.kind, title: spec.title, executable: spec.executable, args: spec.args,
                  cwd: spec.cwd, env: spec.env, log: spec.log)
    }

    /// Ctrl+C, like in any terminal (Odoo stops cleanly); the process is killed if it is still there 8 s later.
    func interrupt() {
        guard running else { return }
        view.send(txt: "\u{03}")
        Task {
            try? await Task.sleep(for: .seconds(8))
            if running { view.terminate() }
        }
    }

    func terminate() {
        if running { view.terminate() }
    }
}

/// SwiftTerm's terminal, which also writes the output (without colors) to the database's log file.
final class OdooTerminalView: LocalProcessTerminalView {
    var onExit: ((Int32?) -> Void)?
    private var logHandle: FileHandle?
    private var pending = Data()
    private static let ansi = try! NSRegularExpression(pattern: "\u{1B}\\[[0-9;?]*[A-Za-z]|\u{1B}\\][^\u{07}]*\u{07}|\r")

    func configure(log: String?) {
        font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        nativeBackgroundColor = NSColor(name: nil) { a in
            a.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? NSColor(white: 0.11, alpha: 1) : NSColor(white: 0.99, alpha: 1)
        }
        nativeForegroundColor = .labelColor
        caretColor = .controlAccentColor
        optionAsMetaKey = true
        if let log {
            FileManager.default.createFile(atPath: log, contents: nil)
            logHandle = FileHandle(forWritingAtPath: log)
            logHandle?.seekToEndOfFile()
        }
    }

    override func dataReceived(slice: ArraySlice<UInt8>) {
        super.dataReceived(slice: slice)
        guard let logHandle else { return }
        pending.append(contentsOf: slice)
        // Whole lines only: an escape sequence may be split between two reads.
        guard let lastNL = pending.lastIndex(of: 0x0A) else { return }
        let lines = pending[pending.startIndex...lastNL]
        pending.removeSubrange(pending.startIndex...lastNL)
        let text = String(decoding: lines, as: UTF8.self)
        let clean = Self.ansi.stringByReplacingMatches(in: text, range: NSRange(text.startIndex..., in: text), withTemplate: "")
        logHandle.write(Data(clean.utf8))
    }

    override func processTerminated(_ source: LocalProcess, exitCode: Int32?) {
        super.processTerminated(source, exitCode: exitCode)
        if !pending.isEmpty { logHandle?.write(pending) }
        try? logHandle?.close()
        logHandle = nil
        let code = exitCode
        DispatchQueue.main.async { self.onExit?(code) }
    }
}

/// Shows a session's terminal view (moved here if it was shown elsewhere before).
struct TerminalHost: NSViewRepresentable {
    let session: TerminalSession

    func makeNSView(context: Context) -> NSView {
        let container = NSView()
        attach(to: container)
        return container
    }

    func updateNSView(_ container: NSView, context: Context) {
        if session.view.superview !== container { attach(to: container) }
    }

    private func attach(to container: NSView) {
        let v = session.view
        v.removeFromSuperview()
        v.translatesAutoresizingMaskIntoConstraints = false
        container.addSubview(v)
        NSLayoutConstraint.activate([
            v.leadingAnchor.constraint(equalTo: container.leadingAnchor, constant: 8),
            v.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            v.topAnchor.constraint(equalTo: container.topAnchor, constant: 6),
            v.bottomAnchor.constraint(equalTo: container.bottomAnchor),
        ])
        DispatchQueue.main.async { v.window?.makeFirstResponder(v) }
    }
}

/// Terminal tab of a database: its sessions (server in the foreground, odoo shell, zsh).
struct TerminalTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    @State private var selected: UUID?

    private var sessions: [TerminalSession] { store.terminals.filter { $0.db == info.name } }
    private var current: TerminalSession? { sessions.first { $0.id == selected } ?? sessions.last }

    var body: some View {
        VStack(spacing: 0) {
            bar
            Divider()
            if let s = current {
                TerminalHost(session: s)
                    .id(s.id)
                    .background(Color(nsColor: s.view.nativeBackgroundColor))
            } else {
                ContentUnavailableView {
                    Label("No terminal", systemImage: "apple.terminal")
                } description: {
                    Text("Run \(info.name) in the foreground (Ctrl+C, breakpoints, colored log), or open an Odoo shell on it.")
                } actions: {
                    HStack {
                        Button { store.openTerminal(info.name, kind: "server") } label: { Label("Run Server", systemImage: "play.fill") }
                            .buttonStyle(.borderedProminent)
                            .disabled(store.isRunning(info.name))
                        Button("Odoo Shell") { store.openTerminal(info.name, kind: "shell") }
                        Button("zsh") { store.openTerminal(info.name, kind: "zsh") }
                    }
                }
            }
        }
        .onChange(of: sessions.count) { _, _ in selected = sessions.last?.id }
    }

    private var bar: some View {
        HStack(spacing: 6) {
            ForEach(sessions) { s in
                let on = s.id == current?.id
                HStack(spacing: 6) {
                    Circle().fill(s.running ? Theme.running : Color.secondary.opacity(0.5)).frame(width: 7, height: 7)
                    Text(label(s)).font(.callout.weight(on ? .semibold : .regular)).lineLimit(1)
                    Button { store.closeTerminal(s) } label: { Image(systemName: "xmark").font(.caption2.weight(.bold)) }
                        .buttonStyle(.plain)
                        .foregroundStyle(.secondary)
                        .help(s.running ? "Stop and close" : "Close")
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .background(on ? Color.primary.opacity(0.08) : .clear, in: Capsule())
                .contentShape(Capsule())
                .onTapGesture { selected = s.id }
            }
            Menu {
                Button("Run Server") { store.openTerminal(info.name, kind: "server") }
                    .disabled(store.isRunning(info.name))
                Button("Odoo Shell") { store.openTerminal(info.name, kind: "shell") }
                Button("zsh") { store.openTerminal(info.name, kind: "zsh") }
            } label: {
                Image(systemName: "plus")
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("New terminal")
            Spacer()
            if let s = current {
                if s.running {
                    Button { s.interrupt() } label: { Label("Ctrl+C", systemImage: "stop.fill") }
                        .help("Send Ctrl+C (stops the server cleanly)")
                } else if let code = s.exitCode {
                    Text("exited (\(code))").font(.caption).foregroundStyle(.secondary)
                }
                Button { s.view.getTerminal().resetToInitialState(); s.view.needsDisplay = true } label: {
                    Label("Clear", systemImage: "eraser")
                }
                .help("Clear the screen")
            }
        }
        .controlSize(.small)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private func label(_ s: TerminalSession) -> String {
        switch s.kind {
        case "server": "Server"
        case "shell": "Odoo shell"
        case "zsh": "zsh"
        default: s.title
        }
    }
}

/// A command run in the built-in terminal, in a sheet (e.g. `gh auth login`).
struct TerminalSheet: View {
    @Environment(\.dismiss) private var dismiss
    let session: TerminalSession
    var onClose: () -> Void = {}

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(session.title).font(.headline)
                Spacer()
                if !session.running {
                    Text(session.exitCode == 0 ? "Done" : "Exited (\(session.exitCode ?? -1))")
                        .font(.callout).foregroundStyle(session.exitCode == 0 ? .green : .secondary)
                }
                Button(session.running ? "Cancel" : "Done") {
                    session.terminate()
                    dismiss()
                    onClose()
                }
                .keyboardShortcut(session.running ? .cancelAction : .defaultAction)
            }
            .padding(14)
            Divider()
            TerminalHost(session: session)
                .background(Color(nsColor: session.view.nativeBackgroundColor))
        }
        .frame(width: 760, height: 460)
    }
}
