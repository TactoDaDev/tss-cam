"""Assetto Corsa hook. The camera already works through AcHandCam's shared memory (the receiver writes the pose, its
or our CSP app reads it). Commands and capabilities need our CSP app's command ring (plan phase 6); until then this
hook answers that car controls aren't available yet."""
from __future__ import annotations

from . import GameHook


class AssettoHook(GameHook):
    key = "assetto"
    title = "Assetto Corsa"

    def forward(self, packet: bytes) -> bool:
        return False  # ponytail: no command ring yet; phase 6 adds the shared-memory ring + capability block
