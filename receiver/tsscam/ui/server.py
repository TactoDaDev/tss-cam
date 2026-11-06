"""The receiver's window: a small local web app (stdlib HTTP, no dependencies) shown in a browser app window.

GET  /                 the page (web/)          GET /api/status   everything the page shows, as JSON
GET  /art/<game>.jpg   the game's Steam art      POST /api/mode    {"mode": "auto"|"beamng"|"assetto"}
POST /api/recenter, /api/freeze, /api/accept_phone, /api/firewall (asks Windows for admin, adds the UDP rule)
The page polls /api/status; when it stops polling for a while (window closed), `idle_for()` grows and the
launcher quits.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from handheld_receiver.netinfo import lan_ipv4_addresses

from .. import __version__
from ..hub import MODES

log = logging.getLogger("tsscam.ui")
WEB = Path(__file__).resolve().parent / "web"
STEAM_IDS = {"beamng": "284160", "assetto": "244210"}
FIREWALL_RULE = "TSS Cam receiver (UDP)"


def steam_dirs() -> list[Path]:
    dirs = [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"]
    try:  # the Steam install the registry knows (it may be on another drive)
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            dirs.insert(0, Path(winreg.QueryValueEx(k, "SteamPath")[0]))
    except OSError:
        pass
    dirs += [Path(f"{d}:/Steam") for d in "DEFG"]
    return [d for d in dirs if d.exists()]


_art_cache: dict[str, Path | None] = {}


def game_art(game: str) -> Path | None:
    """library_hero.jpg from Steam's own cache (new apps keep it in a hashed subfolder), else the capsule."""
    if game in _art_cache:
        return _art_cache[game]
    found = None
    app = STEAM_IDS.get(game)
    for d in steam_dirs() if app else []:
        base = d / "appcache" / "librarycache" / app
        for name in ("library_hero.jpg", "library_header.jpg", "library_capsule.jpg"):
            hits = [base / name] + sorted(base.glob(f"*/{name}"))
            hits = [h for h in hits if h.exists()]
            if hits:
                found = hits[0]
                break
        if found:
            break
    _art_cache[game] = found
    return found


def firewall_rule_exists() -> bool:
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={FIREWALL_RULE}"],
                           capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and FIREWALL_RULE in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def add_firewall_rule(port: int) -> None:
    """Windows asks for admin (UAC); the rule lets the phone's packets in on private networks only."""
    cmd = (f'netsh advfirewall firewall add rule name="{FIREWALL_RULE}" dir=in action=allow protocol=UDP '
           f'localport={port} profile=private')
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                      f"Start-Process cmd -Verb RunAs -WindowStyle Hidden -ArgumentList '/c {cmd}'"],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class UiServer:
    def __init__(self, svc, hub, settings, port: int = 47850):
        self.svc, self.hub, self.settings = svc, hub, settings
        self.port = port
        self.last_poll = 0.0
        self.started = time.monotonic()
        self._fw = (0.0, False)
        self.httpd: ThreadingHTTPServer | None = None

    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def idle_for(self) -> float:
        return time.monotonic() - (self.last_poll or self.started)

    def firewall_ok(self) -> bool:
        t, ok = self._fw
        if time.monotonic() - t > 10:
            ok = firewall_rule_exists()
            self._fw = (time.monotonic(), ok)
        return ok

    def status(self) -> dict:
        s, h = self.svc.snapshot(), self.hub.status()
        lv = s.live
        phone_live = bool(s.source) and (s.seconds_since_packet or 99) < 1.5
        return {
            "version": __version__,
            "port": self.settings.network.port,
            "ips": lan_ipv4_addresses(),
            "listening": s.listening, "listen_error": s.listen_error,
            "firewall": self.firewall_ok(),
            "phone": {"live": phone_live, "source": s.source.split(":")[0] if s.source else "",
                      "app": h["phone_app"], "rate": round(s.stats.rate_hz), "tracking": s.tracking,
                      "state": s.state_name, "latency": None if s.latency_estimate_ms is None else round(s.latency_estimate_ms),
                      "loss": round(s.stats.loss_pct(), 1), "zoom": round(s.zoom, 2),
                      "walk": s.mode == 1, "frozen": s.frozen},
            "game": h,
            "live": {"wanted": s.live_wanted, "running": bool(lv and lv.running), "fps": round(lv.fps) if lv else 0,
                     "mbps": round(lv.kbps / 1000, 1) if lv else 0, "encoder": (lv.encoder or "") if lv else "",
                     "error": (lv.error or "") if lv else ""},
            "modes": list(MODES),
        }

    def start(self) -> None:
        ui = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _json(self, obj, code: int = 200) -> None:
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/status":
                    ui.last_poll = time.monotonic()
                    return self._json(ui.status())
                if path.startswith("/art/"):
                    art = game_art(path[5:].removesuffix(".jpg"))
                    if art:
                        return self._send(200, art.read_bytes(), "image/jpeg")
                    return self._send(404, b"", "text/plain")
                name = "index.html" if path in ("/", "") else path.lstrip("/")
                f = (WEB / name).resolve()
                if WEB in f.parents and f.is_file():
                    return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
                self._send(404, b"not found", "text/plain")

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    body = {}
                path = self.path.split("?")[0]
                try:
                    if path == "/api/mode":
                        ui.hub.set_mode(body.get("mode", "auto"))
                    elif path == "/api/recenter":
                        ui.svc.recenter()
                    elif path == "/api/freeze":
                        ui.svc.set_frozen(not ui.svc.snapshot().frozen)
                    elif path == "/api/accept_phone":
                        ui.svc.accept_new_source()
                    elif path == "/api/firewall":
                        add_firewall_rule(ui.settings.network.port)
                        ui._fw = (0.0, False)
                    else:
                        return self._json({"ok": False, "err": "unknown"}, 404)
                except ValueError as e:
                    return self._json({"ok": False, "err": str(e)}, 400)
                self._json({"ok": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, name="ui-http", daemon=True).start()
        log.info("window at %s", self.url())

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()


def open_window(url: str) -> subprocess.Popen | None:
    """An app window (no tabs or address bar): Edge, else Chrome, with a profile of our own; else the browser."""
    import shutil
    import webbrowser
    candidates = [shutil.which("msedge"), shutil.which("chrome"),
                  r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]
    profile = Path(os.environ.get("LOCALAPPDATA", ".")) / "TSSCam" / "window"
    for exe in candidates:
        if exe and Path(exe).exists():
            return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1040,720",
                                     "--no-first-run", "--no-default-browser-check"])
    webbrowser.open(url)
    return None
