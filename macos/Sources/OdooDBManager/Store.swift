import AppKit
import Observation
import SwiftUI

@MainActor
@Observable
final class Job: Identifiable {
    enum State { case running, success, failure }

    let id = UUID()
    let title: String
    let db: String?
    var lines: [String] = []
    var state: State = .running
    var message: String = ""
    var resultPath: String?
    let started = Date()

    init(title: String, db: String?) {
        self.title = title
        self.db = db
    }

    /// Last meaningful progress line, shortened for compact display.
    var progress: String {
        guard let last = lines.last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) else { return "Starting…" }
        if let r = last.range(of: #"(INFO|WARNING|ERROR) \S+ [\w.]+: "#, options: .regularExpression) {
            return String(last[r.upperBound...])
        }
        return last
    }
}

struct Toast: Identifiable, Equatable {
    let id = UUID()
    var text: String
    var isError = false
    var symbol: String { isError ? "exclamationmark.triangle.fill" : "checkmark.circle.fill" }
}

struct Confirmation: Identifiable {
    let id = UUID()
    var title: String
    var message: String
    var actionLabel: String
    var destructive = false
    var action: () -> Void
}

@MainActor
@Observable
final class Store {
    var list: DBListResponse?
    var running: [RunningProcess] = []
    var loading = false
    var loadError: String?
    var jobs: [Job] = []
    var toast: Toast?
    var confirmation: Confirmation?
    /// db name -> what is happening ("Starting…")
    var busy: [String: String] = [:]
    /// db name -> bumped after every action on it, so its open detail view reloads.
    var revisions: [String: Int] = [:]
    /// Extra bottom space for toasts while a bottom bar (bulk selection) is shown.
    var toastInset: CGFloat = 0

    private var pollTask: Task<Void, Never>?
    private var toastTask: Task<Void, Never>?
    private var refreshing = false
    private var refreshAgain = false
    /// > 0 while a bulk action runs: it refreshes once at the end instead of after every database.
    private var bulkDepth = 0

    var databases: [DBSummary] { list?.databases ?? [] }
    var activeJobs: [Job] { jobs.filter { $0.state == .running } }

    func process(for db: String) -> RunningProcess? {
        running.first { $0.db == db && $0.kind == "server" }
    }

    func isRunning(_ db: String) -> Bool { process(for: db) != nil }

    // MARK: - Loading

    /// Reloads the list. Calls made while one is in flight are coalesced into a single extra run.
    func refresh() async {
        if refreshing {
            refreshAgain = true
            return
        }
        refreshing = true
        loading = true
        defer {
            refreshing = false
            loading = false
        }
        repeat {
            refreshAgain = false
            do {
                apply(try await CLI.json(DBListResponse.self, ["list"]))
            } catch {
                loadError = error.localizedDescription
            }
        } while refreshAgain
    }

    /// Only touches what changed: reassigning an identical list re-renders and re-animates the whole grid.
    private func apply(_ res: DBListResponse) {
        if let list, list.databases == res.databases, list.codeSeries == res.codeSeries, list.reposBranch == res.reposBranch {
            // same databases
        } else {
            withAnimation(.smooth) { list = res }
        }
        applyRunning(res.running)
        if loadError != nil { loadError = nil }
    }

    private func applyRunning(_ new: [RunningProcess]) {
        guard new != running else { return }
        let key: (RunningProcess) -> String = { "\($0.db):\($0.pid):\($0.port)" }
        if Set(new.map(key)) != Set(running.map(key)) {
            withAnimation(.smooth) { running = new }
        } else {
            running = new
        }
    }

