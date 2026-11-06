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
        session.run(cfg, options: running ? [] : [.resetTracking])
        running = true
        appLog("tracking started")
    }

    func pause() {
        session.pause()
        running = false
    }

    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let cam = frame.camera
        let camToWorld = cam.viewMatrix(for: .portrait).inverse
        let rot = simd_float3x3(SIMD3(camToWorld.columns.0.x, camToWorld.columns.0.y, camToWorld.columns.0.z),
                                SIMD3(camToWorld.columns.1.x, camToWorld.columns.1.y, camToWorld.columns.1.z),
                                SIMD3(camToWorld.columns.2.x, camToWorld.columns.2.y, camToWorld.columns.2.z))
        let q = simd_quatf(rot).normalized
        let t = cam.transform.columns.3
        var state: UInt8 = 0, reason: UInt8 = 0
        switch cam.trackingState {
        case .notAvailable: state = 0
        case .normal: state = 2
        case .limited(let r):
            state = 1
            switch r {
            case .initializing: reason = 1
            case .excessiveMotion: reason = 2
            case .insufficientFeatures: reason = 3
            case .relocalizing: reason = 4
            @unknown default: reason = 5
            }
        }
        onPose?(TrackedPose(time: frame.timestamp, position: SIMD3(t.x, t.y, t.z),
                            orientation: SIMD4(q.imag.x, q.imag.y, q.imag.z, q.real), state: state, reason: reason))
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
