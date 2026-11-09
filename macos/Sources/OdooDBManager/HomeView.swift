import SwiftUI
import UniformTypeIdentifiers

enum DBFilter: String, CaseIterable, Identifiable {
    case all = "All", running = "Running", custom = "With addons", problems = "Attention"
    var id: String { rawValue }
}

enum DBSort: String, CaseIterable, Identifiable {
    case recent = "Recent activity", name = "Name", size = "Size", version = "Version"
    var id: String { rawValue }
}

struct HomeView: View {
    @Environment(Store.self) private var store
    @Binding var path: [String]
    @State private var search = ""
    @AppStorage("home.filter") private var filter: DBFilter = .all
    @AppStorage("home.sort") private var sort: DBSort = .recent
    @AppStorage("home.version") private var versionFilter = ""
    @State private var showNew = false
    @State private var droppedDump: URL?
    @State private var dropTargeted = false

    private var versions: [String] {
        Array(Set(store.databases.compactMap { $0.series })).sorted { $0.localizedStandardCompare($1) == .orderedDescending }
    }

    private var filtered: [DBSummary] {
        var items = store.databases
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        if !q.isEmpty {
            items = items.filter {
                $0.name.lowercased().contains(q)
                    || $0.customAddons.contains { $0.lowercased().contains(q) }
                    || ($0.series ?? "").lowercased().contains(q)
                    || ($0.folder ?? "").lowercased().contains(q)
            }
        }
        if !versionFilter.isEmpty { items = items.filter { $0.series == versionFilter } }
        switch filter {
        case .all: break
        case .running: items = items.filter { store.isRunning($0.name) }
        case .custom: items = items.filter { !$0.customAddons.isEmpty }
        case .problems: items = items.filter { !$0.isOdoo || ($0.modulesLocked ?? 0) > 0 }
        }
        switch sort {
        case .name: items.sort { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
        case .size: items.sort { $0.size > $1.size }
        case .version: items.sort { ($0.series ?? "").localizedStandardCompare($1.series ?? "") == .orderedDescending }
        case .recent: items.sort { ($0.lastActivity ?? .distantPast) > ($1.lastActivity ?? .distantPast) }
        }
        return items
    }

    private var runningDBs: [DBSummary] {
        store.running.filter { $0.kind == "server" }.compactMap { p in store.databases.first { $0.name == p.db } }
    }

    var body: some View {
        Group {
            if store.list == nil {
                if let err = store.loadError {
                    ContentUnavailableView {
                        Label("Cannot read the databases", systemImage: "cylinder.split.1x2")
                    } description: {
                        Text(err)
                    } actions: {
                        Button("Retry") { Task { await store.refresh() } }
                    }
                } else {
                    ProgressView("Reading databases…").frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else if store.databases.isEmpty {
                ContentUnavailableView {
                    Label("No database yet", systemImage: "cylinder.split.1x2")
                } description: {
                    Text("Create one here, or ask any Claude agent: “create an Odoo 19 database with sale”.")
                } actions: {
                    Button("New Database") { showNew = true }.buttonStyle(.borderedProminent)
                }
            } else {
                grid
            }
        }
        .navigationTitle("Databases")
        .navigationSubtitle(subtitle)
        .searchable(text: $search, placement: .toolbar, prompt: "Search databases, addons, versions")
        .toolbar { toolbar }
        .sheet(isPresented: $showNew) { NewDatabaseSheet(dump: droppedDump) { name in path.append(name) } }
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            guard let p = providers.first else { return false }
            _ = p.loadObject(ofClass: URL.self) { url, _ in
                guard let url, ["sql", "dump", "zip", "gz"].contains(url.pathExtension.lowercased()) else { return }
                Task { @MainActor in
                    droppedDump = url
                    showNew = true
                }
            }
            return true
        }
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
                    .background(Color.accentColor.opacity(0.06), in: RoundedRectangle(cornerRadius: 16))
                    .overlay(Label("Drop a backup to restore it", systemImage: "arrow.down.doc").font(.title3.weight(.medium)))
                    .padding(12)
                    .allowsHitTesting(false)
            }
        }
    }

