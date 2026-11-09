import SwiftUI

enum DetailTab: String, CaseIterable, Identifiable {
    case overview = "Overview", modules = "Modules", logs = "Logs", translate = "Translate"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .overview: "square.grid.2x2"
        case .modules: "shippingbox"
        case .logs: "text.alignleft"
        case .translate: "character.bubble"
        }
    }
}

struct DetailView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let name: String
    @State private var info: DBInfo?
    @State private var error: String?
    @State private var tab: DetailTab = DevSnapshot.tab ?? .overview
    @State private var loading = false

    var body: some View {
        Group {
            if let info {
                content(info)
            } else if let error {
                ContentUnavailableView("Cannot load \(name)", systemImage: "exclamationmark.triangle", description: Text(error))
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationTitle(name)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                JobsButton()
                Button {
                    Task { await load() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .keyboardShortcut("r")
                Menu {
                    DatabaseMenu(name: name, series: info?.series) { dismiss() }
                } label: {
                    Label("More", systemImage: "ellipsis.circle")
                }
            }
        }
        .task(id: store.revisions[name, default: 0]) { await load() }
        .onChange(of: store.isRunning(name)) { _, _ in Task { await load() } }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let i = try await CLI.json(DBInfo.self, ["info", name, "--modules"])
            withAnimation(.smooth) { info = i; error = nil }
        } catch {
            if info == nil { self.error = error.localizedDescription }
        }
    }

    private func content(_ info: DBInfo) -> some View {
        VStack(spacing: 0) {
            DetailHeader(info: info)
                .padding(.horizontal, 24)
                .padding(.top, 18)
                .padding(.bottom, 14)

            Picker("Section", selection: $tab) {
                ForEach(DetailTab.allCases) { t in Label(t.rawValue, systemImage: t.symbol).tag(t) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 460)
            .padding(.bottom, 12)

            Divider()

            Group {
                switch tab {
                case .overview: OverviewTab(info: info)
                case .modules: ModulesTab(info: info)
                case .logs: LogsTab(info: info)
                case .translate: TranslateTab(info: info)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .transition(.opacity)
            .animation(.smooth(duration: 0.2), value: tab)
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .background {
            Group {
                Button("") { tab = .overview }.keyboardShortcut("1")
                Button("") { tab = .modules }.keyboardShortcut("2")
                Button("") { tab = .logs }.keyboardShortcut("3")
                Button("") { tab = .translate }.keyboardShortcut("4")
            }
            .opacity(0)
        }
    }
}

struct DetailHeader: View {
    @Environment(Store.self) private var store
    var info: DBInfo

    var body: some View {
        let proc = store.process(for: info.name)
        let busy = store.busy[info.name]
        let job = store.activeJobs.first { $0.db == info.name }
        HStack(alignment: .center, spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(Theme.color(forSeries: info.series).gradient)
                Text(String(info.name.prefix(1)).uppercased())
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
            }
            .frame(width: 52, height: 52)
            .shadow(color: Theme.color(forSeries: info.series).opacity(0.3), radius: 6, y: 3)

            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 10) {
                    Text(info.name).font(.title.weight(.bold)).textSelection(.enabled)
                    VersionBadge(series: info.series)
                }
                HStack(spacing: 8) {
                    StatusDot(running: proc != nil, busy: busy != nil)
                    if let job, busy != nil {
                        Text(job.progress).lineLimit(1).truncationMode(.middle).foregroundStyle(Color.accentColor)
                    } else if let proc {
                        Text("Running on")
                        Link(proc.url.replacingOccurrences(of: "http://", with: ""), destination: URL(string: proc.url)!)
                        Text("· \(proc.mode == "terminal" ? "in terminal" : "in background")").foregroundStyle(.secondary)
                    } else {
                        Text("Stopped").foregroundStyle(.secondary)
                        Text("· port \(String(info.port))").foregroundStyle(.tertiary)
                    }
                }
                .font(.callout)
            }

            Spacer()

            HStack(spacing: 8) {
                if proc != nil {
                    Button { store.restart(info.name) } label: { Label("Restart", systemImage: "arrow.clockwise") }
                        .help("Restart (⌘⇧R)").keyboardShortcut("r", modifiers: [.command, .shift])
                    Button { store.stop(info.name) } label: { Label("Stop", systemImage: "stop.fill") }
                        .keyboardShortcut(".", modifiers: .command)
                    Button { store.open(info.name, series: info.series) } label: { Label("Open", systemImage: "safari") }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut("o")
                } else {
                    Button { store.start(info.name) } label: { Label("Start", systemImage: "play.fill") }
                        .buttonStyle(.borderedProminent)
                        .keyboardShortcut(.return, modifiers: .command)
                        .disabled(busy != nil || !info.isOdoo)
                }
            }
            .controlSize(.large)
            .disabled(busy != nil)
        }
    }
}
