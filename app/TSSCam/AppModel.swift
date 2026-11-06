// The phone's state and the per-frame send: every ARFrame becomes one AcHandCam v3 pose (with zoom, freeze, walk
// stick, recenter count folded in) and, while a stick is held, one TSS Cam sticks packet.
//
// Poses run on their own high-priority queue (ARKit's delegate queue), never the main thread: the CI end-to-end
// run showed that with the send on main, UI work delayed poses into bursts (~31/s, 149 lost, 58 too late).
// The UI's controls reach that queue through `controls` (a lock); the queue reaches the UI by main.async.
import Foundation
import SwiftUI
import UIKit

enum Tab: String, CaseIterable { case hand = "HAND", walk = "WALK", car = "CAR" }

struct Controls {
    var zoom: Float = 1
    var frozen = false
    var walk = false
    var move: CGPoint = .zero
    var lift: CGFloat = 0
    var look: CGFloat = 0
    var recenter: UInt16 = 0
}

final class AppModel: ObservableObject {
    let poseQueue = DispatchQueue(label: "tsscam.pose", qos: .userInteractive)
    let link = Link()
    let tracker: Tracker

    @AppStorage("host") var host = ""
    @Published var tab: Tab = .hand { didSet { controls.mutate { $0.walk = tab == .walk } } }
    @Published var zoom: Float = 1 { didSet { controls.mutate { $0.zoom = zoom } } }
    @Published var frozen = false { didSet { controls.mutate { $0.frozen = frozen } } }
    @Published var showConnect = false
    @Published var showCar = false
    // sticks: move (walk), lift (up/down) and look (turn); -1...1
    @Published var move: CGPoint = .zero { didSet { controls.mutate { $0.move = move } } }
    @Published var lift: CGFloat = 0 { didSet { controls.mutate { $0.lift = lift } } }
    @Published var look: CGFloat = 0 { didSet { controls.mutate { $0.look = look } } }
    @Published var trackingText = "Starting…"

    private let controls = Locked(Controls())
    private let session = UInt32.random(in: 1...UInt32.max)
    // pose-queue state
    private var seq: UInt32 = 0
    private var stickIdleFrames = 0
    private var lastTrackingText = ""
    private let vfAspect: Float = {
        let b = UIScreen.main.bounds
        return Float(max(b.width, b.height) / max(1, min(b.width, b.height)))
    }()

    init() {
        tracker = Tracker(queue: poseQueue)
        tracker.onPose = { [weak self] p in self?.frame(p) }
        if host.isEmpty { showConnect = true } else { link.connect(host: host) }
    }

    func start() {
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-demo") { // screenshots on the CI simulator (no ARKit there)
            link.demo()
            showConnect = false
            trackingText = "Tracking"
            if args.contains("-demoCar") { showCar = true }
            if args.contains("-demoWalk") { tab = .walk }
            return
        }
        // CI end-to-end: -host <address> connects straight away, -fakeTracking replaces ARKit (not in the simulator)
        if let i = args.firstIndex(of: "-host"), i + 1 < args.count {
            showConnect = false
            connect(to: args[i + 1])
        }
        if args.contains("-fakeTracking") { tracker.startFake() } else { tracker.start() }
    }
    func stop() { tracker.pause() }

    func connect(to h: String) {
        host = h.trimmingCharacters(in: .whitespaces)
        link.connect(host: host)
    }

    func recenter() {
        controls.mutate { $0.recenter &+= 1 }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    /// On the pose queue.
    private func frame(_ p: TrackedPose) {
        let c = controls.get()
        seq &+= 1
        var pose = Pose()
        pose.trackingState = p.state
        pose.trackingReason = p.reason
        pose.sessionID = session
        pose.seq = seq
        pose.epoch = tracker.epoch
        pose.flags = c.frozen ? Pose.Flag.freeze : 0
        pose.recenterCount = c.recenter
        if let rtt = link.rtt.get() { pose.rttTenthMs = UInt16(max(0, min(65_535, rtt * 10))) }
        pose.captureTime = p.time
        pose.sendTime = ProcessInfo.processInfo.systemUptime
        pose.position = p.position
        pose.orientation = p.orientation
        pose.zoom = c.zoom
        pose.walk = c.walk
        pose.joyX = Float(c.move.x)
        pose.joyY = Float(-c.move.y) // screen y grows down; forward is +1
        pose.vfAspect = vfAspect
        pose.portrait = true
        link.send(pose.encode())

        // sticks: send while held, and a few frames of zeros after release so the game stops cleanly
        let held = abs(c.lift) > 0.02 || abs(c.look) > 0.02
        stickIdleFrames = held ? 0 : stickIdleFrames + 1
        if stickIdleFrames < 6, link.hasCaps.get() {
            link.send(TSC.encodeSticks(lift: Float(c.lift), lookX: Float(c.look), lookY: 0, speed: 1))
        }
        link.tick()

        let text: String
        switch p.state {
        case 2: text = "Tracking"
        case 1: text = ["Limited", "Starting up", "Moving too fast", "Not enough detail", "Relocalizing", "Limited"][Int(min(p.reason, 5))]
        default: text = "No tracking"
        }
        if text != lastTrackingText {
            lastTrackingText = text
            DispatchQueue.main.async { self.trackingText = text }
        }
    }
}
