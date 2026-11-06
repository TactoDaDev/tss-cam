"""Game hooks: how the receiver reaches each game. One is active at a time (picked by the user or auto-detected)."""
from __future__ import annotations

from typing import Callable

GAMES = {"beamng": "BeamNG.drive", "assetto": "Assetto Corsa"}


class GameHook:
    """Base: forwards our command/stick packets to the game, hands the game's answers and capabilities back."""
    key = ""
    title = ""

    def __init__(self, on_message: Callable[[bytes], None]):
        self.on_message = on_message  # a TSCP/TSCA datagram from the game, for the hub
        self.last_game = 0.0          # monotonic time the game was last heard from

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def forward(self, packet: bytes) -> bool:
        """A TSCM/TSCS datagram from the phone to the game. False if it can't be delivered."""
        return False

    def connected(self, now: float) -> bool:
        return now - self.last_game < 1.5
