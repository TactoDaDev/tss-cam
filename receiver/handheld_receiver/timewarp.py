"""Rotational timewarp: reference maths for the iPhone live view.

The receiver only uses `warp_gains()` (it tells the phone how the game camera
responds to phone rotation). Everything else here is the reference
implementation that `ios/.../Timewarp.swift` is a line-by-line port of; the
tests in tests/test_timewarp.py check it against a real pinhole projection.

Idea
----
A game frame arrives showing the view for the phone orientation q_then (the
ARKit pose that drove the camera when the frame was rendered). By the time it
is displayed the phone is at q_now. For a pure rotation the image can be
corrected exactly (for every depth) with a homography, so the picture on the
phone points where the phone points *now*, and only the content (cars,
translation) is late.

Conventions
-----------
* Orientations are ARKit camera-to-world quaternions (x, y, z, w) in the
  filming frame: camera x = screen right, y = screen up, looks down -z.
* Image coordinates: pixels relative to the image centre, x right, y DOWN
  (UIKit/Metal style), focal length f in pixels.
* `homography_dst_to_src` maps a pixel of the displayed (warped) image to the
  pixel of the received frame to sample: x_src ~ H * (x_dst, y_dst, 1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .mathutil import (
    IDENTITY_Q,
    Quat,
    Vec3,
    q_conj,
    q_from_axis_angle,
    q_from_euler_yxz,
    q_mul,
    q_norm,
    q_rotate,
    q_slerp,
    q_to_euler_yxz,
    q_to_matrix,
    wrap_angle,
)

Mat3 = list[list[float]]


@dataclass(frozen=True)
class WarpGains:
    """How the game camera's yaw/pitch/roll follow the phone's (receiver
    sensitivity and invert settings). 1, 1, 1 = one to one."""

    yaw: float = 1.0
    pitch: float = 1.0
    roll: float = 1.0

    def is_identity(self) -> bool:
        return abs(self.yaw - 1) < 1e-9 and abs(self.pitch - 1) < 1e-9 and abs(self.roll - 1) < 1e-9


def warp_gains(ts) -> WarpGains:
    """Gains from TransformSettings, mirroring transform.adjust_rotation()."""
    yaw = ts.rotation_sensitivity * (-1.0 if ts.invert_yaw else 1.0)
    pitch = ts.rotation_sensitivity * (-1.0 if ts.invert_pitch else 1.0)
    roll = 0.0 if ts.roll_lock else ts.roll_sensitivity * (-1.0 if ts.invert_roll else 1.0)
    return WarpGains(yaw, pitch, roll)


def relative_rotation(q_then: Quat, q_now: Quat, gains: WarpGains = WarpGains()) -> Quat:
    """Camera-local rotation from the rendered view to the view to display.

    With unit gains this is conj(q_then) * q_now. Otherwise both orientations
    are mapped the way the receiver maps them (Y-X-Z Euler, scaled), so a
    0.5x yaw sensitivity warps half as far, an inverted yaw warps the other way.
    The calibration yaw cancels out, so it isn't needed.
    """
    if gains.is_identity():
        return q_norm(q_mul(q_conj(q_then), q_now))
    y0, p0, r0 = q_to_euler_yxz(q_then)
    y1, p1, r1 = q_to_euler_yxz(q_now)
    a = q_from_euler_yxz(0.0, gains.pitch * p0, gains.roll * r0)
    b = q_from_euler_yxz(gains.yaw * wrap_angle(y1 - y0), gains.pitch * p1, gains.roll * r1)
    return q_norm(q_mul(q_conj(a), b))


def clamp_rotation(q: Quat, max_angle: float) -> Quat:
    """Limit the warp angle (beyond ~30 deg the picture would mostly be off-screen)."""
    q = q_norm(q)
    if q[3] < 0:
        q = (-q[0], -q[1], -q[2], -q[3])
    angle = 2.0 * math.acos(min(1.0, q[3]))
    if angle <= max_angle or angle < 1e-9:
        return q
    return q_slerp(IDENTITY_Q, q, max_angle / angle)


def homography_dst_to_src(r_rel: Quat, f_px: float) -> Mat3:
    """H = K S R S K^-1 with S = diag(1, -1, -1) (ARKit camera axes -> image
    axes x right, y down, z forward) and K = diag(f, f, 1) (centred pixels)."""
    r = q_to_matrix(r_rel)
    s = (1.0, -1.0, -1.0)
    # S R S
    srs = [[s[i] * r[i][j] * s[j] for j in range(3)] for i in range(3)]
    # K (S R S) K^-1
    k = (f_px, f_px, 1.0)
    return [[k[i] * srs[i][j] / k[j] for j in range(3)] for i in range(3)]


def apply_h(h: Mat3, x: float, y: float) -> tuple[float, float, float]:
    """Returns (x', y', w). w <= 0 means the point is behind the source camera."""
    X = h[0][0] * x + h[0][1] * y + h[0][2]
    Y = h[1][0] * x + h[1][1] * y + h[1][2]
    W = h[2][0] * x + h[2][1] * y + h[2][2]
    if W <= 1e-9:
        return (0.0, 0.0, W)
    return (X / W, Y / W, W)


def focal_px(image_height_px: float, vfov_deg: float) -> float:
    return (image_height_px / 2.0) / math.tan(math.radians(vfov_deg) / 2.0)


# ------------------------------------------------------- pinhole reference


def project(q_cam: Quat, cam_pos: Vec3, point: Vec3, f_px: float) -> tuple[float, float] | None:
    """Ground truth: project a world point with an ARKit-convention camera."""
    d = (point[0] - cam_pos[0], point[1] - cam_pos[1], point[2] - cam_pos[2])
    c = q_rotate(q_conj(q_cam), d)  # camera local: x right, y up, -z forward
    if c[2] >= -1e-6:
        return None
    z = -c[2]
    return (f_px * c[0] / z, -f_px * c[1] / z)


# --------------------------------------------- gyro (CoreMotion) refinement
#
# ARKit poses arrive at 60 Hz and are already ~1 frame old. CoreMotion's
# attitude runs at 100-200 Hz with less delay, so "now" is refined as
#     q_now = q_ar(t_ar) * C^-1 * (att(t_ar)^-1 * att(now)) * C
# where att is the device attitude and C rotates filming-camera axes into
# device axes (a multiple of 90 deg about z, depending on how the phone is
# held). C is not assumed: it is chosen from the four candidates by comparing
# ARKit's and CoreMotion's rotation over the same interval.

# Candidates: (C as degrees about z, attitude convention). `inverted` covers
# an attitude quaternion that maps reference -> device instead of
# device -> reference; the estimator decides, nothing is assumed.
CANDIDATES = tuple((deg, inv) for inv in (False, True) for deg in (0.0, 90.0, 180.0, -90.0))


def z_candidate(deg: float) -> Quat:
    return q_from_axis_angle((0.0, 0.0, 1.0), math.radians(deg))


def device_delta_in_camera(c: Quat, att_then: Quat, att_now: Quat, inverted: bool = False) -> Quat:
    if inverted:
        d = q_mul(att_then, q_conj(att_now))
    else:
        d = q_mul(q_conj(att_then), att_now)
    return q_norm(q_mul(q_mul(q_conj(c), d), c))


def rotation_angle(q: Quat) -> float:
    return 2.0 * math.acos(min(1.0, abs(q_norm(q)[3])))


class AlignmentEstimator:
    """Accumulates, per candidate, the disagreement between ARKit's and
    CoreMotion's rotation over the same intervals. Only intervals with enough
    rotation count. `best()` returns a candidate index once one is clearly
    better than every other, else None (then only ARKit poses are used)."""

    MIN_ANGLE = math.radians(3.0)
    MIN_SAMPLES = 8

    def __init__(self) -> None:
        self.err = [0.0] * len(CANDIDATES)
        self.samples = 0
        self._cands = [(z_candidate(d), inv) for d, inv in CANDIDATES]

    def add(self, ar_then: Quat, ar_now: Quat, att_then: Quat, att_now: Quat) -> None:
        d_ar = q_mul(q_conj(ar_then), ar_now)
        if rotation_angle(d_ar) < self.MIN_ANGLE:
            return
        for i, (c, inv) in enumerate(self._cands):
            d_dev = device_delta_in_camera(c, att_then, att_now, inv)
            self.err[i] = self.err[i] * 0.97 + rotation_angle(q_mul(q_conj(d_ar), d_dev))
        self.samples += 1

    def best(self) -> int | None:
        if self.samples < self.MIN_SAMPLES:
            return None
        order = sorted(range(len(self.err)), key=lambda i: self.err[i])
        b, second = order[0], order[1]
        if self.err[b] * 3.0 < self.err[second]:
            return b
        return None


def refine_now(q_ar: Quat, c: Quat, att_at_ar: Quat, att_now: Quat, inverted: bool = False) -> Quat:
    return q_norm(q_mul(q_ar, device_delta_in_camera(c, att_at_ar, att_now, inverted)))


# ------------------------------------------------- v4: lenses and viewfinder
#
# The game always renders a landscape frame (the AC window). The phone shows
# a *viewfinder* cut out of it: upright for the way the phone is held
# (portrait = a tall slice, landscape = a wide slice), with the phone screen's
# aspect ratio. Everything is expressed as half-tangents of the field of view
# ("half-tans"): for a pinhole camera a point at normalised image position
# x in [-1, 1] lies on the ray (x * half_tan_x, ..., -1).

# 35 mm-equivalent sensor: the iPhone's "24 mm" is defined on the 4:3 photo
# diagonal (43.27 mm), so the long side is 34.6 mm (half: 17.3 mm). Video uses
# the full sensor width, so the viewfinder's long side follows this.
SENSOR_HALF_LONG_MM = 17.3


def lens_half_tan_long(focal_mm: float) -> float:
    return SENSOR_HALF_LONG_MM / max(1.0, focal_mm)


def viewfinder_half_tans(t_long: float, vf_aspect: float, portrait: bool) -> tuple[float, float]:
    """(x, y) half-tans of an upright viewfinder whose long side has half-tan
    t_long. vf_aspect = long / short side (e.g. 19.5/9)."""
    t_short = t_long / vf_aspect
    return (t_short, t_long) if portrait else (t_long, t_short)


def game_vfov_for_lens(focal_mm: float, vf_aspect: float, portrait: bool, game_aspect: float) -> float:
    """Vertical FOV (deg) AC must render so the viewfinder for this lens fits
    inside the game frame exactly (the AC app uses the same formula)."""
    tvx, tvy = viewfinder_half_tans(lens_half_tan_long(focal_mm), vf_aspect, portrait)
    tgy = max(tvy, tvx / game_aspect)
    return math.degrees(2.0 * math.atan(tgy))


def fit_viewfinder(tg: tuple[float, float], vf_aspect: float, portrait: bool, margin: float) -> tuple[float, float]:
    """Largest upright viewfinder of the given aspect inside the game frame
    (half-tans tg), shrunk by `margin` (0.06 = 6 % per side) so timewarp has
    real picture to turn into instead of black edges."""
    ax, ay = ((1.0 / vf_aspect, 1.0) if portrait else (1.0, 1.0 / vf_aspect))
    s = min(tg[0] / ax, tg[1] / ay) * (1.0 - margin)
    return ax * s, ay * s


def vf_sample(r_rel: Quat, xv: float, yv: float, tv: tuple[float, float], tg: tuple[float, float]):
    """Viewfinder point (xv, yv in [-1, 1], y up) -> texture uv in the game
    frame (u right, v DOWN, 0..1), or None if it is behind the game camera.
    r_rel = relative_rotation(q_game, q_viewfinder) (identity = no warp)."""
    ray = (xv * tv[0], yv * tv[1], -1.0)
    g = q_rotate(r_rel, ray)
    if -g[2] <= 1e-9:
        return None
    u = (g[0] / -g[2]) / tg[0]
    v = (g[1] / -g[2]) / tg[1]
    return ((u + 1.0) * 0.5, (1.0 - v) * 0.5)


def warp_fraction(r_rel: Quat, tv: tuple[float, float], tg: tuple[float, float], iters: int = 12) -> float:
    """Largest k in [0, 1] such that the viewfinder warped by k * r_rel still
    lies entirely inside the game frame - so a warp never shows black edges;
    when there isn't enough picture it corrects as much as it can. (A
    rectangle maps to a convex quad, so checking the 4 corners is enough.)"""

    def inside(k: float) -> bool:
        r = q_slerp(IDENTITY_Q, r_rel, k)
        for xv in (-1.0, 1.0):
            for yv in (-1.0, 1.0):
                uv = vf_sample(r, xv, yv, tv, tg)
                if uv is None or not (-1e-6 <= uv[0] <= 1 + 1e-6 and -1e-6 <= uv[1] <= 1 + 1e-6):
                    return False
        return True

    if inside(1.0):
        return 1.0
    if not inside(0.0):
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        if inside(mid):
            lo = mid
        else:
            hi = mid
    return lo


def project_upright(q_cam: Quat, cam_pos: Vec3, point: Vec3, t: tuple[float, float]):
    """Ground truth for tests: normalised (x, y up) image position of a world
    point for a camera with half-tans t, or None if behind."""
    d = (point[0] - cam_pos[0], point[1] - cam_pos[1], point[2] - cam_pos[2])
    c = q_rotate(q_conj(q_cam), d)
    if c[2] >= -1e-9:
        return None
    return ((c[0] / -c[2]) / t[0], (c[1] / -c[2]) / t[1])


# ------------------------------------------------- 4.1: exact rendered view
def warp_rotation(q_then: Quat, filter_lag: Quat, render_rot: Quat, q_now: Quat,
                  gains: WarpGains = WarpGains()) -> Quat:
    """Camera-local rotation from the view AC actually showed to the view to
    show now. q_then: phone pose (ARKit) the frame was rendered from;
    filter_lag: the receiver's smoothing as a phone-local rotation (header);
    render_rot: the AC app's smoothing as a game-local rotation (header)."""
    eff = q_norm(q_mul(q_then, filter_lag))
    return q_norm(q_mul(q_conj(render_rot), relative_rotation(eff, q_now, gains)))


