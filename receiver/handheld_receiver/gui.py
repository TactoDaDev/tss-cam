"""Tkinter GUI for the receiver (tkinter ships with the python.org Windows installer).

Keyboard shortcuts while the window has focus:
    R  recenter        F  freeze on/off        P  pause/resume
"""

from __future__ import annotations

import logging
import math
import queue
import tkinter as tk
from collections import deque
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__
from . import pipeline as pl
from .config import PRESETS, apply_preset, save_settings
from .service import ReceiverService
from .shm import LUA_ERROR_NAMES

REFRESH_MS = 50
PLOT_SECONDS = 12.0

STATE_COLOURS = {
    pl.NO_DATA: "#888888",
    pl.LIVE: "#1a9a3a",
    pl.HOLD_TRACKING: "#d07a00",
    pl.HOLD_FROZEN: "#2a6fd0",
    pl.HOLD_PAUSED: "#2a6fd0",
    pl.HOLD_STALE: "#c0392b",
    pl.PLAYBACK: "#8e44ad",
}


class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait(self.format(record))
        except queue.Full:
            pass


class App:
    def __init__(self, root: tk.Tk, svc: ReceiverService, config_path: Path, ips: list[str]):
        self.root = root
        self.svc = svc
        self.config_path = config_path
        self.s = svc.settings
        self.log_q: queue.Queue = queue.Queue(maxsize=1000)
        self._qh = _QueueHandler(self.log_q)
        logging.getLogger().addHandler(self._qh)
        self.trail: deque = deque(maxlen=int(PLOT_SECONDS * 1000 / REFRESH_MS))

        root.title(f"AcHandCam receiver {__version__}")
        root.minsize(900, 640)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)
        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(main)
        right.pack(side="left", fill="y", padx=(8, 0))

        # ---------------------------------------------------------- status
        st = ttk.LabelFrame(left, text="Status", padding=6)
        st.pack(fill="x")
        self.state_lbl = tk.Label(st, text="", font=("Segoe UI", 14, "bold"), fg="white", bg="#888", padx=8)
        self.state_lbl.grid(row=0, column=0, columnspan=4, sticky="we", pady=(0, 6))
        self.vars: dict[str, tk.StringVar] = {}
        rows = [
            ("PC address (type into phone)", "ips"),
            ("Listening", "listen"),
            ("Phone", "source"),
            ("ARKit tracking", "tracking"),
            ("Packets", "packets"),
            ("Latency (estimate)", "latency"),
            ("Assetto Corsa app", "lua"),
            ("Position R/U/F (m)", "pos"),
            ("Yaw / pitch / roll (deg)", "ang"),
            ("Phone controls", "controls"),
            ("Live view (v3)", "live"),
        ]
        for i, (label, key) in enumerate(rows, start=1):
            ttk.Label(st, text=label + ":").grid(row=i, column=0, sticky="w")
            v = tk.StringVar(value="-")
            self.vars[key] = v
            ttk.Label(st, textvariable=v, font=("Consolas", 10)).grid(row=i, column=1, columnspan=3, sticky="w")
        self.vars["ips"].set(", ".join(ips) or "not found - check your network adapter")

        # ---------------------------------------------------------- buttons
        bf = ttk.Frame(left, padding=(0, 6))
        bf.pack(fill="x")
        ttk.Button(bf, text="Recenter (R)", command=self.svc.recenter).pack(side="left")
        self.freeze_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bf, text="Freeze (F)", variable=self.freeze_var,
                        command=lambda: self.svc.set_frozen(self.freeze_var.get())).pack(side="left", padx=6)
        self.pause_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bf, text="Pause (P)", variable=self.pause_var,
                        command=lambda: self.svc.set_paused(self.pause_var.get())).pack(side="left", padx=6)
        ttk.Button(bf, text="Accept new phone", command=self.svc.accept_new_source).pack(side="left", padx=6)
        self.rec_btn = ttk.Button(bf, text="Record path", command=self.toggle_record)
        self.rec_btn.pack(side="left", padx=6)
        self.play_btn = ttk.Button(bf, text="Play path...", command=self.toggle_play)
        self.play_btn.pack(side="left", padx=6)
        self.loop_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bf, text="loop", variable=self.loop_var).pack(side="left")

        # ---------------------------------------------------------- plot
        pf = ttk.LabelFrame(left, text="Top-down view of the camera path (last 12 s, filming frame)", padding=4)
        pf.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(pf, bg="#111", height=260, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # ---------------------------------------------------------- log
        lf = ttk.LabelFrame(left, text="Log", padding=4)
        lf.pack(fill="both", expand=False)
        self.log_txt = tk.Text(lf, height=8, font=("Consolas", 9), state="disabled", wrap="none")
        self.log_txt.pack(fill="both", expand=True)

        # ---------------------------------------------------------- settings
        self._build_settings(right)

        for key, fn in (("r", self.svc.recenter), ("f", self.key_freeze), ("p", self.key_pause)):
            root.bind(f"<KeyPress-{key}>", lambda e, fn=fn: fn())
            root.bind(f"<KeyPress-{key.upper()}>", lambda e, fn=fn: fn())
        self.root.after(REFRESH_MS, self.refresh)

    # ----------------------------------------------------------------- UI
    def _slider(self, parent, label, getter, setter, lo, hi, fmt="{:.2f}", log_scale=False):
        fr = ttk.Frame(parent)
        fr.pack(fill="x", pady=2)
        ttk.Label(fr, text=label).pack(anchor="w")
        val_lbl = ttk.Label(fr, width=7)
        val_lbl.pack(side="right")

        def to_slider(v):
            return math.log10(v) if log_scale else v

        def from_slider(x):
            return 10 ** x if log_scale else x

        var = tk.DoubleVar(value=to_slider(getter()))

        def on_move(_=None):
            v = from_slider(var.get())
            setter(v)
            val_lbl.config(text=fmt.format(v))
            self.svc.settings_changed()

        sc = ttk.Scale(fr, from_=to_slider(lo), to=to_slider(hi), variable=var, command=on_move)
        sc.pack(side="left", fill="x", expand=True)
        val_lbl.config(text=fmt.format(getter()))
        return var, to_slider, val_lbl, fmt

    def _check(self, parent, label, obj, attr):
        var = tk.BooleanVar(value=getattr(obj, attr))

        def on():
            setattr(obj, attr, var.get())
            self.svc.settings_changed()

        ttk.Checkbutton(parent, text=label, variable=var, command=on).pack(anchor="w")
        return var

    def _build_settings(self, outer):
        t = self.s.transform
        f = self.s.filter
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)
        parent = ttk.Frame(nb, padding=4)
        lvtab = ttk.Frame(nb, padding=4)
        nwtab = ttk.Frame(nb, padding=4)
        nb.add(parent, text="Camera")
        nb.add(lvtab, text="Live view")
        nb.add(nwtab, text="Network")
        self._build_liveview(lvtab)
        mv = ttk.LabelFrame(parent, text="Movement", padding=6)
        mv.pack(fill="x")
        self._slider(mv, "Translation scale (x physical)", lambda: t.translation_scale,
                     lambda v: setattr(t, "translation_scale", v), 0.1, 10.0, "{:.2f}x", log_scale=True)
        self._slider(mv, "Rotation sensitivity (yaw/pitch)", lambda: t.rotation_sensitivity,
                     lambda v: setattr(t, "rotation_sensitivity", v), 0.25, 2.0)
        self._slider(mv, "Roll sensitivity", lambda: t.roll_sensitivity,
                     lambda v: setattr(t, "roll_sensitivity", v), 0.0, 2.0)
        self._check(mv, "Lock roll (level horizon)", t, "roll_lock")
        inv = ttk.Frame(mv)
        inv.pack(fill="x")
        c1, c2 = ttk.Frame(inv), ttk.Frame(inv)
        c1.pack(side="left", fill="x", expand=True)
        c2.pack(side="left", fill="x", expand=True)
        for lbl, attr in (("Invert right", "invert_right"), ("Invert up", "invert_up"), ("Invert forward", "invert_forward")):
            self._check(c1, lbl, t, attr)
        for lbl, attr in (("Invert yaw", "invert_yaw"), ("Invert pitch", "invert_pitch"), ("Invert roll", "invert_roll")):
            self._check(c2, lbl, t, attr)

        sm = ttk.LabelFrame(parent, text="Smoothing (1-euro filter)", padding=6)
        sm.pack(fill="x", pady=(8, 0))
        ttk.Label(sm, text="Preset").pack(anchor="w")
        self.preset_var = tk.StringVar(value=f.preset)
        cb = ttk.Combobox(sm, textvariable=self.preset_var, values=list(PRESETS), state="readonly")
        cb.pack(fill="x")
        cb.bind("<<ComboboxSelected>>", self.on_preset)
        self.filter_sliders = []
        for title, params in (("Position", lambda: self.s.filter.position), ("Rotation", lambda: self.s.filter.rotation)):
            ttk.Label(sm, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 0))
            self.filter_sliders.append(self._slider(
                sm, "  steadiness when still (min cutoff Hz, lower = smoother)",
                lambda p=params: p().min_cutoff, lambda v, p=params: setattr(p(), "min_cutoff", v),
                0.05, 10.0, "{:.2f}", log_scale=True) + (params, "min_cutoff"))
            self.filter_sliders.append(self._slider(
                sm, "  responsiveness when moving (beta)",
                lambda p=params: p().beta, lambda v, p=params: setattr(p(), "beta", v),
                0.0, 10.0, "{:.2f}") + (params, "beta"))

        nw = ttk.LabelFrame(nwtab, text="Network", padding=6)
        nw.pack(fill="x", pady=(8, 0))
        row = ttk.Frame(nw)
        row.pack(fill="x")
        ttk.Label(row, text="Bind").pack(side="left")
        self.bind_var = tk.StringVar(value=self.s.network.bind_address)
        ttk.Entry(row, textvariable=self.bind_var, width=14).pack(side="left", padx=4)
        ttk.Label(row, text="UDP port").pack(side="left")
        self.port_var = tk.StringVar(value=str(self.s.network.port))
        ttk.Entry(row, textvariable=self.port_var, width=7).pack(side="left", padx=4)
        ttk.Button(row, text="Apply", command=self.apply_network).pack(side="left")

        ttk.Button(outer, text="Save settings", command=self.save).pack(fill="x", pady=(10, 0))

    def _build_liveview(self, parent):
        lvs = self.s.liveview
        ttk.Label(parent, text="The game picture on the phone. Runs only while the\n"
                               "phone's viewfinder is set to GAME.", justify="left").pack(anchor="w")
        self._check(parent, "Enabled", lvs, "enabled")
        enc = ttk.LabelFrame(parent, text="Capture and encoder (Apply restarts it)", padding=6)
        enc.pack(fill="x", pady=(6, 0))
        self.lv_vars = {}

        def combo(label, key, values):
            row = ttk.Frame(enc)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=label, width=16).pack(side="left")
            var = tk.StringVar(value=str(getattr(lvs, key)))
            ttk.Combobox(row, textvariable=var, values=values, width=14).pack(side="left", fill="x", expand=True)
            self.lv_vars[key] = var

        combo("Source", "capture", ["desktop", "testpattern"])
        combo("Encoder", "encoder", ["auto", "h264_amf", "h264_nvenc", "h264_qsv", "libx264"])
        combo("Backend", "backend", ["ffmpeg", "native"])
        combo("Monitor (0 = first)", "monitor", ["0", "1", "2", "3"])
        combo("Height (px)", "height", ["270", "360", "480", "540", "720", "1080"])
        combo("Frame rate", "fps", ["20", "30", "45", "60", "90", "120"])
        combo("Bitrate (Mbit/s)", "bitrate_mbps", ["1", "2", "3", "4", "6", "8", "10", "15"])
        combo("Keyframe every (s)", "keyframe_interval_s", ["0.25", "0.5", "1", "2"])
        ttk.Button(enc, text="Apply", command=self.apply_liveview).pack(fill="x", pady=(4, 0))

        tw = ttk.LabelFrame(parent, text="Timewarp timing (applies immediately)", padding=6)
        tw.pack(fill="x", pady=(8, 0))
        self._slider(tw, "Game render delay (ms)", lambda: lvs.render_delay_ms,
                     lambda v: setattr(lvs, "render_delay_ms", v), 0.0, 80.0, "{:.0f}")
        self._slider(tw, "Warp fine tune (ms, + if it still lags)", lambda: lvs.warp_offset_ms,
                     lambda v: setattr(lvs, "warp_offset_ms", v), -40.0, 40.0, "{:+.0f}")
        self.lv_detail = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.lv_detail, font=("Consolas", 8), justify="left",
                  wraplength=330).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------ actions
    def key_freeze(self):
        self.freeze_var.set(not self.freeze_var.get())
        self.svc.set_frozen(self.freeze_var.get())

    def key_pause(self):
        self.pause_var.set(not self.pause_var.get())
        self.svc.set_paused(self.pause_var.get())

    def on_preset(self, _=None):
        apply_preset(self.s, self.preset_var.get())
        for var, to_slider, val_lbl, fmt, params, attr in self.filter_sliders:
            v = getattr(params(), attr)
            var.set(to_slider(v))
            val_lbl.config(text=fmt.format(v))
        self.svc.settings_changed()

    def apply_network(self):
        try:
            port = int(self.port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Port", "Enter a port number between 1 and 65535.")
            return
        self.svc.restart_listener(port, self.bind_var.get().strip() or "0.0.0.0")

    def apply_liveview(self):
        lvs = self.s.liveview
        conv = {"monitor": int, "height": int, "fps": int, "bitrate_mbps": float, "keyframe_interval_s": float}
        for key, var in self.lv_vars.items():
            raw = var.get().strip()
            try:
                setattr(lvs, key, conv.get(key, str)(raw))
            except ValueError:
                messagebox.showerror("Live view", f"Not a valid value for {key}: {raw!r}")
                return
        from .config import validate

        validate(self.s)
        for key, var in self.lv_vars.items():
            var.set(str(getattr(lvs, key)))
        self.svc.liveview_settings_changed()
        logging.getLogger("handheldcam").info("live view settings applied; encoder restarts")

    def toggle_record(self):
        if self.svc.recorder.active:
            p = self.svc.stop_recording()
            self.rec_btn.config(text="Record path")
            if p:
                logging.getLogger("handheldcam").info("saved %s", p)
        else:
            self.svc.start_recording()
            self.rec_btn.config(text="Stop recording")

    def toggle_play(self):
        if self.svc.snapshot().playback:
            self.svc.stop_playback()
            return
        path = filedialog.askopenfilename(initialdir=self.s.recordings_dir,
                                          filetypes=[("Camera paths", "*.jsonl"), ("All files", "*.*")])
        if path:
            try:
                self.svc.start_playback(Path(path), loop=self.loop_var.get())
            except (OSError, ValueError, KeyError) as e:
                messagebox.showerror("Playback", f"Could not load {path}:\n{e}")

    def save(self):
        try:
            save_settings(self.s, self.config_path)
            logging.getLogger("handheldcam").info("settings saved to %s", self.config_path)
        except OSError as e:
            messagebox.showerror("Save", str(e))

    def on_close(self):
        logging.getLogger().removeHandler(self._qh)
        self.root.destroy()

    # ------------------------------------------------------------ refresh
    def refresh(self):
        s = self.svc.snapshot()
        v = self.vars
        self.state_lbl.config(text=s.state_name.upper(), bg=STATE_COLOURS.get(s.state, "#888"))
        v["listen"].set(("UDP " + s.listening) if s.listening else ("ERROR: " + s.listen_error))
        since = "" if s.seconds_since_packet is None else f"  (last packet {s.seconds_since_packet:.1f}s ago)"
        v["source"].set((s.source or "none yet") + since)
        v["tracking"].set(s.tracking or "-")
        st = s.stats
        v["packets"].set(
            f"{st.rate_hz:5.1f}/s  loss {st.loss_pct():.1f}%  stale {st.stale}  out-of-order {st.out_of_order}  "
            f"bad {s.malformed}  foreign {s.foreign}  jumps rejected {st.rejected_jumps}  re-anchors {st.reanchors}"
        )
        if s.latency_estimate_ms is None:
            lat = "waiting for phone round-trip measurement"
        else:
            lat = (f"~{s.latency_estimate_ms:.0f} ms capture->PC  (phone {st.capture_to_send_ms or 0:.0f} ms + "
                   f"network ~{(st.rtt_ms or 0) / 2:.0f} ms + receiver {s.processing_ms:.2f} ms)")
        v["latency"].set(lat)
        if not s.shm_ok:
            v["lua"].set("shared memory unavailable: " + s.shm_error)
        elif not s.lua_alive:
            v["lua"].set("not running (open the 'AcHandCam' app in AC)")
        else:
            lua = s.lua
            err = LUA_ERROR_NAMES.get(lua.error_code, str(lua.error_code)) if lua else ""
            v["lua"].set(f"running, {'ENGAGED' if lua and lua.engaged else 'idle'}, {lua.fps:.0f} fps"
                         + (f"  - {err}" if err else ""))
        o = s.output
        if o is not None:
            v["pos"].set("R %+7.3f   U %+7.3f   F %+7.3f" % o.position)
            v["ang"].set("%+7.1f   %+7.1f   %+7.1f" % o.euler_deg)
            self.trail.append((o.position[0], o.position[2], o.forward[0], o.forward[2]))
        from .protocol import MODE_NAMES

        proto = f"app protocol v{s.phone_protocol}" if s.phone_protocol else "no phone yet"
        extra = ""
        if s.phone_protocol >= 3:
            extra = (f", {'portrait' if s.view_orientation == 1 else 'landscape'}, "
                     f"{s.zoom * 24:.0f} mm, movement {s.phone_scale or self.s.transform.translation_scale:.1f}x"
                     + (" (set on phone)" if s.phone_scale else ""))
        v["controls"].set(
            f"{MODE_NAMES.get(s.mode, '?')} mode, zoom {s.zoom:.1f}x, "
            f"joystick ({s.joy[0]:+.2f}, {s.joy[1]:+.2f}){extra}  [{proto}]"
        )
        v["live"].set(self._live_text(s))
        self.rec_btn.config(text="Stop recording" if s.recording else "Record path")
        self.play_btn.config(text="Stop playback" if s.playback else "Play path...")
        self.draw_plot()
        self.drain_log()
        self.root.after(REFRESH_MS, self.refresh)

    def _live_text(self, s) -> str:
        lv = s.live
        if lv is None or not self.s.liveview.enabled:
            return "off"
        tail = "\n".join(lv.log_tail[-4:])
        if hasattr(self, "lv_detail"):
            view = s.lua_view
            ac = ("AC app: not sending FOV (update the AC app to v3)" if view is None else
                  f"AC app: FOV {view.fov_v:.1f} deg, smoothing {view.smoothing * 1000:.0f} ms, "
                  f"{'following the phone - timewarp on' if view.live else 'not following the phone - timewarp off'}")
            self.lv_detail.set(f"{ac}\nframes sent {s.video_frames_sent}, without a pose {s.video_no_pose}\n{tail}")
        if lv.error:
            return "ERROR: " + lv.error
        if not s.live_wanted and not lv.running:
            return "idle (phone viewfinder is not on GAME)"
        if not lv.running:
            return "starting..."
        age = "-" if lv.capture_to_send_ms is None else f"{lv.capture_to_send_ms:.1f} ms"
        text = (f"{lv.encoder} {lv.fps:.0f} fps {lv.kbps / 1000:.1f} Mbit/s, "
                f"capture->sent {age}, restarts {lv.restarts}")
        sk = lv.seconds_since_keyframe
        if sk is not None and sk > 3 * self.s.liveview.keyframe_interval_s + 1:
            text += f"  WARNING: no keyframe for {sk:.0f} s"
        if lv.keyframe_restarts:
            text += f", keyframe restarts {lv.keyframe_restarts}"
        return text

    def draw_plot(self):
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        cx, cy = w / 2, h / 2
        if not self.trail:
            c.create_text(cx, cy, text="no pose yet", fill="#777")
            return
        span = max(1.0, max(max(abs(p[0]), abs(p[1])) for p in self.trail) * 1.2)
        k = min(w, h) / 2 / span
        c.create_line(0, cy, w, cy, fill="#333")
        c.create_line(cx, 0, cx, h, fill="#333")
        c.create_text(cx + 4, 10, text=f"forward  (grid +/- {span:.1f} m)", anchor="w", fill="#777")
        pts = []
        for r, f, _, _ in self.trail:
            pts += [cx + r * k, cy - f * k]
        if len(pts) >= 4:
            c.create_line(*pts, fill="#4aa3ff", width=2)
        r, f, fr, ff = self.trail[-1]
        x, y = cx + r * k, cy - f * k
        n = math.hypot(fr, ff) or 1.0
        c.create_line(x, y, x + fr / n * 30, y - ff / n * 30, fill="#ffcc00", width=3, arrow="last")
        c.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#fff", outline="")

    def drain_log(self):
        lines = []
        try:
            while True:
                lines.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if lines:
            self.log_txt.config(state="normal")
            self.log_txt.insert("end", "\n".join(lines) + "\n")
            n = int(self.log_txt.index("end-1c").split(".")[0])
            if n > 500:
                self.log_txt.delete("1.0", f"{n - 500}.0")
            self.log_txt.see("end")
            self.log_txt.config(state="disabled")


def run_gui(svc: ReceiverService, config_path: Path, ips: list[str]) -> None:
    root = tk.Tk()
    App(root, svc, config_path, ips)
    root.mainloop()
