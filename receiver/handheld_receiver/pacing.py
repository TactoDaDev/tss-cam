"""Frame pacing for the phone's live view (reference implementation; the
phone's FramePacer in LiveVideo.swift is a line-by-line port, both tested
here and in the GitHub build).

Wi-Fi delivers frames unevenly: two arrive close together, then a gap. Shown
as they arrive, motion stutters. The pacer gives every frame a display time
derived from when the PC *captured* it:

    show_at = pc_capture_time + offset + delay

offset = the smallest (arrival - capture) seen over the last few seconds,
i.e. the fastest trip, which also absorbs the unknown clock difference
between PC and phone. delay = how much later than the fastest trip a frame
may arrive and still be shown on time: in "auto" mode a high percentile of
recent lateness (capped; the phone's Smoothness setting picks 90 / 97 / 99 %),
so occasional slow frames don't stutter and the added latency stays small.

offset + delay is the target latency. The latency actually used rises to the
target at once (plus 2 ms headroom) but only comes down slowly, and only
when the target is clearly lower (`decay_per_s`), so the small frame-to-frame
changes of the estimate don't make the display uneven.
Rotation still feels instant because timewarp re-aims whatever frame is on
screen.
"""

from __future__ import annotations

from collections import deque


class FramePacer:
    PERCENTILES = {"low latency": 0.90, "balanced": 0.97, "smooth": 0.99}
    HEADROOM = 0.002  # s

    def __init__(self, window_s: float = 3.0, fixed_delay_s: float | None = None, max_delay_s: float = 0.05,
                 percentile: float = 0.97, decay_per_s: float = 0.002):
        self.window = window_s
        self.percentile = percentile
        self.fixed = fixed_delay_s
        self.max_delay = max_delay_s
        self.decay = decay_per_s
        self._mins: deque[tuple[float, float]] = deque()  # (arrival, arrival - capture), increasing
        self._late: deque[tuple[float, float]] = deque()  # (arrival, lateness)
        self._queue: list[tuple[float, object]] = []  # (show_at, frame) in display order
        self._latency: float | None = None  # latency in use (phone clock - PC clock)
        self._offset = 0.0
        self._last_arrival = 0.0

    def reset(self) -> None:
        self._mins.clear()
        self._late.clear()
        self._queue.clear()
        self._latency = None

    def _target_delay(self) -> float:
        if self.fixed is not None:
            return self.fixed
        if not self._late:
            return 0.0
        v = sorted(x for _, x in self._late)
        return min(self.max_delay, v[int(self.percentile * (len(v) - 1))])

    def delay(self) -> float:
        """Extra latency in use on top of the fastest trip (s)."""
        if self._latency is None:
            return 0.0
        return max(0.0, self._latency - self._offset)

    def push(self, frame, capture: float, arrival: float) -> None:
        d = arrival - capture
        while self._mins and self._mins[-1][1] >= d:
            self._mins.pop()
        self._mins.append((arrival, d))
        while self._mins and self._mins[0][0] < arrival - self.window:
            self._mins.popleft()
        offset = self._mins[0][1]
        self._offset = offset
        self._late.append((arrival, d - offset))
        while self._late and self._late[0][0] < arrival - self.window:
            self._late.popleft()
        target = offset + self._target_delay()
        if self._latency is None:
            self._latency = target
        elif target > self._latency:
            # rise at once, with a little headroom so it doesn't creep up in tiny steps
            self._latency = target + (0.0 if self.fixed is not None else self.HEADROOM)
        elif target < self._latency - 2 * self.HEADROOM:
            self._latency = max(target, self._latency - self.decay * max(0.0, arrival - self._last_arrival))
        self._latency = min(self._latency, offset + self.max_delay)
        self._last_arrival = arrival
        show_at = capture + self._latency
        i = len(self._queue)
        while i > 0 and self._queue[i - 1][0] > show_at:
            i -= 1
        self._queue.insert(i, (show_at, frame))

    def pop_due(self, now: float):
        """Newest frame whose time has come (older due ones are skipped), or None."""
        due = None
        while self._queue and self._queue[0][0] <= now:
            due = self._queue.pop(0)[1]
        return due

    @property
    def queued(self) -> int:
        return len(self._queue)
