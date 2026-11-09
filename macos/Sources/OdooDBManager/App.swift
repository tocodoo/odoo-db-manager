import AppKit
import SwiftUI

@main
struct OdooDBManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var store = Store()

    var body: some Scene {
        Window("Odoo Databases", id: "main") {
            RootView()
                .environment(store)
                .frame(minWidth: 760, minHeight: 520)
        }
        .defaultSize(width: 1180, height: 780)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { Task { await Updater.check() } }
            }
        }

        Settings {
            SettingsView()
                .environment(store)
        }

        MenuBarExtra {
            MenuBarContent()
                .environment(store)
        } label: {
            let n = store.running.filter { $0.kind == "server" }.count
            Image(systemName: n > 0 ? "cylinder.split.1x2.fill" : "cylinder.split.1x2")
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

struct RootView: View {
    @Environment(Store.self) private var store
    @State private var path: [String] = []

    var body: some View {
        @Bindable var store = store
        NavigationStack(path: $path) {
            HomeView(path: $path)
                .navigationDestination(for: String.self) { name in
                    DetailView(name: name)
                }
        }
        .overlay(alignment: .bottom) { ToastView() }
        .task {
            if let db = DevSnapshot.openDB { path = [db] }
            await store.refresh()
            store.startPolling()
            await DevSnapshot.run(store: store)
        }
        .onReceive(NotificationCenter.default.publisher(for: .openDatabase)) { note in
            if let name = note.object as? String { path = [name] }
        }
        .alert(store.confirmation?.title ?? "", isPresented: Binding(
            get: { store.confirmation != nil },
            set: { if !$0 { store.confirmation = nil } }
        ), presenting: store.confirmation) { c in
            Button(c.actionLabel, role: c.destructive ? .destructive : nil) { c.action() }
            Button("Cancel", role: .cancel) {}
        } message: { c in
            Text(c.message)
        }
    }
}

extension Notification.Name {
    static let openDatabase = Notification.Name("openDatabase")
}

struct ToastView: View {
    @Environment(Store.self) private var store

    var body: some View {
        if let toast = store.toast {
            HStack(spacing: 10) {
                Image(systemName: toast.symbol).foregroundStyle(toast.isError ? .orange : .green)
                Text(toast.text).font(.callout).lineLimit(4).textSelection(.enabled)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 11)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(.quaternary, lineWidth: 0.5))
            .shadow(color: .black.opacity(0.15), radius: 16, y: 6)
            .frame(maxWidth: 560)
            .padding(.bottom, 22 + store.toastInset)
            .transition(.move(edge: .bottom).combined(with: .opacity))
            .id(toast.id)
            .onTapGesture { withAnimation { store.toast = nil } }
        }
    }
}

/// Toolbar button listing the recent jobs (create, start, repair, translate…).
struct JobsButton: View {
    @Environment(Store.self) private var store
    @State private var shown = false

    var body: some View {
        if !store.jobs.isEmpty {
            Button {
                shown.toggle()
            } label: {
                if store.activeJobs.isEmpty {
                    Label("Activity", systemImage: "list.bullet.rectangle")
                } else {
                    HStack(spacing: 4) {
                        ProgressView().controlSize(.small)
                        Text("\(store.activeJobs.count)").monospacedDigit()
                    }
                }
            }
            .help("Activity")
            .popover(isPresented: $shown, arrowEdge: .bottom) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(store.jobs) { job in JobRow(job: job) }
                    }
                    .padding(14)
                }
                .frame(width: 440)
                .frame(maxHeight: 520)
            }
        }
    }
}

struct JobRow: View {
    var job: Job
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                switch job.state {
                case .running: ProgressView().controlSize(.small)
                case .success: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                case .failure: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
                }
                VStack(alignment: .leading, spacing: 1) {
                    Text(job.title).font(.callout.weight(.medium))
                    Text(job.state == .running ? job.progress : job.message)
                        .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                Text(job.started, style: .time).font(.caption2).foregroundStyle(.tertiary)
                if !job.lines.isEmpty {
                    Button { withAnimation { expanded.toggle() } } label: {
                        Image(systemName: "chevron.right").rotationEffect(.degrees(expanded ? 90 : 0))
                    }
                    .buttonStyle(.plain)
                }
            }
            if expanded {
                ScrollView {
                    Text(job.lines.suffix(300).joined(separator: "\n"))
                        .font(.system(size: 10, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 160)
                .padding(6)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }
}

struct MenuBarContent: View {
    @Environment(Store.self) private var store
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        let servers = store.running.filter { $0.kind == "server" }
        VStack(alignment: .leading, spacing: 0) {
            Text(servers.isEmpty ? "No database running" : "Running")
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                .padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 6)
            ForEach(servers) { p in
                HStack(spacing: 10) {
                    StatusDot(running: true, busy: store.busy[p.db] != nil)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(p.db).font(.callout.weight(.medium)).lineLimit(1)
                        Text(verbatim: ":\(p.port) · \(Fmt.duration(p.uptime)) · \(Fmt.bytes(p.rss))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    CircleButton(symbol: "safari", help: "Open") {
                        store.open(p.db, series: store.databases.first { $0.name == p.db }?.series)
                    }
                    CircleButton(symbol: "arrow.clockwise", help: "Restart") { store.restart(p.db) }
                    CircleButton(symbol: "stop.fill", help: "Stop") { store.stop(p.db) }
                }
                .padding(.horizontal, 14).padding(.vertical, 6)
            }
            Divider().padding(.vertical, 6)
            MenuRow(title: "Open Odoo Databases", symbol: "macwindow") {
                openWindow(id: "main")
                NSApp.activate(ignoringOtherApps: true)
            }
            if !servers.isEmpty {
                MenuRow(title: "Stop All", symbol: "stop.circle") {
                    Task { await store.perform("Stop all", db: nil, args: ["stop", "--all"]) { _ in "All databases stopped" } }
                }
            }
            MenuRow(title: "Quit", symbol: "power") { NSApp.terminate(nil) }
                .padding(.bottom, 6)
        }
        .frame(width: 320)
        .task { if store.list == nil { await store.refresh(); store.startPolling() } }
    }
}

struct MenuRow: View {
    var title: String
    var symbol: String
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10).padding(.vertical, 5)
                .background(hover ? Color.accentColor.opacity(0.15) : .clear, in: RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 4)
        .onHover { hover = $0 }
    }
}

struct UpdateStatus: Decodable {
    var currentVersion: String?
    var updateAvailable: Bool?
    var behind: Int?
    var error: String?
}

enum Updater {
    @MainActor
    static func check() async {
        let alert = NSAlert()
        guard let s = try? await CLI.json(UpdateStatus.self, ["self-update", "--check"]) else {
            alert.messageText = "Cannot check for updates"
            alert.runModal()
            return
        }
        if let e = s.error {
            alert.messageText = "Cannot check for updates"
            alert.informativeText = e
            alert.runModal()
        } else if s.updateAvailable == true {
            alert.messageText = "Update available"
            alert.informativeText = "\(s.behind ?? 0) new change(s). The app rebuilds itself and restarts (about a minute)."
            alert.addButton(withTitle: "Update")
            alert.addButton(withTitle: "Later")
            if alert.runModal() == .alertFirstButtonReturn {
                _ = await CLI.run(["self-update"])
            }
        } else {
            alert.messageText = "You're up to date"
            alert.informativeText = s.currentVersion ?? ""
            alert.runModal()
        }
    }
}
