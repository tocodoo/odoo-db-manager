// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OdooDBManager",
    platforms: [.macOS(.v14)],
    dependencies: [
        // Terminal emulator (PTY) of the built-in terminal. 1.12+ ship a Metal shader that the newer
        // Command Line Tools try to compile with `metal`, which only comes with Xcode: the build fails without it.
        .package(url: "https://github.com/migueldeicaza/SwiftTerm.git", exact: "1.11.2"),
    ],
    targets: [
        .executableTarget(
            name: "OdooDBManager",
            dependencies: [.product(name: "SwiftTerm", package: "SwiftTerm")],
            path: "Sources/OdooDBManager"
        )
    ]
)
