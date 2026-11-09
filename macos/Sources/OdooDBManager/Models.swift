import Foundation

// Mirrors the JSON of `odoodb --json` (snake_case converted to camelCase).

struct DBListResponse: Decodable {
    var codeSeries: String
    var reposBranch: String?
    var databases: [DBSummary]
    var running: [RunningProcess]
}

struct DBSummary: Decodable, Identifiable, Hashable {
    var id: String { name }
    var name: String
    var isOdoo: Bool
    var baseVersion: String?
    var series: String?
    var modulesInstalled: Int?
    var modulesLocked: Int?
    var lastLogin: String?
    var created: String?
    var size: Int64
    var script: String?
    var scriptBranch: String?
    var folder: String?
    var customAddons: [String]
    var running: Bool
    var port: Int
    var url: String
    var matchesCode: Bool?

    var lastActivity: Date? {
        [ISO.parse(lastLogin), ISO.parse(created)].compactMap { $0 }.max()
    }
}

struct RunningProcess: Decodable, Identifiable, Hashable {
    var id: String { "\(db)-\(kind)" }
    var pid: Int
    var uptime: Int
    var rss: Int64
    var cpu: Double
    var db: String
    var port: Int
    var kind: String
    var workers: Int
    var mode: String
    var url: String
}

struct PSResponse: Decodable {
    var running: [RunningProcess]
}

struct DBInfo: Decodable {
    var name: String
    var isOdoo: Bool
    var baseVersion: String?
    var series: String?
    var modulesInstalled: Int?
    var modulesLocked: Int?
    var lastLogin: String?
    var created: String?
    var codeSeries: String
    var reposBranch: String?
    var size: Int64
    var filestore: Filestore
    var script: ScriptInfo?
    var addonsPath: [String]
    var repos: [Repo]
    var running: RunningProcess?
    var port: Int
    var url: String
    var log: LogFile
    var pyenv: String?
    var customModules: [String]
    var moduleCounts: ModuleCounts
    var languages: [Language]?
    var users: Int?
    var company: String?
    var adminLogin: String?
    var baseUrl: String?
    var issues: [Issue]
    var modules: [Module]?

    struct Filestore: Decodable { var path: String; var exists: Bool; var size: Int64 }
    struct LogFile: Decodable { var path: String; var exists: Bool; var size: Int64 }
}

struct ScriptInfo: Decodable {
    var path: String
    var db: String
    var branch: String
    var addonsPath: [String]
    var installModules: [String]
    var updateModules: [String]
    var updateMode: String
    var port: Int?
    var pyenv: String
    var managed: Bool
    var subdir: String?
}

struct ModuleCounts: Decodable {
    var installed: Int
    var community: Int
    var enterprise: Int
    var custom: Int
    var missing: Int
    var locked: Int
}

struct Language: Decodable, Hashable, Identifiable {
    var id: String { code }
    var code: String
    var name: String
}

struct Repo: Decodable, Identifiable {
    var id: String { path }
    var path: String
    var kind: String
    var exists: Bool
    var git: Bool
    var name: String
    var root: String?
    var commit: String?
    var subject: String?
    var author: String?
    var date: String?
    var branch: String?
    var changes: Int?
    var ahead: Int?
    var behind: Int?
    var remote: String?
    var installedModules: Int?
    var modules: [String]?
}

struct Issue: Decodable, Identifiable, Hashable {
    var id: String
    var severity: String
    var title: String
    var detail: String
    var fix: String?
    var fixLabel: String
}

struct Module: Decodable, Identifiable, Hashable {
    var id: String { name }
    var name: String
    var state: String
    var version: String
    var author: String
    var title: String
    var application: Bool
    var source: String
    var location: String?
}

struct BranchesResponse: Decodable {
    var current: String?
    var codeSeries: String
    var branches: [String]
}

struct SettingsResponse: Decodable {
    var communityPath: String
    var enterprisePath: String
    var scriptsPaths: [String]
    var pyenvRoot: String
    var pyenvGlobalEnv: String
    var psql: String
    var terminalApp: String
    var defaultPort: Int
    var aiMode: String
    var logsDir: String
    var configFile: String
}

/// Generic result of an action command (start, repair, translate…).
struct ActionResult: Decodable {
    var ok: Bool?
    var name: String?
    var message: String?
    var error: String?
    var hint: String?
    var code: String?
    var url: String?
    var path: String?
    var ready: Bool?
    var alreadyRunning: Bool?
    var errors: [String]?
    var logTail: [String]?
}

enum ISO {
    private static let full: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
    private static let local: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    static func parse(_ s: String?) -> Date? {
        guard let s, !s.isEmpty else { return nil }
        return full.date(from: s) ?? local.date(from: String(s.prefix(19)))
    }
}
