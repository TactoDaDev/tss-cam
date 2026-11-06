"""Record camera paths (filming-frame output) and play them back.

Files are JSON Lines: a header line, then one line per sample:
  {"t": seconds_since_start, "p": [r,u,f], "f": [..], "u": [..]}
Recorded output is *after* smoothing and scaling, so playback reproduces the
exact move. Playback starts a new generation, so the AC app re-anchors the
path at wherever the virtual camera currently is ("replay this move here").
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .transform import CameraOutput


class PathRecorder:
    def __init__(self) -> None:
        self._fh = None
        self._t0 = 0.0
        self.path: Path | None = None
        self.samples = 0

    @property
    def active(self) -> bool:
        return self._fh is not None

    def start(self, directory: Path) -> Path:
        self.stop()
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / time.strftime("camerapath_%Y%m%d_%H%M%S.jsonl")
        self._fh = open(self.path, "w", encoding="utf-8")
        self._fh.write(json.dumps({"format": "handheldcam-path", "version": 1}) + "\n")
        self._t0 = time.perf_counter()
        self.samples = 0
        return self.path

    def add(self, out: CameraOutput, now: float | None = None) -> None:
        if self._fh is None:
            return
        t = (time.perf_counter() if now is None else now) - self._t0
        self._fh.write(
            json.dumps(
                {"t": round(t, 5), "p": [round(v, 5) for v in out.position],
                 "f": [round(v, 6) for v in out.forward], "u": [round(v, 6) for v in out.up]}
            )
            + "\n"
        )
        self.samples += 1

    def stop(self) -> Path | None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None
        return self.path


@dataclass
class _Sample:
    t: float
    out: CameraOutput


def load_path(path: Path) -> list[_Sample]:
    samples: list[_Sample] = []
    with open(path, encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        if header.get("format") != "handheldcam-path":
            raise ValueError("not a camera path file")
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            samples.append(_Sample(float(d["t"]), CameraOutput(tuple(d["p"]), tuple(d["f"]), tuple(d["u"]))))
    if not samples:
        raise ValueError("camera path is empty")
    return samples


class PathPlayer:
    def __init__(self, samples: list[_Sample], loop: bool = False) -> None:
        self.samples = samples
        self.loop = loop
        self._t0: float | None = None
        self._i = 0

    @property
    def duration(self) -> float:
        return self.samples[-1].t

    def start(self, now: float) -> None:
        self._t0 = now
        self._i = 0

    def sample(self, now: float) -> CameraOutput | None:
        """Linear interpolation between recorded samples; None when finished."""
        if self._t0 is None:
            return None
        t = now - self._t0
        if t > self.duration:
            if not self.loop or self.duration <= 0:
                return None
            t %= self.duration
            self._i = 0
        s = self.samples
        while self._i + 1 < len(s) and s[self._i + 1].t < t:
            self._i += 1
        if self._i + 1 >= len(s):
            return s[-1].out
        a, b = s[self._i], s[self._i + 1]
        if t <= a.t:
            return a.out
        k = (t - a.t) / max(1e-9, b.t - a.t)

        def lerp(x, y):
            return tuple(x[j] + (y[j] - x[j]) * k for j in range(3))

        return CameraOutput(lerp(a.out.position, b.out.position), lerp(a.out.forward, b.out.forward),
                            lerp(a.out.up, b.out.up))
