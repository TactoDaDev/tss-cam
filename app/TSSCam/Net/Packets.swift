// Packet codecs. Poses and acks are AcHandCam's v3 layout byte for byte (receiver/handheld_receiver/protocol.py), so
// the stock AcHandCam receiver works with this app too; TSC* packets are ours (receiver/tsscam/protocol.py).
// Little-endian everywhere, every packet ends in a zlib CRC-32 of the bytes before it.
import Foundation

enum CRC32 {
    static let table: [UInt32] = (0..<256).map { i -> UInt32 in
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB8_8320 ^ (c >> 1)) : (c >> 1) }
        return c
    }

    static func of(_ data: Data) -> UInt32 {
        var c: UInt32 = 0xFFFF_FFFF
        for b in data { c = table[Int((c ^ UInt32(b)) & 0xFF)] ^ (c >> 8) }
        return c ^ 0xFFFF_FFFF
    }
}

struct ByteWriter {
    var data = Data()
    mutating func u8(_ v: UInt8) { data.append(v) }
    mutating func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    mutating func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { data.append(contentsOf: $0) } }
    mutating func f32(_ v: Float) { u32(v.bitPattern) }
    mutating func f64(_ v: Double) { withUnsafeBytes(of: v.bitPattern.littleEndian) { data.append(contentsOf: $0) } }
    mutating func ascii(_ s: String) { data.append(contentsOf: Array(s.utf8)) }
    mutating func appendCRC() { u32(CRC32.of(data)) }
}

struct ByteReader {
    let data: Data // always a fresh Data (index 0 based)
    init(_ d: Data) { data = Data(d) }
    func u8(_ at: Int) -> UInt8 { data[at] }
    func u16(_ at: Int) -> UInt16 { UInt16(data[at]) | UInt16(data[at + 1]) << 8 }
    func u32(_ at: Int) -> UInt32 {
        UInt32(data[at]) | UInt32(data[at + 1]) << 8 | UInt32(data[at + 2]) << 16 | UInt32(data[at + 3]) << 24
    }
    func f64(_ at: Int) -> Double {
        var bits: UInt64 = 0
        for i in 0..<8 { bits |= UInt64(data[at + i]) << (8 * UInt64(i)) }
        return Double(bitPattern: bits)
    }
    func ascii(_ at: Int, _ n: Int) -> String { String(decoding: data[at..<(at + n)], as: UTF8.self) }
    func crcOK() -> Bool { data.count >= 4 && u32(data.count - 4) == CRC32.of(data.prefix(data.count - 4)) }
}

// MARK: - AcHandCam pose (phone -> PC), v3, 100 bytes

struct Pose {
    enum Flag { static let freeze: UInt16 = 1, paused: UInt16 = 2, wantVideo: UInt16 = 4, needKeyframe: UInt16 = 8 }
    var trackingState: UInt8 = 0      // 0 not available, 1 limited, 2 normal
    var trackingReason: UInt8 = 0     // 0 none, 1 initializing, 2 excessive motion, 3 few features, 4 relocalizing, 5 other
    var sessionID: UInt32 = 0
    var seq: UInt32 = 0
    var epoch: UInt16 = 0
    var flags: UInt16 = 0
    var recenterCount: UInt16 = 0
    var rttTenthMs: UInt16 = 0
    var captureTime: Double = 0
    var sendTime: Double = 0
    var position: SIMD3<Float> = .zero          // ARKit world, metres
    var orientation: SIMD4<Float> = [0, 0, 0, 1] // x y z w, filming camera -> world
    var zoom: Float = 1
    var joyX: Float = 0
    var joyY: Float = 0
    var walk = false
    var moveScale: Float = 0
    var vfAspect: Float = 0
    var portrait = true

    func encode() -> Data {
        var w = ByteWriter()
        w.ascii("ACHC"); w.u8(3); w.u8(1); w.u8(trackingState); w.u8(trackingReason)
        w.u32(sessionID); w.u32(seq); w.u16(epoch); w.u16(flags); w.u16(recenterCount); w.u16(rttTenthMs)
        w.f64(captureTime); w.f64(sendTime)
        w.f32(position.x); w.f32(position.y); w.f32(position.z)
        w.f32(orientation.x); w.f32(orientation.y); w.f32(orientation.z); w.f32(orientation.w)
        w.f32(zoom); w.f32(joyX); w.f32(joyY); w.u8(walk ? 1 : 0); w.u8(0); w.u16(0)
        w.f32(moveScale); w.f32(vfAspect); w.u8(portrait ? 1 : 0); w.u8(0); w.u16(0)
        w.appendCRC()
        return w.data
    }
}

// MARK: - AcHandCam ack (PC -> phone), 24 bytes

struct Ack {
    let status: UInt8 // 0 ignored, 1 accepted and live, 2 accepted but holding
    let seq: UInt32
    let sendTime: Double

    static func decode(_ d: Data) -> Ack? {
        let r = ByteReader(d)
        guard r.data.count == 24, r.ascii(0, 4) == "ACHA", r.u8(4) == 1, r.crcOK() else { return nil }
        return Ack(status: r.u8(5), seq: r.u32(8), sendTime: r.f64(12))
    }
}

// MARK: - TSS Cam packets

enum TSC {
    static let hello = "TSCH", command = "TSCM", answer = "TSCA", caps = "TSCP", sticks = "TSCS"

    static func encode(_ magic: String, _ payload: Data) -> Data {
        var w = ByteWriter()
        w.ascii(magic); w.u8(1); w.u8(0); w.u16(UInt16(payload.count))
        w.data.append(payload)
        w.appendCRC()
        return w.data
    }

    static func encodeJSON(_ magic: String, _ obj: [String: Any]) -> Data? {
        guard let json = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
        return encode(magic, json)
    }

    static func encodeSticks(lift: Float, lookX: Float, lookY: Float, speed: Float) -> Data {
        func c(_ v: Float) -> Float { max(-1, min(1, v)) }
        var w = ByteWriter()
        w.f32(c(lift)); w.f32(c(lookX)); w.f32(c(lookY)); w.f32(max(0, c(speed)))
        return encode(sticks, w.data)
    }

    /// magic and JSON object, or nil for anything else (an ack, video, damage)
    static func decodeJSON(_ d: Data) -> (String, [String: Any])? {
        let r = ByteReader(d)
        guard r.data.count >= 12 else { return nil }
        let magic = r.ascii(0, 4)
        guard [hello, answer, caps].contains(magic), r.u8(4) == 1, r.crcOK() else { return nil }
        let len = Int(r.u16(6))
        guard r.data.count == 8 + len + 4 else { return nil }
        var payload = r.data.subdata(in: 8..<(8 + len))
        if r.u8(5) & 1 != 0 { // zlib: strip the 2-byte header and the Adler-32, inflate the raw deflate stream
            guard payload.count > 6,
                  let out = try? (payload.subdata(in: 2..<(payload.count - 4)) as NSData).decompressed(using: .zlib)
            else { return nil }
            payload = out as Data
        }
        guard let obj = try? JSONSerialization.jsonObject(with: payload) as? [String: Any] else { return nil }
        return (magic, obj)
    }
}
