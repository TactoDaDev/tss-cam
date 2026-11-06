"""Coordinate conversion: ARKit pose -> camera pose in the *filming frame*.

Frames
------
ARKit world (A): right-handed, +Y up (gravity), origin and heading wherever the
    ARKit session started. Units: metres.
Phone camera (C): +X screen-right, +Y screen-up, looks along -Z. The phone
    sends q_AC (C -> A) and the camera position p_A.
Continuity-corrected world (A'): A' = T_off * A, where T_off is a yaw rotation
    plus translation that the pipeline changes whenever tracking is re-anchored
    (after tracking loss, Wi-Fi drop-outs, freeze/clutch). It keeps the corrected
    pose continuous so the camera never jumps. Gravity is never touched.
Calibration frame (K): origin at the calibrated phone position, rotated by the
    calibrated heading psi0 about +Y. So "forward" in K is wherever the phone
    pointed (horizontally) when you pressed Recenter.
Filming frame (F): K with axes renamed to what the game needs:
    right = +X_K, up = +Y_K, forward = -Z_K.

Maths (column vectors, Ry(a) = rotation by a about +Y):
    d      = Ry(-psi0) (p_A' - c0)                    # position in K
    q_rel  = qy(-psi0) * q_A'C                        # orientation in K
    t_F    = S d,            S = diag(1, 1, -1)       # position in F
    f_F    = S q_rel (0,0,-1)   (camera forward)
    u_F    = S q_rel (0,1,0)    (camera up)
Then axis mapping, per-axis scale/invert, global translation scale, start
offset, and optional rotation sensitivity (applied as Y-X-Z Euler scaling).

The filming frame is converted to Assetto Corsa world space by the Lua app:
    world = anchor_pos + R_anchor * t_F.x + Y * t_F.y + F_anchor * t_F.z
where F_anchor is a horizontal unit vector (the virtual camera's heading when
you engaged) and R_anchor = cross(Y, F_anchor) is its right-hand side in AC's
convention (see ac/.../hcmath.lua for why). Look/up vectors use the same map.
Because both ends describe *physical* right/up/forward, the ARKit (right-
handed) to AC mapping is a reflection by construction; no Euler angles cross
the boundary, only direction vectors, so there is no handedness ambiguity in
the rotation itself.

Pitch and roll are always relative to gravity (absolute), heading and position
are relative to the calibration. That is what makes "tilt the phone down to
film the wheels" look right regardless of where you started.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import TransformSettings, parse_axis_map
from .mathutil import (
    Quat,
    Vec3,
    heading_of,
    q_from_euler_yxz,
    q_mul,
    q_rotate,
    q_to_euler_yxz,
    q_yaw,
    v_sub,
    wrap_angle,
)

FORWARD_C: Vec3 = (0.0, 0.0, -1.0)
UP_C: Vec3 = (0.0, 1.0, 0.0)
MAX_PITCH = math.radians(89.5)


@dataclass(frozen=True)
class Calibration:
    position: Vec3
    yaw: float  # radians, heading of the phone at calibration time

    @staticmethod
    def from_pose(p: Vec3, q: Quat) -> "Calibration":
        return Calibration(position=p, yaw=heading_of(q))


@dataclass(frozen=True)
class CameraOutput:
    """Camera pose in the filming frame (right, up, forward): position in
    metres (already scaled), unit forward/up vectors, and the Y-X-Z Euler
    angles (degrees: yaw left+, pitch up+, roll) for display only."""

    position: Vec3
    forward: Vec3
    up: Vec3
    euler_deg: Vec3 = (0.0, 0.0, 0.0)
    # Phone yaw relative to the calibration heading, unwrapped (may exceed
    # +/-180 deg). Feed it back as `yaw_hint` so a scaled/inverted yaw keeps
    # turning smoothly when you turn all the way round.
    yaw_unwrapped: float = 0.0


def _needs_euler(ts: TransformSettings) -> bool:
    return (
        abs(ts.rotation_sensitivity - 1.0) > 1e-9
        or abs(ts.roll_sensitivity - 1.0) > 1e-9
        or ts.invert_yaw
        or ts.invert_pitch
        or ts.invert_roll
        or ts.roll_lock
        or abs(ts.yaw_offset_deg) > 1e-9
    )


def adjust_rotation(q_rel: Quat, ts: TransformSettings, yaw_hint: float | None = None) -> Quat:
    """yaw_hint: previous unwrapped yaw (radians). Without it the relative yaw
    lives in (-180, 180] and a sensitivity != 1 would jump when you turn
    through 180 deg from the calibration heading."""
    if not _needs_euler(ts):
        return q_rel
    yaw, pitch, roll = q_to_euler_yxz(q_rel)
    if yaw_hint is not None:
        yaw = yaw_hint + wrap_angle(yaw - yaw_hint)
    yaw *= ts.rotation_sensitivity * (-1.0 if ts.invert_yaw else 1.0)
    yaw += math.radians(ts.yaw_offset_deg)
    pitch *= ts.rotation_sensitivity * (-1.0 if ts.invert_pitch else 1.0)
    pitch = max(-MAX_PITCH, min(MAX_PITCH, pitch))
    roll = 0.0 if ts.roll_lock else roll * ts.roll_sensitivity * (-1.0 if ts.invert_roll else 1.0)
    return q_from_euler_yxz(yaw, pitch, roll)


def map_pose(p: Vec3, q: Quat, calib: Calibration, ts: TransformSettings, yaw_hint: float | None = None) -> CameraOutput:
    """p, q: continuity-corrected ARKit pose (A'). Returns the filming-frame pose."""
    to_k = q_yaw(-calib.yaw)
    d = q_rotate(to_k, v_sub(p, calib.position))
    q_rel = q_mul(to_k, q)

    # Translation: K -> F, axis map, per-axis scale/invert, global scale, offset.
    t_in = (d[0], d[1], -d[2])
    amap = parse_axis_map(ts.axis_map)
    t = [sign * t_in[idx] for idx, sign in amap]
    per_axis = (
        ts.scale_right * (-1.0 if ts.invert_right else 1.0),
        ts.scale_up * (-1.0 if ts.invert_up else 1.0),
        ts.scale_forward * (-1.0 if ts.invert_forward else 1.0),
    )
    off = ts.start_offset
    pos = tuple(t[i] * per_axis[i] * ts.translation_scale + off[i] for i in range(3))

    raw_yaw = q_to_euler_yxz(q_rel)[0]
    yaw_unwrapped = raw_yaw if yaw_hint is None else yaw_hint + wrap_angle(raw_yaw - yaw_hint)
    q_rel = adjust_rotation(q_rel, ts, yaw_hint)
    f = q_rotate(q_rel, FORWARD_C)
    u = q_rotate(q_rel, UP_C)
    yaw, pitch, roll = q_to_euler_yxz(q_rel)
    return CameraOutput(
        position=pos,  # type: ignore[arg-type]
        forward=(f[0], f[1], -f[2]),
        up=(u[0], u[1], -u[2]),
        euler_deg=(math.degrees(yaw), math.degrees(pitch), math.degrees(roll)),
        yaw_unwrapped=yaw_unwrapped,
    )