# --------------------------------------- 4.1: sensor-sized phone viewfinder
# The phone shows the game like the iPhone Camera app shows its sensor: a
# fixed 4:3 area of the (portrait) screen whose short side is the screen's
# width and is the 4:3 sensor's short side at the chosen focal length. So the
# scale on screen depends only on the lens, never on how the phone is held.
SENSOR_ASPECT = 4.0 / 3.0


def sensor_half_tans(focal_mm: float) -> tuple[float, float]:
    """(long, short) half-tangents of the 4:3 sensor at this focal length."""
    t_long = lens_half_tan_long(focal_mm)
    return t_long, t_long / SENSOR_ASPECT


def screen_px_per_tan(screen_short_px: float, focal_mm: float) -> float:
    return (screen_short_px / 2.0) / sensor_half_tans(focal_mm)[1]


def from_screen(orientation: int, x: float, y: float) -> tuple[float, float]:
    """Screen (portrait-locked, y up) -> viewer (upright) coordinates.
    orientation: 0 portrait, 1 landscape port-right, 2 landscape port-left
    (ViewOrientation in Orientation.swift)."""
    if orientation == 1:
        return -y, x
    if orientation == 2:
        return y, -x
    return x, y


def screen_sample(r_warp: Quat, sx: float, sy: float, orientation: int, k_px: float, tg: tuple[float, float]):
    """Screen pixel (relative to the sensor area's centre, y up) -> game
    texture uv (v down), or None if behind the game camera. Same maths as the
    phone's Metal shader."""
    vx, vy = from_screen(orientation, sx, sy)
    ray = (vx / k_px, vy / k_px, -1.0)
    g = q_rotate(r_warp, ray)
    if -g[2] <= 1e-9:
        return None
    u = (g[0] / -g[2]) / tg[0]
    v = (g[1] / -g[2]) / tg[1]
    return (u + 1.0) / 2.0, (1.0 - v) / 2.0


