"""BeamNG.drive hook: the former TSS Handcam bridge, inside the receiver.

BeamNG's Lua can't open Windows shared memory (its FFI refuses C function declarations from mods) but has UDP:
  receiver shared memory Pose (108 B)         -> game 127.0.0.1:47830, on change and every 0.25 s
  game Status "HMS1" / View "HMV1" (48 B)     <- 127.0.0.1:47831, written into the receiver's shared memory
  our TSCM commands / TSCS sticks              -> game 47830 (the mod tells them apart by magic)
  the game's TSCP capabilities / TSCA answers  <- 47831, handed to the hub for the phone
"""
from __future__ import annotations

import logging
import mmap
import socket
import threading
import time

from .. import protocol as tsc
from . import GameHook

log = logging.getLogger("tsscam.beamng")

POSE_NAME = "AcTools.CSP.Limited.HandheldCam.Pose"
STATUS_NAME = "AcTools.CSP.Limited.HandheldCam.Status"
VIEW_NAME = "AcTools.CSP.Limited.HandheldCam.View"
POSE_SIZE, STATUS_SIZE, VIEW_SIZE = 108, 48, 48
STATUS_MAGIC, VIEW_MAGIC = b"HMS1", b"HMV1"
GAME_ADDR = ("127.0.0.1", 47830)
HOOK_ADDR = ("127.0.0.1", 47831)
KEEPALIVE = 0.25  # s: resend the pose even when unchanged, so the game knows the receiver is there


def open_mapping(name: str, size: int) -> mmap.mmap:
    # Same session-local mapping the receiver's shm.py opens (creates it if the receiver hasn't yet)
    return mmap.mmap(-1, size, tagname="Local\\" + name, access=mmap.ACCESS_WRITE)


def consistent_pose(buf: bytes) -> bool:
    """The receiver writes seqBegin first and seqEnd last: equal means a whole sample."""
    return len(buf) == POSE_SIZE and buf[8:12] == buf[104:108]


class BeamNGHook(GameHook):
    key = "beamng"
    title = "BeamNG.drive"

    def __init__(self, on_message, game_addr=GAME_ADDR, hook_addr=HOOK_ADDR, mappings=None):
        super().__init__(on_message)
        self.game_addr, self.hook_addr = game_addr, hook_addr
        self._mappings = mappings  # tests pass plain bytearrays
        self.sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_sent, self.last_send_time = b"", 0.0
        self.sent = self.received = 0
        self.error = ""

    def start(self) -> None:
        if self._thread:
            return
        try:
            if self._mappings is None:
                self.pose = open_mapping(POSE_NAME, POSE_SIZE)
                self.status = open_mapping(STATUS_NAME, STATUS_SIZE)
                self.view = open_mapping(VIEW_NAME, VIEW_SIZE)
            else:
                self.pose, self.status, self.view = self._mappings
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.bind(self.hook_addr)
            s.settimeout(0.002)
            self.sock = s
        except OSError as e:
            self.error = f"port {self.hook_addr[1]} busy or shared memory unavailable ({e}); is the old bridge still running?"
            log.error(self.error)
            return
        self.error = ""
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="beamng-hook", daemon=True)
        self._thread.start()
        log.info("BeamNG hook on %s:%d -> game %s:%d", *self.hook_addr, *self.game_addr)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.sock:
            self.sock.close()
            self.sock = None

    def forward(self, packet: bytes) -> bool:
        if not self.sock:
            return False
        try:
            self.sock.sendto(packet, self.game_addr)
            return True
        except OSError:
            return False

    def step(self, now: float) -> None:
        buf = bytes(self.pose[0:POSE_SIZE])
        if consistent_pose(buf) and (buf != self.last_sent or now - self.last_send_time > KEEPALIVE):
            self.forward(buf)
            self.sent += 1
            self.last_sent, self.last_send_time = buf, now
        for _ in range(64):
            try:
                data, _addr = self.sock.recvfrom(4096)
            except (socket.timeout, BlockingIOError, ConnectionResetError):
                break
            except OSError:
                break
            if len(data) == STATUS_SIZE and data[0:4] == STATUS_MAGIC:
                self.status[0:STATUS_SIZE] = data
            elif len(data) == VIEW_SIZE and data[0:4] == VIEW_MAGIC:
                self.view[0:VIEW_SIZE] = data
            elif tsc.is_tsc(data):
                self.received += 1
                self.last_game = now  # before the hub sees it: its caps say "connected"
                self.on_message(data)
                continue
            else:
                continue
            self.received += 1
            self.last_game = now

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.step(time.monotonic())  # the socket timeout (2 ms) paces it: ~500 Hz, ~0 CPU
