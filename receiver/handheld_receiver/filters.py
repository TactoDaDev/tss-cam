"""Adaptive smoothing: the 1-euro filter (Casiez, Roussel & Vogel, CHI 2012).

Why this filter: when the phone is held still it applies heavy low-pass
filtering (kills ARKit micro-jitter), and as the phone moves faster the cutoff
rises so the camera does not lag behind deliberate motion. That is exactly the
trade-off handheld filming needs. Time steps come from the phone's capture
timestamps, so Wi-Fi jitter does not affect the filter.

  min_cutoff (Hz): smoothing when still. Lower = smoother, more lag at rest.
  beta:            speed coefficient. Higher = less lag during fast moves.
  d_cutoff (Hz):   cutoff for the speed estimate (rarely needs changing).

Setting enabled=False passes values through untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .mathutil import Quat, Vec3, q_dot, q_norm


def _alpha(cutoff: float, dt: float) -> float:
    tau = 1.0 / (2.0 * math.pi * cutoff)
    return 1.0 / (1.0 + tau / dt)


@dataclass
class OneEuroParams:
    min_cutoff: float = 1.0
    beta: float = 0.5
    d_cutoff: float = 1.0
    enabled: bool = True


class OneEuroVec:
    """1-euro filter over an N-dimensional vector sharing one adaptive cutoff
    (based on the vector's speed magnitude), so all axes stay in sync."""

    MAX_DT = 0.5  # a longer gap means the stream was interrupted: reset.

    def __init__(self, params: OneEuroParams):
        self.params = params
        self.reset()

    def reset(self) -> None:
        self._x: list[float] | None = None
        self._dx: list[float] | None = None

    def __call__(self, x: tuple[float, ...], dt: float) -> tuple[float, ...]:
        p = self.params
        if not p.enabled:
            self._x = list(x)
            self._dx = [0.0] * len(x)
            return tuple(x)
        if self._x is None or self._dx is None or dt <= 0 or dt > self.MAX_DT:
            self._x = list(x)
            self._dx = [0.0] * len(x)
            return tuple(x)
        dx = [(x[i] - self._x[i]) / dt for i in range(len(x))]
        ad = _alpha(p.d_cutoff, dt)
        self._dx = [self._dx[i] + ad * (dx[i] - self._dx[i]) for i in range(len(x))]
        speed = math.sqrt(sum(v * v for v in self._dx))
        cutoff = p.min_cutoff + p.beta * speed
        a = _alpha(cutoff, dt)
        self._x = [self._x[i] + a * (x[i] - self._x[i]) for i in range(len(x))]
        return tuple(self._x)


class OneEuroPosition(OneEuroVec):
    def __call__(self, x: Vec3, dt: float) -> Vec3:  # type: ignore[override]
        return super().__call__(x, dt)  # type: ignore[return-value]


class OneEuroQuat:
    """1-euro filter for orientations. Works on quaternion components with
    hemisphere continuity and renormalisation; the speed term is the angular
    rate (rad/s), so beta has the same "feel" as for position (m/s)."""

    def __init__(self, params: OneEuroParams):
        self.params = params
        self.reset()

    def reset(self) -> None:
        self._q: Quat | None = None
        self._rate = 0.0

    def __call__(self, q: Quat, dt: float) -> Quat:
        p = self.params
        if self._q is not None and q_dot(q, self._q) < 0:
            q = (-q[0], -q[1], -q[2], -q[3])
        if not p.enabled or self._q is None or dt <= 0 or dt > OneEuroVec.MAX_DT:
            self._q = q_norm(q)
            self._rate = 0.0
            return self._q
        d = min(1.0, abs(q_dot(q_norm(q), self._q)))
        rate = 2.0 * math.acos(d) / dt
        self._rate += _alpha(p.d_cutoff, dt) * (rate - self._rate)
        a = _alpha(p.min_cutoff + p.beta * self._rate, dt)
        prev = self._q
        self._q = q_norm(tuple(prev[i] + a * (q[i] - prev[i]) for i in range(4)))  # type: ignore[arg-type]
        return self._q
