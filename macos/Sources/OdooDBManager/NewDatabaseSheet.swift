import SwiftUI
import UniformTypeIdentifiers

struct NewDatabaseSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    var dump: URL?
    var onCreated: (String) -> Void

    @State private var name = ""
    @State private var branch = ""
    @State private var branches: [String] = []
    @State private var remoteOnly: Set<String> = []
    @State private var modules = ""
    @State private var addons: [URL] = []
    @State private var restore = false
    @State private var dumpURL: URL?
    @State private var filestore: URL?
    @State private var neutralize = true
    @State private var startAfter = true
    @State private var job: Job?

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
                        LabeledContent("Dump") {
                            HStack {
                                Text(dumpURL?.lastPathComponent ?? "—").foregroundStyle(.secondary)
                                Button("Choose…") { pick(folder: false) { dumpURL = $0 } }
                            }
                        }
                        LabeledContent("Filestore") {
                            HStack {
                                Text(filestore?.lastPathComponent ?? "optional").foregroundStyle(.secondary)
                                Button("Choose…") { pick(folder: true) { filestore = $0 } }
                            }
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
                        .disabled(!validName || exists || (restore && dumpURL == nil) || job?.state == .running)
                }
            }
            .padding(20)
        }
        .frame(width: 560)
        .frame(minHeight: 520)
        .task {
            if let dump {
                restore = true
                dumpURL = dump
                name = dump.deletingPathExtension().lastPathComponent
                    .replacingOccurrences(of: " ", with: "-")
                    .filter { $0.isLetter || $0.isNumber || "-_.".contains($0) }
            }
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

    private func pick(folder: Bool, _ done: @escaping (URL) -> Void) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = folder
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
