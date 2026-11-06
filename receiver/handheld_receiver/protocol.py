"""Wire protocol between the iPhone app and the Windows receiver.

All multi-byte values are little-endian. The iPhone app (ios/.../Packet.swift)
implements the exact same layout; tests/test_protocol.py pins the byte offsets.

Pose packet (phone -> PC), 72 bytes:

  off size type  field
    0   4  char  magic            b"ACHC"
    4   1  u8    version          1
    5   1  u8    msg_type         1 = pose
    6   1  u8    tracking_state   0 = not available, 1 = limited, 2 = normal
    7   1  u8    tracking_reason  0 none, 1 initializing, 2 excessive motion,
                                  3 insufficient features, 4 relocalizing, 5 other
    8   4  u32   session_id       random per app launch (source identity)
   12   4  u32   seq              increments by 1 per packet (wraps)
   16   2  u16   epoch            increments when the ARKit session restarts
   18   2  u16   flags            bit0 freeze held, bit1 paused,
                                  bit2 wants the live view (app v3, viewfinder = GAME),
                                  bit3 live view waiting for a keyframe (app v3.1)
   20   2  u16   recenter_count   increments each time "Recenter" is tapped
   22   2  u16   rtt_tenth_ms     phone-measured round trip time / 0.1 ms, 0 = unknown
   24   8  f64   capture_time     ARFrame.timestamp (s, phone uptime clock)
   32   8  f64   send_time        phone uptime clock at send (s)
   40  12  f32x3 position         ARKit world, metres (x right, y up, z back)
   52  16  f32x4 orientation      quaternion x, y, z, w (camera -> world)
   68   4  u32   crc32            zlib CRC-32 of bytes 0..67

Version 2 (phone app v2) is the same up to offset 67, with 16 more bytes
before the CRC (88 bytes total). The receiver accepts both versions.

   68   4  f32   zoom             1.0 = main camera (24 mm equiv.), 0.5 ... 10
   72   4  f32   joy_x            walk joystick, -1 (left) ... +1 (right)
   76   4  f32   joy_y            walk joystick, -1 (back) ... +1 (forward)
   80   1  u8    mode             0 = handheld, 1 = walk
   81   1  u8    reserved
   82   2  u16   reserved
   84   4  u32   crc32            zlib CRC-32 of bytes 0..83

Version 3 (phone app v4) is v2 up to offset 83, then 12 more bytes before the
CRC (100 bytes total):

   84   4  f32   move_scale       movement scale chosen on the phone (0 = not set:
                                  the receiver's own Translation scale applies)
   88   4  f32   vf_aspect        viewfinder long/short side ratio (e.g. 19.5/9), 0 = unknown
   92   1  u8    view_orientation 0 = landscape, 1 = portrait (how the phone is held)
   93   1  u8    reserved
   94   2  u16   reserved
   96   4  u32   crc32            zlib CRC-32 of bytes 0..95

Ack packet (PC -> phone), 24 bytes:

    0   4  char  magic            b"ACHA"
    4   1  u8    version          1
    5   1  u8    status           0 ignored, 1 accepted, 2 accepted but holding
    6   2  u16   reserved
    8   4  u32   seq              echoed seq
   12   8  f64   send_time        echoed send_time (phone computes RTT)
   20   4  u32   crc32            CRC-32 of bytes 0..19

The phone's orientation is the pose of a *filming camera* whose axes are
x = screen right, y = screen up, z = towards the viewer (it looks down -z),
already corrected for the filming orientation chosen on the phone
(ARCamera.viewMatrix(for:) inverted).
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

POSE_MAGIC = b"ACHC"
ACK_MAGIC = b"ACHA"
PROTOCOL_VERSION = 3  # newest pose packet version this receiver writes/reads
ACK_VERSION = 1  # ack format is unchanged since v1 (v1 phones check for 1)
MSG_POSE = 1

_POSE_BODY = struct.Struct("<4sBBBBIIHHHHdd3f4f")
_POSE_V2_EXTRA = struct.Struct("<fffBBH")
_POSE_V3_EXTRA = struct.Struct("<ffBBH")
_ACK_BODY = struct.Struct("<4sBBHId")
_ACK_FULL_SIZE = _ACK_BODY.size + 4

assert _POSE_BODY.size == 68, _POSE_BODY.size
assert _POSE_V2_EXTRA.size == 16
assert _POSE_V3_EXTRA.size == 12
assert _ACK_BODY.size == 20

POSE_PACKET_SIZE_V1 = _POSE_BODY.size + 4  # 72
POSE_PACKET_SIZE_V2 = _POSE_BODY.size + _POSE_V2_EXTRA.size + 4  # 88
POSE_PACKET_SIZE_V3 = POSE_PACKET_SIZE_V2 + _POSE_V3_EXTRA.size  # 100
POSE_PACKET_SIZE = POSE_PACKET_SIZE_V3
_SIZES = {1: POSE_PACKET_SIZE_V1, 2: POSE_PACKET_SIZE_V2, 3: POSE_PACKET_SIZE_V3}

VIEW_LANDSCAPE = 0
VIEW_PORTRAIT = 1
MOVE_SCALE_MIN, MOVE_SCALE_MAX = 0.1, 50.0
ACK_PACKET_SIZE = _ACK_FULL_SIZE

MODE_HANDHELD = 0
MODE_WALK = 1
MODE_NAMES = {MODE_HANDHELD: "handheld", MODE_WALK: "walk"}
ZOOM_MIN, ZOOM_MAX = 0.5, 10.0

TRACKING_NOT_AVAILABLE = 0
TRACKING_LIMITED = 1
TRACKING_NORMAL = 2

TRACKING_STATE_NAMES = {0: "not available", 1: "limited", 2: "normal"}
TRACKING_REASON_NAMES = {
    0: "",
    1: "initializing",
    2: "excessive motion",
    3: "insufficient features",
    4: "relocalizing",
    5: "other",
}

FLAG_FREEZE = 0x0001
FLAG_PAUSED = 0x0002
FLAG_WANT_VIDEO = 0x0004
FLAG_NEED_KEYFRAME = 0x0008  # app v3.1: live view stuck waiting for a keyframe

ACK_IGNORED = 0
ACK_ACCEPTED = 1
ACK_HOLDING = 2


class PacketError(ValueError):
    """Raised for any malformed or untrustworthy packet."""


@dataclass(frozen=True)
class PosePacket:
    tracking_state: int
    tracking_reason: int
    session_id: int
    seq: int
    epoch: int
    flags: int
    recenter_count: int
    rtt_ms: float | None
    capture_time: float
    send_time: float
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]  # x, y, z, w
    # v2 controls (v1 packets get these defaults)
    zoom: float = 1.0
    joy_x: float = 0.0
    joy_y: float = 0.0
    mode: int = MODE_HANDHELD
    # v3 (app v4)
    move_scale: float = 0.0  # 0 = not set by the phone
    vf_aspect: float = 0.0  # 0 = unknown
    view_orientation: int = VIEW_LANDSCAPE
    version: int = PROTOCOL_VERSION

    @property
    def freeze(self) -> bool:
        return bool(self.flags & FLAG_FREEZE)

    @property
    def paused(self) -> bool:
        return bool(self.flags & FLAG_PAUSED)


# Physical sanity limits. ARKit positions are metres from the session origin;
# anything beyond a kilometre is certainly garbage for a handheld use case.
MAX_ABS_POSITION_M = 1000.0
QUAT_NORM_TOLERANCE = 0.05


def encode_pose(p: PosePacket, version: int | None = None) -> bytes:
    version = p.version if version is None else version
    if version not in (1, 2, 3):
        raise ValueError("version must be 1, 2 or 3")
    rtt = 0 if p.rtt_ms is None else max(1, min(65535, int(round(p.rtt_ms * 10.0))))
    body = _POSE_BODY.pack(
        POSE_MAGIC,
        version,
        MSG_POSE,
        p.tracking_state,
        p.tracking_reason,
        p.session_id & 0xFFFFFFFF,
        p.seq & 0xFFFFFFFF,
        p.epoch & 0xFFFF,
        p.flags & 0xFFFF,
        p.recenter_count & 0xFFFF,
        rtt,
        p.capture_time,
        p.send_time,
        *p.position,
        *p.orientation,
    )
    if version >= 2:
        body += _POSE_V2_EXTRA.pack(p.zoom, p.joy_x, p.joy_y, p.mode & 0xFF, 0, 0)
    if version >= 3:
        body += _POSE_V3_EXTRA.pack(p.move_scale, p.vf_aspect, p.view_orientation & 0xFF, 0, 0)
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_pose(data: bytes) -> PosePacket:
    """Parse and validate a pose packet (v1, v2 or v3). Raises PacketError on any problem."""
    if len(data) not in _SIZES.values():
        raise PacketError(f"bad length {len(data)} (expected one of {sorted(_SIZES.values())})")
    body, crc_bytes = data[:-4], data[-4:]
    (crc,) = struct.unpack("<I", crc_bytes)
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        raise PacketError("CRC mismatch")
    fields = _POSE_BODY.unpack(body[: _POSE_BODY.size])
    magic, version, msg_type, tstate, treason = fields[:5]
    if magic != POSE_MAGIC:
        raise PacketError(f"bad magic {magic!r}")
    expected_len = _SIZES.get(version)
    if expected_len is None:
        raise PacketError(f"unsupported protocol version {version}")
    if len(data) != expected_len:
        raise PacketError(f"length {len(data)} does not match version {version}")
    if msg_type != MSG_POSE:
        raise PacketError(f"unsupported message type {msg_type}")
    if tstate not in TRACKING_STATE_NAMES:
        raise PacketError(f"bad tracking state {tstate}")
    if treason not in TRACKING_REASON_NAMES:
        raise PacketError(f"bad tracking reason {treason}")
    session_id, seq, epoch, flags, recenter, rtt_raw, cap_t, send_t = fields[5:13]
    pos = tuple(fields[13:16])
    quat = tuple(fields[16:20])

    for v in (cap_t, send_t, *pos, *quat):
        if not math.isfinite(v):
            raise PacketError("non-finite value")
    if any(abs(v) > MAX_ABS_POSITION_M for v in pos):
        raise PacketError("position out of range")
    norm = math.sqrt(sum(c * c for c in quat))
    if abs(norm - 1.0) > QUAT_NORM_TOLERANCE:
        raise PacketError(f"quaternion not unit length (|q|={norm:.4f})")
    quat = tuple(c / norm for c in quat)
    if send_t < cap_t - 0.001:
        raise PacketError("send time before capture time")

    zoom, jx, jy, mode = 1.0, 0.0, 0.0, MODE_HANDHELD
    if version >= 2:
        zoom, jx, jy, mode, _r1, _r2 = _POSE_V2_EXTRA.unpack_from(body, _POSE_BODY.size)
        if not all(math.isfinite(v) for v in (zoom, jx, jy)):
            raise PacketError("non-finite control value")
        if mode not in MODE_NAMES:
            raise PacketError(f"bad mode {mode}")
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        # clamp the joystick to the unit circle
        mag = math.hypot(jx, jy)
        if mag > 1.0:
            jx, jy = jx / mag, jy / mag

    move_scale, vf_aspect, view_o = 0.0, 0.0, VIEW_LANDSCAPE
    if version >= 3:
        move_scale, vf_aspect, view_o, _r3, _r4 = _POSE_V3_EXTRA.unpack_from(body, _POSE_BODY.size + _POSE_V2_EXTRA.size)
        if not (math.isfinite(move_scale) and math.isfinite(vf_aspect)):
            raise PacketError("non-finite v3 value")
        if view_o not in (VIEW_LANDSCAPE, VIEW_PORTRAIT):
            raise PacketError(f"bad view orientation {view_o}")
        move_scale = 0.0 if move_scale <= 0 else min(max(move_scale, MOVE_SCALE_MIN), MOVE_SCALE_MAX)
        vf_aspect = 0.0 if vf_aspect <= 0 else min(max(vf_aspect, 1.0), 4.0)

    return PosePacket(
        tracking_state=tstate,
        tracking_reason=treason,
        session_id=session_id,
        seq=seq,
        epoch=epoch,
        flags=flags,
        recenter_count=recenter,
        rtt_ms=None if rtt_raw == 0 else rtt_raw / 10.0,
        capture_time=cap_t,
        send_time=send_t,
        position=pos,  # type: ignore[arg-type]
        orientation=quat,  # type: ignore[arg-type]
        zoom=zoom,
        joy_x=jx,
        joy_y=jy,
        mode=mode,
        move_scale=move_scale,
        vf_aspect=vf_aspect,
        view_orientation=view_o,
        version=version,
    )


def encode_ack(seq: int, send_time: float, status: int) -> bytes:
    body = _ACK_BODY.pack(ACK_MAGIC, ACK_VERSION, status, 0, seq & 0xFFFFFFFF, send_time)
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def decode_ack(data: bytes) -> tuple[int, int, float]:
    """Returns (status, seq, send_time). Used by the simulator and tests."""
    if len(data) != _ACK_FULL_SIZE:
        raise PacketError("bad ack length")
    body = data[:-4]
    (crc,) = struct.unpack("<I", data[-4:])
    if zlib.crc32(body) & 0xFFFFFFFF != crc:
        raise PacketError("ack CRC mismatch")
    magic, version, status, _r, seq, send_t = _ACK_BODY.unpack(body)
    if magic != ACK_MAGIC or version != ACK_VERSION:
        raise PacketError("bad ack header")
    return status, seq, send_t


def seq_newer(a: int, b: int) -> bool:
    """True if 32-bit sequence number a is newer than b (wrap-around safe)."""
    return a != b and ((a - b) & 0xFFFFFFFF) < 0x80000000


def seq_gap(new: int, old: int) -> int:
    """Number of packets missing between old and new (0 if consecutive)."""
    return max(0, ((new - old) & 0xFFFFFFFF) - 1)
