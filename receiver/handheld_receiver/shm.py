"""Shared-memory link to the CSP Lua app.

The Lua app opens the same named file mappings with CSP's documented
`ac.readMemoryMappedFile(name, layout)` / `ac.writeMemoryMappedFile(name, layout)`
(acc-lua-sdk common/ac_extras_connectmmf.lua). Names start with
"AcTools.CSP.Limited." because the SDK allows that prefix for every script
type, regardless of whether the script has general I/O permission. CSP's doc
says the name is given "without Local\\", i.e. it lives in the session-local
namespace, which is also where Python's mmap(tagname="Local\\...") creates it.

Layouts are fixed, 4-byte fields only (no padding surprises). The Lua side
declares the same fields as a C struct body; ac/tests/test_layout.lua checks
the offsets with LuaJIT's FFI against these definitions.

Torn-read protection: the writer updates seqBegin first, then the payload,
then seqEnd. The reader reads seqEnd, the payload, then seqBegin and only
accepts the sample when both match.
"""

from __future__ import annotations

import math
import mmap
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

POSE_MAGIC = 0x31434D48  # bytes "HMC1"
STATUS_MAGIC = 0x31534D48  # bytes "HMS1"
POSE_LAYOUT_VERSION = 3  # v2 adds zoom, joystick and mode; v3 view orientation + viewfinder aspect
STATUS_LAYOUT_VERSION = 1
LAYOUT_VERSION = POSE_LAYOUT_VERSION  # kept for older imports

# C struct body used by the Lua app (keep in sync with AcHandCam.lua).
POSE_LAYOUT_C = (
    "uint32_t magic; uint32_t version; uint32_t seqBegin; uint32_t heartbeat; "
    "int32_t outState; uint32_t flags; uint32_t generation; uint32_t trackingState; "
    "float px; float py; float pz; float fx; float fy; float fz; float ux; float uy; float uz; "
    "float latencyMs; float rxHz; float lossPct; "
    "float zoom; float joyX; float joyY; uint32_t mode; "
    "uint32_t viewOrientation; float vfAspect; uint32_t seqEnd;"
)
POSE_STRUCT = struct.Struct("<IIIIiIII15fIIfI")
POSE_SIZE = POSE_STRUCT.size  # 108
OFF_SEQ_BEGIN = 8
OFF_PAYLOAD = 12
OFF_SEQ_END = 104
assert POSE_SIZE == 108

STATUS_LAYOUT_C = (
    "uint32_t magic; uint32_t version; uint32_t heartbeat; uint32_t engaged; "
    "uint32_t grabbed; uint32_t lastGeneration; uint32_t appliedCount; int32_t errorCode; "
    "float fps; float lookSign; float sideSign; uint32_t recenterRequests;"
)
STATUS_STRUCT = struct.Struct("<IIIIIIIi3fI")
STATUS_SIZE = STATUS_STRUCT.size  # 48
assert STATUS_SIZE == 48

# v3: written by the AC app every frame for the live view's timewarp.
VIEW_MAGIC = 0x31564D48  # bytes "HMV1"
VIEW_LAYOUT_VERSION = 2  # v2 (4.1): heartbeat of the rendered pose + the AC app's smoothing as a rotation
VIEW_LAYOUT_C = (
    "uint32_t magic; uint32_t version; uint32_t frame; uint32_t live; "
    "float fovV; float smoothing; uint32_t poseHeartbeat; uint32_t hasRot; "
    "float rx; float ry; float rz; float rw;"
)
VIEW_STRUCT = struct.Struct("<IIII2fII4f")
VIEW_SIZE = VIEW_STRUCT.size  # 48
assert VIEW_SIZE == 48

FLAG_RECORDING = 0x1
FLAG_PLAYBACK = 0x2
FLAG_SOURCE_CONNECTED = 0x4

LUA_ERROR_NAMES = {
    0: "",
    1: "another script is holding the camera",
    2: "receiver data missing",
    3: "camera grab failed",
}


