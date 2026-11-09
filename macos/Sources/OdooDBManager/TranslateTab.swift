import SwiftUI

struct TranslateTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    @State private var module = ""
    @State private var lang = "fr_FR"
    @State private var useAI = true
    @State private var customDest: URL?
    @State private var job: Job?

    static let languages: [(String, String)] = [
        ("fr_FR", "French"), ("fr_BE", "French (BE)"), ("nl_BE", "Dutch (BE)"), ("nl_NL", "Dutch"),
        ("de_DE", "German"), ("es_ES", "Spanish"), ("it_IT", "Italian"), ("pt_PT", "Portuguese"),
        ("pt_BR", "Portuguese (BR)"), ("pl_PL", "Polish"), ("ar_001", "Arabic"), ("zh_CN", "Chinese (Simplified)"),
        ("ja_JP", "Japanese"), ("en_GB", "English (UK)"),
    ]

    private var modules: [Module] {
        let mods = (info.modules ?? []).filter { $0.state == "installed" }
        return mods.filter { $0.source == "custom" } + mods.filter { $0.source != "custom" }
    }

    private var selected: Module? { info.modules?.first { $0.name == module } }

    private var destination: String {
        if let customDest { return customDest.path }
        if let m = selected, m.source == "custom", let loc = m.location {
            let short = lang.split(separator: "_").first.map(String.init) ?? lang
            let file = lang.lowercased() == "\(short)_\(short)" ? "\(short).po" : "\(lang).po"
            return "\(loc)/\(module)/i18n/\(file)"
        }
        return "~/Downloads/\(module)_\(lang).po"
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Translate a module").font(.title2.weight(.semibold))
                    Text("Exports the module's terms from this database, translates them with Claude and writes the .po file.")
                        .foregroundStyle(.secondary)
                }

                Form {
                    Picker("Module", selection: $module) {
                        if modules.contains(where: { $0.source == "custom" }) {
                            Section("Custom") {
                                ForEach(modules.filter { $0.source == "custom" }) { m in Text(m.name).tag(m.name) }
                            }
                        }
                        Section("Odoo") {
                            ForEach(modules.filter { $0.source != "custom" }) { m in Text(m.name).tag(m.name) }
                        }
                    }
                    Picker("Language", selection: $lang) {
                        ForEach(languageOptions, id: \.0) { code, name in Text("\(name) — \(code)").tag(code) }
                    }
                    Toggle("Translate with AI", isOn: $useAI)
                    LabeledContent("Save to") {
                        HStack {
                            Text(destination).lineLimit(1).truncationMode(.head).foregroundStyle(.secondary).textSelection(.enabled)
                            Button("Choose…", action: chooseFolder).controlSize(.small)
                            if customDest != nil { Button("Reset") { customDest = nil }.controlSize(.small) }
                        }
                    }
                }
                .formStyle(.grouped)
                .frame(maxWidth: 640)

                HStack {
                    if store.isRunning(info.name) {
                        Label("The server is stopped during the export, then restarted.", systemImage: "info.circle")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(action: run) {
                        Label(useAI ? "Translate" : "Export .po", systemImage: "character.bubble")
                            .frame(minWidth: 110)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(module.isEmpty || job?.state == .running)
                }
                .frame(maxWidth: 640)

                if let job { JobConsole(job: job).frame(maxWidth: 640) }
            }
            .padding(24)
            .frame(maxWidth: .infinity)
        }
        .onAppear {
            if module.isEmpty { module = modules.first?.name ?? "" }
            if let l = info.languages?.first(where: { $0.code != "en_US" }) { lang = l.code }
        }
    }

    private var languageOptions: [(String, String)] {
        var opts = Self.languages
        for l in info.languages ?? [] where !opts.contains(where: { $0.0 == l.code }) && l.code != "en_US" {
            opts.insert((l.code, l.name), at: 0)
        }
        return opts
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.prompt = "Choose"
        if panel.runModal() == .OK { customDest = panel.url }
    }

    private func run() {
        let db = info.name
        var args = ["translate", db, "-m", module, "-l", lang]
        if let customDest { args += ["-o", customDest.path] }
        if !useAI { args.append("--no-ai") }
        let wasRunning = store.isRunning(db)
        Task {
            if wasRunning { await store.perform("Stop \(db)", db: db, args: ["stop", db]) { _ in "\(db) stopped for the export" } }
            await store.perform("Translate \(module) (\(lang))", db: db, busyLabel: "Translating…", args: args,
                                onJob: { job = $0 }) { r in "Written \(r.path ?? "")" }
            if wasRunning { store.start(db) }
        }
    }
}

/// Live output of a job, with its final state.
struct JobConsole: View {
    var job: Job

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                switch job.state {
                case .running: ProgressView().controlSize(.small)
                case .success: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .failure: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                }
                Text(job.state == .running ? job.progress : job.message)
                    .font(.callout.weight(.medium)).lineLimit(2).textSelection(.enabled)
                Spacer()
                if let p = job.resultPath, job.state == .success {
                    Button("Reveal") { reveal(p) }.controlSize(.small)
                }
            }
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 1) {
                        ForEach(Array(job.lines.enumerated()), id: \.offset) { i, line in
                            Text(line).font(.system(size: 10.5, design: .monospaced))
                                .foregroundStyle(line.contains(" ERROR ") || line.hasPrefix("✗") ? .red : .secondary)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .id(i)
                        }
                    }
                    .textSelection(.enabled)
                    .padding(10)
                }
                .frame(height: 180)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.quaternary, lineWidth: 0.5))
                .onChange(of: job.lines.count) { _, n in proxy.scrollTo(n - 1, anchor: .bottom) }
            }
        }
        .padding(12)
        .card()
    }
}
