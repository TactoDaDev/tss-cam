"""Tracking pipeline: packet ordering, staleness, tracking-loss handling,
continuity re-anchoring, freeze/pause, calibration and smoothing.

Pure logic, no sockets or threads, so it can be unit tested deterministically.

State machine (output state codes are shared with the Lua app):
    NO_DATA        nothing received yet from the current source
    LIVE           applying fresh poses
    HOLD_TRACKING  ARKit tracking limited/lost, or a pose jump was rejected
    HOLD_FROZEN    freeze (clutch) held on the phone or the receiver
    HOLD_PAUSED    tracking paused by the user
    HOLD_STALE     no packets for a short while (Wi-Fi drop-out, phone locked)
    PLAYBACK       a recorded camera path is being played back

While holding, the last output pose is repeated. Whenever the pipeline goes
from any HOLD state back to LIVE it *re-anchors*: it changes the continuity
offset T_off (yaw + translation only) so the corrected pose continues exactly
from the held one, then blends any remaining pitch/roll difference over
`resume_blend_s`. This is what keeps the camera from flying off after a
tracking loss, a relocalisation, an ARKit session restart or a long freeze.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .config import Settings
from .filters import OneEuroPosition, OneEuroQuat
from .mathutil import (
    IDENTITY_Q,
    Quat,
    Vec3,
    heading_of,
    q_angle_between,
    q_conj,
    q_mul,
    q_norm,
    q_rotate,
    q_slerp,
    q_yaw,
    v_add,
    v_len,
    v_norm,
    v_sub,
)
from .protocol import TRACKING_NORMAL, PosePacket, seq_gap, seq_newer
from .transform import Calibration, CameraOutput, map_pose

NO_DATA = 0
LIVE = 1
HOLD_TRACKING = 2
HOLD_FROZEN = 3
HOLD_PAUSED = 4
HOLD_STALE = 5
PLAYBACK = 6

STATE_NAMES = {
    NO_DATA: "waiting for phone",
    LIVE: "live",
    HOLD_TRACKING: "holding (tracking lost)",
    HOLD_FROZEN: "frozen",
    HOLD_PAUSED: "paused",
    HOLD_STALE: "holding (no packets)",
    PLAYBACK: "playback",
}

STALE_AFTER_S = 0.5
RESUME_BLEND_S = 0.25
CLOCK_DRIFT_ALLOWANCE = 0.002  # s per s: lets the min-offset baseline creep up


@dataclass
class Stats:
    received: int = 0
    accepted: int = 0
    lost: int = 0
    out_of_order: int = 0
    stale: int = 0
    rejected_jumps: int = 0
    reanchors: int = 0
    last_age_ms: float = 0.0
    capture_to_send_ms: float | None = None
    rtt_ms: float | None = None
    rate_hz: float = 0.0

    def loss_pct(self) -> float:
        total = self.accepted + self.lost
        return 100.0 * self.lost / total if total else 0.0


@dataclass
class Result:
    accepted: bool
    reason: str = ""
    output_changed: bool = False


@dataclass
class _Blend:
    start_pos: Vec3
    start_q: Quat
    t0: float
    duration: float


def _horizontal_heading_q(q: Quat) -> Quat:
    return q_yaw(heading_of(q))


@dataclass
class PoseProcessor:
    settings: Settings
    state: int = NO_DATA
    generation: int = 0
    calibration: Calibration | None = None
    output: CameraOutput | None = None
    stats: Stats = field(default_factory=Stats)
    tracking_state: int = 0
    tracking_reason: int = 0
    user_frozen: bool = False
    user_paused: bool = False
    # v2 phone controls, passed straight through to the AC app
    zoom: float = 1.0
    joy: tuple[float, float] = (0.0, 0.0)
    mode: int = 0
    # v4 phone settings (protocol v3)
    phone_scale: float | None = None  # movement scale chosen on the phone
    vf_aspect: float = 0.0
    view_orientation: int = 0

    def __post_init__(self) -> None:
        self._pos_filter = OneEuroPosition(self.settings.filter.position)
        self._rot_filter = OneEuroQuat(self.settings.filter.rotation)
        self._yaw_hint: float | None = None  # unwrapped yaw; reset only with the calibration
        self.reset_source()

    # ------------------------------------------------------------ helpers
    def reset_source(self) -> None:
        """Forget everything about the current phone (new source)."""
        self._session: int | None = None
        self._last_seq: int | None = None
        self._last_capture: float | None = None
        self._epoch: int | None = None
        self._recenter_count: int | None = None
        self._min_offset: float | None = None
        self._min_offset_t: float = 0.0
        self._last_recv: float | None = None
        self._last_raw: tuple[Vec3, Quat] | None = None
        self._off_q: Quat = IDENTITY_Q  # continuity offset (yaw only)
        self._off_t: Vec3 = (0.0, 0.0, 0.0)
        self._corrected: tuple[Vec3, Quat] | None = None  # last filtered corrected pose
        # Rotation from the raw phone orientation to the smoothed one the AC
        # camera is driven by (camera-local, phone frame): the live view uses
        # it to timewarp from what was really rendered, not from the raw pose.
        self.filter_lag: Quat = IDENTITY_Q
        self._need_reanchor = True
        self._good_streak = 0
        self._blend: _Blend | None = None
        self._pos_filter.reset()
        self._rot_filter.reset()
        self._pending_recenter = False
        self.joy = (0.0, 0.0)  # never keep walking for a source we no longer hear
        if self.state not in (NO_DATA, PLAYBACK):
            self.state = HOLD_STALE if self.output is not None else NO_DATA

    def request_recenter(self) -> None:
        self._pending_recenter = True

    def set_frozen(self, frozen: bool) -> None:
        self.user_frozen = frozen

    def set_paused(self, paused: bool) -> None:
        self.user_paused = paused

    def rebuild_filters(self) -> None:
        """Call after changing filter settings."""
        self._pos_filter.params = self.settings.filter.position
        self._rot_filter.params = self.settings.filter.rotation

    def _correct(self, p: Vec3, q: Quat) -> tuple[Vec3, Quat]:
        return v_add(q_rotate(self._off_q, p), self._off_t), q_norm(q_mul(self._off_q, q))

    def _reanchor(self, p_raw: Vec3, q_raw: Quat) -> None:
        """Choose T_off so that corrected(raw) has the held position and
        heading. Pitch/roll stay gravity-absolute."""
        if self._corrected is None:
            self._off_q, self._off_t = IDENTITY_Q, (0.0, 0.0, 0.0)
            return
        p_h, q_h = self._corrected
        dyaw = heading_of(q_h) - heading_of(q_raw)
        self._off_q = q_yaw(dyaw)
        self._off_t = v_sub(p_h, q_rotate(self._off_q, p_raw))
        self.stats.reanchors += 1

    # --------------------------------------------------------------- main
    def process(self, pkt: PosePacket, recv_time: float) -> Result:
        st = self.stats
        st.received += 1

        # --- ordering / session -------------------------------------------
        if self._session != pkt.session_id:
            had_output = self.output is not None
            self.reset_source()
            self._session = pkt.session_id
            if had_output:
                # A different phone/app launch: start a fresh calibration. The
                # generation bump makes the AC app re-anchor, so no jump.
                self._pending_recenter = True
        elif self._last_seq is not None:
            if not seq_newer(pkt.seq, self._last_seq):
                st.out_of_order += 1
                return Result(False, "out of order / duplicate")
            st.lost += seq_gap(pkt.seq, self._last_seq)
        if self._last_capture is not None and pkt.capture_time <= self._last_capture and pkt.epoch == self._epoch:
            st.out_of_order += 1
            return Result(False, "capture time went backwards")

        # --- staleness (relative one-way delay vs. the best seen) ----------
        offset = recv_time - pkt.send_time
        if self._min_offset is None or offset < self._min_offset:
            self._min_offset, self._min_offset_t = offset, recv_time
        else:
            creep = (recv_time - self._min_offset_t) * CLOCK_DRIFT_ALLOWANCE
            self._min_offset += creep
            self._min_offset_t = recv_time
        age_ms = (offset - self._min_offset) * 1000.0
        st.last_age_ms = age_ms
        if age_ms > self.settings.network.stale_ms:
            st.stale += 1
            self._last_seq = pkt.seq
            return Result(False, f"stale ({age_ms:.0f} ms late)")

        # rate estimate
        if self._last_recv is not None:
            dtr = recv_time - self._last_recv
            if dtr > 0:
                st.rate_hz += 0.05 * (1.0 / dtr - st.rate_hz)
        self._last_recv = recv_time
        cts = (pkt.send_time - pkt.capture_time) * 1000.0
        st.capture_to_send_ms = cts if 0.0 <= cts < 1000.0 else None
        st.rtt_ms = pkt.rtt_ms

        dt = 0.0 if self._last_capture is None else pkt.capture_time - self._last_capture
        self._last_seq = pkt.seq
        self._last_capture = pkt.capture_time
        st.accepted += 1

        # Controls apply even while the pose is held (you can still zoom or
        # walk while ARKit relocalises); only a silent source zeroes them.
        self.zoom = pkt.zoom
        self.mode = pkt.mode
        self.joy = (pkt.joy_x, pkt.joy_y)
        if pkt.version >= 3:
            self.vf_aspect = pkt.vf_aspect
            self.view_orientation = pkt.view_orientation
            if pkt.move_scale > 0:
                if self.phone_scale is not None and abs(pkt.move_scale - self.phone_scale) > 1e-6:
                    # New scale: start a new calibration here. The AC app
                    # re-anchors on the new generation, so the camera stays put.
                    self._pending_recenter = True
                self.phone_scale = pkt.move_scale

        # --- discontinuities ------------------------------------------------
        # A new epoch (ARKit restarted, or the phone was turned between
        # portrait and landscape) is an intentional discontinuity: re-anchor,
        # don't treat it as a tracking glitch.
        epoch_changed = self._epoch is not None and pkt.epoch != self._epoch
        if epoch_changed:
            self._need_reanchor = True
            self._good_streak = 0
        self._epoch = pkt.epoch

        if self._recenter_count is None:
            self._recenter_count = pkt.recenter_count
        elif pkt.recenter_count != self._recenter_count:
            self._recenter_count = pkt.recenter_count
            self._pending_recenter = True

        self.tracking_state, self.tracking_reason = pkt.tracking_state, pkt.tracking_reason
        p_raw: Vec3 = pkt.position
        q_raw: Quat = pkt.orientation

        tracking_ok = pkt.tracking_state == TRACKING_NORMAL or (
            pkt.tracking_state != 0 and not self.settings.tracking.hold_on_limited
        )

        # Per-frame jump check on the raw ARKit pose (relocalisation snaps).
        if tracking_ok and self._last_raw is not None and 0 < dt < 0.5 and not epoch_changed:
            jump_m = v_len(v_sub(p_raw, self._last_raw[0]))
            jump_deg = math.degrees(q_angle_between(q_raw, self._last_raw[1]))
            tset = self.settings.tracking
            if jump_m > tset.jump_threshold_m + 5.0 * dt or jump_deg > tset.jump_threshold_deg + 720.0 * dt:
                st.rejected_jumps += 1
                self._need_reanchor = True
                self._good_streak = 0
                self._last_raw = (p_raw, q_raw)
                self._set_hold(HOLD_TRACKING)
                return Result(True, f"rejected jump ({jump_m:.2f} m, {jump_deg:.0f} deg)")
        self._last_raw = (p_raw, q_raw)

        if not tracking_ok:
            self._need_reanchor = True
            self._good_streak = 0
            self._set_hold(HOLD_TRACKING)
            return Result(True, "tracking not normal")

        if pkt.freeze or self.user_frozen:
            self._need_reanchor = True
            self._set_hold(HOLD_FROZEN)
            return Result(True, "frozen")
        if pkt.paused or self.user_paused:
            self._need_reanchor = True
            self._set_hold(HOLD_PAUSED)
            return Result(True, "paused")

        if self._need_reanchor and self._corrected is not None:
            self._good_streak += 1
            if self._good_streak < self.settings.tracking.resume_settle_frames and self.state == HOLD_TRACKING:
                return Result(True, "settling after tracking loss")
            self._reanchor(p_raw, q_raw)
            if self.output is not None:
                self._start_blend(recv_time)
        self._need_reanchor = False
        self._good_streak = 0

        # --- corrected + filtered pose ---------------------------------------
        p_c, q_c = self._correct(p_raw, q_raw)
        p_f = self._pos_filter(p_c, dt)
        q_f = self._rot_filter(q_c, dt)
        self._corrected = (p_f, q_f)
        self.filter_lag = q_norm(q_mul(q_conj(q_c), q_f))

        if self.calibration is None or self._pending_recenter:
            self.calibration = Calibration.from_pose(p_f, q_f)
            self._pending_recenter = False
            self.generation = (self.generation + 1) & 0xFFFFFFFF
            self._blend = None  # the AC side re-anchors on a new generation
            self._yaw_hint = None

        ts = self.settings.transform
        if self.phone_scale is not None:
            ts = replace(ts, translation_scale=self.phone_scale)
        out = map_pose(p_f, q_f, self.calibration, ts, self._yaw_hint)
        self._yaw_hint = out.yaw_unwrapped
        out = self._apply_blend(out, recv_time)
        self.output = out
        self.state = LIVE
        return Result(True, "", True)

    def tick(self, now: float) -> None:
        """Call periodically (e.g. 30 Hz) to detect a silent source."""
        if self.state == PLAYBACK:
            return
        if self._last_recv is not None and now - self._last_recv > STALE_AFTER_S:
            if self.state != HOLD_STALE and self.output is not None:
                self.state = HOLD_STALE
            self._need_reanchor = True
            self.joy = (0.0, 0.0)  # Wi-Fi dropped mid-walk: stop, don't run away
        # GUI freeze/pause should be reflected even without new packets
        if self.state == LIVE and self.user_frozen:
            self._set_hold(HOLD_FROZEN)
        elif self.state == LIVE and self.user_paused:
            self._set_hold(HOLD_PAUSED)

    def seconds_since_packet(self, now: float) -> float | None:
        return None if self._last_recv is None else now - self._last_recv

    # ---------------------------------------------------------------- blend
    def _set_hold(self, st: int) -> None:
        if self.output is None:
            self.state = NO_DATA
        else:
            self.state = st

    def _start_blend(self, now: float) -> None:
        o = self.output
        assert o is not None
        self._blend = _Blend(o.position, _basis_to_q(o.forward, o.up), now, RESUME_BLEND_S)

    def _apply_blend(self, out: CameraOutput, now: float) -> CameraOutput:
        b = self._blend
        if b is None:
            return out
        t = (now - b.t0) / b.duration if b.duration > 0 else 1.0
        if t >= 1.0:
            self._blend = None
            return out
        t = t * t * (3 - 2 * t)  # smoothstep
        pos = tuple(b.start_pos[i] + (out.position[i] - b.start_pos[i]) * t for i in range(3))
        q = q_slerp(b.start_q, _basis_to_q(out.forward, out.up), t)
        f = q_rotate(q, (0.0, 0.0, 1.0))
        u = q_rotate(q, (0.0, 1.0, 0.0))
        return CameraOutput(pos, f, u, out.euler_deg)  # type: ignore[arg-type]


def _basis_to_q(forward: Vec3, up: Vec3) -> Quat:
    """Quaternion taking (0,0,1)->forward and (0,1,0)->up (filming frame;
    used only for interpolation, handedness irrelevant as long as consistent)."""
    from .mathutil import q_from_matrix, v_cross

    f = v_norm(forward)
    r = v_norm(v_cross(up, f))
    u = v_cross(f, r)
    m = [[r[0], u[0], f[0]], [r[1], u[1], f[1]], [r[2], u[2], f[2]]]
    return q_from_matrix(m)
