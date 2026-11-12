import AppKit
import SwiftUI

/// What `odoodb script <db>` returns.
struct ScriptSettings: Decodable {
    var script: ScriptInfo?
    var exists: Bool
    var running: Bool
    var port: Int
    var envs: [String]
}

/// The database whose launch script the editor sheet shows.
struct ScriptEditorTarget: Identifiable {
    var db: String
    var id: String { db }
}

/// Edits a database's launch script: its settings through `odoodb script`, or its source as text.
struct ScriptEditorSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let db: String

    enum Mode: String, CaseIterable, Identifiable {
        case settings = "Settings", source = "Source"
        var id: String { rawValue }
    }

    enum AtStart: String, CaseIterable, Identifiable {
        case nothing = "Nothing", update = "Update (-u)", reinit = "Reinitialize (--reinit)"
        var id: String { rawValue }
    }

    @State private var loaded: ScriptSettings?
    @State private var loadError: String?
    @State private var mode: Mode = .settings
    @State private var branches: [String] = []
    @State private var saving = false

    // Settings form
    @State private var branch = ""
    @State private var pyenv = ""
    @State private var port = ""
    @State private var addons: [String] = []
    @State private var install = ""
    @State private var atStart: AtStart = .nothing
    @State private var update = ""

    // Source
    @State private var source = ""
    @State private var savedSource = ""

    private var script: ScriptInfo? { loaded?.script }

    var body: some View {
        VStack(spacing: 0) {
            header
            if let loadError {
                ContentUnavailableView("Cannot read the launch script", systemImage: "exclamationmark.triangle",
                                       description: Text(loadError))
                    .frame(maxHeight: .infinity)
            } else if let loaded, loaded.script == nil {
                ContentUnavailableView {
                    Label("No launch script", systemImage: "doc.badge.gearshape")
                } description: {
                    Text("Starting \(db) creates it with the right branch and addons.")
                } actions: {
                    Button("Create Now") {
                        Task {
                            store.repair(db, fix: "script")
                            try? await Task.sleep(for: .seconds(2))
                            await load()
                        }
                    }
                }
                .frame(maxHeight: .infinity)
            } else if loaded == nil {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                switch mode {
                case .settings: settingsForm
                case .source: sourceEditor
                }
            }
            footer
        }
        .frame(width: 640, height: 640)
        .task { await load() }
        .task {
            if let res = try? await CLI.json(BranchesResponse.self, ["branches"]) { branches = res.branches }
        }
    }

    // MARK: Header / footer

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Launch Script").font(.title2.weight(.semibold))
                    Text(script?.path.replacingOccurrences(of: NSHomeDirectory(), with: "~") ?? db)
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.middle)
                        .textSelection(.enabled)
                }
                Spacer()
                if script != nil {
                    Picker("", selection: $mode) {
                        ForEach(Mode.allCases) { Text($0.rawValue).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .fixedSize()
                    .disabled(dirty)
                    .help(dirty ? "Save or revert the changes to switch" : "")
                }
            }
            if let s = script, !s.managed, mode == .settings {
                Label {
                    Text("Written by hand: saving these settings replaces it with a standard script (a copy is kept as "
                         + "\(URL(fileURLWithPath: s.path).lastPathComponent).bak). Edit the Source to keep it as is.")
                } icon: {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                }
                .font(.caption)
                .padding(10)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(Color.orange.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .padding([.horizontal, .top], 20)
        .padding(.bottom, 8)
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if let s = script {
                Button("Open in Editor") { NSWorkspace.shared.open(URL(fileURLWithPath: s.path)) }
                    .help("Open the file in the default app for .sh files")
                Button("Reveal") { reveal(s.path) }
            }
            Spacer()
            if loaded?.running == true, dirty {
                Text("Applies at the next start").font(.caption).foregroundStyle(.secondary)
            }
            if dirty {
                Button("Revert") { revert() }
            }
            Button(dirty ? "Cancel" : "Close") { dismiss() }
                .keyboardShortcut(.cancelAction)
            if loaded?.running == true {
                Button("Save & Restart") { save(restart: true) }
                    .disabled(!dirty || saving || !valid)
            }
            Button("Save") { save(restart: false) }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(!dirty || saving || !valid)
        }
        .padding(20)
    }

    // MARK: Settings

    private var settingsForm: some View {
        Form {
            Section {
                Picker("Branch", selection: $branch) {
                    if !branches.contains(branch) { Text(branch.isEmpty ? "—" : branch).tag(branch) }
                    ForEach(branches, id: \.self) { Text($0).tag($0) }
                }
                Picker("Python environment", selection: $pyenv) {
                    if !(loaded?.envs ?? []).contains(pyenv) {
                        Text(pyenv.isEmpty ? "Default" : "\(pyenv) — not installed").tag(pyenv)
                    }
                    ForEach(loaded?.envs ?? [], id: \.self) { Text($0).tag($0) }
                }
                TextField("HTTP port", text: $port, prompt: Text("8069"))
                if !portValid {
                    Text("A number between 1024 and 65535.").font(.caption).foregroundStyle(.red)
                }
            }

            Section {
                ForEach(Array(addons.enumerated()), id: \.offset) { idx, path in
                    AddonsRow(path: path, first: idx == 0, last: idx == addons.count - 1,
                              move: { delta in move(idx, by: delta) },
                              change: { pickAddonsFolder(start: path) { addons[idx] = $0 } },
                              remove: { addons.remove(at: idx) })
                }
                HStack {
                    Button("Add Folder…") {
                        pickAddonsFolder(start: addons.last) { url in if !addons.contains(url) { addons.append(url) } }
                    }
                    Spacer()
                }
            } header: {
                Text("Addons path")
            } footer: {
                Text("Repositories and folders of modules, in order: when a module is in two of them, the first one wins.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Modules") {
                TextField("Installed at creation", text: $install, prompt: Text("base"))
                Picker("At each start", selection: $atStart) {
                    ForEach(AtStart.allCases) { Text($0.rawValue).tag($0) }
                }
                if atStart != .nothing {
                    TextField("Modules", text: $update, prompt: Text(atStart == .reinit ? "all" : "my_module"))
                }
            }
        }
        .formStyle(.grouped)
        .disabled(saving)
    }

    private func move(_ idx: Int, by delta: Int) {
        let to = idx + delta
        guard addons.indices.contains(to) else { return }
        withAnimation(.smooth(duration: 0.2)) { addons.swapAt(idx, to) }
    }

    // MARK: Source

    private var sourceEditor: some View {
        CodeTextView(text: $source)
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.separator))
            .padding(.horizontal, 20)
            .disabled(saving)
    }

    // MARK: State

    private var portValid: Bool {
        guard let p = Int(port.trimmingCharacters(in: .whitespaces)) else { return false }
        return (1024...65535).contains(p)
    }

    private var valid: Bool { mode == .source || (portValid && !addons.isEmpty) }

    private var settingsDirty: Bool {
        guard let s = script else { return false }
        return branch != s.branch || pyenv != s.pyenv || port != String(loaded?.port ?? 0)
            || addons != s.addonsPath || modules(install) != s.installModules
            || atStart != Self.atStart(s) || (atStart != .nothing && modules(update) != s.updateModules)
    }

    private var dirty: Bool { mode == .settings ? settingsDirty : source != savedSource }

    private static func atStart(_ s: ScriptInfo) -> AtStart {
        s.updateModules.isEmpty && s.updateMode == "update" ? .nothing : (s.updateMode == "update" ? .update : .reinit)
    }

    private func modules(_ text: String) -> [String] {
        text.split(whereSeparator: { $0 == "," || $0 == " " || $0 == "\n" }).map(String.init)
    }

    private func load() async {
        do {
            let res = try await CLI.json(ScriptSettings.self, ["script", db])
            loaded = res
            loadError = nil
            revert()
        } catch {
            loadError = error.localizedDescription
        }
    }

    private func revert() {
        guard let loaded, let s = loaded.script else { return }
        branch = s.branch
        pyenv = s.pyenv
        port = String(loaded.port)
        addons = s.addonsPath
        install = s.installModules.joined(separator: ", ")
        atStart = Self.atStart(s)
        update = s.updateModules.joined(separator: ", ")
        source = (try? String(contentsOfFile: s.path, encoding: .utf8)) ?? ""
        savedSource = source
    }

    private func save(restart: Bool) {
        guard let s = script else { return }
        saving = true
        Task {
            defer { saving = false }
            var ok: Bool
            if mode == .source {
                ok = writeSource(to: s.path)
            } else {
                var args = ["script", db, "-b", branch, "--port", port.trimmingCharacters(in: .whitespaces),
                            "--install", modules(install).joined(separator: ","),
                            "--update", atStart == .nothing ? "" : modules(update).joined(separator: ","),
                            "--update-mode", atStart == .reinit ? "reinit" : "update",
                            "-a"] + addons
                if !pyenv.isEmpty { args += ["--pyenv", pyenv] }
                ok = await store.perform("Launch script of \(db)", db: db, busyLabel: "Saving…", args: args) { r in
                    r.message ?? "Launch script of \(db) saved"
                } != nil
            }
            guard ok else { return }
            if restart { await store.runRestart(db) }
            dismiss()
        }
    }

    private func writeSource(to path: String) -> Bool {
        do {
            try source.write(toFile: path, atomically: true, encoding: .utf8)
            try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: path)
            store.revisions[db, default: 0] += 1
            store.show("Launch script of \(db) saved")
            return true
        } catch {
            store.show("Cannot save \(path): \(error.localizedDescription)", error: true)
            return false
        }
    }
}

