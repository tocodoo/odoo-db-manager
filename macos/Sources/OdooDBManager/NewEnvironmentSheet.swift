import SwiftUI

/// What `odoodb env list` returns (the parts the new environment sheet needs).
struct EnvListResponse: Decodable {
    struct Env: Decodable { var name: String; var exists: Bool }
    var envs: [Env]
    /// "3.12" -> "3.12.10": Python versions already installed with pyenv.
    var pythons: [String: String]
}

/// Creates a pyenv environment: name, Python version, and optionally the requirements.txt of an Odoo branch.
struct NewEnvironmentSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var nameEdited = false
    @State private var python = "3.12"
    @State private var pythonEdited = false
    @State private var requirements = true
    @State private var branch = ""
    @State private var branches: [String] = []
    @State private var remoteOnly: Set<String> = []
    @State private var existing: Set<String> = []
    @State private var installed: [String: String] = [:]
    @State private var job: Job?

    /// Versions offered: what Odoo supports, plus any other installed one.
    private var pythons: [String] {
        Array(Set(["3.10", "3.11", "3.12", "3.13"]).union(installed.keys))
            .sorted { $0.split(separator: ".").map { Int($0) ?? 0 }.lexicographicallyPrecedes($1.split(separator: ".").map { Int($0) ?? 0 }) }
    }

    private var validName: Bool {
        name.range(of: #"^[A-Za-z0-9][A-Za-z0-9_.\-]*$"#, options: .regularExpression) != nil
            && name.range(of: #"^\d+\.\d+(\.\d+)?$"#, options: .regularExpression) == nil
    }
    private var taken: Bool { existing.contains(name) }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("New Python Environment").font(.title2.weight(.semibold))
                    Text("A pyenv environment. Agents: `odoodb env create`.").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding([.horizontal, .top], 20)

            Form {
                Section {
                    TextField("Name", text: Binding(get: { name }, set: { name = $0; nameEdited = true }),
                              prompt: Text("odoo-19"))
                    if taken {
                        Text("An environment with this name already exists.").font(.caption).foregroundStyle(.red)
                    } else if !name.isEmpty, !validName {
                        Text("Letters, digits, '-', '_' and '.'; not a bare version number.").font(.caption).foregroundStyle(.red)
                    }
                    Picker("Python", selection: Binding(get: { python }, set: { python = $0; pythonEdited = true })) {
                        ForEach(pythons, id: \.self) { v in
                            Text(installed[v].map { "\(v) — \($0) installed" } ?? "\(v) — installed first (a few minutes)").tag(v)
                        }
                    }
                }
                Section {
                    Toggle("Install Odoo's requirements.txt", isOn: $requirements.animation())
                    if requirements {
                        Picker("From the community branch", selection: $branch) {
                            if branches.isEmpty { Text(branch.isEmpty ? "Loading…" : branch).tag(branch) }
                            ForEach(branches, id: \.self) { b in
                                Text(remoteOnly.contains(b) ? "\(b) — from GitHub" : b).tag(b)
                            }
                        }
                        Text(remoteOnly.contains(branch)
                             ? "Not downloaded yet: the branch is fetched from GitHub to read its requirements.txt."
                             : "Its requirements.txt is read from the community clone; nothing is checked out.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text("An empty environment with pip, setuptools and wheel.").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .formStyle(.grouped)
            .disabled(job?.state == .running)

            if let job {
                JobConsole(job: job).padding(.horizontal, 20)
            }

            HStack {
                if job?.state == .success {
                    Spacer()
                    Button("Done") { dismiss() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                } else {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                        .disabled(job?.state == .running)
                    Spacer()
                    Button(job?.state == .failure ? "Retry" : "Create") { create() }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.defaultAction)
                        .disabled(!validName || taken || (requirements && branch.isEmpty) || job?.state == .running)
                }
            }
            .padding(20)
        }
        .frame(width: 520)
        .frame(minHeight: 440)
        .onChange(of: branch) { _, b in suggest(from: b) }
        .task {
            if let res = try? await CLI.json(BranchesResponse.self, ["branches"]) {
                branches = res.branches
                if branch.isEmpty { branch = res.current ?? res.branches.first ?? "" }
            }
            if let res = try? await CLI.json(EnvListResponse.self, ["env", "list"]) {
                existing = Set(res.envs.filter(\.exists).map(\.name))
                installed = res.pythons
            }
            if let res = try? await CLI.json(BranchesResponse.self, ["branches", "--remote"]) {
                remoteOnly = Set(res.remoteOnly ?? [])
                branches = res.branches
            }
        }
    }

    /// Name and Python follow the branch until the user changes them: odoo-<major>, and Odoo's Python for it.
    private func suggest(from branch: String) {
        guard let major = Int(branch.split(whereSeparator: { !$0.isNumber }).first ?? "") else { return }
        if !nameEdited { name = "odoo-\(major)" }
        if !pythonEdited { python = major >= 18 ? "3.12" : "3.10" }
    }

    private func create() {
        var args = ["env", "create", name, "--python", python]
        if requirements { args += ["-b", branch] } else { args.append("--no-requirements") }
        let env = name
        Task {
            await store.perform("Create environment \(env)", db: nil, args: args, onJob: { job = $0 }) { r in
                r.message ?? "\(env) created"
            }
            await store.loadSetup(network: false)
        }
    }
}