    private var subtitle: String {
        guard let list = store.list else { return "" }
        let n = store.running.filter { $0.kind == "server" }.count
        return "\(list.databases.count) databases · \(n) running · repos on \(list.reposBranch ?? list.codeSeries)"
    }

    private var grid: some View {
        ScrollView {
            DatabaseGrid(running: filter != .running && search.isEmpty ? runningDBs : [],
                         items: filtered,
                         title: filter == .all && search.isEmpty ? "All databases" : "\(filtered.count) result\(filtered.count == 1 ? "" : "s")") { name in
                path.append(name)
            }
            .animation(.smooth, value: filtered.map(\.name))
            .animation(.smooth, value: runningDBs.map(\.name))
        }
        .refreshable { await store.refresh() }
    }

    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        ToolbarItemGroup(placement: .primaryAction) {
            Picker("Filter", selection: $filter) {
                ForEach(DBFilter.allCases) { f in Text(f.rawValue).tag(f) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            Menu {
                Picker("Version", selection: $versionFilter) {
                    Text("All versions").tag("")
                    ForEach(versions, id: \.self) { v in Text(Theme.label(forSeries: v)).tag(v) }
                }
                Picker("Sort by", selection: $sort) {
                    ForEach(DBSort.allCases) { s in Text(s.rawValue).tag(s) }
                }
            } label: {
                Label("View options", systemImage: versionFilter.isEmpty ? "line.3.horizontal.decrease.circle" : "line.3.horizontal.decrease.circle.fill")
            }

            JobsButton()

            Button {
                Task { await store.refresh() }
            } label: {
                Label("Refresh", systemImage: "arrow.clockwise")
            }
            .keyboardShortcut("r")
            .disabled(store.loading)

            Button {
                droppedDump = nil
                showNew = true
            } label: {
                Label("New Database", systemImage: "plus")
            }
            .keyboardShortcut("n")
        }
    }
}

struct DatabaseGrid: View {
    var running: [DBSummary]
    var items: [DBSummary]
    var title: String
    var open: (String) -> Void

    private let columns = [GridItem(.adaptive(minimum: 250, maximum: 360), spacing: 14)]

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            if !running.isEmpty {
                section("Running") {
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(running) { db in cardLink(db) }
                    }
                }
            }
            section(title) {
                if items.isEmpty {
                    Text("Nothing matches.").foregroundStyle(.secondary).frame(maxWidth: .infinity).padding(40)
                } else {
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(items.filter { db in !running.contains { $0.name == db.name } }) { db in cardLink(db) }
                    }
                }
            }
        }
        .padding(20)
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.leading, 2)
            content()
        }
    }

    private func cardLink(_ db: DBSummary) -> some View {
        Button {
            open(db.name)
        } label: {
            DatabaseCard(db: db)
        }
        .buttonStyle(CardButtonStyle())
        .contextMenu { DatabaseMenu(name: db.name, series: db.series, onDeleted: {}) }
    }
}

struct CardButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(.spring(duration: 0.2), value: configuration.isPressed)
    }
}

struct DatabaseCard: View {
    @Environment(Store.self) private var store
    var db: DBSummary
    @State private var hovering = false

    var body: some View {
        let proc = store.process(for: db.name)
        let busy = store.busy[db.name]
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                StatusDot(running: proc != nil, busy: busy != nil)
                Text(db.name)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                if db.matchesCode == false {
                    Image(systemName: "arrow.triangle.branch")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .help("This database is \(db.series ?? "?"), the repos are on another branch")
                }
                if db.isOdoo {
                    VersionBadge(series: db.series)
                } else {
                    Chip(text: "not Odoo", tint: .orange)
                }
            }

            Group {
                if let busy {
                    Text(busy).foregroundStyle(Color.accentColor)
                } else if let proc {
                    Text(verbatim: "localhost:\(proc.port) · up \(Fmt.duration(proc.uptime)) · \(Fmt.bytes(proc.rss))")
                        .foregroundStyle(Theme.running)
                } else if !db.customAddons.isEmpty {
                    Text(db.customAddons.joined(separator: " · ")).foregroundStyle(.secondary)
                } else {
                    Text(db.folder.map { "Scripts / \($0)" } ?? (db.script == nil ? "No launch script" : "Standard addons"))
                        .foregroundStyle(.tertiary)
                }
            }
            .font(.callout)
            .lineLimit(1)
            .truncationMode(.tail)

