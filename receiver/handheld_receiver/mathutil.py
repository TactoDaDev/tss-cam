"""Small, dependency-free 3D maths helpers (vectors as tuples, quaternions x,y,z,w).

Conventions (all right-handed, ARKit style, unless a function says otherwise):
  * +Y is up (gravity aligned), the camera looks down its local -Z,
    its local +X is screen-right and +Y is screen-up.
  * Quaternions are Hamilton (x, y, z, w) and rotate camera-local vectors into
    world space: v_world = q * v_local * conj(q).
  * Euler decomposition is Y-X-Z (yaw about world Y, then pitch about the new X,
    then roll about the new Z): R = Ry(yaw) @ Rx(pitch) @ Rz(roll).
    yaw > 0 turns left (counter-clockwise seen from above), pitch > 0 looks up,
    roll > 0 rolls counter-clockwise as seen from behind the camera.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

IDENTITY_Q: Quat = (0.0, 0.0, 0.0, 1.0)


def v_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def v_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def v_scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def v_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def v_cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def v_len(a: Vec3) -> float:
    return math.sqrt(v_dot(a, a))


def v_norm(a: Vec3) -> Vec3:
    n = v_len(a)
    if n < 1e-12:
        raise ValueError("cannot normalise zero vector")
    return (a[0] / n, a[1] / n, a[2] / n)


def q_norm(q: Quat) -> Quat:
    n = math.sqrt(q[0] ** 2 + q[1] ** 2 + q[2] ** 2 + q[3] ** 2)
    if n < 1e-12:
        return IDENTITY_Q
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def q_mul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def q_conj(q: Quat) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def q_dot(a: Quat, b: Quat) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def q_rotate(q: Quat, v: Vec3) -> Vec3:
    # v' = v + 2w(u x v) + 2 u x (u x v)
    u = (q[0], q[1], q[2])
    w = q[3]
    t = v_scale(v_cross(u, v), 2.0)
    return v_add(v_add(v, v_scale(t, w)), v_cross(u, t))


def q_from_axis_angle(axis: Vec3, angle: float) -> Quat:
    ax = v_norm(axis)
    s = math.sin(angle / 2.0)
    return (ax[0] * s, ax[1] * s, ax[2] * s, math.cos(angle / 2.0))


def q_yaw(angle: float) -> Quat:
    """Rotation about world +Y."""
    return (0.0, math.sin(angle / 2.0), 0.0, math.cos(angle / 2.0))


def q_angle_between(a: Quat, b: Quat) -> float:
    d = min(1.0, abs(q_dot(q_norm(a), q_norm(b))))
    return 2.0 * math.acos(d)


def q_to_matrix(q: Quat) -> list[list[float]]:
    x, y, z, w = q
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def q_from_matrix(m: list[list[float]]) -> Quat:
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s, 0.25 * s)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s, (m[2][1] - m[1][2]) / s)
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[0][2] - m[2][0]) / s)
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[1][0] - m[0][1]) / s)
    return q_norm(q)


def q_from_euler_yxz(yaw: float, pitch: float, roll: float) -> Quat:
    return q_mul(q_mul(q_yaw(yaw), q_from_axis_angle((1, 0, 0), pitch)), q_from_axis_angle((0, 0, 1), roll))


def q_to_euler_yxz(q: Quat) -> tuple[float, float, float]:
    """Returns (yaw, pitch, roll). Near +/-90 deg pitch, roll is folded into yaw."""
    m = q_to_matrix(q)
    sp = max(-1.0, min(1.0, -m[1][2]))
    pitch = math.asin(sp)
    if abs(sp) < 0.9999:
        yaw = math.atan2(m[0][2], m[2][2])
        roll = math.atan2(m[1][0], m[1][1])
    else:  # gimbal lock: camera pointing straight up/down
        yaw = math.atan2(-m[2][0], m[0][0])
        roll = 0.0
    return yaw, pitch, roll


def heading_of(q: Quat) -> float:
    """Yaw of a camera orientation (robust when pointing almost straight down/up)."""
    return q_to_euler_yxz(q)[0]


def q_slerp(a: Quat, b: Quat, t: float) -> Quat:
    d = q_dot(a, b)
    if d < 0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9995:
        return q_norm(tuple(a[i] + (b[i] - a[i]) * t for i in range(4)))  # type: ignore[arg-type]
    th = math.acos(d)
    s = math.sin(th)
    wa = math.sin((1 - t) * th) / s
    wb = math.sin(t * th) / s
    return q_norm(tuple(a[i] * wa + b[i] * wb for i in range(4)))  # type: ignore[arg-type]


def wrap_angle(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi
