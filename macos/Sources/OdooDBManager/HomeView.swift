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
    @State private var selecting = false
    @State private var selection: Set<String> = []
    @State private var anchor: String?

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

    /// Running databases shown in their own section above the others.
    private var shownRunning: [DBSummary] { filter != .running && search.isEmpty ? runningDBs : [] }

    /// What the grid shows, computed once per render (filtering and sorting are not free).
    private struct Listing {
        var running: [DBSummary]
        var items: [DBSummary]
        /// Card names in display order (running section first).
        var order: [String]
    }

    private var listing: Listing {
        let running = shownRunning
        let items = filtered
        let names = running.map(\.name)
        let shown = Set(names)
        return Listing(running: running, items: items, order: names + items.map(\.name).filter { !shown.contains($0) })
    }

    /// Plain click toggles in selection mode; ⌘-click toggles and ⇧-click extends from the last one.
    private func select(_ name: String, _ mods: NSEvent.ModifierFlags) {
        withAnimation(.snappy(duration: 0.2)) {
            selecting = true
            let order = listing.order
            if mods.contains(.shift), let anchor, let a = order.firstIndex(of: anchor), let b = order.firstIndex(of: name) {
                selection.formUnion(order[min(a, b)...max(a, b)])
            } else if selection.contains(name) {
                selection.remove(name)
            } else {
                selection.insert(name)
            }
            anchor = name
        }
    }

    private func endSelection() {
        withAnimation(.snappy(duration: 0.2)) {
            selecting = false
            selection = []
            anchor = nil
        }
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
                        HStack {
                            Button("Retry") { Task { await store.refresh() } }
                            Button("Set Up…") { store.setupShown = true }.buttonStyle(.borderedProminent)
                        }
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
                grid(listing)
            }
        }
        .navigationTitle("Databases")
        .navigationSubtitle(subtitle)
        .searchable(text: $search, placement: .toolbar, prompt: "Search databases, addons, versions")
        .toolbar { toolbar }
        .sheet(isPresented: $showNew) { NewDatabaseSheet(dump: droppedDump) { name in path.append(name) } }
        .sheet(isPresented: Binding(get: { store.setupShown }, set: { store.setupShown = $0 })) {
            SetupSheet().environment(store)
        }
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

    private func grid(_ listing: Listing) -> some View {
        let count = listing.items.count
        return ScrollView {
            DatabaseGrid(running: listing.running,
                         items: listing.items,
                         title: filter == .all && search.isEmpty ? "All databases" : "\(count) result\(count == 1 ? "" : "s")",
                         selection: selecting ? selection : nil,
                         open: { name in path.append(name) },
                         select: select)
            .animation(.smooth, value: listing.order)
        }
        .refreshable { await store.refresh() }
        .safeAreaInset(edge: .bottom) {
            if selecting {
                SelectionBar(names: listing.order.filter(selection.contains), total: listing.order.count,
                             selectAll: { withAnimation(.snappy(duration: 0.2)) { selection = Set(listing.order) } },
                             deselectAll: { withAnimation(.snappy(duration: 0.2)) { selection = [] } },
                             done: endSelection)
                    .padding(.bottom, 16)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        // Deleted or filtered-out databases leave the selection: actions only touch what is visible.
        .onChange(of: listing.order) { _, names in selection.formIntersection(names) }
        .onChange(of: selecting) { _, now in withAnimation(.spring(duration: 0.35)) { store.toastInset = now ? 64 : 0 } }
        .onDisappear { store.toastInset = 0 }
        .onAppear { if selecting { store.toastInset = 64 } }
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
                store.setupShown = true
            } label: {
                Label("Setup", systemImage: "wrench.and.screwdriver")
            }
            .overlay(alignment: .topTrailing) {
                if (store.setup?.problems ?? 0) > 0 {
                    Circle().fill(.red).frame(width: 7, height: 7).offset(x: 1, y: -1).allowsHitTesting(false)
                }
            }
            .help((store.setup?.problems ?? 0) > 0 ? "Setup: \(store.setup?.problems ?? 0) thing(s) to fix" : "Setup")

            Button {
                if selecting { endSelection() } else { withAnimation(.snappy(duration: 0.2)) { selecting = true } }
            } label: {
                Label(selecting ? "Done Selecting" : "Select", systemImage: selecting ? "checkmark.circle.fill" : "checkmark.circle")
            }
            .help(selecting ? "Leave selection mode" : "Select several databases (or ⌘-click a card)")
            .disabled(store.databases.isEmpty)

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
    /// Selected names while in selection mode, nil otherwise.
    var selection: Set<String>? = nil
    var open: (String) -> Void
    var select: (String, NSEvent.ModifierFlags) -> Void = { _, _ in }

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
                    let shown = Set(running.map(\.name))
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(items.filter { !shown.contains($0.name) }) { db in cardLink(db) }
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
            let mods = NSEvent.modifierFlags.intersection([.command, .shift])
            if selection != nil || !mods.isEmpty { select(db.name, mods) } else { open(db.name) }
        } label: {
            DatabaseCard(db: db, selected: selection.map { $0.contains(db.name) })
        }
        .buttonStyle(CardButtonStyle())
        .contextMenu {
            if let selection, selection.contains(db.name), selection.count > 1 {
                let order = running.map(\.name) + items.map(\.name).filter { n in !running.contains { $0.name == n } }
                BulkDatabaseMenu(names: order.filter(selection.contains))
            } else {
                DatabaseMenu(name: db.name, series: db.series, onDeleted: {})
            }
        }
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
    /// nil outside selection mode.
    var selected: Bool? = nil
    @State private var hovering = false

    var body: some View {
        let proc = store.process(for: db.name)
        let busy = store.busy[db.name]
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 8) {
                if let selected {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .font(.system(size: 15))
                        .foregroundStyle(selected ? Color.accentColor : Color.secondary.opacity(0.5))
                        .contentTransition(.symbolEffect(.replace))
                        .transition(.scale.combined(with: .opacity))
                }
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
                if hovering && db.isOdoo && selected == nil {
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
        .card(hovering: hovering, highlighted: proc != nil, selected: selected == true)
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
            Button("Start in External Terminal") { store.startInExternalTerminal(name) }
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
        Button("Reset…", role: .destructive) { store.confirmReset(name) }
        Button("Delete…", role: .destructive) { store.confirmDelete(name, then: onDeleted) }
    }
}

/// Context menu of a card that belongs to a multiple selection.
struct BulkDatabaseMenu: View {
    @Environment(Store.self) private var store
    var names: [String]

    var body: some View {
        let running = names.filter { store.isRunning($0) }
        Text("\(names.count) databases")
        Button("Start") { store.bulkStart(names) }.disabled(running.count == names.count)
        Button("Restart") { store.bulkRestart(names) }.disabled(running.isEmpty)
        Button("Stop") { store.bulkStop(names) }.disabled(running.isEmpty)
        Divider()
        Button("Update All Modules") { store.bulkUpdate(names) }
        Button("Neutralize") { store.bulkNeutralize(names) }
        Divider()
        Button("Copy Names") { copyToPasteboard(names.joined(separator: "\n")) }
        Divider()
        Button("Delete \(names.count) Databases…", role: .destructive) { store.confirmBulkDelete(names) }
    }
}

/// Floating bar shown at the bottom of the grid in selection mode.
struct SelectionBar: View {
    @Environment(Store.self) private var store
    var names: [String]
    var total: Int
    var selectAll: () -> Void
    var deselectAll: () -> Void
    var done: () -> Void

    var body: some View {
        let running = names.filter { store.isRunning($0) }
        let none = names.isEmpty
        HStack(spacing: 10) {
            Text(none ? "Select databases" : "\(names.count) selected")
                .font(.callout.weight(.semibold))
                .monospacedDigit()
                .contentTransition(.numericText())
                .frame(minWidth: 96, alignment: .leading)
            Button(total > 0 && names.count == total ? "Deselect All" : "Select All") {
                names.count == total ? deselectAll() : selectAll()
            }
            .buttonStyle(.link)
            .disabled(total == 0)

            Divider().frame(height: 18)

            ViewThatFits(in: .horizontal) {
                actions(running: running, none: none).labelStyle(.titleAndIcon)
                actions(running: running, none: none).labelStyle(.iconOnly)
            }

            Divider().frame(height: 18)

            Button("Done", action: done)
                .keyboardShortcut(.cancelAction)
        }
        .controlSize(.regular)
        .padding(.leading, 18)
        .padding(.trailing, 10)
        .padding(.vertical, 8)
        .background(.regularMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(.quaternary, lineWidth: 0.5))
        .shadow(color: .black.opacity(0.15), radius: 16, y: 6)
        .padding(.horizontal, 20)
    }

    private func actions(running: [String], none: Bool) -> some View {
        HStack(spacing: 2) {
            bar("Start", "play.fill", disabled: none || running.count == names.count) { store.bulkStart(names) }
            bar("Restart", "arrow.clockwise", disabled: running.isEmpty) { store.bulkRestart(names) }
            bar("Stop", "stop.fill", disabled: running.isEmpty) { store.bulkStop(names) }
            bar("Update", "arrow.triangle.2.circlepath", help: "Update all modules", disabled: none) { store.bulkUpdate(names) }
            bar("Neutralize", "shield.lefthalf.filled", disabled: none) { store.bulkNeutralize(names) }
            bar("Delete…", "trash", disabled: none, destructive: true) { store.confirmBulkDelete(names) }
        }
        .fixedSize()
    }

    private func bar(_ title: String, _ symbol: String, help: String? = nil, disabled: Bool,
                     destructive: Bool = false, action: @escaping () -> Void) -> some View {
        Button(role: destructive ? .destructive : nil, action: action) {
            Label(title, systemImage: symbol)
        }
        .buttonStyle(.borderless)
        .foregroundStyle(destructive && !disabled ? AnyShapeStyle(.red) : AnyShapeStyle(.primary))
        .padding(.horizontal, 6)
        .disabled(disabled)
        .help(help ?? title)
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
