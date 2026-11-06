// ARKit world tracking -> the filming camera's pose, once per ARFrame (~60 Hz).
// Orientation is the camera as the phone is held (portrait for now: the UI is portrait-locked), i.e.
// viewMatrix(for: .portrait) inverted, which is what AcHandCam's receiver expects (x screen right, y screen up,
// looking down -z). ARFrames are never kept: ARKit stalls when the app holds on to too many.
import ARKit

struct TrackedPose {
    let time: TimeInterval
    let position: SIMD3<Float>
    let orientation: SIMD4<Float>
    let state: UInt8
    let reason: UInt8
}

final class Tracker: NSObject, ARSessionDelegate {
    let session = ARSession()
    var onPose: ((TrackedPose) -> Void)?
    /// ARKit's delegate queue: the pose queue, so the main thread (UI) can never delay a pose
    let queue: DispatchQueue

    init(queue: DispatchQueue) {
        self.queue = queue
        super.init()
    }
    private(set) var epoch: UInt16 = 0
    private(set) var running = false

    static var supported: Bool { ARWorldTrackingConfiguration.isSupported }

    func start() {
        guard Tracker.supported else {
            appLog("ARKit world tracking isn't available on this device")
            return
        }
        let cfg = ARWorldTrackingConfiguration()
        cfg.worldAlignment = .gravity
        // the highest frame rate the camera offers
        if let best = ARWorldTrackingConfiguration.supportedVideoFormats.max(by: { $0.framesPerSecond < $1.framesPerSecond }) {
            cfg.videoFormat = best
        }
        session.delegate = self
        session.delegateQueue = queue
        session.run(cfg, options: running ? [] : [.resetTracking])
        running = true
        appLog("tracking started")
    }

    func pause() {
        session.pause()
        fakeTimer?.cancel()
        fakeTimer = nil
        running = false
    }

    // MARK: fake tracking (CI simulator: no ARKit there): a smooth slow orbit at 60 Hz, on the pose queue like ARKit
    private var fakeTimer: DispatchSourceTimer?
    private var fakeStart: TimeInterval = 0

    func startFake() {
        fakeTimer?.cancel()
        fakeStart = ProcessInfo.processInfo.systemUptime
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now(), repeating: .nanoseconds(16_666_667), leeway: .milliseconds(1))
        t.setEventHandler { [weak self] in self?.fakeTick() }
        t.resume()
        fakeTimer = t
        running = true
        appLog("fake tracking started (60 Hz)")
    }

    private func fakeTick() {
        let now = ProcessInfo.processInfo.systemUptime
        let t = Float(now - fakeStart)
        let q = simd_quatf(angle: 0.3 * sin(t * 0.5), axis: SIMD3<Float>(0, 1, 0))
        onPose?(TrackedPose(time: now, position: SIMD3(0.3 * sin(t * 0.4), 1.5, 0.3 * cos(t * 0.4) - 0.3),
                            orientation: SIMD4(q.imag.x, q.imag.y, q.imag.z, q.real), state: 2, reason: 0))
    }

    func session(_ session: ARSession, didFailWithError error: Error) {
        appLog("tracking failed: \(error.localizedDescription)")
    }

    func sessionWasInterrupted(_ session: ARSession) { appLog("tracking interrupted") }

    func sessionInterruptionEnded(_ session: ARSession) {
        epoch &+= 1 // the receiver re-anchors instead of jumping
        appLog("tracking resumed (epoch \(epoch))")
    }
}