@dataclass
class PoseFrame:
    heartbeat: int
    out_state: int
    flags: int
    generation: int
    tracking_state: int
    position: tuple[float, float, float]
    forward: tuple[float, float, float]
    up: tuple[float, float, float]
    latency_ms: float
    rx_hz: float
    loss_pct: float
    zoom: float = 1.0
    joy_x: float = 0.0
    joy_y: float = 0.0
    mode: int = 0
    view_orientation: int = 0  # 0 landscape, 1 portrait
    vf_aspect: float = 0.0  # viewfinder long/short, 0 = unknown


@dataclass
class LuaStatus:
    heartbeat: int
    engaged: bool
    grabbed: bool
    last_generation: int
    applied_count: int
    error_code: int
    fps: float
    look_sign: float
    side_sign: float
    recenter_requests: int = 0  # incremented by the AC app's Recenter hotkey


@dataclass
class LuaView:
    frame: int
    live: bool  # the game camera follows the phone right now
    fov_v: float  # vertical FOV applied this frame (deg)
    smoothing: float  # render smoothing time constant in use (s)
    # v2: which pose (shared-memory heartbeat) this frame was rendered from,
    # and the rotation from that pose's target to what was actually shown
    # (AC app smoothing, head bob), camera-local (x right, y up, z back).
    pose_heartbeat: int | None = None
    rot: tuple[float, float, float, float] | None = None  # (x, y, z, w)


def _open_mapping(name: str, size: int, fallback_dir: Path | None) -> tuple[mmap.mmap, object | None]:
    """Named session-local mapping on Windows (what CSP opens). A file-backed
    mapping when a directory is given explicitly (tests, diagnostics) or on
    other platforms."""
    if sys.platform == "win32" and fallback_dir is None:
        return mmap.mmap(-1, size, tagname="Local\\" + name, access=mmap.ACCESS_WRITE), None
    d = fallback_dir or Path(tempfile.gettempdir()) / "handheldcam_shm"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    if not path.exists() or path.stat().st_size != size:
        path.write_bytes(b"\0" * size)
    f = open(path, "r+b")
    return mmap.mmap(f.fileno(), size), f


class PoseWriter:
    def __init__(self, name: str, fallback_dir: Path | None = None):
        self.name = name
        self._mm, self._file = _open_mapping(name, POSE_SIZE, fallback_dir)
        self._seq = 0
        self._heartbeat = 0

    def write(self, frame_without_heartbeat: PoseFrame) -> int:
        """Writes the frame; returns the heartbeat it was written with (the AC
        app reports the heartbeat of the pose it rendered, for the live view)."""
        f = frame_without_heartbeat
        self._heartbeat = (self._heartbeat + 1) & 0xFFFFFFFF
        self._seq = (self._seq + 1) & 0xFFFFFFFF
        packed = POSE_STRUCT.pack(
            POSE_MAGIC,
            POSE_LAYOUT_VERSION,
            self._seq,
            self._heartbeat,
            f.out_state,
            f.flags,
            f.generation & 0xFFFFFFFF,
            f.tracking_state,
            *f.position,
            *f.forward,
            *f.up,
            f.latency_ms,
            f.rx_hz,
            f.loss_pct,
            f.zoom,
            f.joy_x,
            f.joy_y,
            f.mode,
            f.view_orientation,
            f.vf_aspect,
            self._seq,
        )
        mm = self._mm
        mm[OFF_SEQ_BEGIN:OFF_PAYLOAD] = packed[OFF_SEQ_BEGIN:OFF_PAYLOAD]
        mm[0:OFF_SEQ_BEGIN] = packed[0:OFF_SEQ_BEGIN]
        mm[OFF_PAYLOAD:OFF_SEQ_END] = packed[OFF_PAYLOAD:OFF_SEQ_END]
        mm[OFF_SEQ_END:POSE_SIZE] = packed[OFF_SEQ_END:POSE_SIZE]
        return self._heartbeat

    def close(self) -> None:
        self._mm.close()
        if self._file:
            self._file.close()  # type: ignore[attr-defined]


