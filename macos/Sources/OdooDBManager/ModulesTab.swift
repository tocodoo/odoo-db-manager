import SwiftUI

enum ModuleScope: String, CaseIterable, Identifiable {
    case all = "All", custom = "Custom", enterprise = "Enterprise", community = "Community", problems = "Problems"
    var id: String { rawValue }
}

struct ModulesTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    @State private var search = ""
    @State private var scope: ModuleScope = .all
    @State private var selection = Set<Module.ID>()
    @State private var sortOrder = [KeyPathComparator(\Module.name)]

    private var modules: [Module] {
        var mods = info.modules ?? []
        switch scope {
        case .all: break
        case .custom: mods = mods.filter { $0.source == "custom" }
        case .enterprise: mods = mods.filter { $0.source == "enterprise" }
        case .community: mods = mods.filter { $0.source == "community" }
        case .problems: mods = mods.filter { $0.source == "missing" || $0.state != "installed" }
        }
        let q = search.lowercased()
        if !q.isEmpty {
            mods = mods.filter { $0.name.lowercased().contains(q) || $0.title.lowercased().contains(q) }
        }
        return mods.sorted(using: sortOrder)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Picker("Scope", selection: $scope) {
                    ForEach(ModuleScope.allCases) { s in Text(label(s)).tag(s) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Spacer()
                TextField("Filter modules", text: $search)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 220)
                Button("Update Selected") {
                    store.update(info.name, modules: Array(selection))
                }
                .disabled(selection.isEmpty || store.busy[info.name] != nil)
                .help("Stops the server, runs -u on the selection, restarts it")
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)

            Table(modules, selection: $selection, sortOrder: $sortOrder) {
                TableColumn("Module", value: \.name) { m in
                    HStack(spacing: 6) {
                        if m.application { Image(systemName: "app.fill").foregroundStyle(Theme.odoo).imageScale(.small) }
                        Text(m.name).fontWeight(m.source == "custom" ? .semibold : .regular)
                    }
                }
                .width(min: 160, ideal: 220)
                TableColumn("Name", value: \.title) { m in Text(m.title).foregroundStyle(.secondary) }
                TableColumn("Version", value: \.version) { m in Text(m.version).monospacedDigit().foregroundStyle(.secondary) }
                    .width(min: 80, ideal: 110)
                TableColumn("Source", value: \.source) { m in sourceChip(m) }
                    .width(min: 90, ideal: 110)
                TableColumn("State", value: \.state) { m in
                    Text(m.state).foregroundStyle(m.state == "installed" ? Color.secondary : Color.orange)
                }
                .width(min: 80, ideal: 100)
            }
            .contextMenu(forSelectionType: Module.ID.self) { ids in
                Button("Update \(ids.count == 1 ? ids.first! : "\(ids.count) modules")") {
                    store.update(info.name, modules: Array(ids))
                }
                if ids.count == 1, let m = info.modules?.first(where: { $0.name == ids.first }), let loc = m.location {
                    Button("Reveal in Finder") { reveal(loc + "/" + m.name) }
                }
                Button("Copy Names") { copyToPasteboard(ids.sorted().joined(separator: ",")) }
            }
        }
    }

    private func label(_ s: ModuleScope) -> String {
        let mods = info.modules ?? []
        let n: Int = switch s {
        case .all: mods.count
        case .custom: mods.filter { $0.source == "custom" }.count
        case .enterprise: mods.filter { $0.source == "enterprise" }.count
        case .community: mods.filter { $0.source == "community" }.count
        case .problems: mods.filter { $0.source == "missing" || $0.state != "installed" }.count
        }
        return "\(s.rawValue) \(n)"
    }

    @ViewBuilder
    private func sourceChip(_ m: Module) -> some View {
        switch m.source {
        case "custom": Chip(text: "custom", tint: .accentColor)
        case "enterprise": Chip(text: "enterprise", tint: Theme.odoo)
        case "missing": Chip(text: "no code", symbol: "exclamationmark.triangle", tint: .orange)
        default: Text("community").font(.caption).foregroundStyle(.secondary)
        }
    }
}