    func startPolling() {
        guard pollTask == nil else { return }
        pollTask = Task { [weak self] in
            var tick = 0
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(3))
                guard let self else { return }
                tick += 1
                if tick % 40 == 0 {
                    await self.refresh()
                } else if let ps = try? await CLI.json(PSResponse.self, ["ps"]) {
                    self.applyRunning(ps.running)
                }
            }
        }
    }

    // MARK: - Feedback

    func show(_ text: String, error: Bool = false) {
        withAnimation(.spring(duration: 0.35)) { toast = Toast(text: text, isError: error) }
        toastTask?.cancel()
        toastTask = Task {
            try? await Task.sleep(for: .seconds(error ? 6 : 3.5))
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut) { self.toast = nil }
        }
    }

    // MARK: - Actions

    /// Runs an `odoodb` action as a tracked job, with recovery prompts for the known errors.
    @discardableResult
    func perform(_ title: String, db: String?, busyLabel: String? = nil, args: [String],
                 onJob: ((Job) -> Void)? = nil,
                 success: @escaping (ActionResult) -> String) async -> ActionResult? {
        let job = Job(title: title, db: db)
        jobs.insert(job, at: 0)
        onJob?(job)
        if jobs.count > 30 { jobs.removeLast(jobs.count - 30) }
        if let db { busy[db] = busyLabel ?? title }
        defer {
            if let db {
                busy[db] = nil
                revisions[db, default: 0] += 1
            }
        }
        do {
            let res = try await CLI.json(ActionResult.self, args) { line in
                Task { @MainActor in job.lines.append(line) }
            }
            job.state = .success
            job.message = success(res)
            job.resultPath = res.path
            show(job.message)
            if bulkDepth == 0 { Task { await refresh() } }
            return res
        } catch let e as CLIError {
            job.state = .failure
            job.message = e.error
            handle(e, title: title, db: db, args: args, success: success)
        } catch {
            job.state = .failure
            job.message = error.localizedDescription
            show(error.localizedDescription, error: true)
        }
        return nil
    }

    private func handle(_ e: CLIError, title: String, db: String?, args: [String], success: @escaping (ActionResult) -> String) {
        let retry: (String) -> Void = { flag in
            Task { await self.perform(title, db: db, args: args + [flag], success: success) }
        }
        switch e.code {
        case "branch_mismatch":
            confirmation = Confirmation(
                title: "Switch branch?", message: e.error + "\n\ncore and enterprise will be checked out on the database's branch.",
                actionLabel: "Switch & continue") { retry("--switch-branch") }
        case "busy":
            confirmation = Confirmation(
                title: "Other databases are running", message: e.error + "\n\nThey will keep running on code of another version until restarted.",
                actionLabel: "Switch anyway", destructive: true) { retry("--force") }
        default:
            show(e.error + (e.hint.map { "\n" + $0 } ?? ""), error: true)
        }
    }

    func start(_ db: String) { Task { await runStart(db) } }

    @discardableResult
    func runStart(_ db: String) async -> Bool {
        await perform("Start \(db)", db: db, busyLabel: "Starting…", args: ["start", db]) { r in
            r.alreadyRunning == true ? "\(db) is already running" : "\(db) is up on \(r.url ?? "")"
        } != nil
    }

    func startInTerminal(_ db: String) {
        Task { await perform("Start \(db) in terminal", db: db, busyLabel: "Starting…", args: ["start", db, "--terminal", "--no-wait"]) { _ in "\(db) opened in the terminal" } }
    }

    func stop(_ db: String) { Task { await runStop(db) } }

    @discardableResult
    func runStop(_ db: String) async -> Bool {
        await perform("Stop \(db)", db: db, busyLabel: "Stopping…", args: ["stop", db]) { _ in "\(db) stopped" } != nil
    }

    func restart(_ db: String) { Task { await runRestart(db) } }

    @discardableResult
    func runRestart(_ db: String) async -> Bool {
        await perform("Restart \(db)", db: db, busyLabel: "Restarting…", args: ["restart", db]) { _ in "\(db) restarted" } != nil
    }

    func open(_ db: String, series: String? = nil) {
        let url = process(for: db)?.url ?? databases.first { $0.name == db }?.url ?? "http://localhost:8069"
        let major = Int((series ?? "").filter(\.isNumber).prefix(2)) ?? 19
        if let u = URL(string: url + (major >= 18 ? "/odoo" : "/web")) { NSWorkspace.shared.open(u) }
    }

    func repair(_ db: String, fix: String? = nil) {
        var args = ["repair", db]
        if let fix { args += ["--fix", fix] }
        Task { await perform(fix.map { "Repair \(db): \($0)" } ?? "Repair \(db)", db: db, busyLabel: "Repairing…", args: args) { _ in "\(db) repaired" } }
    }

    func update(_ db: String, modules: [String]) { Task { await runUpdate(db, modules: modules) } }

    /// Stops the database if needed, updates the modules, then restarts it if it was running.
    @discardableResult
    func runUpdate(_ db: String, modules: [String]) async -> Bool {
        let mods = modules.isEmpty ? "all" : modules.joined(separator: ",")
        let wasRunning = isRunning(db)
        if wasRunning { _ = await perform("Stop \(db)", db: db, args: ["stop", db]) { _ in "\(db) stopped" } }
        let r = await perform("Update \(mods) on \(db)", db: db, busyLabel: "Updating…", args: ["update", db, "-m", mods]) { _ in "Modules updated on \(db)" }
        if wasRunning, r != nil { await runStart(db) }
        return r != nil
    }

    func neutralize(_ db: String) { Task { await runNeutralize(db) } }

    @discardableResult
    func runNeutralize(_ db: String) async -> Bool {
        await perform("Neutralize \(db)", db: db, busyLabel: "Neutralizing…", args: ["neutralize", db]) { _ in "\(db) neutralized" } != nil
    }

    func duplicate(_ db: String, as new: String) {
        Task { await perform("Duplicate \(db)", db: db, busyLabel: "Duplicating…", args: ["duplicate", db, new]) { _ in "\(new) created from \(db)" } }
    }

    func rename(_ db: String, to new: String) async -> Bool {
        await perform("Rename \(db)", db: db, busyLabel: "Renaming…", args: ["rename", db, new]) { _ in "\(db) renamed to \(new)" } != nil
    }

    func delete(_ db: String) async -> Bool {
        let ok = await perform("Delete \(db)", db: db, busyLabel: "Deleting…", args: ["delete", db, "--yes"]) { _ in "\(db) deleted" } != nil
        // The card leaves the grid right away, without waiting for the next `odoodb list`.
        if ok { withAnimation(.smooth) { list?.databases.removeAll { $0.name == db } } }
        return ok
    }

    func setPort(_ db: String, _ port: Int) {
        Task { await perform("Port of \(db)", db: db, args: ["port", db, String(port)]) { _ in "\(db) now uses port \(port)" } }
    }

    func confirmDelete(_ db: String, then: @escaping () -> Void = {}) {
        confirmation = Confirmation(
            title: "Delete \(db)?",
            message: "The database, its filestore and its launch script are deleted. This cannot be undone.",
            actionLabel: "Delete", destructive: true) {
                Task { if await self.delete(db) { then() } }
            }
    }

    // MARK: - Bulk

    /// Runs `step` on each database one after the other: they share the same core/enterprise
    /// checkout, so running them concurrently would fight over the branch.
    func runBulk(_ verb: String, _ dbs: [String], stopOnFailure: Bool = false, step: @escaping (String) async -> Bool) {
        guard !dbs.isEmpty else { return }
        for db in dbs where busy[db] == nil { busy[db] = "Queued…" }
        Task {
            bulkDepth += 1
            defer { bulkDepth -= 1 }
            var ok = 0
            for (i, db) in dbs.enumerated() {
                if await step(db) {
                    ok += 1
                } else if stopOnFailure {
                    for rest in dbs[(i + 1)...] where busy[rest] == "Queued…" { busy[rest] = nil }
                    break
                }
            }
            await refresh()
            guard dbs.count > 1 else { return }
            if ok == dbs.count {
                show("\(verb) \(ok) databases")
            } else if confirmation == nil {
                show("\(verb) \(ok) of \(dbs.count) databases — see Activity for the failures", error: true)
            }
        }
    }

    func bulkStart(_ dbs: [String]) {
        runBulk("Started", dbs.filter { !isRunning($0) }, stopOnFailure: true) { await self.runStart($0) }
    }

    func bulkStop(_ dbs: [String]) {
        runBulk("Stopped", dbs.filter { isRunning($0) }) { await self.runStop($0) }
    }

    func bulkRestart(_ dbs: [String]) {
        runBulk("Restarted", dbs.filter { isRunning($0) }, stopOnFailure: true) { await self.runRestart($0) }
    }

    func bulkUpdate(_ dbs: [String]) {
        runBulk("Updated", dbs, stopOnFailure: true) { await self.runUpdate($0, modules: []) }
    }

    func bulkNeutralize(_ dbs: [String]) {
        runBulk("Neutralized", dbs) { await self.runNeutralize($0) }
    }

    func confirmBulkDelete(_ dbs: [String], then: @escaping () -> Void = {}) {
        guard !dbs.isEmpty else { return }
        if dbs.count == 1 { return confirmDelete(dbs[0], then: then) }
        let shown = dbs.prefix(8).joined(separator: ", ") + (dbs.count > 8 ? " and \(dbs.count - 8) more" : "")
        confirmation = Confirmation(
            title: "Delete \(dbs.count) databases?",
            message: "\(shown)\n\nThe databases, their filestores and their launch scripts are deleted. This cannot be undone.",
            actionLabel: "Delete \(dbs.count)", destructive: true) {
                self.runBulk("Deleted", dbs) { await self.delete($0) }
                then()
            }
    }
}
