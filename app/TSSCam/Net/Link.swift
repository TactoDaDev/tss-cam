// One UDP flow to the receiver: poses and our packets out, acks and our packets back on the same socket
// (the receiver answers to the datagram's source address). Network.framework, no discovery yet: the user types the
// PC's address (the receiver window shows it).
import Foundation
import Network

struct Control: Identifiable, Equatable {
    let id: String
    let label: String
    let group: String
    let kind: String      // toggle | press | hold | cycle
    let value: Int?
    let states: [String]
}

struct Caps: Equatable {
    var game: String? = nil
    var connected = false
    var car = ""
    var camMode = "hand"
    var modes: [String] = []
    var engaged = false
    var time: Double = 1
    var replayState = "inactive"
    var clean = false
    var controls: [Control] = []

    init() {}
    init(_ o: [String: Any]) {
        game = o["game"] as? String
        connected = o["connected"] as? Bool ?? false
        car = o["car"] as? String ?? ""
        camMode = o["camMode"] as? String ?? "hand"
        modes = o["modes"] as? [String] ?? []
        engaged = o["engaged"] as? Bool ?? false
        time = (o["time"] as? NSNumber)?.doubleValue ?? 1
        clean = o["clean"] as? Bool ?? false
        replayState = (o["replay"] as? [String: Any])?["state"] as? String ?? "inactive"
        controls = (o["controls"] as? [[String: Any]] ?? []).compactMap { c in
            guard let id = c["id"] as? String else { return nil }
            return Control(id: id, label: c["label"] as? String ?? id, group: c["group"] as? String ?? "car",
                           kind: c["kind"] as? String ?? "press", value: (c["value"] as? NSNumber)?.intValue,
                           states: c["states"] as? [String] ?? [])
        }
    }
}

final class Link: ObservableObject {
    @Published private(set) var live = false          // acks are coming back
    @Published private(set) var rttMs: Double? = nil
    @Published private(set) var status = "Not connected"
    @Published private(set) var caps: Caps? = nil      // nil: a receiver without TSS Cam's extras (stock AcHandCam)
    @Published private(set) var lastError = ""

    private var conn: NWConnection?
    private let queue = DispatchQueue(label: "tsscam.link")
    private var lastAck = Date.distantPast
    private var cmdSeq = 0
    private var helloAt = Date.distantPast
    private(set) var host = ""

    func connect(host: String, port: UInt16 = 47820) {
        disconnect()
        self.host = host
        guard let p = NWEndpoint.Port(rawValue: port), !host.isEmpty else { return }
        let params = NWParameters.udp
        params.serviceClass = .interactiveVideo
        let c = NWConnection(host: NWEndpoint.Host(host), port: p, using: params)
        c.stateUpdateHandler = { [weak self] st in
            appLog("link: \(st)")
            if case .failed(let e) = st { self?.publish { $0.lastError = "\(e)" } }
        }
        c.start(queue: queue)
        conn = c
        receive(c)
        appLog("connecting to \(host):\(port)")
        publish { $0.status = "Connecting to \(host)…" }
        sayHello()
    }

    func disconnect() {
        conn?.cancel()
        conn = nil
        publish { $0.live = false; $0.caps = nil; $0.status = "Not connected" }
    }

    func send(_ d: Data) {
        conn?.send(content: d, completion: .contentProcessed { _ in })
    }

    func sayHello() {
        helloAt = Date()
        let info: [String: Any] = ["app": "tsscam", "ver": Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0"]
        if let d = TSC.encodeJSON(TSC.hello, info) { send(d) }
    }

    /// A command for the game (or the receiver); the answer arrives as a TSCA, errors land in `lastError`.
    func command(_ cmd: String, _ args: [String: Any] = [:]) {
        cmdSeq += 1
        if let d = TSC.encodeJSON(TSC.command, ["seq": cmdSeq, "cmd": cmd, "args": args]) { send(d) }
    }

    /// Called every frame: hello until the receiver answers with its capabilities, live flag from ack age.
    func tick() {
        let now = Date()
        if caps == nil, now.timeIntervalSince(helloAt) > 2, conn != nil { sayHello() }
        let isLive = now.timeIntervalSince(lastAck) < 1.0
        if isLive != live {
            publish {
                $0.live = isLive
                $0.status = isLive ? "Live" : ($0.conn == nil ? "Not connected" : "No answer from \($0.host): same Wi-Fi? Local Network allowed?")
            }
        }
    }

    private func receive(_ c: NWConnection) {
        c.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            if let d = data, !d.isEmpty { self.handle(d) }
            if error == nil, self.conn === c { self.receive(c) }
        }
    }

    private func handle(_ d: Data) {
        if let ack = Ack.decode(d) {
            lastAck = Date()
            let rtt = (ProcessInfo.processInfo.systemUptime - ack.sendTime) * 1000
            publish { $0.rttMs = rtt }
            return
        }
        guard let decoded = TSC.decodeJSON(d) else { return } // live video (ACHV) comes in phase 5
        let (magic, obj) = decoded
        switch magic {
        case TSC.caps:
            let caps = Caps(obj)
            publish { if $0.caps != caps { $0.caps = caps } }
        case TSC.answer:
            if (obj["ok"] as? Bool) == false {
                let err = obj["err"] as? String ?? "failed"
                appLog("command \(obj["seq"] ?? "?") refused: \(err)")
                publish { $0.lastError = err }
            }
        case TSC.hello:
            appLog("receiver \(obj["ver"] ?? "?"), game \(obj["game"] ?? "none")")
        default: break
        }
    }

    private func publish(_ f: @escaping (Link) -> Void) {
        DispatchQueue.main.async { f(self) }
    }
}
