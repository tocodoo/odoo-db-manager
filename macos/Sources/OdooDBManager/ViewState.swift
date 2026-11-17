import SwiftUI

/// SwiftUI's `State` property wrapper. Since the macOS 27 SDK, `@State` is a macro whose plugin
/// (SwiftUIMacros) only ships with Xcode: with the Command Line Tools alone, every `@State` fails to build.
/// The type alias reaches the property wrapper directly, on every SDK.
typealias ViewState<Value> = SwiftUI.State<Value>
