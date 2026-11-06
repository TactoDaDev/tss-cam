"""Networking + shared-memory service. Owns two threads:

  * UDP thread: receives pose packets, validates them, enforces the source
    lock, runs the pipeline, writes shared memory, sends acks.
  * heartbeat thread: detects silent sources, keeps the shared-memory
    heartbeat alive (so the AC app can tell the receiver is running), reads
    the AC app's status block and drives path playback.

All state shared with the GUI is protected by one lock; the GUI only reads
snapshots via `snapshot()` and calls the command methods.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import pipeline as pl
from .config import Settings
from .liveview import EncodedFrame, LiveStats, LiveView, packetize, to_avcc
from .pipeline import PoseProcessor
from .protocol import (
    ACK_ACCEPTED,
    ACK_HOLDING,
    ACK_IGNORED,
    FLAG_NEED_KEYFRAME,
    FLAG_WANT_VIDEO,
    PacketError,
    decode_pose,
    encode_ack,
)
from .recorder import PathPlayer, PathRecorder, load_path
from .shm import (
    FLAG_PLAYBACK,
    FLAG_RECORDING,
    FLAG_SOURCE_CONNECTED,
    LuaStatus,
    LuaView,
    PoseFrame,
    PoseWriter,
    StatusReader,
    ViewReader,
)
from .timewarp import warp_gains
from .transform import CameraOutput

log = logging.getLogger("handheldcam")


@dataclass
class Snapshot:
    listening: str = ""
    listen_error: str = ""
    source: str = ""
    state: int = pl.NO_DATA
    state_name: str = ""
    tracking: str = ""
    output: CameraOutput | None = None
    generation: int = 0
    stats: pl.Stats = field(default_factory=pl.Stats)
    malformed: int = 0
    foreign: int = 0
    last_foreign: str = ""
    seconds_since_packet: float | None = None
    latency_estimate_ms: float | None = None
    processing_ms: float = 0.0
    lua: LuaStatus | None = None
    lua_alive: bool = False
    recording: bool = False
    recording_path: str = ""
    playback: bool = False
    shm_ok: bool = False
    shm_error: str = ""
    frozen: bool = False
    paused: bool = False
    zoom: float = 1.0
    joy: tuple[float, float] = (0.0, 0.0)
    mode: int = 0
    phone_protocol: int = 0
    live: LiveStats | None = None
    live_wanted: bool = False
    lua_view: LuaView | None = None
    video_frames_sent: int = 0
    video_no_pose: int = 0
    phone_scale: float | None = None
    view_orientation: int = 0


PACE_BURST = 16  # datagrams sent back to back before pacing kicks in (~19 kB)


def send_chunks(sock, addr, chunks: list[bytes], pace_mbps: float, sleep=time.sleep, clock=time.perf_counter) -> None:
    """Send one frame's datagrams. Small frames go out at once; big ones
    (keyframes) are spread at `pace_mbps`, because a 100-200 kB burst can
    overflow the Wi-Fi queue or the phone's socket buffer, and a single lost
    datagram costs the picture until the next keyframe."""
    rate = pace_mbps * 1e6 / 8.0
    t0 = clock()
    sent = 0
    for i, c in enumerate(chunks):
        if rate > 0 and i and i % PACE_BURST == 0:
            ahead = t0 + sent / rate - clock()
            if ahead > 0:
                sleep(ahead)
        try:
            sock.sendto(c, addr)
        except OSError:
            return
        sent += len(c)


class ReceiverService:
    def __init__(self, settings: Settings, use_shm: bool = True, shm_dir: Path | None = None):
        self.settings = settings
        self.proc = PoseProcessor(settings)
        self.recorder = PathRecorder()
        self._player: PathPlayer | None = None
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._source: tuple[str, int] | None = None
        self._source_session: int | None = None
        self._source_last: float = 0.0
        self._accept_next_source = False
        self._malformed = 0
        self._foreign = 0
        self._last_foreign = ""
        self._listening = ""
        self._listen_error = ""
        self._proc_ms = 0.0
        self._phone_protocol = 0
        self._latency_ms: float | None = None
        self._lua: LuaStatus | None = None
        self._lua_hb_seen: deque[tuple[float, int]] = deque(maxlen=4)
        self._shm_error = ""
        self._writer: PoseWriter | None = None
        self._status: StatusReader | None = None
        self._view: ViewReader | None = None
        self._lua_view: LuaView | None = None
        if use_shm:
            try:
                self._writer = PoseWriter(settings.output.pose_shm_name, shm_dir)
                self._status = StatusReader(settings.output.status_shm_name, shm_dir)
                self._view = ViewReader(settings.output.view_shm_name, shm_dir)
            except OSError as e:  # pragma: no cover - platform specific
                self._shm_error = str(e)
                log.error("shared memory unavailable: %s", e)
        # live view (v3)
        # (t written, phone capture t, live, shm heartbeat, filter lag) per accepted pose
        self._pose_hist: deque[tuple[float, float, bool, int, tuple]] = deque(maxlen=600)
        self._view_hist: deque[tuple[float, LuaView]] = deque(maxlen=400)  # (t first seen, AC frame)
        self._last_hb = 0
        self._want_video_until = 0.0
        self._frame_id = 0
        self._frames_sent = 0
        self._frames_no_pose = 0
        self.live = LiveView(settings.liveview, self._on_video_frame)
        self.extra_handler = None  # TSS Cam: callable(data, addr, sock) for TSC* datagrams

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        self._stop.clear()
        self._open_socket()
        for target, name in ((self._udp_loop, "udp"), (self._heartbeat_loop, "heartbeat")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        self.live.stop()
        self.recorder.stop()
        if self._writer:
            self._writer.close()
        if self._status:
            self._status.close()
        if self._view:
            self._view.close()

    def _open_socket(self) -> None:
        net = self.settings.network
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.bind((net.bind_address, net.port))
            except OSError as e:
                self._listen_error = f"cannot listen on {net.bind_address}:{net.port}: {e}"
                self._listening = ""
                log.error(self._listen_error)
                s.close()
                self._sock = None
                return
            s.settimeout(0.2)
            try:  # room for a whole keyframe burst of live-view datagrams
                s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)
            except OSError:
                pass
            self._sock = s
            self._listening = f"{net.bind_address}:{net.port}"
            self._listen_error = ""
            log.info("listening for the phone on UDP %s", self._listening)

    def restart_listener(self, port: int | None = None, bind_address: str | None = None) -> None:
        with self._lock:
            if port is not None:
                self.settings.network.port = port
            if bind_address is not None:
                self.settings.network.bind_address = bind_address
        self._open_socket()

    # ------------------------------------------------------------ commands
    def recenter(self) -> None:
        with self._lock:
            self.proc.request_recenter()
        log.info("recenter requested")

    def set_frozen(self, v: bool) -> None:
        with self._lock:
            self.proc.set_frozen(v)

    def set_paused(self, v: bool) -> None:
        with self._lock:
            self.proc.set_paused(v)
        log.info("tracking %s", "paused" if v else "resumed")

    def accept_new_source(self) -> None:
        with self._lock:
            self._accept_next_source = True
        log.info("will accept the next phone that sends packets")

    def settings_changed(self) -> None:
        with self._lock:
            self.proc.rebuild_filters()

    def liveview_settings_changed(self) -> None:
        self.live.settings_changed()

    def start_recording(self) -> Path:
        with self._lock:
            p = self.recorder.start(Path(self.settings.recordings_dir))
        log.info("recording camera path to %s", p)
        return p

    def stop_recording(self) -> Path | None:
        with self._lock:
            p = self.recorder.stop()
        log.info("recording stopped (%d samples)", self.recorder.samples)
        return p

    def start_playback(self, path: Path, loop: bool = False) -> None:
        samples = load_path(path)
        with self._lock:
            self._player = PathPlayer(samples, loop)
            self._player.start(time.perf_counter())
            self.proc.state = pl.PLAYBACK
            self.proc.generation = (self.proc.generation + 1) & 0xFFFFFFFF
        log.info("playing %s (%.1f s)", path.name, samples[-1].t)

    def stop_playback(self) -> None:
        with self._lock:
            if self._player is None:
                return
            self._player = None
            # Hand back to live tracking without a jump: re-anchor + new generation
            self.proc.state = pl.HOLD_STALE
            self.proc.request_recenter()
        log.info("playback stopped")

    # --------------------------------------------------------------- loops
    def _udp_loop(self) -> None:
        buf_size = 2048
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                time.sleep(0.2)
                continue
            try:
                data, addr = sock.recvfrom(buf_size)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" for an earlier ack
                # (phone app closed) as an error on the next recvfrom. Harmless.
                continue
            except OSError:
                if self._stop.is_set():
                    return
                time.sleep(0.05)  # socket being replaced
                continue
            now = time.perf_counter()
            self._handle_datagram(data, addr, now, sock)

    def _handle_datagram(self, data: bytes, addr: tuple[str, int], now: float, sock: socket.socket | None) -> None:
        t0 = time.perf_counter()
        # TSS Cam: our own TSC* packets (hello, commands, sticks) go to the hub; poses stay AcHandCam's
        if self.extra_handler is not None and data[:3] == b"TSC":
            self.extra_handler(data, addr, sock)
            return
        try:
            pkt = decode_pose(data)
        except PacketError as e:
            with self._lock:
                self._malformed += 1
                if self._malformed <= 20 or self._malformed % 500 == 0:
                    log.warning("malformed packet from %s:%d: %s (total %d)", addr[0], addr[1], e, self._malformed)
            return

        net = self.settings.network
        with self._lock:
            if net.allowed_ips and addr[0] not in net.allowed_ips:
                self._note_foreign(addr, "not in allowed_ips")
                return
            if not self._source_matches(addr, pkt.session_id, now):
                self._note_foreign(addr, "another phone is locked in")
                self._send_ack(sock, addr, pkt.seq, pkt.send_time, ACK_IGNORED)
                return
            self._source_last = now
            self._phone_protocol = pkt.version
            if self.proc.state == pl.PLAYBACK:
                self._send_ack(sock, addr, pkt.seq, pkt.send_time, ACK_HOLDING)
                return
            res = self.proc.process(pkt, now)
            status = ACK_ACCEPTED if (res.accepted and self.proc.state == pl.LIVE) else ACK_HOLDING
            if not res.accepted:
                status = ACK_IGNORED
            self._proc_ms = (time.perf_counter() - t0) * 1000.0
            st = self.proc.stats
            # Estimated capture -> shared-memory latency: phone-measured
            # capture->send + half the phone-measured round trip + our processing.
            if st.capture_to_send_ms is not None and st.rtt_ms is not None:
                self._latency_ms = st.capture_to_send_ms + st.rtt_ms / 2.0 + self._proc_ms
            else:
                self._latency_ms = None
            if res.output_changed and self.proc.output is not None:
                self.recorder.add(self.proc.output)
            hb = self._write_shm()
            if res.accepted:
                # what the AC app will show from now on, for the live view
                self._pose_hist.append((time.perf_counter(), pkt.capture_time, self.proc.state == pl.LIVE,
                                        hb if hb is not None else -1, self.proc.filter_lag))
            if pkt.flags & FLAG_WANT_VIDEO:
                self._want_video_until = now + LiveView.IDLE_STOP_S
                self.live.want(now)
                self.live.request_keyframe(now, bool(pkt.flags & FLAG_NEED_KEYFRAME))
        self._send_ack(sock, addr, pkt.seq, pkt.send_time, status)

    def _note_foreign(self, addr: tuple[str, int], why: str) -> None:
        self._foreign += 1
        desc = f"{addr[0]}:{addr[1]}"
        if desc != self._last_foreign:
            log.warning("ignoring packets from %s (%s)", desc, why)
        self._last_foreign = desc

    def _source_matches(self, addr: tuple[str, int], session: int, now: float) -> bool:
        net = self.settings.network
        if self._source is None or self._accept_next_source:
            self._lock_source(addr, session)
            return True
        if addr == self._source and session == self._source_session:
            return True
        if addr[0] == self._source[0] and session != self._source_session:
            # same phone restarted the app (new session id / new port)
            self._lock_source(addr, session)
            return True
        if net.auto_switch_source and now - self._source_last > net.source_timeout_s:
            self._lock_source(addr, session)
            return True
        return False

    def _lock_source(self, addr: tuple[str, int], session: int) -> None:
        if self._source is not None:
            log.info("switching source from %s:%d to %s:%d", *self._source, *addr)
        else:
            log.info("phone connected: %s:%d (session %08x)", addr[0], addr[1], session)
        self._source = addr
        self._source_session = session
        self._accept_next_source = False

    def _send_ack(self, sock: socket.socket | None, addr, seq: int, send_time: float, status: int) -> None:
        if sock is None:
            return
        try:
            sock.sendto(encode_ack(seq, send_time, status), addr)
        except OSError:
            pass

    def _heartbeat_loop(self) -> None:
        period = 1.0 / max(1.0, self.settings.output.heartbeat_hz)
        play_period = 1.0 / 60.0
        next_beat = time.perf_counter()
        while not self._stop.is_set():
            now = time.perf_counter()
            with self._lock:
                if self._player is not None:
                    out = self._player.sample(now)
                    if out is None:
                        self._player = None
                        self.proc.state = pl.HOLD_STALE
                        self.proc.request_recenter()
                        log.info("playback finished")
                    else:
                        self.proc.output = out
                        self.proc.state = pl.PLAYBACK
                        self._write_shm()
                if now >= next_beat:
                    next_beat = now + period
                    self.proc.tick(now)
                    if self._player is None:
                        self._write_shm()
                    self._read_lua(now)
                video = now < self._want_video_until
                if video:
                    # live view: note when each AC frame appears (to the ms),
                    # so captured pictures can be matched to the frame shown
                    self._read_view(now)
            self.live.poll(now)
            if video:
                time.sleep(0.001)
            else:
                time.sleep(play_period if self._player is not None else min(period, 0.02))

    def _write_shm(self) -> int | None:
        """Writes the current output; returns the heartbeat it went out with."""
        w = self._writer
        if w is None:
            return None
        o = self.proc.output
        flags = 0
        if self.recorder.active:
            flags |= FLAG_RECORDING
        if self._player is not None:
            flags |= FLAG_PLAYBACK
        if self._source is not None and (time.perf_counter() - self._source_last) < 1.0:
            flags |= FLAG_SOURCE_CONNECTED
        st = self.proc.stats
        try:
            hb = w.write(
                PoseFrame(
                    heartbeat=0,
                    out_state=self.proc.state if o is not None else pl.NO_DATA,
                    flags=flags,
                    generation=self.proc.generation,
                    tracking_state=self.proc.tracking_state,
                    position=o.position if o else (0.0, 0.0, 0.0),
                    forward=o.forward if o else (0.0, 0.0, 1.0),
                    up=o.up if o else (0.0, 1.0, 0.0),
                    latency_ms=self._latency_ms if self._latency_ms is not None else -1.0,
                    rx_hz=st.rate_hz,
                    loss_pct=st.loss_pct(),
                    zoom=self.proc.zoom,
                    joy_x=0.0 if self._player is not None else self.proc.joy[0],
                    joy_y=0.0 if self._player is not None else self.proc.joy[1],
                    mode=self.proc.mode,
                    view_orientation=self.proc.view_orientation,
                    vf_aspect=self.proc.vf_aspect,
                )
            )
            self._last_hb = hb
            return hb
        except (OSError, ValueError) as e:  # pragma: no cover
            self._shm_error = str(e)
            return None

    def _read_lua(self, now: float) -> None:
        if self._status is None:
            return
        try:
            s = self._status.read()
        except (OSError, ValueError):
            s = None
        prev = self._lua
        self._lua = s
        if s is not None and prev is not None and s.recenter_requests != prev.recenter_requests:
            self.proc.request_recenter()
            log.info("recenter requested from the AC app")
        if s is not None:
            if not self._lua_hb_seen or self._lua_hb_seen[-1][1] != s.heartbeat:
                self._lua_hb_seen.append((now, s.heartbeat))
        self._read_view(now)

    def _read_view(self, now: float) -> None:
        if self._view is None:
            return
        try:
            v = self._view.read()
        except (OSError, ValueError):
            v = None
        self._lua_view = v
        if v is not None and (not self._view_hist or self._view_hist[-1][1].frame != v.frame):
            self._view_hist.append((now, v))

    # ------------------------------------------------------------ live view
    def pose_time_for(self, t_shown: float) -> tuple[float, bool] | None:
        """Phone capture time of the newest pose the receiver had written by
        t_shown (receiver clock), and whether output was live then."""
        for t_w, cap, live, _hb, _lag in reversed(self._pose_hist):
            if t_w <= t_shown:
                return cap, live
        return None

    def view_for(self, t_shown: float) -> LuaView | None:
        """The AC frame that was on screen at t_shown (receiver clock): the
        newest view block first seen at or before it."""
        for t_seen, v in reversed(self._view_hist):
            if t_seen <= t_shown:
                return v
        return None

    def pose_for_heartbeat(self, hb: int) -> tuple[float, bool, tuple] | None:
        """The phone pose the AC app rendered when it read heartbeat `hb`: the
        newest accepted pose written with a heartbeat <= hb (heartbeats also
        tick without new poses)."""
        for _t, cap, live, phb, lag in reversed(self._pose_hist):
            if phb >= 0 and ((hb - phb) & 0xFFFFFFFF) < 0x80000000:
                return cap, live, lag
        return None

    def _on_video_frame(self, frame: EncodedFrame) -> None:
        """Called from the encoder reader thread for every encoded frame."""
        lv = self.settings.liveview
        now = time.perf_counter()
        with self._lock:
            addr = self._source
            sock = self._sock
            if addr is None or sock is None or now > self._want_video_until:
                return
            t_shown = frame.t_cap - lv.render_delay_ms / 1000.0
            view = self._lua_view
            shown = self.view_for(t_shown)
            hit = self.pose_time_for(t_shown)
            pose_time = None
            warp = False
            fov = 0.0
            lag = render_rot = (0.0, 0.0, 0.0, 1.0)
            exact = self.pose_for_heartbeat(shown.pose_heartbeat) if (shown is not None and shown.pose_heartbeat) else None
            if exact is not None:
                # v4.1 AC app: the exact pose this frame was rendered from, plus
                # the receiver's and the AC app's smoothing as rotations
                cap, was_live, lag = exact
                pose_time = cap - lv.warp_offset_ms / 1000.0
                if shown.rot is not None:
                    render_rot = shown.rot
                lua_ok = bool(self._lua_hb_seen) and (now - self._lua_hb_seen[-1][0]) < 1.0
                warp = was_live and shown.live and lua_ok and self._player is None
                fov = shown.fov_v
            elif hit is not None:
                cap, was_live = hit
                smoothing = view.smoothing if view is not None else 0.0
                pose_time = cap - smoothing - lv.warp_offset_ms / 1000.0
                lua_ok = bool(self._lua_hb_seen) and (now - self._lua_hb_seen[-1][0]) < 1.0
                warp = was_live and view is not None and view.live and lua_ok and self._player is None
                fov = view.fov_v if view is not None else 0.0
            else:
                self._frames_no_pose += 1
            g = warp_gains(self.settings.transform)
            self._frame_id = (self._frame_id + 1) & 0xFFFFFFFF
            fid = self._frame_id
        w, h = self.live.frame_size(frame.nals)
        age_ms = (time.perf_counter() - frame.t_cap) * 1000.0
        chunks = packetize(
            fid,
            to_avcc(frame.nals),
            key=frame.key,
            pose_time=pose_time,
            pc_age_ms=age_ms,
            fov_v=fov,
            gains=(g.yaw, g.pitch, g.roll),
            width=w,
            height=h,
            warp=warp,
            max_chunk=lv.max_chunk,
            pc_capture_time=frame.t_cap,
            filter_lag=lag,
            render_rot=render_rot,
        )
        send_chunks(sock, addr, chunks, lv.pace_mbps)
        self.live.note_send_age(age_ms)
        with self._lock:
            self._frames_sent += 1

    # ------------------------------------------------------------ snapshot
    def snapshot(self) -> Snapshot:
        now = time.perf_counter()
        with self._lock:
            p = self.proc
            src = f"{self._source[0]}:{self._source[1]}" if self._source else ""
            from .protocol import TRACKING_REASON_NAMES, TRACKING_STATE_NAMES

            tr = TRACKING_STATE_NAMES.get(p.tracking_state, "?")
            reason = TRACKING_REASON_NAMES.get(p.tracking_reason, "")
            lua_alive = bool(self._lua_hb_seen) and (now - self._lua_hb_seen[-1][0]) < 1.0
            return Snapshot(
                listening=self._listening,
                listen_error=self._listen_error,
                source=src,
                state=p.state,
                state_name=pl.STATE_NAMES.get(p.state, "?"),
                tracking=tr + (f" ({reason})" if reason else ""),
                output=p.output,
                generation=p.generation,
                stats=pl.Stats(**vars(p.stats)),
                malformed=self._malformed,
                foreign=self._foreign,
                last_foreign=self._last_foreign,
                seconds_since_packet=p.seconds_since_packet(now),
                latency_estimate_ms=self._latency_ms,
                processing_ms=self._proc_ms,
                lua=self._lua,
                lua_alive=lua_alive,
                recording=self.recorder.active,
                recording_path=str(self.recorder.path or ""),
                playback=self._player is not None,
                shm_ok=self._writer is not None,
                shm_error=self._shm_error,
                frozen=p.user_frozen,
                paused=p.user_paused,
                zoom=p.zoom,
                joy=p.joy,
                mode=p.mode,
                phone_protocol=self._phone_protocol,
                live=self.live.stats(),
                live_wanted=now < self._want_video_until,
                lua_view=self._lua_view,
                video_frames_sent=self._frames_sent,
                phone_scale=p.phone_scale,
                view_orientation=p.view_orientation,
                video_no_pose=self._frames_no_pose,
            )