            Spacer(minLength: 0)

            HStack(spacing: 10) {
                if let n = db.modulesInstalled, db.isOdoo {
                    Label("\(n)", systemImage: "shippingbox").help("\(n) installed modules")
                }
                Label(Fmt.bytes(db.size), systemImage: "internaldrive")
                if (db.modulesLocked ?? 0) > 0 {
                    Label("locked", systemImage: "lock.fill").foregroundStyle(.orange)
                }
                Spacer()
                if hovering && db.isOdoo {
                    quickAction(proc != nil)
                        .transition(.opacity.combined(with: .scale(scale: 0.8)))
                } else {
                    Text(Fmt.ago(db.lastActivity))
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .labelStyle(CompactLabelStyle())
            .frame(height: 22)
        }
        .padding(14)
        .frame(height: 118)
        .contentShape(RoundedRectangle(cornerRadius: Theme.cardRadius))
        .card(hovering: hovering, highlighted: proc != nil)
        .offset(y: hovering ? -2 : 0)
        .onHover { h in withAnimation(.spring(duration: 0.25)) { hovering = h } }
    }

    @ViewBuilder
    private func quickAction(_ running: Bool) -> some View {
        HStack(spacing: 6) {
            if running {
                CircleButton(symbol: "safari", help: "Open in browser") { store.open(db.name, series: db.series) }
                CircleButton(symbol: "stop.fill", help: "Stop") { store.stop(db.name) }
            } else {
                CircleButton(symbol: "play.fill", help: "Start", prominent: true) { store.start(db.name) }
            }
        }
    }
}

struct CircleButton: View {
    var symbol: String
    var help: String
    var prominent = false
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 10, weight: .bold))
                .frame(width: 22, height: 22)
                .foregroundStyle(prominent ? .white : .primary)
                .background(prominent ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(.quaternary), in: Circle())
                .scaleEffect(hover ? 1.1 : 1)
        }
        .buttonStyle(.plain)
        .help(help)
        .onHover { h in withAnimation(.spring(duration: 0.2)) { hover = h } }
    }
}

struct CompactLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) {
            configuration.icon.imageScale(.small)
            configuration.title
        }
    }
}

/// Actions shared by the card context menu, the detail "…" menu and the menu bar.
struct DatabaseMenu: View {
    @Environment(Store.self) private var store
    var name: String
    var series: String?
    var onDeleted: () -> Void

    var body: some View {
        let running = store.isRunning(name)
        if running {
            Button("Open in Browser") { store.open(name, series: series) }
            Button("Restart") { store.restart(name) }
            Button("Stop") { store.stop(name) }
        } else {
            Button("Start") { store.start(name) }
            Button("Start in Terminal") { store.startInTerminal(name) }
        }
        Divider()
        Button("Update All Modules") { store.update(name, modules: []) }
        Button("Repair") { store.repair(name) }
        Button("Neutralize") { store.neutralize(name) }
        Divider()
        Button("Duplicate…") { NamePrompt.ask(title: "Duplicate \(name)", placeholder: "\(name)-copy", action: "Duplicate") { store.duplicate(name, as: $0) } }
        Button("Rename…") { NamePrompt.ask(title: "Rename \(name)", placeholder: name, action: "Rename") { new in Task { _ = await store.rename(name, to: new) } } }
        Button("Copy Name") { copyToPasteboard(name) }
        Divider()
        Button("Delete…", role: .destructive) { store.confirmDelete(name, then: onDeleted) }
    }
}

/// Small native text prompt (works from menus, where sheets cannot be attached).
enum NamePrompt {
    @MainActor
    static func ask(title: String, placeholder: String, action: String, completion: @escaping (String) -> Void) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = "Letters, digits, '-', '_' and '.'."
        alert.addButton(withTitle: action)
        alert.addButton(withTitle: "Cancel")
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 260, height: 24))
        field.stringValue = placeholder
        alert.accessoryView = field
        alert.window.initialFirstResponder = field
        if alert.runModal() == .alertFirstButtonReturn {
            let v = field.stringValue.trimmingCharacters(in: .whitespaces)
            if !v.isEmpty { completion(v) }
        }
    }
}
