"""Entry point:  python -m handheld_receiver [--headless] [--config FILE] [--port N]"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from .config import load_settings, save_settings, validate
from .netinfo import lan_ipv4_addresses
from .service import ReceiverService

HERE = Path(__file__).resolve().parent.parent  # receiver/


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = logging.handlers.RotatingFileHandler(log_dir / "receiver.log", maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def run_headless(svc: ReceiverService) -> None:
    print("Headless diagnostic mode. Ctrl+C to quit. Commands are not available here;")
    print("use the GUI (run without --headless) for recenter/freeze/pause.\n")
    try:
        while True:
            s = svc.snapshot()
            o = s.output
            pos = "-" if o is None else "R%+.3f U%+.3f F%+.3f m" % o.position
            ang = "-" if o is None else "yaw%+6.1f pitch%+6.1f roll%+6.1f" % o.euler_deg
            lat = "n/a" if s.latency_estimate_ms is None else f"{s.latency_estimate_ms:.0f}ms"
            ac = "AC app: " + ("running" + (" ENGAGED" if s.lua and s.lua.engaged else "") if s.lua_alive else "not seen")
            ctl = f"{'walk' if s.mode == 1 else 'hand'} z{s.zoom:.1f} j({s.joy[0]:+.1f},{s.joy[1]:+.1f})"
            lv = s.live
            if lv is not None and (s.live_wanted or lv.running or lv.error):
                live = (f" | live view {lv.encoder or '-'} {lv.fps:.0f}fps {lv.kbps / 1000:.1f}Mbit/s "
                        f"cap->send {'-' if lv.capture_to_send_ms is None else f'{lv.capture_to_send_ms:.1f}ms'}"
                        + (f" ERROR: {lv.error}" if lv.error else ""))
            else:
                live = ""
            print(
                f"[{s.state_name:<24}] src={s.source or '-':<21} trk={s.tracking:<14} "
                f"{s.stats.rate_hz:5.1f}Hz loss={s.stats.loss_pct():4.1f}% lat~{lat:<6} {pos} {ang} {ctl} | {ac}{live}",
                flush=True,
            )
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="handheld_receiver", description=__doc__)
    ap.add_argument("--config", type=Path, default=HERE / "config.json")
    ap.add_argument("--port", type=int, help="UDP port to listen on (overrides config)")
    ap.add_argument("--bind", help="address to bind (default 0.0.0.0 = all interfaces)")
    ap.add_argument("--headless", action="store_true", help="no GUI; print status lines (diagnostic mode)")
    ap.add_argument("--no-shm", action="store_true", help="do not create the shared memory for Assetto Corsa")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args(argv)

    setup_logging(args.log_level, HERE / "logs")
    settings = load_settings(args.config)
    if args.port:
        settings.network.port = args.port
    if args.bind:
        settings.network.bind_address = args.bind
    validate(settings)
    if not Path(settings.recordings_dir).is_absolute():
        settings.recordings_dir = str(HERE / settings.recordings_dir)
    if not args.config.exists():
        try:
            save_settings(settings, args.config)
        except OSError:
            pass

    log = logging.getLogger("handheldcam")
    ips = lan_ipv4_addresses()
    log.info("this PC's LAN address(es): %s  -> enter one in the phone app, port %d",
             ", ".join(ips) or "(none found)", settings.network.port)

    svc = ReceiverService(settings, use_shm=not args.no_shm)
    svc.start()
    try:
        if args.headless:
            run_headless(svc)
        else:
            try:
                from .gui import run_gui
            except ImportError as e:  # tkinter missing
                log.error("GUI unavailable (%s); falling back to headless mode", e)
                run_headless(svc)
            else:
                run_gui(svc, args.config, ips)
    finally:
        svc.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
