"""Live view: capture the PC screen, hardware-encode H.264, stream it to the phone.

Pipeline (FFmpeg backend, the default):

    ddagrab (Desktop Duplication, GPU)  ->  scale_d3d11 (GPU)  ->  h264_amf
    (AMD VCN, ultra-low-latency, no B-frames, async_depth 1)  ->  FLV on stdout

FLV is used only as framing on the pipe: every tag carries one encoded
frame with its length and its capture timestamp, so a frame can be sent the
moment it is complete (raw Annex-B would need the *next* frame's start code
to know where this one ends, costing a whole frame of latency).

The optional native backend (native/handheld_capture.exe) writes "ACHF"
frames instead, carrying the exact QueryPerformanceCounter capture time.

Each encoded frame is cut into UDP datagrams ("ACHV", below) and sent to the
phone's address. The header tells the phone which of *its own* ARKit poses
the picture was rendered from, so it can measure the true motion-to-screen
latency and timewarp the picture to its current orientation.

Video datagram (PC -> phone), little-endian, 90-byte header + payload
(version 3; v2 ended after pc_capture_time, v1 also lacked pc_capture_time):

  off size type  field
    0   4  char  magic        b"ACHV"
    4   1  u8    version      3
    5   1  u8    flags        bit0 keyframe, bit1 pose_time valid, bit2 warp allowed
    6   2  u16   chunk_index
    8   2  u16   chunk_count
   10   4  u32   frame_id     increments per frame (wraps)
   14   8  f64   pose_time    phone clock (s): ARKit capture time of the pose shown
   22   4  f32   pc_age_ms    PC: capture -> send
   26   4  f32   fov_v        game vertical FOV of the frame (deg), 0 = unknown
   30  12  f32x3 gains        yaw, pitch, roll warp gains
   42   2  u16   width
   44   2  u16   height
   46   2  u16   payload_len  bytes of payload in this datagram
   48   2  u16   reserved
   50   8  f64   pc_capture_time  PC clock (s) when the frame was captured; only
                              differences matter (the phone paces display with it)
   58  16  f32x4 filter_lag   (x, y, z, w) phone-camera-local rotation from the raw
                              pose at pose_time to the receiver's smoothed one
   74  16  f32x4 render_rot   (x, y, z, w) game-camera-local rotation from that
                              pose's target to what AC actually showed (AC app
                              smoothing); identity when unknown
   90   …        payload      a slice of the frame: H.264 NAL units, each
                              prefixed with a 4-byte big-endian length (AVCC).
                              Keyframes always start with SPS and PPS.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

log = logging.getLogger("handheldcam.liveview")

VIDEO_MAGIC = b"ACHV"
VIDEO_VERSION = 3
VIDEO_HEADER = struct.Struct("<4sBBHHIdff3fHHHHd4f4f")
VIDEO_HEADER_SIZE = VIDEO_HEADER.size
assert VIDEO_HEADER_SIZE == 90
_IDQ = (0.0, 0.0, 0.0, 1.0)

VFLAG_KEY = 0x01
VFLAG_POSE = 0x02
VFLAG_WARP = 0x04

NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8
NAL_AUD = 9


# ------------------------------------------------------------------ H.264


def nal_type(nal: bytes) -> int:
    return nal[0] & 0x1F if nal else -1


def split_annexb(buf: bytes) -> list[bytes]:
    """Split an Annex-B byte stream (00 00 01 / 00 00 00 01 start codes)."""
    out: list[bytes] = []
    n = len(buf)
    i = 0
    starts: list[tuple[int, int]] = []  # (start code pos, payload pos)
    while i + 3 <= n:
        if buf[i] == 0 and buf[i + 1] == 0:
            if buf[i + 2] == 1:
                starts.append((i, i + 3))
                i += 3
                continue
            if i + 4 <= n and buf[i + 2] == 0 and buf[i + 3] == 1:
                starts.append((i, i + 4))
                i += 4
                continue
        i += 1
    for k, (_, p) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else n
        nal = buf[p:end]
        # trailing zero bytes belong to the next start code (zero_byte)
        while nal and nal[-1] == 0 and end != n:
            nal = nal[:-1]
        if nal:
            out.append(bytes(nal))
    return out


def split_lengths(buf: bytes, length_size: int = 4) -> list[bytes]:
    """Split length-prefixed (AVCC) NAL units. Raises ValueError if malformed."""
    out = []
    i = 0
    n = len(buf)
    while i < n:
        if i + length_size > n:
            raise ValueError("truncated NAL length")
        ln = int.from_bytes(buf[i : i + length_size], "big")
        i += length_size
        if ln == 0 or i + ln > n:
            raise ValueError("bad NAL length")
        out.append(bytes(buf[i : i + ln]))
        i += ln
    return out


def to_avcc(nals: list[bytes]) -> bytes:
    return b"".join(len(x).to_bytes(4, "big") + x for x in nals)


def parse_avcc_config(rec: bytes) -> tuple[int, list[bytes], list[bytes]]:
    """AVCDecoderConfigurationRecord -> (length_size, [sps], [pps])."""
    if len(rec) < 7 or rec[0] != 1:
        raise ValueError("not an AVC configuration record")
    length_size = (rec[4] & 0x03) + 1
    n_sps = rec[5] & 0x1F
    i = 6
    sps, pps = [], []
    for _ in range(n_sps):
        ln = int.from_bytes(rec[i : i + 2], "big")
        sps.append(bytes(rec[i + 2 : i + 2 + ln]))
        i += 2 + ln
    n_pps = rec[i]
    i += 1
    for _ in range(n_pps):
        ln = int.from_bytes(rec[i : i + 2], "big")
        pps.append(bytes(rec[i + 2 : i + 2 + ln]))
        i += 2 + ln
    if any(not s for s in sps + pps):
        raise ValueError("truncated AVC configuration record")
    return length_size, sps, pps


@dataclass
class EncodedFrame:
    nals: list[bytes]
    key: bool
    t_rx: float  # receiver clock when the frame arrived from the encoder
    t_cap: float  # receiver clock estimate of when the screen was captured
    pts_s: float = 0.0


# ------------------------------------------------------------- FLV framing


class FlvParser:
    """Incremental FLV reader. feed() returns ('config', ls, sps, pps) and
    ('frame', pts_ms, key, nals) events. Handles legacy AVC tags and the
    "enhanced FLV" header newer FFmpeg builds may use."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._header_done = False
        self.length_size = 4

    def feed(self, data: bytes) -> list[tuple]:
        self._buf += data
        events: list[tuple] = []
        b = self._buf
        if not self._header_done:
            if len(b) < 13:
                return events
            if bytes(b[0:3]) != b"FLV":
                raise ValueError("encoder output is not FLV")
            hsize = int.from_bytes(b[5:9], "big")
            if len(b) < hsize + 4:
                return events
            del b[: hsize + 4]  # header + PreviousTagSize0
            self._header_done = True
        while len(b) >= 11:
            tag_type = b[0] & 0x1F
            size = int.from_bytes(b[1:4], "big")
            total = 11 + size + 4
            if len(b) < total:
                break
            ts = int.from_bytes(b[4:7], "big") | (b[7] << 24)
            body = bytes(b[11 : 11 + size])
            del b[:total]
            if tag_type == 9 and body:
                ev = self._video(ts, body)
                if ev is not None:
                    events.append(ev)
        return events

    def _video(self, ts: int, body: bytes):
        b0 = body[0]
        if b0 & 0x80:  # enhanced FLV: ExVideoTagHeader
            frame_type = (b0 >> 4) & 0x07
            pkt_type = b0 & 0x0F
            if body[1:5] != b"avc1":
                return None
            rest = body[5:]
            if pkt_type == 0:  # SequenceStart
                return self._config(rest)
            if pkt_type == 1:  # CodedFrames (with composition time)
                rest = rest[3:]
            elif pkt_type != 3:  # 3 = CodedFramesX
                return None
            nals = split_lengths(rest, self.length_size)
            return self._frame(ts, frame_type == 1, nals)
        frame_type = b0 >> 4
        codec = b0 & 0x0F
        if codec != 7 or len(body) < 5:
            return None
        pkt_type = body[1]
        if pkt_type == 0:
            return self._config(body[5:])
        if pkt_type != 1:
            return None
        return self._frame(ts, frame_type == 1, split_lengths(body[5:], self.length_size))

    def _config(self, rec: bytes):
        if not rec:
            return None
        ls, sps, pps = parse_avcc_config(rec)
        self.length_size = ls
        return ("config", ls, sps, pps)

    @staticmethod
    def _frame(ts: int, key: bool, nals: list[bytes]):
        key = key or any(nal_type(n) == NAL_IDR for n in nals)
        return ("frame", ts, key, nals)


