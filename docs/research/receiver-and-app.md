# AcHandCam receiver and our own iPhone app: research

Date: 2026-11-06. Sources read: `D:\tss-cam\ref\achandcam-pc\AcHandCam-Receiver\handheld_receiver\*.py`
(receiver 0.1.0, MIT, "Copyright (c) 2026 AcHandCam contributors"), the AcHandCam 0.1.0 `.ipa` (only its
Info.plist, Mach-O load commands and symbol names were read; unzipped to `ref\achandcam-iphone\unzipped\`),
and our `D:\beamng-tss-handcam` (README, bridge, mod). `native\handheld_capture.exe` was not run.

All multi-byte fields are **little-endian** unless stated. "Phone clock" = iOS uptime seconds (ARFrame.timestamp
clock); "PC clock" = `time.perf_counter()` (QueryPerformanceCounter).

---

## 0. What the AcHandCam phone app is built with (from the .ipa)

| Item | Value | Where |
|---|---|---|
| Language/UI | Swift + SwiftUI (no Flutter/RN; file names `HandheldCamApp.swift`, `ContentView.swift`, `SetupView.swift`, `ShutterControl.swift`, `ZoomChips`) | symbol strings |
| Frameworks linked | ARKit, SwiftUI, UIKit, Combine, CoreMotion, Metal, MetalKit, VideoToolbox, CoreMedia, CoreVideo, QuartzCore, Network (`libswiftNetwork`) | LC_LOAD_DYLIB |
| Networking | `PoseStreamer` owns one `Network.NWConnection` (`openConnection(host:port:)`) | symbols |
| Video | `H264Decoder` (`VTDecompressionSession`), `FrameAssembler`, `FramePacer`, `LiveRenderer: MTKViewDelegate` (Metal timewarp), `VideoPacket.swift`, `OrientationHistory.swift`, `MotionTracker.swift` (CoreMotion), `Timewarp.swift` | symbols; receiver docstrings say these are line-by-line ports of `liveview.py`, `pacing.py`, `timewarp.py` |
| Minimum iOS | 15.0 (`MinimumOSVersion`, LC_BUILD_VERSION minos 15.0) | Info.plist |
| Built with | Xcode 26.6 (17F113), iOS SDK 26.5, build machine macOS 25G83 = 26.6.2 | Info.plist `DTXcode*`, `BuildMachineOSBuild` |
| Signing | **no LC_CODE_SIGNATURE: shipped unsigned**, re-signed by Sideloadly | load commands |
| Size | 1.1 MB binary, no asset catalog, no embedded frameworks | Payload listing |
| Info.plist keys | `NSCameraUsageDescription`, `NSLocalNetworkUsageDescription`, **no `NSBonjourServices`** (typed IP only), `UIRequiredDeviceCapabilities = [arkit, arm64]`, `UISupportedInterfaceOrientations = [Portrait]` (UI locked to portrait; landscape handled in-app), `CADisableMinimumFrameDurationOnPhone = true` (ProMotion 120 Hz), `UILaunchScreen = {}`, `UIDeviceFamily = [1]` | Info.plist |

The Xcode build and the build-machine OS match **exactly** the current GitHub `macos-26` arm64 runner image
(Xcode 26.6 17F113 default, macOS 26.6.2 25G83 [1]). AcHandCam is almost certainly built the same way we plan to
build ours: GitHub Actions, unsigned, Sideloadly. The receiver's `pacing.py` docstring confirms: "both tested here and
in the GitHub build".

UI strings worth knowing (behaviour, not to copy): "Pinch or tap .5 / 1 / 2 / 3 (13 / 24 / 48 / 77 mm) to zoom",
"HOLD: hold the shutter to freeze", "WALK: drag the shutter like a joystick to walk", viewfinder toggles
GAME/(phone), "Smoothness" (low latency / balanced / smooth), "No reply from the PC yet - check IP, port and firewall",
"PC is ignoring this phone (another phone locked in?)", "Waiting for a keyframe".

---

## 1. Phone <-> receiver protocol

### Transport
- **One UDP socket on the PC**, default port **47820** (`config.DEFAULT_PORT`), bound `0.0.0.0`.
- Phone sends pose datagrams to `PC:47820`. The PC answers **to the datagram's source address/port**: acks *and*
  live-view video go back on the same 5-tuple. So the phone needs only one `NWConnection` (UDP) and receives on it.
- No handshake, no discovery: the user types the PC's IP (receiver lists its LAN IPv4s, `netinfo.py`).
  The first valid sender is locked in as the source (see "Source lock").
- Integrity: every packet ends in a zlib CRC-32 (IEEE, same as Swift `crc32` from zlib / `libz`) over all
  preceding bytes.
- Rate: one pose per ARFrame (~60 Hz; `timewarp.py`: "ARKit poses arrive at 60 Hz"). Receiver buffer 2048 B.

### Pose packet (phone -> PC), magic `ACHC`, versions 1/2/3 = 72/88/100 bytes
Receiver accepts all three; length must match the version byte. Send **v3**.

| off | size | type | field | notes |
|---:|---:|---|---|---|
| 0 | 4 | char[4] | magic | `"ACHC"` |
| 4 | 1 | u8 | version | 3 |
| 5 | 1 | u8 | msg_type | 1 = pose (only type) |
| 6 | 1 | u8 | tracking_state | 0 not available, 1 limited, 2 normal (`ARCamera.trackingState`) |
| 7 | 1 | u8 | tracking_reason | 0 none, 1 initializing, 2 excessive motion, 3 insufficient features, 4 relocalizing, 5 other |
| 8 | 4 | u32 | session_id | random per app launch (source identity) |
| 12 | 4 | u32 | seq | +1 per packet, wraps |
| 16 | 2 | u16 | epoch | +1 when the ARKit session restarts **or the phone is turned portrait<->landscape** (receiver re-anchors, no jump) |
| 18 | 2 | u16 | flags | bit0 FREEZE held, bit1 PAUSED, bit2 WANT_VIDEO (viewfinder = GAME), bit3 NEED_KEYFRAME |
| 20 | 2 | u16 | recenter_count | +1 per Recenter tap |
| 22 | 2 | u16 | rtt_tenth_ms | phone-measured RTT / 0.1 ms, 0 = unknown |
| 24 | 8 | f64 | capture_time | `ARFrame.timestamp` (s, phone uptime) |
| 32 | 8 | f64 | send_time | phone uptime at send; must be >= capture_time - 1 ms |
| 40 | 12 | f32x3 | position | ARKit world, metres (x right, y up, z back); \|v\| <= 1000 |
| 52 | 16 | f32x4 | orientation | quaternion x,y,z,w, camera->world, unit (+-0.05) |
| 68 | 4 | f32 | zoom | v2+: 1.0 = 24 mm equiv.; clamped 0.5..10 |
| 72 | 4 | f32 | joy_x | v2+: walk stick -1 left .. +1 right |
| 76 | 4 | f32 | joy_y | v2+: -1 back .. +1 forward (clamped to unit circle) |
| 80 | 1 | u8 | mode | v2+: 0 handheld, 1 walk |
| 81 | 1 | u8 | reserved | 0 |
| 82 | 2 | u16 | reserved | 0 |
| 84 | 4 | f32 | move_scale | v3: movement scale chosen on the phone, 0 = use receiver's; clamped 0.1..50 |
| 88 | 4 | f32 | vf_aspect | v3: screen long/short side (e.g. 19.5/9), 0 = unknown; clamped 1..4 |
| 92 | 1 | u8 | view_orientation | v3: 0 landscape, 1 portrait (how the phone is held) |
| 93 | 1 | u8 | reserved | |
| 94 | 2 | u16 | reserved | |
| 96 | 4 | u32 | crc32 | over bytes 0..95 (v1: at 68 over 0..67; v2: at 84 over 0..83) |

Python struct: body `<4sBBBBIIHHHHdd3f4f` (68) + v2 `<fffBBH` (16) + v3 `<ffBBH` (12) + `<I`.

**Orientation frame.** The quaternion is the pose of a *filming camera*: x = screen right, y = screen up, looks
down -z, **already corrected for how the phone is held** — i.e. `ARCamera.viewMatrix(for: interfaceOrientation)`
inverted, not the raw sensor-landscape `ARCamera.transform`. Position is `ARCamera.transform` translation.

Validation the receiver applies (any failure = silently dropped, counted "malformed"): length/version match, CRC,
magic, msg_type 1, known tracking enums, finite numbers, |pos| <= 1000 m, unit quaternion, send >= capture,
mode in {0,1}, view_orientation in {0,1}.

### Ack packet (PC -> phone), magic `ACHA`, 24 bytes, one per pose packet

| off | size | type | field |
|---:|---:|---|---|
| 0 | 4 | char[4] | `"ACHA"` |
| 4 | 1 | u8 | version = 1 |
| 5 | 1 | u8 | status: 0 ignored (another phone locked in, out of order, stale), 1 accepted and live, 2 accepted but holding (frozen/paused/tracking lost/playback) |
| 6 | 2 | u16 | reserved |
| 8 | 4 | u32 | echoed seq |
| 12 | 8 | f64 | echoed send_time -> phone computes RTT = now - send_time, puts it in the next pose's rtt_tenth_ms |
| 20 | 4 | u32 | crc32 of 0..19 |

### How the controls are sent (all inside every pose packet; no separate commands)
- **Freeze (clutch):** hold `flags.bit0` while the shutter is held. Receiver holds the last output; on release it
  re-anchors (yaw + translation) so the camera continues from where it was.
- **Pause:** `flags.bit1` (same hold logic).
- **Recenter:** increment `recenter_count`. Receiver recalibrates "forward"/origin and bumps `generation`; the game
  re-anchors on a new generation so the view doesn't jump.
- **Zoom:** `zoom` (focal / 24 mm). Passed straight to shared memory; the *game* computes the FOV from zoom +
  vf_aspect + view_orientation (`timewarp.game_vfov_for_view`, our `rig.fovForView`).
- **Walk:** `mode = 1` + `joy_x/joy_y`. Passed through; the game moves the rig. Receiver zeroes the stick after
  0.5 s of silence ("Wi-Fi dropped mid-walk: stop").
- **Movement scale:** `move_scale` (> 0 overrides the receiver's Translation scale; a change triggers a recenter).
- **Live view on/off:** `flags.bit2` in every packet while the viewfinder is on GAME; the encoder stops 3 s after the
  flag stops (`LiveView.IDLE_STOP_S`). `flags.bit3` = "I am stuck waiting for a keyframe"; held > 1.5 s, the receiver
  restarts the encoder (max every 5 s) because FFmpeg can't be asked for an IDR.

### Source lock and timing rules (service.py, pipeline.py)
- First valid sender is locked. Same IP + new session_id = app relaunch, accepted. Another phone takes over only
  after 3 s of silence (`source_timeout_s`, `auto_switch_source`), or via the GUI's "Accept new phone".
  Optional `allowed_ips` whitelist. **No authentication**: anyone on the LAN can drive the camera.
- Out-of-order / duplicate seq dropped; capture_time must increase within an epoch.
- Staleness: one-way delay relative to the best seen (`recv - send_time` minus running minimum, creeping 2 ms/s for
  clock drift); > 150 ms late = dropped.
- After tracking loss: 6 good frames before resuming; relocalisation jumps > 0.30 m (+5 m/s·dt) or > 25 deg
  (+720 deg/s·dt) are rejected and re-anchored.
- Smoothing: 1-euro filter on position and rotation (presets Handheld/Product/Fast action/Raw).
- Silence > 0.5 s = HOLD_STALE.

### Coordinate frames (transform.py)
ARKit world A (right-handed, +Y up) -> continuity-corrected A' (yaw+translation offset changed at every re-anchor;
gravity never touched) -> calibration frame K (origin and heading at Recenter) -> **filming frame F**: right = +X_K,
up = +Y_K, forward = -Z_K. Shared memory carries position (m, scaled), forward and up unit vectors in F. Pitch/roll
are gravity-absolute; heading/position are relative to Recenter. Games map F onto their world at engage time (our
`rig.lua`). Only direction vectors cross to the game, so no Euler/handedness ambiguity.

### What a new phone app must do to stay compatible
Send v3 `ACHC` at ARFrame rate with the fields above, read `ACHA` acks for RTT and status text, keep
`session_id` per launch, bump `epoch` on session restart and orientation change, and (for live view) set bit2,
reassemble `ACHV`, report bit3. That is the whole contract; nothing else is negotiated.

---

## 2. Live view

### Capture/encode (PC)
- **Runs only while the phone sets WANT_VIDEO**; stops 3 s after.
- Backend `ffmpeg` (default) or `native` (`native\handheld_capture.exe`, recommended for AMD in the README; emits
  `ACHF` frames: 32-byte header `<4sBBHIIdd` magic, ver 1, flags bit0 key, size, t_cap and t_enc as QPC seconds,
  then Annex-B). We have no source for the native exe.
- FFmpeg: `ddagrab` (Desktop Duplication, GPU) of a monitor -> GPU scale (`vpp_amf` on AMD, `scale_d3d11` on
  NVIDIA, CPU `scale` fallback) -> H.264 encoder ladder `h264_amf`, `h264_nvenc`, `h264_qsv`, `libx264`, each tried
  with several option levels; the working one is cached in `liveview_cache.json`. Low latency: no B-frames,
  CBR, VBV = 0.2 s of bitrate, AMF `ultralowlatency`/`-latency 1`/`-async_depth 1`/`max_au_size` = 80 ms of bits,
  NVENC `p1 ull zerolatency`, keyframes forced on a clock (`-force_key_frames expr:gte(t,n_forced*0.5)`) because AMF
  ignored `-g`. Output `-f flv pipe:1`: FLV is used only as framing so a frame can be sent the moment it's complete
  (Annex-B would need the next start code). Capture time = FLV pts mapped with a sliding-minimum clock + 4 ms.
- Test pattern source (`testsrc2`) for checking phone + network without the game.
- **Defaults: 480 p (width from monitor aspect, 854 for 16:9), 30 fps, 4 Mbit/s, keyframe every 0.5 s**
  (range 144-2160 p, 10-240 fps, 0.5-100 Mbit/s). AUD/SEI dropped; every keyframe re-prefixed with SPS+PPS.
- Profile isn't pinned (AMF/NVENC default, normally Main/High, no B-frames). VideoToolbox decodes all of these.

### Transport: `ACHV` datagrams (PC -> phone, same socket), 90-byte header + payload <= 1200 B

| off | size | type | field |
|---:|---:|---|---|
| 0 | 4 | char[4] | `"ACHV"` |
| 4 | 1 | u8 | version = 3 |
| 5 | 1 | u8 | flags: bit0 keyframe, bit1 pose_time valid, bit2 warp allowed |
| 6 | 2 | u16 | chunk_index |
| 8 | 2 | u16 | chunk_count |
| 10 | 4 | u32 | frame_id (+1 per frame, wraps) |
| 14 | 8 | f64 | pose_time: **phone clock** capture_time of the pose this frame shows (the phone's own ARFrame.timestamp echoed back) |
| 22 | 4 | f32 | pc_age_ms: PC capture -> send |
| 26 | 4 | f32 | fov_v: game vertical FOV (deg), 0 = unknown |
| 30 | 12 | f32x3 | gains yaw, pitch, roll (receiver sensitivity/invert; roll 0 if roll-locked) |
| 42 | 2 | u16 | width |
| 44 | 2 | u16 | height (cropped, from the SPS) |
| 46 | 2 | u16 | payload_len |
| 48 | 2 | u16 | reserved |
| 50 | 8 | f64 | pc_capture_time (PC clock; only differences matter, for pacing) |
| 58 | 16 | f32x4 | filter_lag x,y,z,w: phone-camera-local rotation raw pose -> receiver's smoothed pose |
| 74 | 16 | f32x4 | render_rot x,y,z,w: game-camera-local rotation from that pose's target to what the game actually showed (game-side smoothing); identity if unknown |
| 90 | n | bytes | slice of the frame: **AVCC** (4-byte big-endian length + NAL), keyframes start with SPS, PPS |

Struct `<4sBBHHIdff3fHHHHd4f4f`. Big frames are paced: 16 datagrams back to back, then spread at 120 Mbit/s.
Socket send buffer 1 MiB on the PC; the phone should raise its receive buffer too.

### Timewarp (timewarp.py) — what it does
The frame shows the game for phone orientation `q_then` (the pose at `pose_time`). By display time the phone is at
`q_now`. For pure rotation a homography re-aims the picture exactly, so aiming feels instant and only content is late.
1. Phone keeps a **history of its own poses keyed by ARFrame.timestamp** (`OrientationHistory.swift`) and looks up
   `q_then` at `pose_time`.
2. `q_now` = newest ARKit pose refined by CoreMotion attitude (100-200 Hz): `q_ar * C^-1 (att_then^-1 att_now) C`,
   C (0/90/180/-90 deg about z, normal or inverted attitude) picked automatically by comparing ARKit vs gyro deltas
   (`AlignmentEstimator`).
3. `r = conj(render_rot) * relative_rotation(q_then * filter_lag, q_now, gains)` (`warp_rotation`).
4. Render per screen pixel: ray in viewer frame -> rotate by r -> project into the game frame using half-tangents
   from `fov_v` and width/height -> texture uv (`screen_sample`). Only if flag bit2 is set; else identity.
   Clamp so no black edges (`warp_fraction`), max ~30 deg.
5. The viewfinder is sized like the Camera app: 4:3 "sensor" at the chosen focal length (24 mm x zoom; sensor half
   long side 17.3 mm), filling the whole screen from 1x up (`view_coverage`, eases in 20-24 mm). The game renders
   `game_vfov_for_view(...)` so its frame covers what the phone shows.

**What the phone sends back for timewarp:** nothing extra — only the normal pose stream (its capture_time is what
comes back as `pose_time`). The *game* side must write the View block (FOV, live, pose heartbeat, render_rot) so the
receiver can fill `fov_v`, `render_rot`, and the warp flag (section 3).

### Phone-side work to display it
1. UDP receive on the pose `NWConnection`; route by magic (`ACHA` vs `ACHV`).
2. `FrameAssembler`: collect chunks per frame_id; drop partial frames after 250 ms or when a newer one completes;
   after any gap require a keyframe (set NEED_KEYFRAME in pose flags until one arrives).
3. Split AVCC NALs; SPS/PPS -> `CMVideoFormatDescriptionCreateFromH264ParameterSets` (recreate the
   `VTDecompressionSession` when they change); remaining NALs -> `CMBlockBuffer` + `CMSampleBuffer` (already AVCC,
   4-byte lengths, no conversion) -> `VTDecompressionSessionDecodeFrame` with real-time flags,
   output `kCVPixelFormatType_420YpCbCr8BiPlanarFullRange` IOSurface-backed for zero-copy Metal textures
   (`CVMetalTextureCache`).
4. `FramePacer` (pacing.py): show_at = pc_capture_time + min(arrival - capture over 3 s) + percentile lateness
   (90/97/99 % for low latency/balanced/smooth, max 50 ms); show the newest due frame each display refresh.
5. Metal draw in an `MTKView` (or `CAMetalLayer` + `CADisplayLink` at 120 Hz) with the warp in the fragment shader:
   NV12 -> RGB + homography sampling.
(`AVSampleBufferDisplayLayer` would be simpler but can't timewarp; use it only for a first milestone.)

---

## 3. Shared-memory blocks (shm.py)

Windows named session-local file mappings, `mmap(-1, size, tagname="Local\\<name>")`. Names start with
`AcTools.CSP.Limited.` so CSP's Lua `ac.readMemoryMappedFile` can open them from any script type. All fields
4 bytes, no padding. Torn-read protection: writer writes seqBegin, then payload, then seqEnd; reader accepts only if
equal. Off Windows / in tests: files in `%TEMP%\handheldcam_shm\`.

### Pose — `AcTools.CSP.Limited.HandheldCam.Pose`, 108 bytes, receiver writes (on every accepted pose and at 30 Hz heartbeat)
`<IIIIiIII15fIIfI`

| off | field | type | notes |
|---:|---|---|---|
| 0 | magic | u32 | 0x31434D48 ("HMC1") |
| 4 | version | u32 | 3 |
| 8 | seqBegin | u32 | |
| 12 | heartbeat | u32 | +1 per write (the game reports which heartbeat it rendered) |
| 16 | outState | i32 | 0 no data, 1 live, 2 hold tracking, 3 frozen, 4 paused, 5 stale, 6 playback |
| 20 | flags | u32 | 1 recording, 2 playback, 4 source connected (< 1 s) |
| 24 | generation | u32 | bumps on recenter/new calibration: game re-anchors |
| 28 | trackingState | u32 | 0/1/2 |
| 32 | px,py,pz | f32x3 | filming-frame position (m, scaled) right/up/forward |
| 44 | fx,fy,fz | f32x3 | forward unit vector |
| 56 | ux,uy,uz | f32x3 | up unit vector |
| 68 | latencyMs | f32 | capture->shm estimate, -1 unknown |
| 72 | rxHz | f32 | |
| 76 | lossPct | f32 | |
| 80 | zoom | f32 | |
| 84 | joyX | f32 | 0 during playback |
| 88 | joyY | f32 | |
| 92 | mode | u32 | 0 handheld, 1 walk |
| 96 | viewOrientation | u32 | 0 landscape, 1 portrait |
| 100 | vfAspect | f32 | |
| 104 | seqEnd | u32 | |

### Status — `...HandheldCam.Status`, 48 bytes, game writes every frame
`<IIIIIIIi3fI`: magic 0x31534D48 ("HMS1"), version 1, heartbeat, engaged, grabbed, lastGeneration, appliedCount,
errorCode (1 another script holds the camera, 2 receiver data missing, 3 grab failed), fps, lookSign, sideSign,
recenterRequests (a change = receiver recenters: game-side Recenter hotkey). Receiver treats the game as alive if
the heartbeat changed within 1 s (timewarp is disabled otherwise).

### View — `...HandheldCam.View`, 48 bytes, game writes every frame (for timewarp)
`<IIII2fII4f`: magic 0x31564D48 ("HMV1"), version 2 (1 accepted), frame, live (camera follows the phone now),
fovV (deg, applied this frame), smoothing (s), poseHeartbeat (Pose heartbeat this frame was rendered from),
hasRot, rx, ry, rz, rw (rotation from that pose's target to what was shown, camera-local x right/y up/z back).

Our BeamNG mod can't open mappings (FFI refuses C functions), so `handcam_bridge.py` relays: Pose -> UDP
127.0.0.1:47830 on change + every 0.25 s; Status/View (48 B, told apart by magic) from the game on 47831 back into
the mappings.

---

## 4. Receiver GUI (gui.py) and ideas for ours

**Today:** Tkinter + ttk (ships with python.org Python), 900x640 min, refresh every 50 ms from `svc.snapshot()`.
- Big coloured state label (LIVE green, holding orange, frozen/paused blue, stale red, playback purple).
- Status rows: PC address(es) to type into the phone, listening port, phone ip:port + last packet age, ARKit
  tracking + reason, packets (rate, loss, stale, out-of-order, bad, foreign, rejected jumps, re-anchors), latency
  breakdown (phone capture->send + RTT/2 + receiver), AC app state (running/engaged/fps/error), position R/U/F,
  yaw/pitch/roll, phone controls (mode, zoom in mm, joystick, orientation, movement scale, protocol version), live
  view (encoder, fps, Mbit/s, capture->sent ms, restarts, keyframe warnings).
- Buttons: Recenter (R), Freeze (F), Pause (P), Accept new phone, Record path / Play path (JSONL, loop).
- Canvas: top-down 12 s camera trail with heading arrow. Log pane.
- Tabs: Camera (translation scale, rotation/roll sensitivity, roll lock, six inverts, 1-euro preset + sliders),
  Live view (enabled, source desktop/testpattern, encoder, backend, monitor, height, fps, bitrate, keyframe interval,
  timewarp render delay and fine-tune), Network (bind, port). Save settings -> `config.json`.
- Also `--headless` (status line every 0.5 s), `--no-shm`, logs in `logs\receiver.log`.

**Ideas for ours** (keep the service/pipeline as the core; only the shell and game layer change):
- One window, three areas: big **connection card** (QR code with `tsscam://ip:port` + typed IP, phone name, state
  colour, latency), **game card**, **live preview** (the same frame the phone gets, small) — advanced settings
  behind a gear, not in the main view.
