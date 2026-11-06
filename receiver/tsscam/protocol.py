"""TSS Cam's own packets, beside AcHandCam's (which stay byte-exact so the stock receiver keeps working).

Every packet: magic (4) | version u8 | flags u8 | payload length u16 | payload | crc32 (zlib, over all before it).
Little-endian. The stock AcHandCam receiver drops these as malformed (it only takes ACHC), which is harmless.

  TSCH  hello            phone -> PC and PC -> phone   JSON  {"app": "tsscam", "ver": "0.1", ...}
  TSCM  command          phone -> PC -> game           JSON  {"seq": 12, "cmd": "door", "args": {"id": "door_FL"}}
  TSCA  command answer   game -> PC -> phone           JSON  {"seq": 12, "ok": true, "err": ""}
  TSCP  capabilities     game -> PC -> phone           JSON  {"rev": 3, "game": "beamng", "car": ..., "controls": [...]}
  TSCS  sticks (~60 Hz)  phone -> PC -> game           4 x f32: lift (-1 down .. 1 up), look_x, look_y, speed (0..1)

JSON payloads may be zlib-compressed (flags bit0), so a car with many doors still fits one Wi-Fi datagram.
"""
from __future__ import annotations

import json
import struct
import zlib
from dataclasses import dataclass

VERSION = 1
HELLO, COMMAND, ANSWER, CAPS, STICKS = b"TSCH", b"TSCM", b"TSCA", b"TSCP", b"TSCS"
MAGICS = (HELLO, COMMAND, ANSWER, CAPS, STICKS)
FLAG_ZLIB = 0x01
MAX_DATAGRAM = 1400  # under a Wi-Fi MTU, no IP fragmentation
_HEAD = struct.Struct("<4sBBH")
_STICKS = struct.Struct("<4f")


class TscError(ValueError):
    pass


@dataclass
class Packet:
    magic: bytes
    payload: bytes
    flags: int = 0

    def json(self) -> dict:
        data = zlib.decompress(self.payload) if self.flags & FLAG_ZLIB else self.payload
        try:
            v = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise TscError(f"bad JSON: {e}") from e
        if not isinstance(v, dict):
            raise TscError("JSON payload is not an object")
        return v

    def sticks(self) -> tuple[float, float, float, float]:
        if self.magic != STICKS or len(self.payload) != _STICKS.size:
            raise TscError("not a sticks packet")
        vals = _STICKS.unpack(self.payload)
        if not all(v == v and abs(v) <= 1.0001 for v in vals):  # finite and in range
            raise TscError("stick value out of range")
        return vals


def is_tsc(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] in MAGICS


def encode(magic: bytes, payload: bytes, flags: int = 0) -> bytes:
    if magic not in MAGICS:
        raise TscError(f"unknown magic {magic!r}")
    body = _HEAD.pack(magic, VERSION, flags, len(payload)) + payload
    return body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_json(magic: bytes, obj: dict) -> bytes:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    pkt = encode(magic, raw)
    if len(pkt) > MAX_DATAGRAM:
        packed = zlib.compress(raw, 9)
        pkt = encode(magic, packed, FLAG_ZLIB)
        if len(pkt) > MAX_DATAGRAM:
            raise TscError(f"{magic.decode()} payload too big ({len(pkt)} bytes compressed)")
    return pkt


def encode_sticks(lift: float, look_x: float, look_y: float, speed: float) -> bytes:
    clamp = lambda v: max(-1.0, min(1.0, float(v)))  # noqa: E731
    return encode(STICKS, _STICKS.pack(clamp(lift), clamp(look_x), clamp(look_y), max(0.0, clamp(speed))))


def decode(data: bytes) -> Packet:
    if len(data) < _HEAD.size + 4:
        raise TscError("too short")
    magic, version, flags, length = _HEAD.unpack_from(data)
    if magic not in MAGICS:
        raise TscError(f"unknown magic {magic!r}")
    if version != VERSION:
        raise TscError(f"unsupported version {version}")
    if len(data) != _HEAD.size + length + 4:
        raise TscError("length mismatch")
    (crc,) = struct.unpack_from("<I", data, len(data) - 4)
    if zlib.crc32(data[:-4]) & 0xFFFFFFFF != crc:
        raise TscError("bad CRC")
    return Packet(magic, data[_HEAD.size:-4], flags)
