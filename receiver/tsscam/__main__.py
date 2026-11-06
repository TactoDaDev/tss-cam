"""TSS Cam receiver:  python -m tsscam [--game auto|beamng|assetto] [--headless] [--port N]

AcHandCam's receiver service (poses, live view, shared memory) + our hub (game picker, car controls, capabilities).
The window (phase 3) isn't built yet: --headless prints a status line every second.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from handheld_receiver.__main__ import setup_logging
from handheld_receiver.config import load_settings, validate
from handheld_receiver.netinfo import lan_ipv4_addresses
from handheld_receiver.service import ReceiverService

from . import __version__
from .hub import MODES, TscHub

HERE = Path(__file__).resolve().parent.parent  # receiver/


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tsscam", description=__doc__)
    ap.add_argument("--game", choices=MODES, default="auto")
    ap.add_argument("--config", type=Path, default=HERE / "config.json")
    ap.add_argument("--port", type=int)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    setup_logging(args.log_level, HERE / "logs")
    settings = load_settings(args.config)
    if args.port:
        settings.network.port = args.port
    validate(settings)
    log = logging.getLogger("tsscam")
    log.info("TSS Cam receiver %s; this PC: %s, port %d", __version__, ", ".join(lan_ipv4_addresses()) or "?",
             settings.network.port)

    svc = ReceiverService(settings)
    hub = TscHub(mode=args.game)
    svc.extra_handler = hub.handle_phone
    svc.start()
    hub.start()
    try:
        while True:
            s, h = svc.snapshot(), hub.status()
            game = h["game_title"] or "no game"
            link = "connected" if h["game_connected"] else ("not connected" if h["game"] else "-")
            print(f"\r[{h['mode']}] {game}: {link} | phone {h['phone'] or s.source or '-'} {s.stats.rate_hz:4.0f}Hz "
                  f"| car {h['car'] or '-'} ({h['controls']} controls) {h['hook_error']}   ", end="", flush=True)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        svc.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
