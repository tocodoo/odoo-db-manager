import SwiftUI

/// First launch: the setup as steps (tools, GitHub, Odoo code, Python), each with only its items.
/// It opens at every launch until it is finished; the full checklist stays in the toolbar and in Settings.
struct SetupAssistant: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @ViewState private var step = Step.github

    static let completedKey = "setupAssistantCompleted"

    enum Step: Int, CaseIterable {
        case github, tools, code, python, ready

        var title: String {
            switch self {
            case .tools: "Tools"
            case .github: "GitHub"
            case .code: "Odoo Code"
            case .python: "Python"
            case .ready: "Ready"
            }
        }

        var heading: String {
            switch self {
            case .tools: "Install the tools"
            case .github: "Install GitHub and sign in"
            case .code: "Get the Odoo code"
            case .python: "Create the Python environments"
            case .ready: "This Mac is ready"
            }
        }

        var explanation: String {
            switch self {
            case .tools: "PostgreSQL stores the databases, pyenv runs each Odoo version on its own Python, "
                + "wkhtmltopdf prints PDF reports."
            case .github: "First, because everything else is downloaded from GitHub: install the GitHub command "
                + "(and GitHub Desktop), then sign in with an account that can see odoo/enterprise. "
                + "The sign-in opens in a terminal, then in your browser."
            case .code: "Point to the community and enterprise folders you already have, "
                + "or clone them here (GitHub command) or in GitHub Desktop."
            case .python: "One environment per Odoo version, with the packages its requirements need. "
                + "Each takes a few minutes."
            case .ready: "Create your first database, or ask any Claude agent: "
                + "“create an Odoo 19 database with sale and start it”."
            }
        }
    }

    /// The step an item of `odoodb setup` belongs to.
    static func step(of item: SetupItem) -> Step {
        switch item.id {
        case "gh", "desktop": .github
        case "community", "enterprise": .code
        case "forced": .python
        case _ where item.id.hasPrefix("env:"): .python
        default: .tools
        }
    }

    private func items(_ s: Step) -> [SetupItem] {
        (store.setup?.items ?? []).filter { Self.step(of: $0) == s }
    }

    private func ok(_ id: String) -> Bool {
        store.setup?.items.contains { $0.id == id && $0.status == "ok" } == true
    }

    /// What must be done before the next step; nil when it can go on.
    private var missing: String? {
        switch step {
        case .github:
            if !ok("gh") { return "Install the GitHub command and sign in to continue." }
        case .tools:
            if !ok("postgres") { return "PostgreSQL is needed to continue." }
            if !ok("pyenv") { return "pyenv is needed to continue." }
        case .code:
            if !ok("community") { return "Odoo community is needed to continue." }
        default: break
        }
        return nil
    }

    private func done(_ s: Step) -> Bool {
        let list = items(s)
        return !list.isEmpty && list.allSatisfy { $0.status == "ok" || $0.status == "optional" }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            VStack(alignment: .leading, spacing: 4) {
                Text(step.heading).font(.title3.weight(.semibold))
                Text(step.explanation)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 24)
            .padding(.top, 16)
            if step == .ready {
                ready
            } else if step == .python, items(.python).isEmpty {
                placeholder("Get the Odoo code first: the environments follow its versions.")
            } else {
                let s = step
                SetupView(include: { Self.step(of: $0) == s }, footer: false, installAll: s != .python,
                          newEnvironmentButton: s == .python)
                    .id(s)
            }
            Divider()
            footer
        }
        .frame(width: 680, height: 640)
    }

    private var header: some View {
        HStack(spacing: 0) {
            ForEach(Step.allCases, id: \.self) { s in
                if s != .github {
                    Rectangle()
                        .fill(s.rawValue <= step.rawValue ? Color.accentColor : Color.secondary.opacity(0.3))
                        .frame(height: 1)
                        .frame(minWidth: 12, maxWidth: 32)
                }
                Button { step = s } label: {
                    HStack(spacing: 6) {
                        ZStack {
                            Circle()
                                .fill(s == step ? Color.accentColor : s.rawValue < step.rawValue || done(s)
                                      ? Color.accentColor.opacity(0.2) : Color.secondary.opacity(0.15))
                                .frame(width: 22, height: 22)
                            if s != step, done(s) || (s == .ready && step == .ready) {
                                Image(systemName: "checkmark").font(.caption2.weight(.bold)).foregroundStyle(Color.accentColor)
                            } else {
                                Text("\(s.rawValue + 1)").font(.caption.weight(.semibold))
                                    .foregroundStyle(s == step ? Color.white : Color.secondary)
                            }
                        }
                        Text(s.title)
                            .font(.callout.weight(s == step ? .semibold : .regular))
                            .lineLimit(1)
                            .fixedSize()
                            .foregroundStyle(s == step ? Color.primary : Color.secondary)
                    }
                    .padding(.horizontal, 6)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 14)
    }

    private func placeholder(_ text: String) -> some View {
        Text(text)
            .font(.callout)
            .foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(24)
    }

    private var ready: some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 44))
                .foregroundStyle(.green)
            if let s = store.setup, s.warnings > 0 || s.problems > 0 {
                Text("\(s.problems + s.warnings) recommended item\(s.problems + s.warnings == 1 ? "" : "s") left: "
                     + "find them any time in Setup (the wrench in the toolbar).")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(24)
    }

    private var footer: some View {
        HStack {
            Button("Finish Later") { close(completed: false) }
                .help("The assistant opens again at the next launch.")
            Spacer()
            if store.setupLoading { ProgressView().controlSize(.small) }
            if let missing, step != .ready {
                Text(missing).font(.callout).foregroundStyle(.secondary)
            }
            if step != .github {
                Button("Back") { step = Step(rawValue: step.rawValue - 1) ?? .github }
            }
            if step == .ready {
                Button("Start Using Odoo Database Manager") { close(completed: true) }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
            } else {
                Button(done(step) || missing != nil ? "Continue" : "Skip for Now") {
                    step = Step(rawValue: step.rawValue + 1) ?? .ready
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(missing != nil)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .background(.bar)
    }

    private func close(completed: Bool) {
        if completed { UserDefaults.standard.set(true, forKey: Self.completedKey) }
        store.setupAssistant = false
        dismiss()
        Task { await store.refresh() }
    }
}
