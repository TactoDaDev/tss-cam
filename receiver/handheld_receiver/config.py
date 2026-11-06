"""Receiver settings: defaults, presets, JSON load/save."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .filters import OneEuroParams

log = logging.getLogger(__name__)

DEFAULT_PORT = 47820
POSE_SHM_NAME = "AcTools.CSP.Limited.HandheldCam.Pose"
STATUS_SHM_NAME = "AcTools.CSP.Limited.HandheldCam.Status"
VIEW_SHM_NAME = "AcTools.CSP.Limited.HandheldCam.View"


@dataclass
class NetworkSettings:
    bind_address: str = "0.0.0.0"  # all interfaces; set to your LAN IP to restrict
    port: int = DEFAULT_PORT
    source_timeout_s: float = 3.0  # silence before another phone may take over
    auto_switch_source: bool = True
    allowed_ips: list[str] = field(default_factory=list)  # empty = any LAN sender
    stale_ms: float = 150.0  # packets delayed more than this (vs. best seen) are dropped


@dataclass
class TransformSettings:
    translation_scale: float = 1.0  # 1 = 1:1, 2 = double speed, 0.5 = precise
    scale_right: float = 1.0
    scale_up: float = 1.0
    scale_forward: float = 1.0
    invert_right: bool = False
    invert_up: bool = False
    invert_forward: bool = False
    # Signed axis permutation for translation, e.g. ["+right", "+up", "+forward"].
    # Each output axis (right, up, forward) takes the listed input axis.
    axis_map: list[str] = field(default_factory=lambda: ["+right", "+up", "+forward"])
    rotation_sensitivity: float = 1.0  # scales yaw and pitch
    roll_sensitivity: float = 1.0
    invert_yaw: bool = False
    invert_pitch: bool = False
    invert_roll: bool = False
    roll_lock: bool = False  # keep the horizon level
    yaw_offset_deg: float = 0.0
    start_offset: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])  # metres right/up/forward


@dataclass
class FilterSettings:
    preset: str = "Handheld (natural)"
    position: OneEuroParams = field(default_factory=lambda: OneEuroParams(2.0, 3.0, 1.0, True))
    rotation: OneEuroParams = field(default_factory=lambda: OneEuroParams(2.0, 2.0, 1.0, True))


@dataclass
class TrackingSettings:
    hold_on_limited: bool = True  # freeze output while ARKit reports "limited"
    jump_threshold_m: float = 0.30  # per-frame jump treated as a tracking glitch
    jump_threshold_deg: float = 25.0
    resume_settle_frames: int = 6  # good frames required before resuming after loss


@dataclass
class OutputSettings:
    pose_shm_name: str = POSE_SHM_NAME
    status_shm_name: str = STATUS_SHM_NAME
    view_shm_name: str = VIEW_SHM_NAME
    heartbeat_hz: float = 30.0


@dataclass
class LiveViewSettings:
    """Game picture streamed to the phone (v3). Runs only while the phone's
    viewfinder is set to GAME."""

    enabled: bool = True
    backend: str = "ffmpeg"  # "ffmpeg" or "native" (native/handheld_capture.exe)
    ffmpeg_path: str = ""  # empty = PATH, receiver\ffmpeg\bin, or the winget install
    native_path: str = ""  # empty = receiver\native\handheld_capture.exe
    encoder: str = "auto"  # auto, h264_amf, h264_nvenc, h264_qsv, libx264
    capture: str = "desktop"  # "desktop" (the game) or "testpattern" (checks phone + network only)
    monitor: int = 0  # Desktop Duplication output index (0 = first monitor of the GPU)
    # v4 defaults: the live view is for framing, so it favours latency over
    # picture quality (smaller frames = faster capture/encode/send/decode)
    height: int = 480  # stream height; width follows the monitor's aspect ratio (16:9 -> 854)
    fps: int = 30
    bitrate_mbps: float = 4.0
    keyframe_interval_s: float = 0.5  # also the longest freeze after a lost packet
    draw_mouse: bool = False
    # Timing model for timewarp (see docs/ARCHITECTURE.md, "Live view")
    render_delay_ms: float = 25.0  # pose written for AC -> that picture captured
    encode_min_ms: float = 4.0  # fastest capture -> receiver (FFmpeg backend only)
    warp_offset_ms: float = 0.0  # fine tune: + if the picture still lags when you pan
    max_chunk: int = 1200  # UDP payload per datagram
    pace_mbps: float = 120.0  # spread big frames (keyframes) at this rate; 0 = send at once
    defaults_version: int = 4  # config files from before v4 get the new size/fps/bitrate defaults once


@dataclass
class Settings:
    network: NetworkSettings = field(default_factory=NetworkSettings)
    transform: TransformSettings = field(default_factory=TransformSettings)
    filter: FilterSettings = field(default_factory=FilterSettings)
    tracking: TrackingSettings = field(default_factory=TrackingSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    liveview: LiveViewSettings = field(default_factory=LiveViewSettings)
    recordings_dir: str = "recordings"


PRESETS: dict[str, tuple[OneEuroParams, OneEuroParams]] = {
    # (position params, rotation params). Starting points - tune on your setup.
    "Handheld (natural)": (OneEuroParams(2.0, 3.0, 1.0, True), OneEuroParams(2.0, 2.0, 1.0, True)),
    "Product (smooth)": (OneEuroParams(0.25, 0.8, 1.0, True), OneEuroParams(0.25, 0.6, 1.0, True)),
    "Fast action": (OneEuroParams(4.0, 6.0, 1.0, True), OneEuroParams(4.0, 4.0, 1.0, True)),
    "Raw (no smoothing)": (OneEuroParams(1.0, 0.0, 1.0, False), OneEuroParams(1.0, 0.0, 1.0, False)),
}


def apply_preset(settings: Settings, name: str) -> None:
    pos, rot = PRESETS[name]
    settings.filter.preset = name
    settings.filter.position = OneEuroParams(**asdict(pos))
    settings.filter.rotation = OneEuroParams(**asdict(rot))


def _merge(dc_obj, data: dict) -> None:
    """Copy known keys from data into a dataclass instance (recursively),
    ignoring unknown keys so old/new config files stay compatible."""
    for f in fields(dc_obj):
        if f.name not in data:
            continue
        cur = getattr(dc_obj, f.name)
        val = data[f.name]
        if hasattr(cur, "__dataclass_fields__") and isinstance(val, dict):
            _merge(cur, val)
        else:
            try:
                if isinstance(cur, bool):
                    val = bool(val)
                elif isinstance(cur, float):
                    val = float(val)
                elif isinstance(cur, int):
                    val = int(val)
                elif isinstance(cur, list):
                    val = list(val)
                elif isinstance(cur, str):
                    val = str(val)
            except (TypeError, ValueError):
                log.warning("config: ignoring bad value for %s: %r", f.name, val)
                continue
            setattr(dc_obj, f.name, val)


LIVEVIEW_DEFAULTS_VERSION = 4
_V4_LIVEVIEW_KEYS = ("height", "fps", "bitrate_mbps")


def _migrate(data: dict) -> dict:
    """v4 changed the live view's default size/fps/bitrate (480p30, 4 Mbit/s):
    an older config file keeps everything else but takes the new defaults once."""
    lv = data.get("liveview")
    if isinstance(lv, dict) and int(lv.get("defaults_version", 0) or 0) < LIVEVIEW_DEFAULTS_VERSION:
        for k in _V4_LIVEVIEW_KEYS:
            lv.pop(k, None)
        lv["defaults_version"] = LIVEVIEW_DEFAULTS_VERSION
        log.info("config: live view now defaults to 480p, 30 fps, 4 Mbit/s (v4); change it in the Live view tab")
    return data


def load_settings(path: Path) -> Settings:
    s = Settings()
    if path.exists():
        try:
            _merge(s, _migrate(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError) as e:
            log.error("could not read %s (%s); using defaults", path, e)
    validate(s)
    return s


def save_settings(s: Settings, path: Path) -> None:
    path.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")


_AXES = ("right", "up", "forward")


def parse_axis_map(entries: list[str]) -> list[tuple[int, float]]:
    """['+right','-forward','+up'] -> [(0, 1.0), (2, -1.0), (1, 1.0)]"""
    if len(entries) != 3:
        raise ValueError("axis_map needs exactly 3 entries")
    out = []
    for e in entries:
        e = e.strip().lower()
        sign = -1.0 if e.startswith("-") else 1.0
        name = e.lstrip("+-")
        if name not in _AXES:
            raise ValueError(f"unknown axis {e!r}; use right/up/forward with +/-")
        out.append((_AXES.index(name), sign))
    if sorted(i for i, _ in out) != [0, 1, 2]:
        raise ValueError("axis_map must use each of right/up/forward exactly once")
    return out


def validate(s: Settings) -> None:
    t = s.transform
    try:
        parse_axis_map(t.axis_map)
    except ValueError as e:
        log.error("config: %s; resetting axis_map", e)
        t.axis_map = ["+right", "+up", "+forward"]
    if len(t.start_offset) != 3:
        t.start_offset = [0.0, 0.0, 0.0]
    t.translation_scale = min(max(t.translation_scale, 0.01), 100.0)
    if not (1 <= s.network.port <= 65535):
        s.network.port = DEFAULT_PORT
    lv = s.liveview
    if lv.backend not in ("ffmpeg", "native"):
        lv.backend = "ffmpeg"
    if lv.capture not in ("desktop", "testpattern"):
        lv.capture = "desktop"
    if lv.encoder not in ("auto", "h264_amf", "h264_nvenc", "h264_qsv", "libx264"):
        log.error("config: unknown live view encoder %r; using auto", lv.encoder)
        lv.encoder = "auto"
    lv.fps = min(max(lv.fps, 10), 240)
    lv.height = min(max(lv.height, 144), 2160)
    lv.bitrate_mbps = min(max(lv.bitrate_mbps, 0.5), 100.0)
    lv.keyframe_interval_s = min(max(lv.keyframe_interval_s, 0.1), 10.0)
    lv.max_chunk = min(max(lv.max_chunk, 200), 1400)
    lv.pace_mbps = min(max(lv.pace_mbps, 0.0), 2000.0)
    for p in (s.filter.position, s.filter.rotation):
        p.min_cutoff = max(p.min_cutoff, 0.01)
        p.d_cutoff = max(p.d_cutoff, 0.01)
        p.beta = max(p.beta, 0.0)
