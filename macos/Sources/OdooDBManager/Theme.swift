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

    // Formatters are expensive to create (ICU locale setup): one of each, reused by every card.
    private static let relative: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.locale = Locale(identifier: "en_US")
        f.unitsStyle = .short
        return f
    }()

    private static func durationFormatter(_ units: NSCalendar.Unit) -> DateComponentsFormatter {
        var cal = Calendar.current
        cal.locale = Locale(identifier: "en_US")
        let f = DateComponentsFormatter()
        f.calendar = cal
        f.allowedUnits = units
        f.unitsStyle = .abbreviated
        return f
    }
    private static let days = durationFormatter([.day, .hour])
    private static let hours = durationFormatter([.hour, .minute])
    private static let minutes = durationFormatter([.minute, .second])

    static func ago(_ date: Date?) -> String {
        guard let date else { return "never" }
        return relative.localizedString(for: date, relativeTo: Date())
    }

    static func duration(_ seconds: Int) -> String {
        let f = seconds >= 86400 ? days : seconds >= 3600 ? hours : minutes
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

    var body: some View {
        ZStack {
            if busy {
                ProgressView().controlSize(.mini)
            } else {
                if running {
                    // Larger than the dot so the growing ring is never clipped; layout stays 12×12.
                    PulseRing().frame(width: 22, height: 22).allowsHitTesting(false)
                }
                Circle()
                    .fill(running ? Theme.running : Color.secondary.opacity(0.35))
                    .frame(width: 8, height: 8)
            }
        }
        .frame(width: 12, height: 12)
    }
}

/// Endless "alive" ring around the status dot. Driven by Core Animation, so it runs in the
/// render server: a SwiftUI repeatForever animation re-evaluates the view graph on every frame.
struct PulseRing: NSViewRepresentable {
    func makeNSView(context: Context) -> PulseRingView { PulseRingView() }
    func updateNSView(_ view: PulseRingView, context: Context) {}
}

final class PulseRingView: NSView {
    private let ring = CAShapeLayer()

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
        ring.fillColor = nil
        ring.lineWidth = 2
        layer?.addSublayer(ring)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override var isFlipped: Bool { true }
    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        ring.frame = bounds
        let d: CGFloat = 8
        ring.path = CGPath(ellipseIn: CGRect(x: (bounds.width - d) / 2, y: (bounds.height - d) / 2, width: d, height: d), transform: nil)
        CATransaction.commit()
    }

    override func viewDidChangeEffectiveAppearance() {
        super.viewDidChangeEffectiveAppearance()
        updateColor()
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        ring.removeAllAnimations()
        updateColor()
        guard window != nil, !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion else { return }
        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 1
        scale.toValue = 2.2
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        let group = CAAnimationGroup()
        group.animations = [scale, fade]
        group.duration = 1.6
        group.timingFunction = CAMediaTimingFunction(name: .easeOut)
        group.repeatCount = .infinity
        group.isRemovedOnCompletion = false
        ring.add(group, forKey: "pulse")
    }

    private func updateColor() {
        effectiveAppearance.performAsCurrentDrawingAppearance {
            ring.strokeColor = NSColor.systemGreen.withAlphaComponent(0.5).cgColor
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
    var selected = false

    func body(content: Content) -> some View {
        content
            .background {
                let shape = RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                ZStack {
                    // The large hover shadow fades in as its own layer instead of animating the blur
                    // radius, which re-rasterized the shadow on every frame of the hover animation.
                    if hovering {
                        shape.fill(Color(nsColor: .controlBackgroundColor))
                            .shadow(color: .black.opacity(0.12), radius: 12, y: 6)
                            .transition(.opacity)
                    }
                    shape.fill(Color(nsColor: .controlBackgroundColor))
                        .shadow(color: .black.opacity(0.05), radius: 3, y: 1)
                    if selected {
                        shape.fill(Color.accentColor.opacity(0.07))
                    }
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous)
                    .strokeBorder(selected ? Color.accentColor
                                  : highlighted ? Theme.running.opacity(0.45) : Color.primary.opacity(hovering ? 0.12 : 0.07),
                                  lineWidth: selected ? 2 : highlighted ? 1.2 : 0.5)
            }
    }
}

extension View {
    func card(hovering: Bool = false, highlighted: Bool = false, selected: Bool = false) -> some View {
        modifier(CardBackground(hovering: hovering, highlighted: highlighted, selected: selected))
    }
}

func reveal(_ path: String) {
    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
}

func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
