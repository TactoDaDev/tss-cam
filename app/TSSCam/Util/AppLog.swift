// In-app log: there is no Mac and no debugger, so what happened is shown on the phone (Connect sheet).
import Foundation

final class AppLog: ObservableObject {
    static let shared = AppLog()
    @Published private(set) var lines: [String] = []
    private let fmt: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    func add(_ text: String) {
        let line = "\(fmt.string(from: Date()))  \(text)"
        DispatchQueue.main.async {
            self.lines.append(line)
            if self.lines.count > 300 { self.lines.removeFirst(self.lines.count - 300) }
        }
    }
}

func appLog(_ text: String) { AppLog.shared.add(text) }
