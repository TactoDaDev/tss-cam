"""A stand-in phone for testing the receiver and the games without the iPhone app.

Streams AcHandCam v3 poses (60 Hz) to the receiver, says hello, sends TSS Cam commands and prints the answers and the
capabilities the game reports.

  python tools/fake_phone.py --seconds 8 --cmd cam.engage --cmd "car panel:door_FL" --cmd "cam.mode chase"
  --cmd takes "<cmd> [arg]": "car <control id>", "cam.mode <mode>", "time <speed>", "replay toggle", or raw JSON.
  --walk makes the phone walk slowly forward; --orbit turns it slowly.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "receiver"))
from handheld_receiver.protocol import PosePacket, encode_pose  # noqa: E402
from tsscam import protocol as tsc  # noqa: E402


def parse_cmd(text: str, seq: int) -> dict:
    if text.startswith("{"):
        msg = json.loads(text)
        msg.setdefault("seq", seq)
        return msg
    name, _, arg = text.partition(" ")
    args: dict = {}
    if name == "car":
        args = {"id": arg}
    elif name == "cam.mode":
        args = {"mode": arg}
    elif name == "time":
        args = {"speed": float(arg)}
    elif name == "replay":
        op, _, val = arg.partition(" ")
        args = {"op": op, **({"value": float(val)} if val else {})}
    elif name == "clean":
        args = {"on": arg != "off"}
    elif name == "game.select":
        args = {"game": arg}
    return {"seq": seq, "cmd": name, "args": args}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47820)
    ap.add_argument("--seconds", type=float, default=6)
    ap.add_argument("--cmd", action="append", default=[], help="sent one per second, in order")
    ap.add_argument("--walk", action="store_true")
    ap.add_argument("--orbit", action="store_true")
    ap.add_argument("--lift", type=float, default=0.0, help="hold the lift stick at this value")
    ap.add_argument("--quiet-caps", action="store_true")
    a = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.001)
    dest = (a.host, a.port)
    session = random.getrandbits(32)
    sock.sendto(tsc.encode_json(tsc.HELLO, {"app": "fake_phone", "ver": "0.1"}), dest)
    t0 = time.monotonic()
    seq, cmd_seq, next_cmd = 0, 0, t0 + 1.0
    last_caps = ""
    while time.monotonic() - t0 < a.seconds:
        now = time.monotonic()
        t = now - t0
        yaw = 0.3 * t if a.orbit else 0.0
        z = -0.4 * t if a.walk else 0.0  # ARKit: -z is forward
        q = (0.0, math.sin(yaw / 2), 0.0, math.cos(yaw / 2))
        seq += 1
        sock.sendto(encode_pose(PosePacket(tracking_state=2, tracking_reason=0, session_id=session, seq=seq, epoch=0,
                                           flags=0, recenter_count=0, rtt_ms=None, capture_time=now, send_time=now,
                                           position=(0.0, 1.5, z), orientation=q, vf_aspect=19.5 / 9)), dest)
        if a.lift:
            sock.sendto(tsc.encode_sticks(a.lift, 0, 0, 1), dest)
        if a.cmd and now >= next_cmd:
            cmd_seq += 1
            msg = parse_cmd(a.cmd.pop(0), cmd_seq)
            print(f"-> {msg}", flush=True)
            sock.sendto(tsc.encode_json(tsc.COMMAND, msg), dest)
            next_cmd = now + 1.0
        for _ in range(20):
            try:
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                break
            if not tsc.is_tsc(data):
                continue
            pkt = tsc.decode(data)
            body = pkt.json()
            if pkt.magic == tsc.CAPS:
                summary = json.dumps({k: body.get(k) for k in ("game", "connected", "car", "camMode", "engaged", "time")})
                summary += f" controls={len(body.get('controls', []))}"
                if summary != last_caps and not a.quiet_caps:
                    print(f"<- caps {summary}", flush=True)
                    if body.get("controls"):
                        print("   " + ", ".join(f"{c['id']}={c.get('value', '')}" for c in body["controls"]), flush=True)
                last_caps = summary
            else:
                print(f"<- {pkt.magic.decode()} {body}", flush=True)
        time.sleep(1 / 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
