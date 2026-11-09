import SwiftUI

struct OverviewTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    var scrolls = true

    var body: some View {
        if scrolls { ScrollView { content } } else { content }
    }

    var content: some View {
        VStack(alignment: .leading, spacing: 22) {
            if !info.issues.isEmpty { issues }
            stats
            repos
            if !info.customModules.isEmpty { customModules }
            launch
        }
        .padding(24)
        .frame(maxWidth: 1100)
        .frame(maxWidth: .infinity)
    }

    // MARK: Health

    private var issues: some View {
        VStack(spacing: 8) {
            ForEach(info.issues) { issue in
                let color: Color = issue.severity == "error" ? .red : .orange
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: issue.severity == "error" ? "exclamationmark.octagon.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(color)
                        .font(.title3)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(issue.title).font(.callout.weight(.semibold))
                        if !issue.detail.isEmpty {
                            Text(issue.detail).font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                        }
                    }
                    Spacer()
                    if let fix = issue.fix {
                        Button(issue.fixLabel.isEmpty ? "Fix" : issue.fixLabel) { store.repair(info.name, fix: fix) }
                            .disabled(store.busy[info.name] != nil)
                    }
                }
                .padding(12)
                .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(color.opacity(0.2), lineWidth: 0.5))
            }
        }
    }

    // MARK: Stats

    private var stats: some View {
        let proc = store.process(for: info.name)
        let cols = [GridItem(.adaptive(minimum: 170), spacing: 12)]
        return LazyVGrid(columns: cols, spacing: 12) {
            StatTile(title: "Version", value: Theme.label(forSeries: info.series), symbol: "tag",
                     detail: info.baseVersion.map { "base \($0) · code \(info.codeSeries)" },
                     tint: Theme.color(forSeries: info.series))
            StatTile(title: "Modules", value: "\(info.moduleCounts.installed)", symbol: "shippingbox",
                     detail: "\(info.moduleCounts.custom) custom · \(info.moduleCounts.enterprise) enterprise")
            StatTile(title: "Database", value: Fmt.bytes(info.size), symbol: "cylinder",
                     detail: info.filestore.exists ? "+ \(Fmt.bytes(info.filestore.size)) filestore" : "no filestore")
            if let proc {
                StatTile(title: "Server", value: Fmt.bytes(proc.rss), symbol: "memorychip",
                         detail: "up \(Fmt.duration(proc.uptime)) · pid \(proc.pid)", tint: Theme.running)
            } else {
                StatTile(title: "Port", value: String(info.port), symbol: "network", detail: info.url.replacingOccurrences(of: "http://", with: ""))
            }
            StatTile(title: "Company", value: info.company ?? "—", symbol: "building.2",
                     detail: "\(info.users ?? 0) user\(info.users == 1 ? "" : "s") · admin: \(info.adminLogin ?? "?")")
            StatTile(title: "Last login", value: Fmt.ago(ISO.parse(info.lastLogin)), symbol: "clock",
                     detail: ISO.parse(info.created).map { "created \($0.formatted(.dateTime.day().month(.abbreviated).year().locale(Locale(identifier: "en_US"))))" })
            StatTile(title: "Languages", value: (info.languages ?? []).map(\.code).joined(separator: ", ").ifEmpty("—"),
                     symbol: "globe", detail: "\(info.languages?.count ?? 0) active")
            StatTile(title: "Python", value: info.pyenv ?? "—", symbol: "terminal", detail: "pyenv environment")
        }
    }

    // MARK: Repos

    private var repos: some View {
        SectionBox(title: "Repositories", subtitle: "Addons path of the launch script") {
            VStack(spacing: 0) {
                ForEach(Array(info.repos.enumerated()), id: \.element.id) { idx, repo in
                    if idx > 0 { Divider().padding(.leading, 44) }
                    RepoRow(repo: repo)
                }
            }
        }
    }

    private var customModules: some View {
        SectionBox(title: "Custom modules", subtitle: "Installed from your own addons") {
            FlowLayout(spacing: 6) {
                ForEach(info.customModules, id: \.self) { m in
                    Chip(text: m, symbol: "puzzlepiece.extension", tint: .accentColor)
                }
            }
            .padding(12)
        }
    }

    private var launch: some View {
        SectionBox(title: "Launch script", subtitle: info.script?.path ?? "None — created automatically on first start") {
            if let s = info.script {
                VStack(alignment: .leading, spacing: 8) {
                    KeyValue("Branch", s.branch)
                    KeyValue("Install", s.installModules.joined(separator: ", ").ifEmpty("—"))
                    KeyValue("At each start", s.updateModules.isEmpty ? "nothing" : "\(s.updateMode == "update" ? "-u" : "--reinit") \(s.updateModules.joined(separator: ", "))")
                    KeyValue("Port", s.port.map(String.init) ?? "—")
                    HStack {
                        Button("Reveal in Finder") { reveal(s.path) }
                        Button("Edit") { NSWorkspace.shared.open(URL(fileURLWithPath: s.path)) }
                        Button("Change Port…") {
                            NamePrompt.ask(title: "HTTP port of \(info.name)", placeholder: String(info.port), action: "Apply") { v in
                                if let p = Int(v) { store.setPort(info.name, p) }
                            }
                        }
                        Spacer()
                    }
                    .controlSize(.small)
                    .padding(.top, 4)
                }
                .padding(12)
            } else {
                HStack {
                    Text("Starting the database creates it with the right branch and addons.").foregroundStyle(.secondary)
                    Spacer()
                    Button("Create Now") { store.repair(info.name, fix: "script") }
                }
                .padding(12)
            }
        }
    }
}

