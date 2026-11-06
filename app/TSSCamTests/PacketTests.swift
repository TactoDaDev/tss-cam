// The app's packets against the Python receiver's own bytes (receiver/handheld_receiver/protocol.py and
// receiver/tsscam/protocol.py produced these hex strings), so both sides agree byte for byte.
import XCTest
@testable import TSSCam

final class PacketTests: XCTestCase {
    func hex(_ s: String) -> Data {
        var d = Data()
        var i = s.startIndex
        while i < s.endIndex {
            let j = s.index(i, offsetBy: 2)
            d.append(UInt8(s[i..<j], radix: 16)!)
            i = j
        }
        return d
    }

    func testCRC() {
        XCTAssertEqual(CRC32.of(Data("abc".utf8)), 891_568_578)
    }

    func testPoseMatchesPython() {
        var p = Pose()
        p.trackingState = 2; p.trackingReason = 0; p.sessionID = 0x1234_5678; p.seq = 7; p.epoch = 1
        p.flags = 0b101; p.recenterCount = 2; p.rttTenthMs = 123; p.captureTime = 100.25; p.sendTime = 100.26
        p.position = [0.5, 1.5, -2.0]; p.orientation = [0, 0.38268343, 0, 0.9238795]
        p.zoom = 2; p.joyX = 0.25; p.joyY = -0.5; p.walk = true; p.moveScale = 1.5; p.vfAspect = 19.5 / 9; p.portrait = true
        let py = hex("414348430301020078563412070000000100050002007b000000000000105940713d0ad7a31059400000003f0000c03f000000c00000000015efc33e000000005e836c3f000000400000803e000000bf010000000000c03fabaa0a400100000053baff2e")
        XCTAssertEqual(p.encode(), py)
    }

    func testAck() {
        let a = Ack.decode(hex("414348410101000007000000713d0ad7a3105940d4de7ec8"))
        XCTAssertEqual(a?.status, 1)
        XCTAssertEqual(a?.seq, 7)
        XCTAssertEqual(a?.sendTime ?? 0, 100.26, accuracy: 1e-9)
        var bad = hex("414348410101000007000000713d0ad7a3105940d4de7ec8")
        bad[9] ^= 0xFF
        XCTAssertNil(Ack.decode(bad))
    }

    func testCommandMatchesPython() throws {
        let py = hex("5453434d010036007b22736571223a332c22636d64223a22636172222c2261726773223a7b226964223a22686f726e222c22646f776e223a747275657d7d33ed36b4")
        // JSON key order isn't fixed on our side: compare the decoded object, and the frame around a known payload
        let payload = py.subdata(in: 8..<(py.count - 4))
        XCTAssertEqual(TSC.encode(TSC.command, payload), py)
        let obj = try JSONSerialization.jsonObject(with: payload) as? [String: Any]
        XCTAssertEqual(obj?["cmd"] as? String, "car")
    }

    func testSticksMatchPython() {
        XCTAssertEqual(TSC.encodeSticks(lift: 0.5, lookX: -0.25, lookY: 0, speed: 1),
                       hex("54534353010010000000003f000080be000000000000803ff3dc8b56"))
    }

    func testCompressedCaps() {
        // a TSCP the receiver zlib-compressed (60 controls); only the first bytes are fixed here, so build one the
        // same way: zlib header + raw deflate + adler, as Python's zlib.compress does
        let raw = Data(#"{"controls":[{"id":"door_FL","label":"Front left door"}],"car":"ETK"}"#.utf8)
        let deflated = try! (raw as NSData).compressed(using: .zlib) as Data
        var z = Data([0x78, 0xDA]); z.append(deflated); z.append(contentsOf: [0, 0, 0, 0])
        var w = ByteWriter()
        w.ascii("TSCP"); w.u8(1); w.u8(1); w.u16(UInt16(z.count)); w.data.append(z); w.appendCRC()
        let got = TSC.decodeJSON(w.data)
        XCTAssertEqual(got?.0, "TSCP")
        XCTAssertEqual(got?.1["car"] as? String, "ETK")
    }

    func testRejectsJunk() {
        XCTAssertNil(TSC.decodeJSON(Data("ACHC".utf8) + Data(count: 96)))
        XCTAssertNil(TSC.decodeJSON(Data()))
    }
}
