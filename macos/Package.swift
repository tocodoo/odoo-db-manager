// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "OdooDBManager",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "OdooDBManager",
            path: "Sources/OdooDBManager"
        )
    ]
)