# ------------------------------------------- 4.2: the picture fills the screen
def view_coverage(focal_mm: float) -> float:
    """0 = the picture fills only the 4:3 sensor area (the .5 lens, as in the
    Camera app); 1 = the whole screen (1x and longer). Eases in 20-24 mm."""
    t = min(1.0, max(0.0, (focal_mm - 20.0) / 4.0))
    return t * t * (3 - 2 * t)


def view_half_tans(focal_mm: float, screen_aspect: float, portrait: bool) -> tuple[float, float]:
    """Half-tangents (x, y) of what the phone shows, in the viewer's frame."""
    t_short = sensor_half_tans(focal_mm)[1]
    c = view_coverage(focal_mm)
    a = max(SENSOR_ASPECT, screen_aspect)
    t_long = t_short * (SENSOR_ASPECT * (1 - c) + a * c)
    return (t_short, t_long) if portrait else (t_long, t_short)


def game_vfov_for_view(focal_mm: float, screen_aspect: float, portrait: bool, game_aspect: float) -> float:
    """Vertical FOV (deg) AC renders so its frame covers what the phone shows
    (the AC app's hcmath.fovForView)."""
    ex, ey = view_half_tans(focal_mm, screen_aspect, portrait)
    return math.degrees(2 * math.atan(max(ey, ex / max(0.1, game_aspect))))
