"""The phone <-> game switchboard for TSS Cam's own packets (the pose path stays AcHandCam's).

Phone packets arrive on the receiver's UDP socket (the service hands every TSC* datagram here) and go to the active
game hook; the game's capabilities and answers come back through the hook and go to the phone's address. Which game
is active: the user's pick, or in Auto mode whichever supported game is running (checked every 2 s).
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

from . import __version__
from . import protocol as tsc
from .games import GAMES, GameHook
from .games.assetto import AssettoHook
from .games.beamng import BeamNGHook
from .games.detect import running_game

log = logging.getLogger("tsscam.hub")
MODES = ("auto",) + tuple(GAMES)
CAPS_EVERY = 2.0    # s: resend the capabilities so a phone that missed one catches up
DETECT_EVERY = 2.0  # s


class TscHub:
    def __init__(self, mode: str = "auto", hooks: dict[str, Callable[..., GameHook]] | None = None,
                 detect: Callable[[], str | None] = running_game):
        self.hook_factories = hooks or {"beamng": BeamNGHook, "assetto": AssettoHook}
        self.detect = detect
        self.mode = mode if mode in MODES else "auto"
        self.hook: GameHook | None = None
        self.phone: tuple[str, int] | None = None
        self.phone_sock: socket.socket | None = None
        self.phone_app = ""
        self.caps: dict = {}
        self.last_detect = -1e9
        self.last_caps_sent = 0.0
        self.counts = {"hello": 0, "command": 0, "sticks": 0, "caps": 0, "answer": 0, "bad": 0}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tsc-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._switch(None)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.tick(time.monotonic())
            time.sleep(0.25)

    # ------------------------------------------------------------------ game choice
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode is one of {MODES}")
        with self._lock:
            self.mode = mode
            self.last_detect = -1e9  # decide now
        self.tick(time.monotonic())

    def wanted_game(self) -> str | None:
        return running_game_or(self.detect) if self.mode == "auto" else self.mode

    def _switch(self, game: str | None) -> None:
        with self._lock:
            if (self.hook.key if self.hook else None) == game:
                return
            if self.hook:
                self.hook.stop()
                log.info("game hook %s stopped", self.hook.key)
            self.hook = None
            self.caps = {}
            if game:
                self.hook = self.hook_factories[game](self.on_game_message)
                self.hook.start()
                log.info("game hook %s started", game)
            self._send_caps()

    def tick(self, now: float) -> None:
        with self._lock:
            if now - self.last_detect >= DETECT_EVERY:
                self.last_detect = now
                self._switch(self.wanted_game())
            if self.phone and now - self.last_caps_sent >= CAPS_EVERY:
                self._send_caps()

    # ------------------------------------------------------------------ phone side
    def handle_phone(self, data: bytes, addr: tuple[str, int], sock: socket.socket | None) -> None:
        try:
            pkt = tsc.decode(data)
            with self._lock:
                self.phone, self.phone_sock = addr, sock
                if pkt.magic == tsc.HELLO:
                    self.counts["hello"] += 1
                    self.phone_app = str(pkt.json().get("ver", ""))
                    self._send(tsc.encode_json(tsc.HELLO, {"app": "tsscam-receiver", "ver": __version__,
                                                           "mode": self.mode, "game": self.game_key()}))
                    self._send_caps()
                elif pkt.magic == tsc.COMMAND:
                    self.counts["command"] += 1
                    self._command(pkt, data)
                elif pkt.magic == tsc.STICKS:
                    pkt.sticks()  # validates
                    self.counts["sticks"] += 1
                    if self.hook:
                        self.hook.forward(data)
                else:
                    raise tsc.TscError(f"{pkt.magic.decode()} is not a phone packet")
        except tsc.TscError as e:
            self.counts["bad"] += 1
            if self.counts["bad"] <= 20 or self.counts["bad"] % 500 == 0:
                log.warning("bad TSC packet from %s: %s", addr[0], e)

    def _command(self, pkt: tsc.Packet, raw: bytes) -> None:
        msg = pkt.json()
        seq, cmd = msg.get("seq"), msg.get("cmd")
        if not isinstance(seq, int) or not isinstance(cmd, str):
            raise tsc.TscError("a command needs an int seq and a string cmd")
        if cmd == "game.select":  # the phone can switch games itself
            mode = (msg.get("args") or {}).get("game")
            ok = mode in MODES
            if ok:
                self.mode = mode
                self.last_detect = -1e9
            self._answer(seq, ok, "" if ok else f"game is one of {MODES}")
            return
        hook = self.hook
        if not hook:
            self._answer(seq, False, "no game selected or running")
        elif not hook.connected(time.monotonic()):
            self._answer(seq, False, f"{hook.title} isn't connected (is the mod/app running?)")
        elif not hook.forward(raw):
            self._answer(seq, False, f"car controls for {hook.title} aren't available yet")
        # else: the game answers with TSCA itself

    def _answer(self, seq: int, ok: bool, err: str = "") -> None:
        self._send(tsc.encode_json(tsc.ANSWER, {"seq": seq, "ok": ok, "err": err}))

    # ------------------------------------------------------------------ game side
    def on_game_message(self, data: bytes) -> None:
        try:
            pkt = tsc.decode(data)
        except tsc.TscError as e:
            log.warning("bad TSC packet from the game: %s", e)
            return
        with self._lock:
            if pkt.magic == tsc.CAPS:
                self.counts["caps"] += 1
                self.caps = pkt.json()
                self._send_caps()
            elif pkt.magic == tsc.ANSWER:
                self.counts["answer"] += 1
                self._send(data)

    def game_key(self) -> str | None:
        return self.hook.key if self.hook else None

    def _send_caps(self) -> None:
        caps = dict(self.caps)
        caps["game"] = self.game_key()
        caps["mode"] = self.mode
        caps["connected"] = bool(self.hook and self.hook.connected(time.monotonic()))
        caps.setdefault("controls", [])
        caps.setdefault("modes", [])
        self.last_caps_sent = time.monotonic()
        self._send(tsc.encode_json(tsc.CAPS, caps))

    def _send(self, data: bytes) -> None:
        if self.phone and self.phone_sock:
            try:
                self.phone_sock.sendto(data, self.phone)
            except OSError:
                pass

    def status(self) -> dict:
        with self._lock:
            now = time.monotonic()
            return {"mode": self.mode, "game": self.game_key(),
                    "game_title": self.hook.title if self.hook else "",
                    "game_connected": bool(self.hook and self.hook.connected(now)),
                    "hook_error": getattr(self.hook, "error", "") if self.hook else "",
                    "phone": f"{self.phone[0]}" if self.phone else "", "phone_app": self.phone_app,
                    "car": self.caps.get("car", ""), "controls": len(self.caps.get("controls", [])),
                    "counts": dict(self.counts)}


def running_game_or(detect: Callable[[], str | None]) -> str | None:
    try:
        return detect()
    except Exception:  # noqa: BLE001 - detection must never take the receiver down
        log.exception("game detection failed")
        return None
