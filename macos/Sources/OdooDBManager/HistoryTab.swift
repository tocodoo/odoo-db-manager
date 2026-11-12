import SwiftUI

/// Warnings, errors and tracebacks the database's server logged, folded with counts (`odoodb history`).
struct HistoryTab: View {
    @Environment(Store.self) private var store
    var info: DBInfo
    @State private var res: HistoryResponse?
    @State private var level: Level = .all
    @State private var expanded: Set<String> = []
    @State private var error: String?

    enum Level: String, CaseIterable, Identifiable {
        case all = "All", errors = "Errors", warnings = "Warnings"
        var id: String { rawValue }
    }

    private var shown: [HistoryEvent] {
        let all = res?.events ?? []
        switch level {
        case .all: return all
        case .errors: return all.filter(\.isError)
        case .warnings: return all.filter { !$0.isError }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Picker("Level", selection: $level) {
                    ForEach(Level.allCases) { l in Text(title(l)).tag(l) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                Spacer()
                Text("From the server log of every run started by the app or `odoodb`")
                    .font(.caption).foregroundStyle(.tertiary)
                Button("Clear History") {
                    store.confirmation = Confirmation(
                        title: "Clear the history of \(info.name)?", message: "Past warnings and errors are forgotten. The log files are kept.",
                        actionLabel: "Clear", destructive: true) {
                            Task {
                                _ = try? await CLI.json(ActionResult.self, ["history", info.name, "--clear"])
                                await load()
                            }
                        }
                }
                .disabled((res?.events ?? []).isEmpty)
            }
            .controlSize(.small)
            .padding(.horizontal, 24)
            .padding(.vertical, 10)
            Divider()
            if let res, shown.isEmpty {
                ContentUnavailableView {
                    Label(res.events.isEmpty ? "Nothing logged" : "Nothing here", systemImage: "checkmark.seal")
                } description: {
                    Text(res.events.isEmpty
                         ? "No warning or error in the logs of \(info.name) so far."
                         : "No \(level == .errors ? "error" : "warning") in the history.")
                }
            } else if res == nil {
                if let error {
                    ContentUnavailableView("Cannot read the history", systemImage: "exclamationmark.triangle", description: Text(error))
                } else {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 8) {
                        ForEach(shown) { e in
                            EventRow(event: e, expanded: expanded.contains(e.id)) {
                                withAnimation(.snappy(duration: 0.2)) {
                                    if expanded.contains(e.id) { expanded.remove(e.id) } else { expanded.insert(e.id) }
                                }
                            }
                        }
                    }
                    .padding(24)
                    .frame(maxWidth: 1100)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        // Reads again while the database runs: new errors show up without reloading.
        .task(id: store.revisions[info.name, default: 0]) {
            await load()
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(store.isRunning(info.name) ? 4 : 30))
                await load()
            }
        }
    }

    private func title(_ l: Level) -> String {
        guard let s = res?.summary else { return l.rawValue }
        switch l {
        case .all: return "All \(s.errors + s.warnings)"
        case .errors: return "Errors \(s.errors)"
        case .warnings: return "Warnings \(s.warnings)"
        }
    }

    private func load() async {
        do {
            let r = try await CLI.json(HistoryResponse.self, ["history", info.name, "-n", "300"])
            if r.events != res?.events || r.summary != res?.summary {
                withAnimation(.smooth) { res = r }
            }
            error = nil
        } catch {
            if res == nil { self.error = error.localizedDescription }
        }
    }
}

struct EventRow: View {
    var event: HistoryEvent
    var expanded: Bool
    var toggle: () -> Void

    var body: some View {
        let tint: Color = event.isError ? .red : .orange
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: event.isError ? "xmark.octagon.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(tint)
                    .font(.title3)
                VStack(alignment: .leading, spacing: 4) {
                    Text(event.message)
                        .font(.callout.weight(.medium))
                        .lineLimit(expanded ? nil : 2)
                        .textSelection(.enabled)
                    if !event.exception.isEmpty {
                        Text(event.exception)
                            .font(.system(.caption, design: .monospaced))
                            .foregroundStyle(tint)
                            .lineLimit(expanded ? nil : 1)
                            .textSelection(.enabled)
                    }
                    HStack(spacing: 6) {
                        Text(event.logger)
                        Text("·")
                        Text(event.count > 1 ? "\(event.count)× · last \(Fmt.ago(ISO.parse(event.last)))" : Fmt.ago(ISO.parse(event.last)))
                        if event.count > 1, let first = ISO.parse(event.first) {
                            Text("· first \(Fmt.ago(first))")
                        }
                    }
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                if event.count > 1 {
                    Text("\(event.count)")
                        .font(.caption.weight(.semibold)).monospacedDigit()
                        .padding(.horizontal, 7).padding(.vertical, 2)
                        .background(tint.opacity(0.15), in: Capsule())
                        .foregroundStyle(tint)
                }
                if !event.detail.isEmpty {
                    Image(systemName: "chevron.right")
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                        .foregroundStyle(.secondary)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { if !event.detail.isEmpty { toggle() } }

            if expanded, !event.detail.isEmpty {
                ScrollView([.horizontal, .vertical]) {
                    Text(event.detail)
                        .font(.system(size: 11, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(10)
                }
                .frame(maxHeight: 280)
                .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                HStack {
                    Button("Copy") { copyToPasteboard("\(event.level) \(event.logger): \(event.message)\n\(event.detail)") }
                    Spacer()
                }
                .controlSize(.small)
            }
        }
        .padding(12)
        .background(tint.opacity(0.05), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(tint.opacity(0.18), lineWidth: 0.5))
    }
}
