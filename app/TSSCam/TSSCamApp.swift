import SwiftUI

@main
struct TSSCamApp: App {
    @StateObject private var model = AppModel()
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(model.link)
                .environmentObject(AppLog.shared)
                .preferredColorScheme(.dark)
                .statusBarHidden()
                .onChange(of: phase) { p in
                    if p == .active { model.start() } else if p == .background { model.stop() }
                }
                .onAppear { model.start() }
        }
    }
}
