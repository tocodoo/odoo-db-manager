import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store
    @State private var s: SettingsResponse?
    @State private var community = ""
    @State private var enterprise = ""
    @State private var scripts = ""
    @State private var pyenvRoot = ""
    @State private var pyenvEnv = ""
    @State private var psql = ""
    @State private var terminal = "Warp"
    @State private var port = "8069"
    @State private var saved = false

    var body: some View {
        TabView {
            SetupView()
                .tabItem { Label("Setup", systemImage: "wrench.and.screwdriver") }

            Form {
                Section("Odoo") {
                    pathField("Community (odoo-bin)", $community, folder: true)
                    pathField("Enterprise", $enterprise, folder: true)
                    pathField("Launch scripts", $scripts, folder: true)
                }
                Section("Runtime") {
                    pathField("pyenv root", $pyenvRoot, folder: true)
                    TextField("Force pyenv env", text: $pyenvEnv, prompt: Text("automatic (odoo-<version>)"))
                    pathField("psql", $psql, folder: false)
                    Picker("External terminal", selection: $terminal) {
                        ForEach(["Warp", "Terminal", "iTerm2"], id: \.self) { Text($0) }
                    }
                    TextField("Default port", text: $port)
                }
                HStack {
                    Spacer()
                    if saved { Label("Saved", systemImage: "checkmark").foregroundStyle(.green).transition(.opacity) }
                    Button("Save") { save() }.keyboardShortcut(.defaultAction)
                }
            }
            .formStyle(.grouped)
            .tabItem { Label("General", systemImage: "gearshape") }

            AgentsSettings()
                .tabItem { Label("Agents", systemImage: "sparkles") }
        }
        .frame(width: 640, height: 600)
        .task { await load() }
    }

    private func pathField(_ title: String, _ value: Binding<String>, folder: Bool) -> some View {
        LabeledContent(title) {
            HStack {
                TextField("", text: value).labelsHidden()
                Button("…") {
                    let panel = NSOpenPanel()
                    panel.canChooseDirectories = folder
                    panel.canChooseFiles = !folder
                    if panel.runModal() == .OK, let u = panel.url { value.wrappedValue = u.path }
                }
            }
        }
    }

    private func load() async {
        guard let r = try? await CLI.json(SettingsResponse.self, ["settings"]) else { return }
        s = r
        community = r.communityPath
        enterprise = r.enterprisePath
        scripts = r.scriptsPaths.first ?? ""
        pyenvRoot = r.pyenvRoot
        pyenvEnv = r.pyenvGlobalEnv
        psql = r.psql
        terminal = r.terminalApp
        port = String(r.defaultPort)
    }

    private func save() {
        let sets = [
            "odoo_community_path=\(community)", "odoo_enterprise_path=\(enterprise)", "scripts_paths=\(scripts)",
            "pyenv_root=\(pyenvRoot)", "pyenv_global_env=\(pyenvEnv)", "psql_path=\(psql)",
            "terminal_app=\(terminal)", "odoo_http_port=\(Int(port) ?? 8069)",
        ]
        Task {
            _ = try? await CLI.json(SettingsResponse.self, ["settings", "--set"] + sets)
            withAnimation { saved = true }
            await store.refresh()
            try? await Task.sleep(for: .seconds(2))
            withAnimation { saved = false }
        }
    }
}

struct AgentsSettings: View {
    @State private var skill = false
    @State private var cli = false

    private var skillPath: String { NSHomeDirectory() + "/.claude/skills/odoo-db/SKILL.md" }
    private var cliPath: String { NSHomeDirectory() + "/.local/bin/odoodb" }

    var body: some View {
        Form {
            Section {
                Text("This app is a monitor. Creating, starting and repairing databases is done by the `odoodb` command, which every Claude agent knows how to use.")
                    .foregroundStyle(.secondary)
            }
            Section("Installed for agents") {
                status("odoodb command", cli, cliPath)
                status("Claude Code skill", skill, skillPath)
            }
            Section("Try it") {
                Text("In any Claude Code session:").foregroundStyle(.secondary)
                Text("“Create an Odoo 19 database with website and my_module from ~/odoo/psbe-custom, then start it.”")
                Text("“The database bike won't start, fix it.”")
            }
            Section {
                HStack {
                    Spacer()
                    Button("Install / Repair") {
                        let p = Process()
                        p.executableURL = URL(fileURLWithPath: "/bin/bash")
                        p.arguments = [CLI.repo.appendingPathComponent("agent/install-agent.sh").path]
                        p.currentDirectoryURL = CLI.repo
                        try? p.run()
                        p.waitUntilExit()
                        check()
                    }
                }
            }
        }
        .formStyle(.grouped)
        .onAppear(perform: check)
    }

    private func status(_ title: String, _ ok: Bool, _ path: String) -> some View {
        LabeledContent(title) {
            HStack(spacing: 6) {
                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle").foregroundStyle(ok ? .green : .secondary)
                Text(ok ? path.replacingOccurrences(of: NSHomeDirectory(), with: "~") : "not installed")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func check() {
        skill = FileManager.default.fileExists(atPath: skillPath)
        cli = FileManager.default.fileExists(atPath: cliPath)
    }
}
