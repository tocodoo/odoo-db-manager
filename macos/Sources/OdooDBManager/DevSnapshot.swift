import AppKit
import SwiftUI

/// Development helper: with DBM_SNAPSHOT_DIR set, renders the home grid and the detail of
/// DBM_OPEN (or the first running database) to PNG files with ImageRenderer, in light and dark.
enum DevSnapshot {
    static let dir = ProcessInfo.processInfo.environment["DBM_SNAPSHOT_DIR"]
    static let openDB = ProcessInfo.processInfo.environment["DBM_OPEN"]
    static let tab = ProcessInfo.processInfo.environment["DBM_TAB"].flatMap(DetailTab.init(rawValue:))
    /// Opens a built-in terminal of this kind on DBM_OPEN at launch (server, shell, zsh).
    static let terminal = ProcessInfo.processInfo.environment["DBM_TERMINAL"]
    /// Opens the launch script editor of DBM_OPEN at launch.
    static let scriptEditor = ProcessInfo.processInfo.environment["DBM_SCRIPT_EDITOR"] != nil

    @MainActor
    static func run(store: Store) async {
        guard let dir else { return }
        if scriptEditor {
            // Forms do not render in ImageRenderer: capture the windows themselves (the editor is a sheet).
            for scheme in [NSAppearance.Name.aqua, .darkAqua] {
                NSApp.appearance = NSAppearance(named: scheme)
                try? await Task.sleep(for: .seconds(3))
                for (i, w) in NSApp.windows.enumerated() where w.isVisible {
                    guard let v = w.contentView?.superview ?? w.contentView,
                          let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) else { continue }
                    v.cacheDisplay(in: v.bounds, to: rep)
                    try? rep.representation(using: .png, properties: [:])?
                        .write(to: URL(fileURLWithPath: "\(dir)/window-\(i)-\(scheme.rawValue).png"))
                }
            }
            NSApp.appearance = nil
            return
        }
        try? await Task.sleep(for: .seconds(1))
        let running = store.running.filter { $0.kind == "server" }.compactMap { p in store.databases.first { $0.name == p.db } }
        let items = Array(store.databases.sorted { ($0.lastActivity ?? .distantPast) > ($1.lastActivity ?? .distantPast) }.prefix(12))
        for scheme in [ColorScheme.light, .dark] {
            let grid = DatabaseGrid(running: running, items: items, title: "All databases") { _ in }
                .environment(store)
                .frame(width: 1180)
                .background(Color(nsColor: .windowBackgroundColor))
                .environment(\.colorScheme, scheme)
            save(grid, "\(dir)/home-\(scheme).png")
        }
        await store.loadSetup()
        for scheme in [ColorScheme.light, .dark] {
            // Form does not render in ImageRenderer: the rows alone.
            let setup = VStack(alignment: .leading, spacing: 10) {
                ForEach(store.setup?.items ?? []) { SetupRow(item: $0, job: nil) {} }
            }
                .padding(20)
                .environment(store)
                .frame(width: 640, height: 900)
                .background(Color(nsColor: .windowBackgroundColor))
                .environment(\.colorScheme, scheme)
            save(setup, "\(dir)/setup-\(scheme).png")
        }
        let name = openDB ?? running.first?.name ?? store.databases.first?.name ?? ""
        guard let info = try? await CLI.json(DBInfo.self, ["info", name, "--modules"]) else { return }
        for scheme in [ColorScheme.light, .dark] {
            let detail = VStack(spacing: 0) {
                DetailHeader(info: info).padding(.horizontal, 24).padding(.vertical, 18)
                Divider()
                OverviewTab(info: info, scrolls: false)
            }
            .environment(store)
            .frame(width: 1180)
            .background(Color(nsColor: .windowBackgroundColor))
            .environment(\.colorScheme, scheme)
            save(detail, "\(dir)/detail-\(scheme).png")
        }
    }

    @MainActor
    private static func save<V: View>(_ view: V, _ path: String) {
        let r = ImageRenderer(content: view)
        r.scale = 2
        guard let img = r.cgImage else { return }
        let rep = NSBitmapImageRep(cgImage: img)
        try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
    }
}