- **Game picker:** BeamNG / Assetto Corsa / Auto. Auto = poll running processes every 2 s (stdlib `tasklist /fo csv`
  or ctypes `EnumProcesses`, no psutil): `BeamNG.drive.x64.exe` (BeamNG; launcher is `BeamNG.drive.exe`),
  `acs.exe` (Assetto Corsa; `AssettoCorsa.exe` is only the launcher).
- **How game hooks plug in:** a game = a small module with `process_names`, `start()/stop()`, how it reaches the game,
  and `capabilities()`. The core already publishes one game-neutral Pose/Status/View triple:
  - AC: nothing to do (CSP Lua reads the mappings directly).
  - BeamNG: run our bridge loop in-process (it's 60 lines; drop the separate window/batch) — UDP 47830/47831.
  The game module also reports the game's capability list (below) to forward to the phone, and maybe the game window
  to capture (ddagrab `output_idx` of the monitor the game is on, or a window-capture backend later).
- Tech: a tray icon + small window. Stay on Python (reuse the MIT service). Tkinter can look decent with a dark ttk
  theme; a web UI (stdlib `http.server` + one HTML page in the browser) is the zero-dependency way to "nicer". Ship
  as an embedded-Python zip or PyInstaller exe so users don't install Python. Bundle FFmpeg (LGPL/GPL build: check
  licence before redistributing) or keep "winget install Gyan.FFmpeg".
