import AppKit
import SwiftUI

enum Theme {
    static let odoo = Color(red: 0x71 / 255, green: 0x4B / 255, blue: 0x67 / 255)
    static let running = Color(nsColor: .systemGreen)
    static let cardRadius: CGFloat = 14

    /// Stable color per Odoo major version.
    static func color(forSeries series: String?) -> Color {
        let major = Int((series ?? "").split(whereSeparator: { !$0.isNumber }).first ?? "") ?? 0
        switch major {
        case 20...: return Color(nsColor: .systemOrange)
        case 19: return Color(red: 0.62, green: 0.36, blue: 0.56)
        case 18: return Color(nsColor: .systemTeal)
        case 17: return Color(nsColor: .systemIndigo)
        case 1...16: return Color(nsColor: .systemGray)
        default: return .secondary
        }
    }

    static func label(forSeries series: String?) -> String {
        guard let s = series, !s.isEmpty else { return "—" }
        return s.replacingOccurrences(of: "saas~", with: "saas ")
    }
}

enum Fmt {
    static func bytes(_ n: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }

    static func ago(_ date: Date?) -> String {
        guard let date else { return "never" }
        let f = RelativeDateTimeFormatter()
        f.locale = Locale(identifier: "en_US")
        f.unitsStyle = .short
        return f.localizedString(for: date, relativeTo: Date())
    }

    static func duration(_ seconds: Int) -> String {
        let f = DateComponentsFormatter()
        var cal = Calendar.current
        cal.locale = Locale(identifier: "en_US")
        f.calendar = cal
        f.allowedUnits = seconds >= 86400 ? [.day, .hour] : seconds >= 3600 ? [.hour, .minute] : [.minute, .second]
        f.unitsStyle = .abbreviated
        return f.string(from: TimeInterval(seconds)) ?? "\(seconds)s"
    }
}

struct VersionBadge: View {
    var series: String?
    var compact = false

    var body: some View {
        let color = Theme.color(forSeries: series)
        Text(Theme.label(forSeries: series))
            .font(.system(compact ? .caption2 : .caption, design: .rounded).weight(.semibold))
            .monospacedDigit()
            .padding(.horizontal, compact ? 6 : 8)
            .padding(.vertical, compact ? 2 : 3)
            .foregroundStyle(color)
            .background(color.opacity(0.14), in: Capsule())
    }
}

struct StatusDot: View {
    var running: Bool
    var busy = false
    @State private var pulse = false

    var body: some View {
        ZStack {
            if busy {
                ProgressView().controlSize(.mini)
            } else {
                Circle()
                    .fill(running ? Theme.running : Color.secondary.opacity(0.35))
                    .frame(width: 8, height: 8)
                    .overlay {
                        if running {
                            Circle()
                                .stroke(Theme.running.opacity(0.5), lineWidth: 2)
                                .scaleEffect(pulse ? 2.2 : 1)
                                .opacity(pulse ? 0 : 1)
                        }
                    }
            }
        }
        .frame(width: 12, height: 12)
        .onAppear {
            guard running else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { pulse = true }
        }
        .onChange(of: running) { _, now in
            pulse = false
            if now {
                withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { pulse = true }
            }
        }
    }
}

struct Chip: View {
    var text: String
    var symbol: String?
    var tint: Color = .secondary

    var body: some View {
        HStack(spacing: 4) {
            if let symbol { Image(systemName: symbol).imageScale(.small) }
            Text(text).lineLimit(1)
        }
        .font(.caption)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .foregroundStyle(tint)
        .background(tint.opacity(0.1), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }
}

/// Card surface used across the app.
struct CardBackground: ViewModifier {
    var hovering = false
    var highlighted = false

    func body(content: Content) -> some View {
        content
            .background {
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
                    .shadow(color: .black.opacity(hovering ? 0.12 : 0.05), radius: hovering ? 12 : 3, y: hovering ? 6 : 1)
            }
            .overlay {
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .strokeBorder(highlighted ? Theme.running.opacity(0.45) : Color.primary.opacity(hovering ? 0.12 : 0.07),
                                  lineWidth: highlighted ? 1.2 : 0.5)
            }
    }
}

extension View {
    func card(hovering: Bool = false, highlighted: Bool = false) -> some View {
        modifier(CardBackground(hovering: hovering, highlighted: highlighted))
    }
}

func reveal(_ path: String) {
    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
}

func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