NATIVE_MAGIC = b"ACHF"
NATIVE_HEADER = struct.Struct("<4sBBHIIdd")  # magic, version, flags, reserved, size, reserved, t_cap, t_enc
assert NATIVE_HEADER.size == 32


class NativeParser:
    """Frames from native/handheld_capture.exe: 32-byte header + Annex-B.
    Times are QueryPerformanceCounter seconds = Python's time.perf_counter()
    on Windows, so the capture time needs no estimation."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple]:
        self._buf += data
        out = []
        b = self._buf
        while len(b) >= NATIVE_HEADER.size:
            magic, ver, flags, _r, size, _r2, t_cap, t_enc = NATIVE_HEADER.unpack_from(b, 0)
            if magic != NATIVE_MAGIC or ver != 1:
                raise ValueError("native capture output out of sync")
            if len(b) < NATIVE_HEADER.size + size:
                break
            payload = bytes(b[NATIVE_HEADER.size : NATIVE_HEADER.size + size])
            del b[: NATIVE_HEADER.size + size]
            nals = split_annexb(payload)
            key = bool(flags & 1) or any(nal_type(n) == NAL_IDR for n in nals)
            out.append(("native", t_cap, t_enc, key, nals))
        return out


class CaptureClock:
    """Maps encoder timestamps (seconds since the first frame, from the
    capture source) onto the receiver's clock.

    offset = min over the last few seconds of (arrival - pts): the fastest
    frame. Its capture->arrival time is `min_transit` (a setting), so
    capture = pts + offset - min_transit. This removes encode/pipe jitter;
    only the constant min_transit is assumed."""

    def __init__(self, window_s: float = 5.0) -> None:
        self.window = window_s
        self._d: deque[tuple[float, float]] = deque()

    def reset(self) -> None:
        self._d.clear()

    def capture_time(self, pts_s: float, t_rx: float, min_transit_s: float) -> float:
        diff = t_rx - pts_s
        d = self._d
        while d and d[-1][1] >= diff:  # monotonic deque for a sliding minimum
            d.pop()
        d.append((t_rx, diff))
        while d and d[0][0] < t_rx - self.window:
            d.popleft()
        return pts_s + d[0][1] - min_transit_s


# ------------------------------------------------------------ packetising


@dataclass
class VideoHeader:
    flags: int
    chunk_index: int
    chunk_count: int
    frame_id: int
    pose_time: float
    pc_age_ms: float
    fov_v: float
    gains: tuple[float, float, float]
    width: int
    height: int
    payload_len: int
    pc_capture_time: float = 0.0
    filter_lag: tuple[float, float, float, float] = _IDQ
    render_rot: tuple[float, float, float, float] = _IDQ


def packetize(
    frame_id: int,
    payload: bytes,
    *,
    key: bool,
    pose_time: float | None,
    pc_age_ms: float,
    fov_v: float,
    gains: tuple[float, float, float],
    width: int,
    height: int,
    warp: bool,
    max_chunk: int = 1200,
    pc_capture_time: float = 0.0,
    filter_lag: tuple[float, float, float, float] = _IDQ,
    render_rot: tuple[float, float, float, float] = _IDQ,
) -> list[bytes]:
    n = max(1, (len(payload) + max_chunk - 1) // max_chunk)
    if n > 0xFFFF:
        raise ValueError("frame too large")
    flags = (VFLAG_KEY if key else 0) | (VFLAG_POSE if pose_time is not None else 0)
    if warp and pose_time is not None and fov_v > 0:
        flags |= VFLAG_WARP
    out = []
    for i in range(n):
        part = payload[i * max_chunk : (i + 1) * max_chunk]
        out.append(
            VIDEO_HEADER.pack(
                VIDEO_MAGIC,
                VIDEO_VERSION,
                flags,
                i,
                n,
                frame_id & 0xFFFFFFFF,
                pose_time if pose_time is not None else 0.0,
                pc_age_ms,
                fov_v,
                *gains,
                width & 0xFFFF,
                height & 0xFFFF,
                len(part),
                0,
                pc_capture_time,
                *filter_lag,
                *render_rot,
            )
            + part
        )
    return out


def parse_chunk(data: bytes) -> tuple[VideoHeader, bytes]:
    if len(data) < VIDEO_HEADER_SIZE:
        raise ValueError("short video datagram")
    v = VIDEO_HEADER.unpack_from(data, 0)
    if v[0] != VIDEO_MAGIC or v[1] != VIDEO_VERSION:
        raise ValueError("not a video datagram")
    h = VideoHeader(v[2], v[3], v[4], v[5], v[6], v[7], v[8], (v[9], v[10], v[11]), v[12], v[13], v[14], v[16],
                    (v[17], v[18], v[19], v[20]), (v[21], v[22], v[23], v[24]))
    payload = data[VIDEO_HEADER_SIZE : VIDEO_HEADER_SIZE + h.payload_len]
    if len(payload) != h.payload_len or h.chunk_index >= h.chunk_count:
        raise ValueError("bad video datagram")
    return h, payload


class FrameAssembler:
    """Reference reassembler (the phone's FrameAssembler.swift mirrors it).
    Returns complete frames in order; drops a frame as soon as a newer one
    completes or after `timeout_s`, and asks for a keyframe after any loss
    (P-frames after a gap would show corruption)."""

    def __init__(self, timeout_s: float = 0.25) -> None:
        self.timeout = timeout_s
        self._parts: dict[int, tuple[float, VideoHeader, list[bytes | None]]] = {}
        self._last_done: int | None = None
        self.need_key = True
        self.dropped = 0

    def add(self, data: bytes, now: float) -> tuple[VideoHeader, bytes] | None:
        h, payload = parse_chunk(data)
        if self._last_done is not None and ((h.frame_id - self._last_done) & 0xFFFFFFFF) > 0x7FFFFFFF:
            return None  # older than what we already showed
        if self._last_done is not None and h.frame_id == self._last_done:
            return None
        entry = self._parts.get(h.frame_id)
        if entry is None:
            entry = (now, h, [None] * h.chunk_count)
            self._parts[h.frame_id] = entry
        parts = entry[2]
        if len(parts) != h.chunk_count:
            return None
        parts[h.chunk_index] = payload
        # expire stale partial frames
        for fid in [f for f, e in self._parts.items() if now - e[0] > self.timeout]:
            del self._parts[fid]
            self.dropped += 1
            self.need_key = True
        if any(p is None for p in parts) or h.frame_id not in self._parts:
            return None  # incomplete, or it just expired
        del self._parts[h.frame_id]
        # anything older that is still incomplete is lost now
        for fid in [f for f in self._parts if ((h.frame_id - f) & 0xFFFFFFFF) < 0x7FFFFFFF]:
            del self._parts[fid]
            self.dropped += 1
            self.need_key = True
        gap = self._last_done is not None and ((h.frame_id - self._last_done) & 0xFFFFFFFF) != 1
        self._last_done = h.frame_id
        if gap:
            self.need_key = True
        if self.need_key and not (h.flags & VFLAG_KEY):
            self.dropped += 1
            return None
        if h.flags & VFLAG_KEY:
            self.need_key = False
        return h, b"".join(parts)  # type: ignore[arg-type]


# ------------------------------------------------------------ FFmpeg setup

ENCODERS = ("h264_amf", "h264_nvenc", "h264_qsv", "libx264")
GPU_FRAMES_OK = {"h264_amf", "h264_nvenc"}  # take D3D11 frames from ddagrab directly


def find_ffmpeg(configured: str = "") -> str | None:
    cands: list[str] = []
    if configured:
        cands.append(configured)
    w = shutil.which("ffmpeg")
    if w:
        cands.append(w)
    here = Path(__file__).resolve().parent.parent
    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    cands += [str(here / "ffmpeg" / "bin" / exe), str(here / "ffmpeg" / exe)]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(os.path.join(la, "Microsoft", "WinGet", "Links", "ffmpeg.exe"))
        cands += sorted(glob.glob(os.path.join(la, "Microsoft", "WinGet", "Packages", "Gyan.FFmpeg*", "*", "bin", "ffmpeg.exe")), reverse=True)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


LEVEL_NAMES = {
    0: "all low-latency options, GPU scaling",
    1: "basic options, GPU scaling",
    2: "all low-latency options, CPU scaling",
    3: "basic options, CPU scaling",
    4: "all low-latency options, GPU, full screen resolution",
    5: "all low-latency options, AMF GPU scaling",
    6: "basic options, AMF GPU scaling",
}


def levels_for(encoder: str) -> tuple[int, ...]:
    """Option sets to try, best first. Older FFmpeg builds lack some options
    (async_depth, dup_frames, ...); scale_d3d11 (GPU scaling) is new and does
    not work on every setup. Without it, encoding the full-resolution frames
    straight from the GPU beats copying every frame to the CPU to shrink it."""
    if encoder == "h264_amf":
        # AMD: AMF's own scaler (vpp_amf) first - scale_d3d11 could not create
        # its encoder-bound textures on an RX 7700 XT (E_INVALIDARG), and AMF
        # rejected full-resolution BGRA frames (error 18).
        return (5, 6, 0, 1, 2, 3)
    return (0, 1, 4, 2, 3) if encoder in GPU_FRAMES_OK else (2, 3)


def ffmpeg_command(ffmpeg: str, s, encoder: str, level: int) -> list[str]:
    """level: see LEVEL_NAMES. Even levels use every low-latency option, odd
    levels only long-standing ones; 0/1 keep frames on the GPU (scale_d3d11)."""
    full = level in (0, 2, 4, 5)
    amf_scale = level in (5, 6) and encoder == "h264_amf"
    gpu_scale = level < 2 and encoder in GPU_FRAMES_OK
    gpu_native = level == 4 and encoder in GPU_FRAMES_OK
    fps = max(10, min(240, int(s.fps)))
    gop = max(1, int(round(s.keyframe_interval_s * fps)))
    h = max(144, int(s.height)) // 2 * 2
    br = f"{max(0.5, float(s.bitrate_mbps)):g}M"
    # small rate-control buffer (0.2 s): even frame sizes = no long bursts on Wi-Fi
    vbv = f"{max(0.1, float(s.bitrate_mbps) * 0.2):g}M"
    cpu_fmt = "yuv420p" if encoder == "libx264" else "nv12"
    if getattr(s, "capture", "desktop") == "testpattern":
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-re", "-f", "lavfi",
               "-i", f"testsrc2=size={h * 16 // 9 // 2 * 2}x{h}:rate={fps}"]
        cmd += ["-vf", f"format={cpu_fmt}"]
    else:
        src = f"ddagrab=output_idx={int(s.monitor)}:framerate={fps}:draw_mouse={1 if s.draw_mouse else 0}"
        if full:
            src += ":dup_frames=0"
        cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-fflags", "nobuffer", "-f", "lavfi", "-i", src]
        if amf_scale:
            # AMF converts + scales on the GPU and hands AMF surfaces to the encoder
            cmd += ["-vf", f"vpp_amf=w=-2:h={h}:format=nv12"]
        elif gpu_native:
            pass  # D3D11 frames go straight to the encoder at the screen's resolution
        elif gpu_scale:
            # scale_d3d11 needs an explicit output format (NV12/P010) and, unlike
            # "scale", doesn't understand width=-2 (it became 4294967294 px), so
            # the width is spelled out: keep the aspect ratio, round to even.
            cmd += ["-vf", f"scale_d3d11=width=trunc(iw*{h}/ih/2)*2:height={h}:format=nv12"]
        else:
            # fast_bilinear: the cheapest good-enough downscale (this runs on
            # the CPU for every frame, so it is part of the latency)
            cmd += ["-vf", f"hwdownload,format=bgra,scale=-2:{h}:flags=fast_bilinear,format={cpu_fmt}"]
    # Keyframes on a clock, forced by FFmpeg itself: AMF's ultra-low-latency
    # mode was seen to ignore -g and send only the first IDR, so one lost
    # packet froze the phone's picture for good.
    ki = max(0.1, float(s.keyframe_interval_s))
    cmd += ["-force_key_frames", f"expr:gte(t,n_forced*{ki:g})"]
    cmd += ["-c:v", encoder, "-b:v", br, "-maxrate", br, "-g", str(gop), "-bf", "0"]
    if encoder == "h264_amf":
        cmd += ["-usage", "ultralowlatency", "-quality", "speed", "-rc", "cbr"]
        if full:
            # max_au_size: no single frame above 80 ms worth of bits, so keyframes
            # don't arrive as one huge burst that Wi-Fi / the phone partly drops
            au = int(float(s.bitrate_mbps) * 1e6 * 0.08)
            cmd += ["-latency", "1", "-async_depth", "1", "-header_spacing", str(gop), "-bufsize", vbv,
                    "-forced_idr", "1", "-max_au_size", str(au)]
    elif encoder == "h264_nvenc":
        cmd += ["-preset", "p1", "-tune", "ull", "-rc", "cbr", "-zerolatency", "1"]
        if full:
            cmd += ["-delay", "0", "-bufsize", vbv, "-forced-idr", "1"]
    elif encoder == "h264_qsv":
        cmd += ["-preset", "veryfast"]
        if full:
            cmd += ["-async_depth", "1", "-low_power", "1"]
    else:
        cmd += ["-preset", "ultrafast", "-tune", "zerolatency", "-bufsize", vbv]
    cmd += ["-fps_mode", "passthrough", "-flush_packets", "1", "-f", "flv", "pipe:1"]
    return cmd


def native_command(exe: str, s) -> list[str]:
    return [
        exe,
        "--monitor", str(int(s.monitor)),
        "--height", str(int(s.height)),
        "--fps", str(int(s.fps)),
        "--bitrate", f"{float(s.bitrate_mbps):g}",
        "--gop", str(max(1, int(round(s.keyframe_interval_s * s.fps)))),
    ] + (["--mouse"] if s.draw_mouse else [])


@dataclass
class LiveStats:
    running: bool = False
    backend: str = ""
    encoder: str = ""
    fps: float = 0.0
    kbps: float = 0.0
    frames: int = 0
    keyframes: int = 0
    width: int = 0
    height: int = 0
    capture_to_send_ms: float | None = None
    restarts: int = 0
    keyframe_restarts: int = 0
    seconds_since_keyframe: float | None = None
    error: str = ""
    log_tail: list[str] = field(default_factory=list)


class LiveView:
    """Runs the encoder process while a phone wants video and calls
    on_frame(EncodedFrame) from its reader thread for every encoded frame."""

    START_GRACE_S = 4.0  # an encoder that dies sooner without a frame is skipped
    IDLE_STOP_S = 3.0

    def __init__(self, settings, on_frame: Callable[[EncodedFrame], None], popen=subprocess.Popen,
                 cache_path: Path | None = None):
        self.s = settings
        self.cache_path = cache_path or Path(__file__).resolve().parent.parent / "liveview_cache.json"
        self.on_frame = on_frame
        self._popen = popen
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._threads: list[threading.Thread] = []
        self._wanted_until = 0.0
        self._ladder: list[tuple[str, int]] = []
        self._ladder_pos = 0
        self._started_at = 0.0
        self._got_frame = False
        self._next_try = 0.0
        self._clock = CaptureClock()
        self._sps: list[bytes] = []
        self._pps: list[bytes] = []
        self._stats = LiveStats()
        self._tail: deque[str] = deque(maxlen=12)
        self._rate: deque[tuple[float, int]] = deque()
        self._age_avg: float | None = None
        self._stop_all = False
        self._last_key_at = 0.0
        self._key_wanted_since: float | None = None
        self._last_key_restart = 0.0

    # -------------------------------------------------------------- control
    def want(self, now: float) -> None:
        """Called whenever the phone asks for video (flag in its pose packets)."""
        self._wanted_until = now + self.IDLE_STOP_S

    KEY_REQUEST_AFTER_S = 1.5  # phone stuck without a keyframe this long ...
    KEY_RESTART_EVERY_S = 5.0  # ... restarts the encoder (a new stream starts with one)

    def request_keyframe(self, now: float, waiting: bool) -> None:
        """The phone reports (in every pose packet) whether it is waiting for
        a keyframe. FFmpeg can't be asked for one while running, so if the
        phone stays stuck and no keyframe went out meanwhile, restart it."""
        with self._lock:
            if not waiting:
                self._key_wanted_since = None
                return
            if self._key_wanted_since is None:
                self._key_wanted_since = now
                return
            if (self._proc is not None and self._got_frame
                    and now - self._key_wanted_since > self.KEY_REQUEST_AFTER_S
                    and now - self._last_key_at > self.KEY_REQUEST_AFTER_S
                    and now - self._last_key_restart > self.KEY_RESTART_EVERY_S):
                log.warning("live view: phone waiting for a keyframe; restarting the encoder")
                self._last_key_restart = now
                self._key_wanted_since = None
                self._stats.keyframe_restarts += 1
                self._kill()
                self._next_try = now

    def poll(self, now: float) -> None:
        """Call a few times a second: starts/stops/restarts the encoder."""
        with self._lock:
            wanted = self.s.enabled and now < self._wanted_until and not self._stop_all
            proc = self._proc
            if proc is not None and proc.poll() is not None:
                self._on_exit(now, proc.returncode)
                proc = None
            if wanted and proc is None and now >= self._next_try:
                self._start(now)
            elif not wanted and proc is not None:
                self._kill()
                self._stats.running = False

    def stop(self) -> None:
        with self._lock:
            self._stop_all = True
            self._kill()
        for t in self._threads:
            t.join(timeout=1.0)

    def settings_changed(self) -> None:
        """Restart with new settings (and a fresh encoder search)."""
        with self._lock:
            self._kill()
            self._ladder = []
            self._next_try = 0.0

    def stats(self) -> LiveStats:
        with self._lock:
            st = LiveStats(**{k: getattr(self._stats, k) for k in self._stats.__dataclass_fields__})
            st.log_tail = list(self._tail)
            now = time.perf_counter()
            while self._rate and self._rate[0][0] < now - 1.0:
                self._rate.popleft()
            st.fps = float(len(self._rate))
            st.kbps = sum(b for _, b in self._rate) * 8 / 1000.0
            st.capture_to_send_ms = self._age_avg
            st.seconds_since_keyframe = (now - self._last_key_at) if self._last_key_at > 0 and st.running else None
            return st

    def note_send_age(self, age_ms: float) -> None:
        a = self._age_avg
        self._age_avg = age_ms if a is None else a + (age_ms - a) * 0.05

    # ------------------------------------------------------------- internal
    def _build_ladder(self) -> list[tuple[str, int]]:
        if self.s.backend == "native":
            return [("native", 0)]
        encs = list(ENCODERS) if self.s.encoder == "auto" else [self.s.encoder]
        ladder = [(e, lvl) for e in encs for lvl in levels_for(e)]
        # Start with what worked last time on this PC (saves several seconds
        # of failed attempts every time GAME is switched on).
        known = self._load_cache()
        if known in ladder:
            ladder.remove(known)
            ladder.insert(0, known)
        return ladder

    def _cache_key(self) -> str:
        return f"{find_ffmpeg(self.s.ffmpeg_path)}|{getattr(self.s, 'capture', 'desktop')}"

    def _load_cache(self) -> tuple[str, int] | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if data.get("key") == self._cache_key():
                return (str(data["encoder"]), int(data["level"]))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def _save_cache(self, enc: str, level: int) -> None:
        try:
            self.cache_path.write_text(json.dumps({"key": self._cache_key(), "encoder": enc, "level": level}),
                                       encoding="utf-8")
        except OSError:
            pass

    def _start(self, now: float) -> None:
        if not self._ladder:
            self._ladder = self._build_ladder()
            self._ladder_pos = 0
        if self._ladder_pos >= len(self._ladder):
            self._stats.error = "no working encoder; see the log lines below. Will retry in 10 s."
            self._ladder_pos = 0
            self._next_try = now + 10.0
            return
        enc, level = self._ladder[self._ladder_pos]
        if enc == "native":
            exe = self.s.native_path or str(Path(__file__).resolve().parent.parent / "native" / "handheld_capture.exe")
            if not os.path.isfile(exe):
                self._stats.error = f"native capture program not found: {exe}"
                self._next_try = now + 5.0
                return
            cmd = native_command(exe, self.s)
            parser_kind = "native"
        else:
            ff = find_ffmpeg(self.s.ffmpeg_path)
            if ff is None:
                self._stats.error = "FFmpeg not found. Install it: winget install Gyan.FFmpeg (then restart the receiver)"
                self._next_try = now + 5.0
                return
            cmd = ffmpeg_command(ff, self.s, enc, level)
            parser_kind = "flv"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = self._popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, creationflags=flags)
        except OSError as e:
            self._stats.error = f"cannot start encoder: {e}"
            self._ladder_pos += 1
            self._next_try = now + 1.0
            return
        log.info("live view: started %s (%s, %s)", parser_kind, enc,
                 "native" if enc == "native" else LEVEL_NAMES.get(level, str(level)))
        self._proc = proc
        self._started_at = now
        self._got_frame = False
        self._clock.reset()
        self._sps, self._pps = [], []
        self._stats.running = True
        self._stats.backend = parser_kind
        self._stats.encoder = enc
        self._stats.error = ""
        self._tail.append("$ " + " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
        rt = threading.Thread(target=self._read_stdout, args=(proc, parser_kind), name="liveview-out", daemon=True)
        et = threading.Thread(target=self._read_stderr, args=(proc,), name="liveview-err", daemon=True)
        rt.start()
        et.start()
        self._threads = [rt, et]

    def _kill(self) -> None:
        p = self._proc
        self._proc = None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except Exception:  # pragma: no cover
                try:
                    p.kill()
                except Exception:
                    pass

    def _on_exit(self, now: float, code: int) -> None:
        self._proc = None
        self._stats.running = False
        self._stats.restarts += 1
        if not self._got_frame and now - self._started_at < self.START_GRACE_S:
            self._ladder_pos += 1  # this encoder/option set doesn't work here
            self._next_try = now
            self._stats.error = f"encoder exited (code {code}); trying the next option"
        else:
            self._next_try = now + 2.0
            self._stats.error = f"encoder stopped (code {code}); restarting"
        log.warning("live view: %s", self._stats.error)

    def _read_stderr(self, proc) -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    with self._lock:
                        self._tail.append(line)
                    log.debug("ffmpeg: %s", line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stderr.close()
            except OSError:
                pass

    def _read_stdout(self, proc, kind: str) -> None:
        try:
            self._read_frames(proc, kind)
        finally:
            try:
                proc.stdout.close()
            except OSError:
                pass

    def _read_frames(self, proc, kind: str) -> None:
        parser = NativeParser() if kind == "native" else FlvParser()
        fd = proc.stdout.fileno()
        min_transit = max(0.0, self.s.encode_min_ms) / 1000.0
        while True:
            try:
                data = os.read(fd, 1 << 16)
            except OSError:
                break
            if not data:
                break
            t_rx = time.perf_counter()
            try:
                events = parser.feed(data)
            except ValueError as e:
                with self._lock:
                    self._stats.error = f"bad encoder output: {e}"
                break
            for ev in events:
                if ev[0] == "config":
                    self._sps, self._pps = ev[2], ev[3]
                    continue
                if ev[0] == "frame":
                    _, pts_ms, key, nals = ev
                    t_cap = self._clock.capture_time(pts_ms / 1000.0, t_rx, min_transit)
                    frame = EncodedFrame(self._with_params(nals, key), key, t_rx, t_cap, pts_ms / 1000.0)
                else:
                    _, t_cap, _t_enc, key, nals = ev
                    frame = EncodedFrame(self._with_params(nals, key), key, t_rx, t_cap)
                with self._lock:
                    if not self._got_frame and 0 <= self._ladder_pos < len(self._ladder):
                        enc_lvl = self._ladder[self._ladder_pos]
                        if enc_lvl[0] != "native":
                            self._save_cache(*enc_lvl)
                    self._got_frame = True
                    self._ladder_pos = self._ladder_pos  # keep the working option set
                    self._stats.frames += 1
                    self._stats.keyframes += int(key)
                    if key:
                        self._last_key_at = t_rx
                    self._rate.append((t_rx, sum(len(n) + 4 for n in frame.nals)))
                try:
                    self.on_frame(frame)
                except Exception:  # pragma: no cover - never kill the reader
                    log.exception("live view: sending a frame failed")

    def _with_params(self, nals: list[bytes], key: bool) -> list[bytes]:
        """Drop AUD/SEI, remember in-band SPS/PPS, make sure every keyframe
        starts with SPS + PPS so the phone can (re)start decoding there."""
        keep = []
        sps, pps = [], []
        for n in nals:
            t = nal_type(n)
            if t == NAL_SPS:
                sps.append(n)
            elif t == NAL_PPS:
                pps.append(n)
            elif t not in (NAL_AUD, NAL_SEI):
                keep.append(n)
        if sps:
            self._sps = sps
        if pps:
            self._pps = pps
        if key and self._sps and self._pps:
            return self._sps + self._pps + keep
        return keep

    def frame_size(self, nals: list[bytes]) -> tuple[int, int]:
        sps = next((n for n in nals if nal_type(n) == NAL_SPS), None) or (self._sps[0] if self._sps else None)
        if sps is None:
            return (0, 0)
        try:
            return sps_dimensions(sps)
        except (ValueError, IndexError):
            return (0, 0)


# --------------------------------------------------------- SPS dimensions


class _Bits:
    def __init__(self, data: bytes) -> None:
        # remove emulation prevention bytes (00 00 03)
        out = bytearray()
        z = 0
        for b in data:
            if z >= 2 and b == 3:
                z = 0
                continue
            out.append(b)
            z = z + 1 if b == 0 else 0
        self.d = bytes(out)
        self.i = 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            byte = self.d[self.i >> 3]
            v = (v << 1) | ((byte >> (7 - (self.i & 7))) & 1)
            self.i += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("bad exp-golomb")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k & 1 else -(k // 2)


def sps_dimensions(sps: bytes) -> tuple[int, int]:
    """Cropped picture size from an H.264 SPS NAL (including the NAL header)."""
    r = _Bits(sps[1:])
    profile = r.u(8)
    r.u(8)
    r.u(8)
    r.ue()
    chroma = 1
    if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
        chroma = r.ue()
        if chroma == 3:
            r.u(1)
        r.ue()
        r.ue()
        r.u(1)
        if r.u(1):
            for i in range(8 if chroma != 3 else 12):
                if r.u(1):
                    last, nxt = 8, 8
                    for _ in range(16 if i < 6 else 64):
                        if nxt:
                            nxt = (last + r.se() + 256) % 256
                        last = nxt or last
    r.ue()  # log2_max_frame_num
    poc = r.ue()
    if poc == 0:
        r.ue()
    elif poc == 1:
        r.u(1)
        r.se()
        r.se()
        for _ in range(r.ue()):
            r.se()
    r.ue()
    r.u(1)
    w_mbs = r.ue() + 1
    h_map = r.ue() + 1
    frame_mbs_only = r.u(1)
    if not frame_mbs_only:
        r.u(1)
    r.u(1)
    w = w_mbs * 16
    h = h_map * 16 * (2 - frame_mbs_only)
    if r.u(1):  # cropping
        cl, cr, ct, cb = r.ue(), r.ue(), r.ue(), r.ue()
        sx = 2 if chroma in (1, 2) else 1
        sy = (2 if chroma == 1 else 1) * (2 - frame_mbs_only)
        w -= (cl + cr) * sx
        h -= (ct + cb) * sy
    return w, h
