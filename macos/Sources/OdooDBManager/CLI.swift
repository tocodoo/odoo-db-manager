import Foundation

/// Error returned by `odoodb` (`{"ok": false, "error": …, "hint": …, "code": …}`).
struct CLIError: LocalizedError, Decodable {
    var error: String
    var hint: String?
    var code: String?
    var errorDescription: String? { error }
}

/// Runs the `odoodb` command line, the single source of truth shared with the agents.
enum CLI {
    /// Folder of the Python project (odoodb.py, dbtools.py…).
    static let repo: URL = {
        if let env = ProcessInfo.processInfo.environment["ODOODB_REPO"] {
            return URL(fileURLWithPath: env)
        }
        if let path = Bundle.main.object(forInfoDictionaryKey: "DBMRepoPath") as? String,
           FileManager.default.fileExists(atPath: path + "/odoodb.py") {
            return URL(fileURLWithPath: path)
        }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("odoodbmanager")
    }()

    static var python: String {
        let venv = repo.appendingPathComponent("venv/bin/python").path
        return FileManager.default.isExecutableFile(atPath: venv) ? venv : "/usr/bin/python3"
    }

    static var script: String { repo.appendingPathComponent("odoodb.py").path }

    struct Output {
        var status: Int32
        var stdout: Data
        var stderr: String
    }

    /// Runs `odoodb --json <args>`; stderr lines (progress) are streamed to `onLine`.
    static func run(_ args: [String], onLine: (@Sendable (String) -> Void)? = nil) async -> Output {
        await withCheckedContinuation { cont in
            let p = Process()
            p.executableURL = URL(fileURLWithPath: python)
            p.arguments = ["-u", script, "--json"] + args
            p.currentDirectoryURL = repo
            var env = ProcessInfo.processInfo.environment
            env["PYTHONIOENCODING"] = "utf-8"
            env["LANG"] = env["LANG"] ?? "en_US.UTF-8"
            p.environment = env
            let out = Pipe(), err = Pipe()
            p.standardOutput = out
            p.standardError = err
            p.standardInput = FileHandle.nullDevice

            let collector = OutputCollector()
            out.fileHandleForReading.readabilityHandler = { h in
                let d = h.availableData
                if !d.isEmpty { collector.appendOut(d) }
            }
            err.fileHandleForReading.readabilityHandler = { h in
                let d = h.availableData
                guard !d.isEmpty else { return }
                for line in collector.appendErr(d) { onLine?(line) }
            }
            p.terminationHandler = { proc in
                out.fileHandleForReading.readabilityHandler = nil
                err.fileHandleForReading.readabilityHandler = nil
                collector.appendOut(out.fileHandleForReading.readDataToEndOfFile())
                for line in collector.appendErr(err.fileHandleForReading.readDataToEndOfFile(), flush: true) {
                    onLine?(line)
                }
                cont.resume(returning: Output(status: proc.terminationStatus, stdout: collector.out, stderr: collector.errText))
            }
            do {
                try p.run()
            } catch {
                cont.resume(returning: Output(status: -1, stdout: Data(), stderr: "Cannot run \(python): \(error.localizedDescription)"))
            }
        }
    }

    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    /// Runs a command and decodes its JSON result, throwing `CLIError` when `ok` is false.
    static func json<T: Decodable>(_ type: T.Type, _ args: [String], onLine: (@Sendable (String) -> Void)? = nil) async throws -> T {
        let res = await run(args, onLine: onLine)
        if res.status != 0 {
            if let e = try? decoder.decode(CLIError.self, from: res.stdout) { throw e }
        }
        do {
            return try decoder.decode(T.self, from: res.stdout)
        } catch {
            if let e = try? decoder.decode(CLIError.self, from: res.stdout) { throw e }
            let text = String(data: res.stdout, encoding: .utf8) ?? ""
            let detail = res.stderr.isEmpty ? text : res.stderr
            throw CLIError(error: "odoodb failed: \(String(detail.suffix(600)))", hint: nil, code: "cli")
        }
    }
}

/// Thread-safe accumulation of the process output (readability handlers run on background queues).
private final class OutputCollector: @unchecked Sendable {
    private let lock = NSLock()
    private(set) var out = Data()
    private var errBuffer = Data()
    private(set) var errText = ""

    func appendOut(_ d: Data) {
        lock.lock(); out.append(d); lock.unlock()
    }

    func appendErr(_ d: Data, flush: Bool = false) -> [String] {
        lock.lock(); defer { lock.unlock() }
        errBuffer.append(d)
        var lines: [String] = []
        while let nl = errBuffer.firstIndex(of: 0x0A) {
            let lineData = errBuffer[errBuffer.startIndex..<nl]
            errBuffer.removeSubrange(errBuffer.startIndex...nl)
            let line = String(decoding: lineData, as: UTF8.self)
            lines.append(line)
        }
        if flush, !errBuffer.isEmpty {
            lines.append(String(decoding: errBuffer, as: UTF8.self))
            errBuffer.removeAll()
        }
        for l in lines { errText += l + "\n" }
        return lines
    }
}
