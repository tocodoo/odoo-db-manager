import Foundation

/// What a dropped (or chosen) backup turns into: the dump file and the filestore folder that goes with it.
/// Accepts an Odoo backup .zip (dump.sql + filestore/), a folder holding a dump and/or a filestore,
/// a filestore folder alone, or a .sql / .dump / .gz file (with a `filestore` folder next to it, if any).
struct ResolvedBackup {
    var dump: URL?
    var filestore: URL?
    /// Suggested database name (from the zip, folder or file name).
    var name: String
    /// Folder the zip was extracted to, deleted when the sheet closes.
    var extracted: URL?
}

enum BackupDrop {
    static let dumpExtensions: Set<String> = ["sql", "dump", "gz", "backup", "pgdump"]

    struct Failure: LocalizedError {
        var errorDescription: String?
    }

    static func resolve(_ url: URL) async throws -> ResolvedBackup {
        try await Task.detached(priority: .userInitiated) { try resolveSync(url) }.value
    }

    private static func resolveSync(_ url: URL) throws -> ResolvedBackup {
        let fm = FileManager.default
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else {
            throw Failure(errorDescription: "\(url.lastPathComponent) cannot be found.")
        }
        let name = suggestedName(url)
        if isDir.boolValue {
            let found = scan(url)
            guard found.dump != nil || found.filestore != nil else {
                throw Failure(errorDescription: "No dump (.sql, .dump) or filestore in \(url.lastPathComponent).")
            }
            return ResolvedBackup(dump: found.dump, filestore: found.filestore, name: name)
        }
        if url.pathExtension.lowercased() == "zip" {
            var tmp = fm.temporaryDirectory.appendingPathComponent("odoodb-restore-\(UUID().uuidString.prefix(8))")
            try fm.createDirectory(at: tmp, withIntermediateDirectories: true)
            // /var → /private/var: the paths found inside must start with the folder's own path.
            if let real = realpath(tmp.path, nil) {
                tmp = URL(fileURLWithPath: String(cString: real), isDirectory: true)
                free(real)
            }
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
            p.arguments = ["-x", "-k", url.path, tmp.path]
            let err = Pipe()
            p.standardError = err
            p.standardOutput = FileHandle.nullDevice
            try p.run()
            p.waitUntilExit()
            guard p.terminationStatus == 0 else {
                try? fm.removeItem(at: tmp)
                let msg = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                throw Failure(errorDescription: "Cannot extract \(url.lastPathComponent): \(msg.trimmingCharacters(in: .whitespacesAndNewlines))")
            }
            let found = scan(tmp)
            guard found.dump != nil else {
                try? fm.removeItem(at: tmp)
                throw Failure(errorDescription: "No dump.sql in \(url.lastPathComponent).")
            }
            return ResolvedBackup(dump: found.dump, filestore: found.filestore, name: name, extracted: tmp)
        }
        guard dumpExtensions.contains(url.pathExtension.lowercased()) else {
            throw Failure(errorDescription: "\(url.lastPathComponent) is not a backup: drop a .zip, a .sql / .dump file or a folder.")
        }
        // A dump file: its filestore is usually next to it.
        let sibling = url.deletingLastPathComponent().appendingPathComponent("filestore")
        return ResolvedBackup(dump: url, filestore: isDirectory(sibling) ? normalizeFilestore(sibling) : nil, name: name)
    }

    /// Finds the dump (dump.sql first) and the filestore inside a folder, a few levels deep.
    private static func scan(_ root: URL) -> (dump: URL?, filestore: URL?) {
        let fm = FileManager.default
        var dumps: [URL] = []
        var filestores: [URL] = []
        let keys: [URLResourceKey] = [.isDirectoryKey]
        guard let walker = fm.enumerator(at: root, includingPropertiesForKeys: keys, options: [.skipsHiddenFiles]) else {
            return (nil, nil)
        }
        for case let u as URL in walker {
            if walker.level > 4 { walker.skipDescendants(); continue }
            let dir = (try? u.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
            if dir {
                if u.lastPathComponent == "filestore" {
                    filestores.append(u)
                    walker.skipDescendants()
                } else if isFilestoreContent(u) {
                    // A filestore folder not named "filestore" (e.g. ~/…/Odoo/filestore/<db> dropped directly).
                    filestores.append(u)
                    walker.skipDescendants()
                }
            } else if dumpExtensions.contains(u.pathExtension.lowercased()) {
                dumps.append(u)
            }
        }
        // The root itself may be the filestore (hex folders ab/, cd/… right inside).
        if filestores.isEmpty, isFilestoreContent(root) { filestores.append(root) }
        let depth = { (u: URL) in u.pathComponents.count }
        let dump = dumps.first { $0.lastPathComponent == "dump.sql" }
            ?? dumps.min { depth($0) < depth($1) }
        // Prefer the filestore that sits next to the dump.
        let filestore = dump.flatMap { d in filestores.first { $0.deletingLastPathComponent() == d.deletingLastPathComponent() } }
            ?? filestores.min { depth($0) < depth($1) }
        return (dump, filestore.map(normalizeFilestore))
    }

    /// Odoo copies the folder as the database's filestore: it must hold the hex folders (ab/, cd/…).
    /// A `filestore/<db>/ab/…` layout is unwrapped to `filestore/<db>`.
    private static func normalizeFilestore(_ url: URL) -> URL {
        var current = url
        for _ in 0..<3 where !isFilestoreContent(current) {
            let subs = subfolders(current)
            guard subs.count == 1 else { break }
            current = subs[0]
        }
        return current
    }

    private static func isFilestoreContent(_ url: URL) -> Bool {
        let subs = subfolders(url)
        guard !subs.isEmpty else { return false }
        let hex = subs.filter { $0.lastPathComponent.range(of: "^[0-9a-f]{2}$", options: .regularExpression) != nil }
        return hex.count * 2 > subs.count
    }

    private static func subfolders(_ url: URL) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: url, includingPropertiesForKeys: [.isDirectoryKey],
                                                        options: [.skipsHiddenFiles])) ?? [])
            .filter { isDirectory($0) }
    }

    private static func isDirectory(_ url: URL) -> Bool {
        var d: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &d) && d.boolValue
    }

    /// "acme_2026-11-17_10-00-00.zip" → "acme"; spaces and odd characters dropped.
    private static func suggestedName(_ url: URL) -> String {
        var base = url.lastPathComponent
        for ext in ["zip", "gz", "sql", "dump", "backup", "pgdump"] where base.lowercased().hasSuffix(".\(ext)") {
            base = String(base.dropLast(ext.count + 1))
        }
        if let r = base.range(of: #"_\d{4}-\d{2}-\d{2}[_T]\d{2}[-:]\d{2}[-:]\d{2}$"#, options: .regularExpression) {
            base.removeSubrange(r)
        }
        return base.replacingOccurrences(of: " ", with: "-")
            .filter { $0.isLetter || $0.isNumber || "-_.".contains($0) }
    }
}
