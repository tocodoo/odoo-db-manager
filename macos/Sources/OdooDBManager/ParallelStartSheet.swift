import SwiftUI

/// Starting a database while others run: stop them and start it alone, or run it alongside on a free port —
/// on other code (another version or branch) from a temporary worktree, with the Python environment of choice.
struct ParallelStartSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    var request: ParallelStartRequest

    enum Mode: Hashable { case alone, alongside }

    @ViewState private var plan: ParallelPlan?
    @ViewState private var loadError: String?
    @ViewState private var mode: Mode = .alongside
    @ViewState private var port = 0
    @ViewState private var otherCode = false
    @ViewState private var coreBranch = ""
    @ViewState private var entBranch = ""
    @ViewState private var sameEnterprise = true
    @ViewState private var pyenv = ""
    @ViewState private var pyenvEdited = false
    @ViewState private var branches: [String] = []
    @ViewState private var remoteOnly: Set<String> = []
    @ViewState private var envs: Set<String> = []
    @ViewState private var envsLoaded = false

    private var db: String { request.db }
    private var others: [RunningProcess] { plan?.running ?? store.othersRunning(db) }
    private var useWorktree: Bool { otherCode || plan?.needsWorktree == true }
    private var envMissing: Bool { useWorktree && envsLoaded && !pyenv.isEmpty && !envs.contains(pyenv) }
    private var portBusy: Bool { others.contains { $0.port == port } }
    private var suggestedEnv: String {
        Int(coreBranch.split(whereSeparator: { !$0.isNumber }).first ?? "").map { "odoo-\($0)" } ?? (plan?.pyenv ?? "")
    }

    var body: some View {
        VStack(spacing: 0) {
            header.padding([.horizontal, .top], 20)

            if let plan {
                form(plan)
            } else if let loadError {
                Text(loadError).foregroundStyle(.red).padding(20).frame(maxHeight: .infinity)
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }

            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(startLabel) { go() }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                    .disabled(plan == nil || (mode == .alongside && (port < 1024 || portBusy || (useWorktree && (coreBranch.isEmpty || pyenv.isEmpty)))))
            }
            .padding(20)
        }
        .frame(width: 560)
        .frame(minHeight: 420)
        .task { await load() }
        .onChange(of: coreBranch) { _, b in
            if sameEnterprise { entBranch = b }
            if !pyenvEdited { pyenv = suggestedEnv }
        }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(request.inTerminal ? "Start \(db) in Terminal" : "Start \(db)").font(.title2.weight(.semibold))
                    if let s = plan?.series, !s.isEmpty { VersionBadge(series: s) }
                }
                Text(others.isEmpty ? "No other database is running."
                     : others.map { "\($0.db) on :\($0.port)" }.joined(separator: ", ") + (others.count > 1 ? " are running." : " is running."))
                    .font(.callout).foregroundStyle(.secondary)
            }
            Spacer()
        }
    }

    @ViewBuilder
    private func form(_ plan: ParallelPlan) -> some View {
        Form {
            Section {
                Picker("", selection: $mode.animation()) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(others.count == 1 ? "Stop \(others[0].db), start \(db) alone" : "Stop the others, start \(db) alone")
                        Text(aloneDetail(plan)).font(.caption).foregroundStyle(.secondary)
                    }
                    .tag(Mode.alone)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Run both at the same time")
                        Text("On a free port, at its own address so the logins do not collide.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    .tag(Mode.alongside)
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
            }

            if mode == .alongside {
                Section {
                    LabeledContent("Port") {
                        HStack(spacing: 8) {
                            TextField("", value: $port, format: .number.grouping(.never))
                                .frame(width: 70).multilineTextAlignment(.trailing).labelsHidden()
                            Text(verbatim: "http://\(plan.host):\(port)").foregroundStyle(.secondary).textSelection(.enabled)
                        }
                    }
                    if portBusy {
                        Text("Port \(String(port)) is used by \(others.first { $0.port == port }?.db ?? "another database").")
                            .font(.caption).foregroundStyle(.red)
                    }
                }

                Section {
                    if plan.needsWorktree {
                        Label("\(db) is \(plan.series), the repos are on \(plan.reposBranch ?? plan.codeSeries): it runs from its own copy of the code.",
                              systemImage: "arrow.triangle.branch")
                            .font(.callout)
                    } else {
                        Toggle("Run on another branch or version", isOn: $otherCode.animation())
                        if !otherCode {
                            Text("Same code as the running databases (repos on \(plan.reposBranch ?? plan.codeSeries)).")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if useWorktree {
                        branchPicker("Core branch", selection: $coreBranch)
                        if plan.hasEnterprise {
                            Toggle("Same enterprise branch", isOn: $sameEnterprise.animation())
                                .onChange(of: sameEnterprise) { _, same in if same { entBranch = coreBranch } }
                            if !sameEnterprise { branchPicker("Enterprise branch", selection: $entBranch) }
                        }
                        envPicker
                    }
                } header: {
                    Text("Code")
                } footer: {
                    if useWorktree { worktreeNote(plan) }
                }
            }
        }
        .formStyle(.grouped)
    }

    private func aloneDetail(_ plan: ParallelPlan) -> String {
        var s = "On port \(String(plan.port)), from the main core and enterprise"
        if plan.needsWorktree { s += " (switched to \(plan.defaultBranch) first)" }
        return s + "."
    }

    private func branchPicker(_ title: String, selection: Binding<String>) -> some View {
        Picker(title, selection: selection) {
            if !branches.contains(selection.wrappedValue) { Text(selection.wrappedValue.isEmpty ? "Loading…" : selection.wrappedValue).tag(selection.wrappedValue) }
            ForEach(branches, id: \.self) { b in
                Text(remoteOnly.contains(b) ? "\(b) — from GitHub" : b).tag(b)
            }
        }
    }

    @ViewBuilder
    private var envPicker: some View {
        let names = Array(envs.union([suggestedEnv, pyenv].filter { !$0.isEmpty })).sorted { a, b in
            let ma = Int(a.filter(\.isNumber)) ?? 0, mb = Int(b.filter(\.isNumber)) ?? 0
            return ma != mb ? ma > mb : a < b
        }
        Picker("Python environment", selection: Binding(get: { pyenv }, set: { pyenv = $0; pyenvEdited = true })) {
            ForEach(names, id: \.self) { n in
                Text(envs.contains(n) || !envsLoaded ? n : "\(n) — not created yet").tag(n)
            }
        }
        if envMissing {
            Text("\(pyenv) is created first with pyenv, with the requirements.txt of \(coreBranch) (a few minutes, once).")
                .font(.caption).foregroundStyle(.orange)
        }
    }

    private func worktreeNote(_ plan: ParallelPlan) -> some View {
        let key = sameEnterprise || entBranch == coreBranch ? coreBranch : "\(coreBranch)+\(entBranch)"
        let reused = plan.worktrees.contains(key.replacingOccurrences(of: "/", with: "_"))
        return Text(reused
             ? "The \(key) worktree already exists: it is shared. Removed when the last database on it stops."
             : "A temporary git worktree of core and enterprise on \(key): about 2 GB, ready in under a minute, shares the git history of your clones. Removed when the last database on it stops — kept if you modify files in it.")
            .font(.caption).foregroundStyle(.secondary)
    }

    private var startLabel: String {
        if mode == .alone { return others.count == 1 ? "Stop & Start" : "Stop Others & Start" }
        return envMissing ? "Create \(pyenv) & Start" : "Start Alongside"
    }

    private func load() async {
        do {
            let p = try await CLI.json(ParallelPlan.self, ["parallel", db])
            plan = p
            port = p.freePort
            coreBranch = p.defaultBranch
            entBranch = p.defaultBranch
            pyenv = p.pyenv
        } catch {
            loadError = error.localizedDescription
            return
        }
        if let res = try? await CLI.json(EnvListResponse.self, ["env", "list"]) {
            envs = Set(res.envs.filter(\.exists).map(\.name))
            envsLoaded = true
        }
        if let res = try? await CLI.json(BranchesResponse.self, ["branches"]) { branches = res.branches }
        if let res = try? await CLI.json(BranchesResponse.self, ["branches", "--remote"]) {
            remoteOnly = Set(res.remoteOnly ?? [])
            branches = res.branches
        }
    }

    private func go() {
        let inTerminal = request.inTerminal
        dismiss()
        if mode == .alone {
            store.startAlone(db, inTerminal: inTerminal)
            return
        }
        var flags = ["--alongside", "--port", String(port)]
        if useWorktree {
            flags += ["--core-branch", coreBranch, "--enterprise-branch", sameEnterprise ? coreBranch : entBranch, "--pyenv", pyenv]
        }
        store.startAlongside(db, flags: flags, inTerminal: inTerminal,
                             createEnv: envMissing ? (pyenv, coreBranch) : nil)
    }
}
