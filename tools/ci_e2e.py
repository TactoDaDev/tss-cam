"""End-to-end check on the CI Mac: the REAL receiver (AcHandCam service + our hub + BeamNG hook) and a fake BeamNG,
while the app runs in the simulator (pointed at 127.0.0.1, fake tracking) and a UI test taps through it.

  python3 tools/ci_e2e.py --seconds 240 --report e2e.json [--expect cam.toggle,cam.mode:chase,car:panel:door_FL]

The fake game does what the TSS Cam mod does over UDP 47830/47831: reports capabilities (an ETK 800's controls),
answers every command, records commands and sticks. When the UI test is done it writes a `done` file (--done),
then the report is written and checked:
  poses accepted by the receiver at >= 40 Hz, the hello answered, capabilities delivered, every expected command
  received by the game exactly as the app sent it.
Exit code 0 when everything passed. Shared memory (Windows only) is off: the hook uses in-process buffers.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "receiver"))

from handheld_receiver.config import Settings  # noqa: E402
from handheld_receiver.service import ReceiverService  # noqa: E402
from tsscam import protocol as tsc  # noqa: E402
from tsscam.games.beamng import BeamNGHook  # noqa: E402
from tsscam.hub import TscHub  # noqa: E402

CAPS = {"rev": 1, "car": "ETK 800-Series", "camMode": "hand", "engaged": False,
        "modes": ["hand", "ride", "hood", "passenger", "chase", "drone", "lockon"],
        "controls": [{"id": "panel:door_FL", "label": "Front left door", "group": "doors", "kind": "toggle"},
                     {"id": "panel:tailgate", "label": "Tailgate", "group": "doors", "kind": "toggle"},
                     {"id": "lights", "label": "Lights", "group": "lights", "kind": "cycle", "states": ["Off", "Low", "High"]},
                     {"id": "hazard", "label": "Hazards", "group": "lights", "kind": "toggle"},
                     {"id": "horn", "label": "Horn", "group": "horn", "kind": "hold"}]}


class FakeGame(threading.Thread):
    """The BeamNG mod's side of the link: caps every 0.5 s, an answer for every command."""

    def __init__(self, hook_addr):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 47830))
        self.sock.settimeout(0.05)
        self.hook_addr = hook_addr
        self.commands: list[dict] = []
        self.sticks = 0
        self.poses = 0
        self.engaged = False
        self.mode = "hand"
        self.stop = threading.Event()

    def caps(self):
        c = dict(CAPS, engaged=self.engaged, camMode=self.mode)
        self.sock.sendto(tsc.encode_json(tsc.CAPS, c), self.hook_addr)

    def run(self):
        next_caps = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            if now >= next_caps:
                self.caps()
                next_caps = now + 0.5
            try:
                data, _ = self.sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            if len(data) == 108:
                self.poses += 1
            elif data[:4] == tsc.COMMAND:
                msg = tsc.decode(data).json()
                self.commands.append(msg)
                print(f"game <- {msg}", flush=True)
                if msg.get("cmd") == "cam.toggle":
                    self.engaged = not self.engaged
                if msg.get("cmd") == "cam.mode":
                    self.mode = msg.get("args", {}).get("mode", self.mode)
                self.sock.sendto(tsc.encode_json(tsc.ANSWER, {"seq": msg.get("seq"), "ok": True, "err": ""}), self.hook_addr)
                self.caps()
            elif data[:4] == tsc.STICKS:
                self.sticks += 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=240)
    ap.add_argument("--report", default="e2e.json")
    ap.add_argument("--done", default="", help="stop early once this file exists (the UI test finished)")
    ap.add_argument("--expect", default="cam.toggle,cam.mode:chase,car:panel:door_FL,car:horn")
    a = ap.parse_args()

    settings = Settings()
    settings.network.bind_address = "127.0.0.1"
    settings.network.port = 47820
    svc = ReceiverService(settings, use_shm=False)
    maps = (bytearray(108), bytearray(48), bytearray(48))
    hooks = {"beamng": lambda cb: BeamNGHook(cb, game_addr=("127.0.0.1", 47830), hook_addr=("127.0.0.1", 47831), mappings=maps)}
    hub = TscHub(mode="beamng", hooks=hooks, detect=lambda: "beamng")
    svc.extra_handler = hub.handle_phone
    svc.start()
    hub.start()
    hub.tick(time.monotonic())
    game = FakeGame(("127.0.0.1", 47831))
    game.start()
    print("e2e: receiver on 127.0.0.1:47820, fake BeamNG on 47830", flush=True)

    best_rate, accepted_seen, t0 = 0.0, False, time.monotonic()
    gen_live = None  # the receiver's generation when it first went live: a recenter bumps it
    per_sec: list[int] = []  # accepted poses per second while live: the real rate (the receiver's rate_hz is an
    last_acc = None          # average of 1/gap, which a burst of packets inflates)
    while time.monotonic() - t0 < a.seconds:
        time.sleep(1)
        s = svc.snapshot()
        best_rate = max(best_rate, s.stats.rate_hz)
        accepted_seen = accepted_seen or s.state_name == "live"
        if accepted_seen and gen_live is None:
            gen_live = s.generation
        if s.state_name == "live" and last_acc is not None:
            per_sec.append(s.stats.accepted - last_acc)
        last_acc = s.stats.accepted
        h = hub.status()
        print(f"e2e: {s.state_name} {(per_sec[-1] if per_sec else 0)}/s (rate_hz {s.stats.rate_hz:.0f}) lost {s.stats.lost} stale {s.stats.stale} ooo {s.stats.out_of_order} | hub {h['counts']} | game cmds {len(game.commands)} sticks {game.sticks}", flush=True)
        if a.done and Path(a.done).exists():
            time.sleep(2)  # let the last packets land
            break

    h = hub.status()
    got = []
    for c in game.commands:
        args = c.get("args") or {}
        tag = c.get("cmd", "")
        if tag == "cam.mode":
            tag += ":" + str(args.get("mode"))
        if tag == "car":
            tag += ":" + str(args.get("id"))
        got.append(tag)
    expected = [e for e in a.expect.split(",") if e]
    checks = {
        "poses: 45-130 accepted per second (median)": bool(per_sec) and 45 <= sorted(per_sec)[len(per_sec) // 2] <= 130,
        "receiver went live": accepted_seen,
        "hello answered": h["counts"]["hello"] >= 1,
        "capabilities delivered": h["counts"]["caps"] >= 1,
        "no bad packets": h["counts"]["bad"] == 0,
        "recenter reached the receiver": gen_live is not None and svc.snapshot().generation > gen_live,
        "sticks reached the game": game.sticks > 0,
    }
    for e in expected:
        checks[f"command {e}"] = e in got
    snap = svc.snapshot().stats
    report = {"checks": checks, "best_rate_hz": best_rate, "accepted_per_second": per_sec,
              "lost": snap.lost, "stale": snap.stale, "out_of_order": snap.out_of_order, "received": snap.received, "commands": game.commands, "sticks": game.sticks,
              "hub": h, "passed": all(checks.values())}
    Path(a.report).write_text(json.dumps(report, indent=1))
    for k, v in checks.items():
        print(f"{'PASS' if v else 'FAIL'}  {k}")
    game.stop.set()
    hub.stop()
    svc.stop()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
