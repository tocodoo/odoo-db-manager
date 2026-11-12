import SwiftUI

/// Checklist of what this Mac needs (`odoodb setup`), each item with the button that installs or fixes it.
struct SetupView: View {
    @Environment(Store.self) private var store
    /// item id -> job of its running action
    @State private var jobs: [String: Job] = [:]
    /// Interactive action (GitHub sign-in) running in the built-in terminal.
    @State private var terminal: TerminalSession?
    @State private var newEnvironment = false

    var body: some View {
        Form {
            if let setup = store.setup {
                ForEach(groups(setup.items), id: \.0) { group, items in
                    Section(group) {
                        ForEach(items) { item in
                            SetupRow(item: item, job: jobs[item.id], action: { run(item) },
                                     alternative: { run($0, of: item) })
                        }
                        if group == "Python", items.contains(where: { $0.id == "pyenv" && $0.status == "ok" }) {
                            HStack {
                                Button("New Environment…") { newEnvironment = true }
                                Spacer()
                            }
                        }
                    }
                }
            } else {
                HStack {
                    Spacer()
                    ProgressView("Checking this Mac…")
                    Spacer()
                }
                .padding(30)
            }
        }
        .formStyle(.grouped)
        .safeAreaInset(edge: .bottom) {
            HStack {
                if let s = store.setup {
                    Text(s.problems > 0 ? "\(s.problems) thing\(s.problems == 1 ? "" : "s") to fix"
                         : s.warnings > 0 ? "Ready · \(s.warnings) recommended" : "Everything needed is installed.")
                        .font(.callout)
                        .foregroundStyle(s.problems > 0 ? Color.red : Color.secondary)
                }
                Spacer()
                if store.setupLoading { ProgressView().controlSize(.small) }
                Button("Check Again") { Task { await store.loadSetup() } }
                    .disabled(store.setupLoading)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)
            .background(.bar)
        }
        .task { await store.loadSetup() }
        .sheet(isPresented: $newEnvironment) { NewEnvironmentSheet() }
        .sheet(item: $terminal) { s in
            TerminalSheet(session: s) { Task { await store.loadSetup() } }
        }
    }

    /// Groups in the order the command lists them.
    private func groups(_ items: [SetupItem]) -> [(String, [SetupItem])] {
        var order: [String] = []
        var byGroup: [String: [SetupItem]] = [:]
        for i in items {
            if byGroup[i.group] == nil { order.append(i.group) }
            byGroup[i.group, default: []].append(i)
        }
        return order.map { ($0, byGroup[$0] ?? []) }
    }

    private func run(_ alt: SetupAlternative, of item: SetupItem) {
        if let link = alt.url, let url = URL(string: link) {
            NSWorkspace.shared.open(url)
            if let path = alt.path {
                store.show("Clone it in GitHub Desktop into \(path.replacingOccurrences(of: NSHomeDirectory(), with: "~")) "
                           + "(or anywhere, then Locate…), and Check Again here.")
            }
            return
        }
        guard let action = alt.action else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.prompt = "Use This Folder"
        panel.message = item.id == "enterprise" ? "Choose your odoo/enterprise clone"
            : "Choose your odoo/odoo clone (the folder that contains odoo-bin)"
        panel.directoryURL = URL(fileURLWithPath: NSHomeDirectory())
        guard panel.runModal() == .OK, let folder = panel.url else { return }
        Task {
            _ = await store.perform("Locate \(item.title)", db: nil, args: ["setup", "--fix", action, "--path", folder.path],
                                    onJob: { jobs[item.id] = $0 }) { r in r.message ?? "\(item.title) found" }
            jobs[item.id] = nil
            await store.loadSetup()
            await store.refresh()
        }
    }

    private func run(_ item: SetupItem) {
        if let cmd = item.terminal {
            terminal = TerminalSession(db: nil, kind: "setup", title: item.actionLabel.isEmpty ? item.title : item.actionLabel,
                                       executable: "/bin/zsh", args: ["-l", "-c", cmd], cwd: NSHomeDirectory(),
                                       env: ProcessInfo.processInfo.environment, log: nil)
            return
        }
        Task {
            await store.runSetup(item) { job in jobs[item.id] = job }
            jobs[item.id] = nil
        }
    }
}

struct SetupRow: View {
    var item: SetupItem
    var job: Job?
    var action: () -> Void
    var alternative: (SetupAlternative) -> Void = { _ in }

    private var alternatives: [SetupAlternative] { item.alternatives ?? [] }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(tint)
                .font(.title3)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(item.title).font(.callout.weight(.medium))
                if let job {
                    Text(job.progress)
                        .font(.caption)
                        .foregroundStyle(Color.accentColor)
                        .lineLimit(2)
                        .truncationMode(.middle)
                } else if !item.detail.isEmpty {
                    Text(item.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                }
                // Several ways to fix it: all the buttons on their own line, the main one first.
                if job == nil, !alternatives.isEmpty {
                    HStack(spacing: 8) {
                        if item.action != nil {
                            Button(item.actionLabel.isEmpty ? "Fix" : item.actionLabel, action: action)
                                .buttonStyle(.borderedProminent)
                        }
                        ForEach(alternatives) { alt in
                            Button(alt.label) { alternative(alt) }
                        }
                    }
                    .controlSize(.small)
                    .padding(.top, 4)
                }
            }
            Spacer(minLength: 8)
            if job != nil {
                ProgressView().controlSize(.small)
            } else if !alternatives.isEmpty {
                EmptyView()
            } else if item.action != nil {
                Button(item.actionLabel.isEmpty ? "Fix" : item.actionLabel, action: action)
                    .buttonStyle(.borderedProminent)
                    .tint(item.status == "ok" ? .secondary : .accentColor)
                    .controlSize(.small)
            } else if let link = item.link, item.status != "ok", let url = URL(string: link) {
                Button("Download") { NSWorkspace.shared.open(url) }.controlSize(.small)
            }
        }
        .padding(.vertical, 2)
    }

    private var symbol: String {
        switch item.status {
        case "ok": "checkmark.circle.fill"
        case "error": "xmark.octagon.fill"
        case "warning": "exclamationmark.triangle.fill"
        default: "circle.dashed"
        }
    }

    private var tint: Color {
        switch item.status {
        case "ok": .green
        case "error": .red
        case "warning": .orange
        default: .secondary
        }
    }
}

/// The checklist as a sheet (from the home toolbar, or opened when something blocking is missing).
struct SetupSheet: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Setup").font(.title2.weight(.semibold))
                    Text("What this Mac needs to run Odoo databases. Agents: `odoodb setup`.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding([.horizontal, .top], 20)
            .padding(.bottom, 8)
            SetupView()
        }
        .frame(width: 640, height: 640)
    }
}