/// One folder of the addons path, with its order and edit buttons.
private struct AddonsRow: View {
    var path: String
    var first: Bool
    var last: Bool
    var move: (Int) -> Void
    var change: () -> Void
    var remove: () -> Void

    private var exists: Bool {
        var dir: ObjCBool = false
        return FileManager.default.fileExists(atPath: path, isDirectory: &dir) && dir.boolValue
    }

    /// "core/addons" rather than "addons".
    private var name: String {
        let url = URL(fileURLWithPath: path)
        return url.lastPathComponent == "addons"
            ? "\(url.deletingLastPathComponent().lastPathComponent)/addons" : url.lastPathComponent
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: exists ? "folder" : "folder.badge.questionmark")
                .foregroundStyle(exists ? Color.secondary : Color.red)
            VStack(alignment: .leading, spacing: 1) {
                Text(name).lineLimit(1)
                Text(exists ? path.replacingOccurrences(of: NSHomeDirectory(), with: "~") : "Folder not found: \(path)")
                    .font(.caption).foregroundStyle(exists ? Color.secondary : Color.red)
                    .lineLimit(1).truncationMode(.head)
            }
            Spacer()
            HStack(spacing: 2) {
                iconButton("chevron.up", help: "Move up") { move(-1) }.disabled(first)
                iconButton("chevron.down", help: "Move down") { move(1) }.disabled(last)
                iconButton("folder.badge.gearshape", help: "Change folder…", action: change)
                iconButton("minus.circle", help: "Remove from the addons path", action: remove)
            }
            .buttonStyle(.borderless)
        }
        .contextMenu {
            Button("Change Folder…", action: change)
            Button("Move Up") { move(-1) }.disabled(first)
            Button("Move Down") { move(1) }.disabled(last)
            Divider()
            Button("Reveal in Finder") { reveal(path) }.disabled(!exists)
            Button("Copy Path") { copyToPasteboard(path) }
            Divider()
            Button("Remove", role: .destructive, action: remove)
        }
    }

    private func iconButton(_ symbol: String, help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).frame(width: 22, height: 22) }
            .help(help)
    }
}

/// Plain-text editor for shell scripts: monospaced, no smart quotes, dashes or spelling fixes.
struct CodeTextView: NSViewRepresentable {
    @Binding var text: String

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        let tv = scroll.documentView as! NSTextView
        tv.delegate = context.coordinator
        tv.isRichText = false
        tv.allowsUndo = true
        tv.font = .monospacedSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled = false
        tv.isAutomaticTextReplacementEnabled = false
        tv.isAutomaticSpellingCorrectionEnabled = false
        tv.isContinuousSpellCheckingEnabled = false
        tv.isGrammarCheckingEnabled = false
        tv.textContainerInset = NSSize(width: 6, height: 8)
        tv.string = text
        scroll.drawsBackground = true
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        let tv = scroll.documentView as! NSTextView
        if tv.string != text { tv.string = text }
    }

    func makeCoordinator() -> Coordinator { Coordinator(text: $text) }

    final class Coordinator: NSObject, NSTextViewDelegate {
        var text: Binding<String>
        init(text: Binding<String>) { self.text = text }

        func textDidChange(_ notification: Notification) {
            if let tv = notification.object as? NSTextView { text.wrappedValue = tv.string }
        }
    }
}
