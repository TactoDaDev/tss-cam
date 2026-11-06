// The phone's state and the per-frame send: every ARFrame becomes one AcHandCam v3 pose (with zoom, freeze, walk
// stick, recenter count folded in) and, while a stick is held, one TSS Cam sticks packet.
import Foundation
import SwiftUI
import UIKit

enum Tab: String, CaseIterable { case hand = "HAND", walk = "WALK", car = "CAR" }

final class AppModel: ObservableObject {
    let link = Link()
    let tracker = Tracker()

    @AppStorage("host") var host = ""
    @Published var tab: Tab = .hand
    @Published var zoom: Float = 1
    @Published var frozen = false
    @Published var showConnect = false
    @Published var showCar = false
    // sticks: move (walk), lift (up/down) and look (turn); -1...1
    @Published var move: CGPoint = .zero
    @Published var lift: CGFloat = 0
    @Published var look: CGFloat = 0
    @Published var trackingText = "Starting…"

    private let session = UInt32.random(in: 1...UInt32.max)
    private var seq: UInt32 = 0
    private var recenterCount: UInt16 = 0
    private var stickIdleFrames = 0

    init() {
        tracker.onPose = { [weak self] p in self?.frame(p) }
        if host.isEmpty { showConnect = true } else { link.connect(host: host) }
    }

    func start() { tracker.start() }
    func stop() { tracker.pause() }

    func connect(to h: String) {
        host = h.trimmingCharacters(in: .whitespaces)
        link.connect(host: host)
    }

    func recenter() {
        recenterCount &+= 1
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    private func frame(_ p: TrackedPose) {
        seq &+= 1
        var pose = Pose()
        pose.trackingState = p.state
        pose.trackingReason = p.reason
        pose.sessionID = session
        pose.seq = seq
        pose.epoch = tracker.epoch
        pose.flags = frozen ? Pose.Flag.freeze : 0
        pose.recenterCount = recenterCount
        if let rtt = link.rttMs { pose.rttTenthMs = UInt16(max(0, min(65_535, rtt * 10))) }
        pose.captureTime = p.time
        pose.sendTime = ProcessInfo.processInfo.systemUptime
        pose.position = p.position
        pose.orientation = p.orientation
        pose.zoom = zoom
        pose.walk = tab == .walk
        pose.joyX = Float(move.x)
        pose.joyY = Float(-move.y) // screen y grows down; forward is +1
        let b = UIScreen.main.bounds
        pose.vfAspect = Float(max(b.width, b.height) / max(1, min(b.width, b.height)))
        pose.portrait = true
        link.send(pose.encode())

        // sticks: send while held, and a few frames of zeros after release so the game stops cleanly
        let held = abs(lift) > 0.02 || abs(look) > 0.02
        stickIdleFrames = held ? 0 : stickIdleFrames + 1
        if stickIdleFrames < 6, link.caps != nil {
            link.send(TSC.encodeSticks(lift: Float(lift), lookX: Float(look), lookY: 0, speed: 1))
        }
        link.tick()
        let text: String
        switch p.state {
        case 2: text = "Tracking"
        case 1: text = ["Limited", "Starting up", "Moving too fast", "Not enough detail", "Relocalizing", "Limited"][Int(min(p.reason, 5))]
        default: text = "No tracking"
        }
        if text != trackingText { trackingText = text }
    }
}
