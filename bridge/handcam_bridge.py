"""TSS Handcam bridge: AcHandCam receiver's shared memory <-> BeamNG over local UDP.

BeamNG's Lua can't open Windows shared memory (its FFI accepts struct types but refuses C function declarations from
mods, checked in game 2026-11-03), but it has UDP sockets. The AcHandCam receiver (unchanged) talks to the game
through three named session-local file mappings (receiver/handheld_receiver/shm.py):
  Pose   (108 bytes, the receiver writes)  -> sent to the game on 127.0.0.1:47830 whenever it changes
  Status (48 bytes, the game writes)       <- packets from the game on 127.0.0.1:47831 (magic "HMS1")
  View   (48 bytes, the game writes)       <- same port (magic "HMV1"), for the phone's live-view timewarp
Standard library only. Run it next to run_receiver.bat (start order doesn't matter).
"""
from __future__ import annotations

import mmap
import socket
import struct
import sys
import time

POSE_NAME = "AcTools.CSP.Limited.HandheldCam.Pose"
STATUS_NAME = "AcTools.CSP.Limited.HandheldCam.Status"
VIEW_NAME = "AcTools.CSP.Limited.HandheldCam.View"
POSE_SIZE, STATUS_SIZE, VIEW_SIZE = 108, 48, 48
STATUS_MAGIC, VIEW_MAGIC = b"HMS1", b"HMV1"  # little-endian 0x31534D48, 0x31564D48
GAME_ADDR = ("127.0.0.1", 47830)
BRIDGE_ADDR = ("127.0.0.1", 47831)
KEEPALIVE = 0.25  # s: resend the pose even when it hasn't changed, so the game knows the bridge is there


def open_mapping(name: str, size: int) -> mmap.mmap:
    # Opens the receiver's mapping, or creates it first (the receiver then opens the same one): same call as shm.py
    return mmap.mmap(-1, size, tagname="Local\\" + name, access=mmap.ACCESS_WRITE)


def consistent_pose(buf: bytes) -> bool:
    """The receiver writes seqBegin first and seqEnd last: equal means a whole sample."""
    return len(buf) == POSE_SIZE and buf[8:12] == buf[104:108]


class Bridge:
    def __init__(self, game_addr=GAME_ADDR, bridge_addr=BRIDGE_ADDR):
        self.pose = open_mapping(POSE_NAME, POSE_SIZE)
        self.status = open_mapping(STATUS_NAME, STATUS_SIZE)
        self.view = open_mapping(VIEW_NAME, VIEW_SIZE)
        self.game_addr = game_addr
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(bridge_addr)
        self.sock.setblocking(False)
        self.last_sent = b""
        self.last_send_time = 0.0
        self.sent = self.received = 0
        self.last_game = 0.0

    def step(self, now: float) -> None:
        buf = self.pose[0:POSE_SIZE]
        if consistent_pose(buf) and (buf != self.last_sent or now - self.last_send_time > KEEPALIVE):
            try:
                self.sock.sendto(buf, self.game_addr)
                self.sent += 1
            except OSError:
                pass  # the game isn't listening yet (Windows reports it on the next receive)
            self.last_sent, self.last_send_time = buf, now
        for _ in range(64):
            try:
                data, _addr = self.sock.recvfrom(256)
            except (BlockingIOError, ConnectionResetError, OSError):
                break
            if len(data) == STATUS_SIZE and data[0:4] == STATUS_MAGIC:
                self.status[0:STATUS_SIZE] = data
            elif len(data) == VIEW_SIZE and data[0:4] == VIEW_MAGIC:
                self.view[0:VIEW_SIZE] = data
            else:
                continue
            self.received += 1
            self.last_game = now

    def close(self) -> None:
        self.sock.close()
        for m in (self.pose, self.status, self.view):
            m.close()


def main() -> None:
    try:
        b = Bridge()
    except OSError as e:
        sys.exit(f"TSS Handcam bridge: can't start ({e}). Is another copy already running?")
    print("TSS Handcam bridge running: AcHandCam receiver <-> BeamNG (UDP 47830/47831). Close this window to stop.")
    last_print = 0.0
    try:
        while True:
            now = time.monotonic()
            b.step(now)
            if now - last_print > 2:
                last_print = now
                hb = struct.unpack_from("<I", b.pose, 12)[0]
                game = "connected" if now - b.last_game < 1.5 else "not seen (start BeamNG with TSS Handcam)"
                print(f"\rreceiver heartbeat {hb:>10}   game {game:<45}", end="", flush=True)
            time.sleep(0.002)  # ~500 Hz: well above the phone's 60 Hz, ~0 CPU
    except KeyboardInterrupt:
        pass
    finally:
        b.close()


if __name__ == "__main__":
    main()