def read_pose(buf: bytes) -> PoseFrame | None:
    """Reference reader (mirrors the Lua logic); returns None if torn/invalid."""
    if len(buf) < POSE_SIZE:
        return None
    v = POSE_STRUCT.unpack(buf[:POSE_SIZE])
    magic, version, seq_b, hb, ostate, flags, gen, tstate = v[:8]
    floats = v[8:23]
    mode = v[23]
    view_o = v[24]
    vf_aspect = v[25]
    seq_e = v[26]
    if magic != POSE_MAGIC or version != POSE_LAYOUT_VERSION or seq_b != seq_e:
        return None
    return PoseFrame(
        heartbeat=hb,
        out_state=ostate,
        flags=flags,
        generation=gen,
        tracking_state=tstate,
        position=tuple(floats[0:3]),  # type: ignore[arg-type]
        forward=tuple(floats[3:6]),  # type: ignore[arg-type]
        up=tuple(floats[6:9]),  # type: ignore[arg-type]
        latency_ms=floats[9],
        rx_hz=floats[10],
        loss_pct=floats[11],
        zoom=floats[12],
        joy_x=floats[13],
        joy_y=floats[14],
        mode=mode,
        view_orientation=view_o,
        vf_aspect=vf_aspect,
    )


class StatusReader:
    """Reads the status block the Lua app writes every frame."""

    def __init__(self, name: str, fallback_dir: Path | None = None):
        self._mm, self._file = _open_mapping(name, STATUS_SIZE, fallback_dir)

    def read(self) -> LuaStatus | None:
        v = STATUS_STRUCT.unpack(self._mm[0:STATUS_SIZE])
        if v[0] != STATUS_MAGIC or v[1] != STATUS_LAYOUT_VERSION:
            return None
        return LuaStatus(
            heartbeat=v[2],
            engaged=bool(v[3]),
            grabbed=bool(v[4]),
            last_generation=v[5],
            applied_count=v[6],
            error_code=v[7],
            fps=v[8],
            look_sign=v[9],
            side_sign=v[10],
            recenter_requests=v[11],
        )

    def close(self) -> None:
        self._mm.close()
        if self._file:
            self._file.close()  # type: ignore[attr-defined]


class ViewReader:
    """Reads the AC app's view block (absent with a v2 AC app: read() -> None)."""

    def __init__(self, name: str, fallback_dir: Path | None = None):
        self._mm, self._file = _open_mapping(name, VIEW_SIZE, fallback_dir)

    def read(self) -> LuaView | None:
        v = VIEW_STRUCT.unpack(self._mm[0:VIEW_SIZE])
        if v[0] != VIEW_MAGIC or v[1] not in (1, VIEW_LAYOUT_VERSION):
            return None
        if v[1] == 1:  # AC app 3.x/4.0
            return LuaView(frame=v[2], live=bool(v[3]), fov_v=v[4], smoothing=v[5])
        rot = (v[8], v[9], v[10], v[11]) if v[7] else None
        if rot is not None and not all(math.isfinite(c) for c in rot):
            rot = None
        return LuaView(frame=v[2], live=bool(v[3]), fov_v=v[4], smoothing=v[5], pose_heartbeat=v[6], rot=rot)

    def close(self) -> None:
        self._mm.close()
        if self._file:
            self._file.close()  # type: ignore[attr-defined]


def fallback_path(name: str, fallback_dir: Path | None = None) -> Path:
    d = fallback_dir or Path(tempfile.gettempdir()) / "handheldcam_shm"
    return d / name


__all__ = [
    "PoseWriter",
    "StatusReader",
    "ViewReader",
    "LuaView",
    "VIEW_LAYOUT_C",
    "PoseFrame",
    "LuaStatus",
    "read_pose",
    "POSE_LAYOUT_C",
    "STATUS_LAYOUT_C",
    "fallback_path",
]
