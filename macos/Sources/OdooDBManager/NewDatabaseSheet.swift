import SwiftUI
import UniformTypeIdentifiers

struct NewDatabaseSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    /// Backup dropped on the window (zip, folder, dump file): fills Dump and Filestore.
    var dropped: URL?
    var onCreated: (String) -> Void

    @ViewState private var name = ""
    @ViewState private var branch = ""
    @ViewState private var branches: [String] = []
    @ViewState private var remoteOnly: Set<String> = []
    @ViewState private var modules = ""
    @ViewState private var addons: [URL] = []
    @ViewState private var restore = false
    @ViewState private var dumpURL: URL?
    @ViewState private var filestore: URL?
    @ViewState private var reading = false
    @ViewState private var readError: String?
    @ViewState private var extracted: URL?
    @ViewState private var dropTargeted = false
    @ViewState private var lastSuggested = ""
    @ViewState private var neutralize = true
    @ViewState private var startAfter = true
    @ViewState private var job: Job?

    private var validName: Bool { name.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.\-]*$"#, options: .regularExpression) != nil }
    private var exists: Bool { store.databases.contains { $0.name == name } }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("New Database").font(.title2.weight(.semibold))
                    Text("Agents do the same with `odoodb create`.").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding([.horizontal, .top], 20)

            Form {
                Section {
                    TextField("Name", text: $name, prompt: Text("my-project"))
                    if exists { Text("A database with this name already exists.").font(.caption).foregroundStyle(.red) }
                    Picker("Branch", selection: $branch) {
                        if branches.isEmpty { Text(branch.isEmpty ? "Loading…" : branch).tag(branch) }
                        ForEach(branches, id: \.self) { b in
                            Text(remoteOnly.contains(b) ? "\(b) — from GitHub" : b).tag(b)
                        }
                    }
                    if remoteOnly.contains(branch) {
                        Text("Not downloaded yet: core and enterprise fetch it from GitHub when the database is created.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    TextField("Modules", text: $modules, prompt: Text("website, sale, my_module"))
                }
                Section("Custom addons") {
                    ForEach(addons, id: \.self) { url in
                        HStack {
                            Image(systemName: "folder").foregroundStyle(.secondary)
                            Text(url.path.replacingOccurrences(of: NSHomeDirectory(), with: "~")).lineLimit(1).truncationMode(.head)
                            Spacer()
                            Button { addons.removeAll { $0 == url } } label: { Image(systemName: "minus.circle.fill") }
                                .buttonStyle(.plain).foregroundStyle(.secondary)
                        }
                    }
                    Button("Add Folder…") { pick(folder: true) { addons.append($0) } }
                    Text("Folders that contain your modules are also detected automatically under the Odoo folder.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    Toggle("Restore a backup", isOn: $restore.animation())
                    if restore {
                        if reading {
                            HStack(spacing: 8) {
                                ProgressView().controlSize(.small)
                                Text("Reading the backup…").foregroundStyle(.secondary)
                            }
                        }
                        LabeledContent("Dump") {
                            HStack {
                                Text(display(dumpURL) ?? "—").foregroundStyle(.secondary)
                                    .lineLimit(1).truncationMode(.middle)
                                Button("Choose…") { pick(folder: false) { load($0) } }
                            }
                        }
                        LabeledContent("Filestore") {
                            HStack {
                                Text(display(filestore) ?? "optional").foregroundStyle(.secondary)
                                    .lineLimit(1).truncationMode(.middle)
                                if filestore != nil {
                                    Button { filestore = nil } label: { Image(systemName: "xmark.circle.fill") }
                                        .buttonStyle(.plain).foregroundStyle(.secondary)
                                }
                                Button("Choose…") { pick(folder: true) { load($0) } }
                            }
                        }
                        if let readError {
                            Text(readError).font(.caption).foregroundStyle(.red)
                        } else {
                            Text("Drop a backup .zip, a dump or a folder on this window: both fields are filled.")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Toggle("Neutralize (admin/admin, crons and mail servers off)", isOn: $neutralize)
                    }
                }
                Section {
                    Toggle("Start the server when ready", isOn: $startAfter)
                }
            }
            .formStyle(.grouped)
            .disabled(job != nil)

            if let job {
                JobConsole(job: job).padding(.horizontal, 20)
            }

            HStack {
                if job?.state == .success {
                    Spacer()
                    Button("Close") { dismiss() }
                    Button("Open \(name)") {
                        dismiss()
                        onCreated(name)
                    }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                } else {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                        .disabled(job?.state == .running)
                    Spacer()
                    Button(job?.state == .failure ? "Retry" : "Create") { create() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(!validName || exists || (restore && dumpURL == nil) || reading || job?.state == .running)
                }
            }
            .padding(20)
        }
        .frame(width: 560)
        .frame(minHeight: 520)
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            guard job == nil, let p = providers.first else { return false }
            _ = p.loadObject(ofClass: URL.self) { url, _ in
                guard let url else { return }
                Task { @MainActor in load(url) }
            }
            return true
        }
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
                    .background(Color.accentColor.opacity(0.06), in: RoundedRectangle(cornerRadius: 12))
                    .overlay(Label("Drop to fill Dump and Filestore", systemImage: "arrow.down.doc").font(.title3.weight(.medium)))
                    .padding(8)
                    .allowsHitTesting(false)
            }
        }
        // The extracted zip is only needed until the restore has run.
        .onDisappear { if let extracted, job?.state != .running { try? FileManager.default.removeItem(at: extracted) } }
        .task {
            if let dropped { load(dropped) }
            if let res = try? await CLI.json(BranchesResponse.self, ["branches"]) {
                branches = res.branches
                if branch.isEmpty { branch = res.current ?? res.branches.first ?? "" }
            }
            // Then the version branches on GitHub that are not fetched yet (network, cached an hour).
            if let res = try? await CLI.json(BranchesResponse.self, ["branches", "--remote"]) {
                remoteOnly = Set(res.remoteOnly ?? [])
                branches = res.branches
            }
        }
    }

    /// Reads a backup (zip, folder, dump file or filestore folder) and fills the restore fields.
    private func load(_ url: URL) {
        withAnimation { restore = true }
        reading = true
        readError = nil
        Task {
            defer { reading = false }
            do {
                let b = try await BackupDrop.resolve(url)
                if let old = extracted, old != b.extracted { try? FileManager.default.removeItem(at: old) }
                if b.extracted != nil { extracted = b.extracted }
                if let d = b.dump {
                    dumpURL = d
                    filestore = b.filestore
                    if name.isEmpty || name == lastSuggested { name = b.name; lastSuggested = b.name }
                } else if let f = b.filestore {
                    filestore = f  // a filestore folder alone: keep the dump already chosen
                }
            } catch {
                readError = error.localizedDescription
            }
        }
    }

    /// "dump.sql (from acme.zip)" for an extracted backup, a ~ path otherwise.
    private func display(_ url: URL?) -> String? {
        guard let url else { return nil }
        if let extracted, url.path.hasPrefix(extracted.path) {
            let inside = url.path.dropFirst(extracted.path.count + 1)
            return "\(inside.isEmpty ? url.lastPathComponent : String(inside)) — from the dropped backup"
        }
        return url.path.replacingOccurrences(of: NSHomeDirectory(), with: "~")
    }

    private func pick(folder: Bool, _ done: @escaping (URL) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true  // the dump picker also takes a backup folder
        panel.canChooseFiles = !folder
        panel.directoryURL = URL(fileURLWithPath: NSHomeDirectory() + "/odoo")
        if panel.runModal() == .OK, let url = panel.url { done(url) }
    }

    private func create() {
        var args = ["create", name]
        if !branch.isEmpty { args += ["-b", branch] }
        let mods = modules.split(whereSeparator: { $0 == "," || $0 == " " || $0 == "\n" }).map(String.init)
        if !mods.isEmpty { args += ["-m", mods.joined(separator: ",")] }
        if !addons.isEmpty { args += ["-a"] + addons.map(\.path) }
        if restore, let dumpURL {
            args += ["--dump", dumpURL.path]
            if let filestore { args += ["--filestore", filestore.path] }
            if !neutralize { args.append("--no-sanitize") }
        }
        if !startAfter { args.append("--no-start") }
        let db = name
        Task {
            await store.perform("Create \(db)", db: db, busyLabel: "Creating…", args: args, onJob: { job = $0 }) { r in
                r.url != nil ? "\(db) is ready — admin / admin" : "\(db) created"
            }
        }
    }
}