struct RepoRow: View {
    var repo: Repo
    @State private var hover = false

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(tint)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(repo.name).font(.callout.weight(.semibold))
                    if let b = repo.branch { Chip(text: b, symbol: "arrow.triangle.branch", tint: tint) }
                    if let c = repo.changes, c > 0 { Chip(text: "\(c) changed", tint: .orange) }
                    if let b = repo.behind, b > 0 { Chip(text: "\(b) behind", symbol: "arrow.down", tint: .secondary) }
                    if let a = repo.ahead, a > 0 { Chip(text: "\(a) ahead", symbol: "arrow.up", tint: .blue) }
                }
                if !repo.exists {
                    Text("Folder not found: \(repo.path)").font(.caption).foregroundStyle(.red)
                } else if let subject = repo.subject {
                    Text("\(repo.commit ?? "") · \(subject)")
                        .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
                } else {
                    Text("Not a git repository").font(.caption).foregroundStyle(.tertiary)
                }
                if let mods = repo.modules, !mods.isEmpty {
                    Text(mods.joined(separator: " · ")).font(.caption).foregroundStyle(Color.accentColor).lineLimit(1)
                }
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                if let n = repo.installedModules, n > 0 {
                    Text("\(n) installed").font(.caption.weight(.medium))
                }
                if let d = ISO.parse(repo.date) {
                    Text(Fmt.ago(d)).font(.caption).foregroundStyle(.tertiary)
                }
            }
            if hover && repo.exists {
                HStack(spacing: 4) {
                    CircleButton(symbol: "folder", help: "Reveal in Finder") { reveal(repo.root ?? repo.path) }
                    CircleButton(symbol: "doc.on.doc", help: "Copy path") { copyToPasteboard(repo.root ?? repo.path) }
                }
                .transition(.opacity)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .background(hover ? Color.primary.opacity(0.03) : .clear)
        .onHover { h in withAnimation(.easeOut(duration: 0.15)) { hover = h } }
        .contextMenu {
            Button("Reveal in Finder") { reveal(repo.root ?? repo.path) }
            Button("Copy Path") { copyToPasteboard(repo.root ?? repo.path) }
            if let r = repo.remote { Button("Copy Remote URL") { copyToPasteboard(r) } }
        }
    }

    private var symbol: String {
        switch repo.kind {
        case "community": "building.columns"
        case "enterprise": "briefcase"
        default: "puzzlepiece.extension"
        }
    }

    private var tint: Color {
        switch repo.kind {
        case "community": .secondary
        case "enterprise": Theme.odoo
        default: .accentColor
        }
    }
}

struct StatTile: View {
    var title: String
    var value: String
    var symbol: String
    var detail: String?
    var tint: Color = .secondary

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: symbol)
                .font(.caption.weight(.medium))
                .foregroundStyle(tint == .secondary ? .secondary : tint)
                .labelStyle(CompactLabelStyle())
            Text(value)
                .font(.title3.weight(.semibold))
                .lineLimit(1)
                .truncationMode(.tail)
                .textSelection(.enabled)
            if let detail {
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .card()
    }
}

struct SectionBox<Content: View>: View {
    var title: String
    var subtitle: String?
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.headline)
                if let subtitle {
                    Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
            }
            content
                .frame(maxWidth: .infinity, alignment: .leading)
                .card()
        }
    }
}

struct KeyValue: View {
    var key: String
    var value: String
    init(_ key: String, _ value: String) { self.key = key; self.value = value }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(key).foregroundStyle(.secondary).frame(width: 110, alignment: .leading)
            Text(value).textSelection(.enabled)
            Spacer()
        }
        .font(.callout)
    }
}

/// Wrapping horizontal layout for chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineH: CGFloat = 0, maxX: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > width, x > 0 { x = 0; y += lineH + spacing; lineH = 0 }
            x += s.width + spacing
            lineH = max(lineH, s.height)
            maxX = max(maxX, x)
        }
        return CGSize(width: min(maxX, width), height: y + lineH)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineH: CGFloat = 0
        for v in subviews {
            let s = v.sizeThatFits(.unspecified)
            if x + s.width > bounds.maxX, x > bounds.minX { x = bounds.minX; y += lineH + spacing; lineH = 0 }
            v.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            lineH = max(lineH, s.height)
        }
    }
}

extension String {
    func ifEmpty(_ fallback: String) -> String { isEmpty ? fallback : self }
}