- Keep the MIT notice of AcHandCam in a `THIRD_PARTY_NOTICES` file and in any file derived from theirs.

---

## 5. Our iPhone app: architecture proposal

Target iOS 16+ (Developer Mode exists from 16 and is required for sideloaded apps anyway; iPhone 8 and later).
Use iOS 17 only if every test phone has it (gives `@Observable`). Portrait-locked UI like AcHandCam; rotate
the controls in-app from CoreMotion/ARKit so ARKit never restarts on rotation.

**Modules (one target, folders, no frameworks):**
- `Tracking/` — `ARSession` + `ARWorldTrackingConfiguration` (6DoF, `worldAlignment = .gravity`, no plane detection,
  no scene reconstruction, `isAutoFocusEnabled` default, video format with the highest fps). `ARSessionDelegate`
  `session(_:didUpdate:)` -> pose on a serial queue (don't retain ARFrames: ARKit stalls when > ~10 are held).
  Orientation for the filming camera: `frame.camera.viewMatrix(for: .portrait/.landscapeRight).inverse`. Track
  `trackingState` -> enums; restart/relocalise handling bumps `epoch`. `CMMotionManager` deviceMotion at 200 Hz for
  the timewarp's "now".
- `Net/` — `NWConnection(host:port:using: .udp)`, `serviceClass = .interactiveVideo`, `prohibitExpensivePaths`
  off, receive loop with `receiveMessage`. One connection for pose out + acks/video in. Packet codecs as plain
  `Data` writers mirroring the tables above, plus unit tests against hex vectors produced by `protocol.encode_pose`.
- `Video/` — FrameAssembler, VideoToolbox decoder, FramePacer, Metal timewarp renderer (section 2).
- `UI/` (SwiftUI) camera-app style:
  - Full-screen viewfinder: GAME (live view) or PHONE (ARKit owns the camera, so no AVCaptureSession: draw
    `ARFrame.capturedImage` with the same Metal renderer).
  - Bottom: mode tabs (HANDHELD · WALK · later CAR), big shutter (tap = recenter? hold = freeze), zoom chips
    .5 / 1 / 2 / 3 above it + pinch; 13/24/48/77 mm are the Camera-app equivalents.
  - **Thumbsticks:** left stick = move/strafe (joy_x/joy_y), right vertical slider = up/down. The v3 packet has no
    up/down axis: until our own protocol exists, send up/down only to our receiver (below). Haptics on mode changes.
  - Status pill top: PC name, latency, tracking state. Setup sheet: discovered PCs + typed IP, movement scale,
    smoothness.
  - **Car-control panel** (CAR tab): built from a capability list the receiver sends, e.g.
    `[{id:"horn",kind:"button"},{id:"lights",kind:"toggle"},{id:"camera_target",kind:"enum",values:[...]},
    {id:"ride_with_car",kind:"toggle"}]`; the phone renders buttons/toggles generically and sends `{id,value}`.
    Unknown ids are ignored, so games can add controls without an app update.
- **Our protocol extension (keeps AcHandCam compatibility):** keep sending byte-exact `ACHC` v3 poses. Add new
  packet types with their **own magic** on the same port, e.g. `TSSH` hello (phone -> PC: app version, device,
  wants caps), `TSSI` info (PC -> phone: receiver name, game, capability JSON), `TSSC` command (phone -> PC:
  control id + value, seq, ack'd), `TSSX` extra axes (up/down, roll lock...). The AcHandCam receiver drops unknown
  datagrams as "malformed" (logs the first 20, then every 500th) — harmless. If the phone hears no `TSSI` it runs
  in AcHandCam-compatible mode and hides CAR/up-down.
- **Discovery:** Bonjour + typed IP fallback. Receiver advertises `_tsscam._udp` (Windows 10 1809+ has
  `DnsServiceRegister` in dnsapi.dll — ctypes, no dependency; or `python-zeroconf`). Phone uses `NWBrowser` for
  `.bonjour(type: "_tsscam._udp", domain: nil)` and connects to the endpoint directly. Needs `NSBonjourServices`
  = `["_tsscam._udp"]` and `NSLocalNetworkUsageDescription`. Bonjour does **not** need the multicast entitlement
  (the system does the mDNS); our own broadcast/multicast would, and free accounts can't get it [5].
  Keep the typed IP + QR (receiver shows `tsscam://192.168.x.y:47820`; scan before ARKit starts) because Bonjour is
  often blocked on guest/mesh Wi-Fi.

---

## 6. Building without a Mac

### Project generation
- **XcodeGen** [8]: a readable `project.yml` -> `.xcodeproj` on the runner (`brew install xcodegen`, ~30 s; not
  preinstalled on the image as far as the readme shows). Recommended: no pbxproj in git, no merge pain, editable on
  Windows.
- Tuist: more powerful, heavier (own toolchain, Swift manifests); overkill for one target.
- Hand-written `.xcodeproj`: pbxproj UUID soup, impossible to maintain without Xcode. No.
- SwiftPM-only: can't produce an iOS `.app` bundle with Info.plist/entitlements via `xcodebuild` cleanly. No.

### Runner
`runs-on: macos-26` = macOS 26 arm64, default **Xcode 26.6 (17F113)**, also 26.5 … 26.0.1; iOS SDKs 26.0-26.5;
`macos-latest` points to the same image [1][2]. Pin the Xcode with `xcode-select` so an image update doesn't change
the toolchain silently. Standard runners are **free for public repos**; private repos pay $0.062/min on macOS
(~190 min from the free $12/month) [3].

### Workflow (`.github/workflows/ios.yml`)
```yaml
name: ios
on: { push: { paths: ["ios/**", ".github/workflows/ios.yml"] }, workflow_dispatch: {} }
jobs:
  build:
    runs-on: macos-26
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - run: sudo xcode-select -s /Applications/Xcode_26.6.app
      - run: brew install xcodegen && xcodegen generate --spec ios/project.yml
      - name: Unit tests (packet codecs, timewarp maths) on the simulator
        run: xcodebuild test -project ios/TSSCam.xcodeproj -scheme TSSCam
               -destination 'platform=iOS Simulator,name=iPhone 17' | xcpretty || exit ${PIPESTATUS[0]}
      - name: Archive unsigned
        run: |
          xcodebuild archive -project ios/TSSCam.xcodeproj -scheme TSSCam -configuration Release \
            -sdk iphoneos -destination 'generic/platform=iOS' \
            -archivePath "$RUNNER_TEMP/TSSCam.xcarchive" \
            CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" DEVELOPMENT_TEAM=""
      - name: Package .ipa
        run: |
          mkdir -p "$RUNNER_TEMP/ipa/Payload"
          cp -R "$RUNNER_TEMP/TSSCam.xcarchive/Products/Applications/TSSCam.app" "$RUNNER_TEMP/ipa/Payload/"
          cd "$RUNNER_TEMP/ipa" && zip -qry "TSSCam-${GITHUB_SHA::7}-unsigned.ipa" Payload
      - uses: actions/upload-artifact@v4
        with: { name: TSSCam-ipa, path: "${{ runner.temp }}/ipa/*.ipa", retention-days: 14 }
```
(Check the simulator name with `xcrun simctl list devices` on the image; the test step can use
`-destination 'generic/platform=iOS Simulator'` for build-only. `xcpretty` may not be installed: drop it if not.)
Download the artifact with `gh run download -n TSSCam-ipa` on Windows, then Sideloadly.

### Info.plist / entitlements
- `NSCameraUsageDescription` (ARKit uses the camera) [6]
- `NSLocalNetworkUsageDescription` (required for any LAN traffic, incl. unicast UDP to a typed IP) [4][5]
- `NSBonjourServices` = `["_tsscam._udp"]` if we browse [4]
- `UIRequiredDeviceCapabilities` = `[arkit, arm64]`; `UISupportedInterfaceOrientations` = portrait;
  `CADisableMinimumFrameDurationOnPhone` = YES (120 Hz on ProMotion); `UILaunchScreen` = {}.
- `UIApplicationSupportsIndirectInputEvents` not needed; no background modes (iOS suspends networking in background;
  camera stops anyway).
- **Entitlements: none.** Don't add multicast, App Groups, push, iCloud: free Personal Teams can't sign most of
  them and Sideloadly would fail or strip them.

### Free Apple ID (Personal Team) limits
- Profiles expire after **7 days**; the app stops launching until re-signed (Sideloadly can auto-refresh while the PC
  and phone are on the same network) [7].
- **3 sideloaded apps** active per device at a time [9].
- **10 App IDs per 7 days** per account [10]. Every new bundle id costs one: keep one fixed bundle id
  (e.g. `com.tss.cam`; Sideloadly may append a suffix, keep that setting stable).
- iOS 16+: **Developer Mode** must be on (Settings > Privacy & Security), and the developer profile trusted
  (Settings > General > VPN & Device Management) [11].
- Sideloadly on Windows needs iTunes and iCloud from apple.com (not the Microsoft Store builds) — already in our
  handcam README.

### Pitfalls
- `xcodebuild archive` without signing still embeds nothing to sign: fine. Don't use `-exportArchive` (needs
  signing); zip `Payload/` yourself. Make sure `Payload/X.app/X` is arm64 (`lipo -info`).
- Metal shaders compile into `default.metallib` at build time: fine on the runner, but a shader error only shows in
  CI logs; add a `xcrun metal -c` step or keep the simulator test step.
- Swift version drift: pin Xcode; the runner image changes defaults (26.6 now, Xcode 27 preview available).
- ARKit doesn't run in the simulator: everything ARKit-dependent must be behind protocols so codecs and maths are
  testable in CI; real tests only on the phone.
- Local network prompt appears on first send, not at launch; until the user answers, sends silently fail/"waiting".
  No API reports the permission: show "Allow Local Network in Settings" after N s without acks [5].
- Unsigned build + Bonjour type not in `NSBonjourServices` = browse fails with -65555 (NoAuth).
- `CODE_SIGNING_ALLOWED=NO` also skips entitlements processing: entitlements we'd want only come from
  Sideloadly's signing anyway.
- Public repo during development: never commit any Apple ID, Sideloadly or signing material; artifacts are public
  to anyone with repo read access (retention 14 days).

---

## 7. Risks and the fastest test loop

**Risks**
- **Video latency/robustness over Wi-Fi** is the hardest part (assembler, keyframe recovery, pacing, timewarp).
  Mitigation: port the reference Python (`liveview.FrameAssembler`, `pacing.FramePacer`, `timewarp`) 1:1 with tests
  sharing vectors, as AcHandCam did.
- **No Mac, no debugger:** crashes only show in Settings > Privacy > Analytics Data (`.ips` files) or via Sideloadly's
  device log. Add an in-app log view + "send log to PC" (UDP/HTTP to the receiver) from day one.
- **7-day expiry / 3-app cap** for testers; App ID burn if the bundle id changes.
- **Protocol coupling to AcHandCam:** if they change `ACHC` v4, our app keeps talking v3 (receiver accepts old
  versions); our own receiver removes the dependency. Their receiver is MIT (fine to fork, keep notice); the phone
  app is closed: we reuse nothing from it, only the wire format documented by the MIT receiver.
- **Local network permission denial** and Bonjour blocked on some routers (typed IP/QR fallback).
- **ARKit thermal throttling** with ARKit + H.264 decode + Metal at 120 Hz: drop to 60 Hz display, 30 fps video.
- **FFmpeg availability / encoder quirks** per GPU (the ladder + cache exists); the native exe is closed source.
- **Security:** any LAN host can drive the camera; add a pairing code in `TSSH` for our receiver.

**Fastest test loop**
1. **No phone:** a Python phone simulator that sends `ACHC` v3 (use `protocol.encode_pose`) with a scripted path
   into the real receiver + our bridge + BeamNG mod (our `tools/run_tests.py` already fakes the receiver for the Lua
   side). Covers protocol, shm, game.
2. **Phone app logic without a Mac:** codecs/assembler/pacer/timewarp as plain Swift in a package-like folder,
   unit-tested in CI on the simulator against hex/JSON vectors produced by the Python reference (one script writes
   `vectors.json`; both sides read it).
3. **Live view without the game:** receiver `capture = testpattern` -> phone. Isolates network + decode.
4. **On device:** push -> CI (~5-8 min) -> `gh run download` -> Sideloadly install (~1 min). Keep the phone
   connected by USB; reinstalling with the same bundle id keeps settings and doesn't burn an App ID.
5. Milestones: (a) pose only, typed IP, against the **unchanged AcHandCam receiver** — proves compatibility;
   (b) acks/RTT/status; (c) live view with `AVSampleBufferDisplayLayer`; (d) Metal timewarp; (e) our receiver + TSS
   packets (Bonjour, caps, CAR panel, up/down).

---

## References
1. GitHub runner image readme, macOS 26 arm64 (Xcode 26.6 17F113 default, macOS 26.6.2 25G83):
   https://github.com/actions/runner-images/blob/main/images/macos/macos-26-arm64-Readme.md
2. Runner labels (`macos-26`, `macos-latest` = macOS 26 arm64): https://github.com/actions/runner-images
3. GitHub Actions billing (standard runners free in public repos; macOS $0.062/min private):
   https://docs.github.com/en/billing/concepts/product-billing/github-actions
4. `NSLocalNetworkUsageDescription`:
   https://developer.apple.com/documentation/bundleresources/information-property-list/nslocalnetworkusagedescription ;
   `NSBonjourServices`: https://developer.apple.com/documentation/bundleresources/information-property-list/nsbonjourservices
5. TN3179 Understanding local network privacy (what triggers the prompt; multicast entitlement):
   https://developer.apple.com/documentation/technotes/tn3179-understanding-local-network-privacy
   (page body didn't render for the fetch tool; the summary above is from Apple's published behaviour — re-read it
   before shipping.)
6. `NSCameraUsageDescription`: https://developer.apple.com/documentation/bundleresources/information-property-list/nscamerausagedescription ;
   ARWorldTrackingConfiguration: https://developer.apple.com/documentation/arkit/arworldtrackingconfiguration ;
   VTDecompressionSession: https://developer.apple.com/documentation/videotoolbox/vtdecompressionsession ;
   NWConnection: https://developer.apple.com/documentation/network/nwconnection ;
   NWBrowser: https://developer.apple.com/documentation/network/nwbrowser
7. Free provisioning profile 7-day expiry: https://developer.apple.com/forums/thread/62300
8. XcodeGen: https://github.com/yonaskolb/XcodeGen ; upload-artifact: https://github.com/actions/upload-artifact
9. 3 sideloaded apps per device (AltStore FAQ): https://faq.altstore.io/altstore-world/your-altstore
10. "You may create up to 10 App IDs every 7 days": https://developer.apple.com/forums/thread/675347
11. Developer Mode: https://developer.apple.com/documentation/xcode/enabling-developer-mode-on-a-device
12. Sideloadly: https://sideloadly.io
