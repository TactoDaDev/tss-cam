# Assetto Corsa + CSP: Lua APIs for the phone camera kit

Research date: 2026-11-06. Read-only; nothing was installed and the game was not launched.

## Sources

- **Generated CSP Lua API for app scripts, installed locally (the main source):**
  `E:\Steam\steamapps\common\assettocorsa\extension\internal\lua-sdk\ac_apps\lib.lua` (18,489 lines).
  It belongs to the installed CSP **0.3.2-preview2, build 4305** (`extension/config/data_manifest.ini`).
  Line numbers below refer to this file. Other script types have their own folder next to it
  (`ac_car_cphys`, `ac_new_modes`, `ac_tools`, ...), and `rules.json` there says which folder
  goes with which script type.
- **CSP Lua SDK source:** https://github.com/ac-custom-shaders-patch/acc-lua-sdk (commit
  7cf60f5, 2026-08-17). It has the hand-written Lua wrappers (`common/*.lua`). The bulk of the
  definitions are generated from C++ by `__definitions()` and only appear in the installed
  `lib.lua`.
- **CSP Lua apps wiki:** https://github.com/ac-custom-shaders-patch/acc-lua-sdk/wiki/Lua-apps
  (manifest keys `LAZY`, `HIDDEN_ONLINE`, `HIDDEN_OFFLINE`, `FUNCTION_MAIN`. Lua apps exist since
  CSP 0.1.76.)
- **CSP's shared Lua libraries:** https://github.com/ac-custom-shaders-patch/acc-lua-internal
  ("use `shared/` prefix, like `require('shared/socket')`"), installed at
  `extension/internal/lua-shared/`.
- **Code that uses these calls:**
  - CSP's own `extension/internal/lua-apps/CspDebug/CspDebug.lua` (replay toggle, driver door,
    extra animations);
  - `extension/lua/new-modes/*/mode.lua` (`ac.grabCamera`, `ac.setAppsHidden`);
  - the installed third-party `apps/lua/HorizonLifeHud` (`ac.setExtraSwitch`);
  - AcHandCam (below).
- **Other camera apps work online:** the russozl camera apps' FAQ says "client side camera apps"
  work online and need CSP 0.3.0 preview or later (https://www.patreon.com/russozl/about).

"Verified" below means the definition is in the 4305 `lib.lua`. Nothing here has been tested in
the game. Items marked **verify** need a quick in-game check.

---

## 0. How the AcHandCam AC app works (MIT, `D:\tss-cam\ref\achandcam-ac\apps\lua\AcHandCam`)

**Files**
- `manifest.ini`: `[CORE] LAZY = NONE`, so the app loads with the session and its hotkeys work
  even when its window is closed. It has one window, `FUNCTION_MAIN = windowMain`.
- `AcHandCam.lua` (768 lines): all game logic.
- `src/hcmath.lua`: pure maths, with no CSP globals, so plain LuaJIT can unit-test it. It also
  holds the shared-memory layouts.

**Data path:** phone sends UDP (`protocol.py`, "ACHC" packets of 72/88/100 bytes with a
CRC-32) → Python receiver → three named shared-memory blocks → Lua app → AC's free camera.

| Block (name) | Writer → reader | Layout (4-byte fields only) |
|---|---|---|
| `AcTools.CSP.Limited.HandheldCam.Pose` (108 B) | receiver → app | magic `HMC1`, version 3, `seqBegin`, heartbeat, state, flags, generation, tracking, pos xyz, forward xyz, up xyz, latency, rxHz, loss, zoom, joyX/Y, mode, viewOrientation, vfAspect, `seqEnd` |
| `...HandheldCam.Status` (48 B) | app → receiver | magic, version, heartbeat, engaged, grabbed, lastGeneration, appliedCount, errorCode, fps, lookSign, sideSign, recenterRequests |
| `...HandheldCam.View` (48 B) | app → receiver | magic, version, frame, live, fovV, smoothing, poseHeartbeat, hasRot, quat xyzw (used to timewarp the live view on the phone) |

- **Shared memory**
  - Python opens the blocks with `mmap.mmap(-1, size, tagname="Local\\" + name)`.
  - Lua opens them with `ac.readMemoryMappedFile(name, layoutString)` and
    `ac.writeMemoryMappedFile(name, layoutString)`. The name has no `Local\` prefix.
  - The `AcTools.CSP.Limited.` prefix is the one every script type may open, with or without
    I/O permission (`common/ac_extras_connectmmf.lua`:
    `if not __allowIO__ and not string.startsWith(filename, 'AcTools.CSP.Limited.') then error(...)`).
  - **Torn reads:** the writer stores `seqBegin`, then the payload, then `seqEnd`. The reader
    reads `seqEnd`, then the payload, then `seqBegin`, and drops the sample unless the two match.
  - **Reconnecting:** the app retries `openPose()` once a second until the receiver exists.
  - **Liveness:** the heartbeat counters show whether each side is alive (1 s timeout).
- **Taking the camera (the "Drone Camera way")**
  - The app deliberately does **not** use `ac.grabCamera()`. The grabbed camera's transform
    "will be applied with the next frame", so a camera attached to a moving car trails it by one
    frame and shakes.
  - Instead, the app calls `ac.setCurrentCamera(ac.CameraMode.Free)` (F7). Then, every
    `script.update(dt)`, it calls `ac.setCameraPosition(vec3)`,
    `ac.setCameraDirection(look, up)` and `ac.setCameraFOV(deg)`.
  - It keeps an object shaped like `GrabbedCamera` (`ownShare` blend, `transformOriginal`) so it
    can blend in and out over `blendTime`.
  - On release it restores the previous `sim.cameraMode`.
  - If `ac.getSim().cameraMode` stops being `Free` (the user pressed a camera key), the app
    lets go.
- **Conventions it measures instead of assuming**
  - The sign of the look vector, by comparing its first applied frame with `sim.cameraLook`.
  - Left/right, from the car's wheels: `car.wheels[0].position` against `car.wheels[1].position`
    with `car.look`.
  - The side vector, which it leaves for CSP to derive (v1 got it wrong and mirrored the
    picture).
- **Follow-car mode:** the camera is anchored in the car's frame. It uses `car.bodyTransform`
  (the drawn body) or `car.position/look/up` (physics), and can keep or drop pitch and roll.
- **Walk mode:** the phone joystick moves the rig. `physics.raycastTrack(pos, dir, len)` keeps
  it above the ground, with a minimum clearance of 0.15 m.
- **FOV:** follows the phone zoom; `ac.getUI().windowSize` gives the aspect ratio.
- **Hotkeys:** `ac.ControlButton('AcHandCam/...', {keyboard={key=..., ctrl=true, shift=true}})`
  for engage, recenter, freeze and emergency release; `btn:pressed()` and `btn:control(vec2)`
  for rebinding in the UI.
- **Settings:** `ac.storage({...}, 'v2.')`.
- **Clean-up:** `ac.onRelease(releaseNow)`, plus `pcall` around every call that might be missing
  in an older CSP.
- **What it lacks for our kit:**
  - no PC→app command channel other than the pose block (recenter goes app→PC through
    `recenterRequests`);
  - no car controls, replay control or UI hiding;
  - no recording into the replay.

---

## 1. Camera control

**A. Free camera moved directly (what AcHandCam uses).** All verified at lib.lua 15355-15440.
- `ac.setCurrentCamera(mode: ac.CameraMode)`, with `ac.CameraMode.Free = 6` (F7).
- `ac.setCameraPosition(pos: vec3)`: "Moves free camera to a certain position".
- `ac.setCameraDirection(look: vec3, up: vec3?)`.
- `ac.setCameraFOV(fov: number)`: vertical FOV in degrees.
- `ac.setCameraExposure(exposure)` and `ac.setCameraDOF(distanceM)`: 0 turns DOF off.
- **Caveats**
  - These only work while the F7 free camera is active.
  - `ac.getSim().freeCameraAllowed` must be true. It maps to `[CAMERA] ALLOW_FREE_CAMERA=1` in
    `system/cfg/assetto_corsa.ini` (set to 1 on this PC); Content Manager exposes it as an AC
    setting.
  - Check it, and tell the user if it's off.

**B. Grabbed camera.** Verified at lib.lua 18127 and in the SDK `common/ac_gameplay.lua`.
- `ac.grabCamera(reason: string?) -> ac.GrabbedCamera|nil, err: string?`. It fails if another
  script holds the camera, and `err` names that script. `ac.isCameraGrabbed()` tells whether
  someone holds it.
- **Fields:**
  - `transform` (`mat4x4`: `.position`, `.look`, `.up`, `.side`; normalised automatically),
    `transformOriginal`;
  - `fov`/`fovOriginal`, `dofDistance`, `dofFactor`, `exposure`;
  - in the FFI struct: `clipNear`, `clipFar`, `lodMultiplier`;
  - `listenerVelocity`, `ownShare` (0-1 blend with AC's camera; at 1 CSP switches to the free
    camera), `cameraRestoreThreshold`.
- **Methods:** `:active()`, `:dispose()`, `:normalize()`, `:alignCar(...)`.
- **Caveat:** the transform applies on the next frame (the one-frame lag on moving cars, see
  section 0).
- It works without the free-camera setting and blends nicely.
- **Use it for** static and world shots and for blends. Use A for car-mounted shots.
- CSP's own new modes use it (`drift-challenge`, `rally`, `driver-interview`).

**C. Other camera controls**
- **Read the current camera:**
  - `ac.getSim().cameraPosition/cameraLook/cameraUp/cameraSide/cameraFOV/cameraMode`;
  - `ac.getCameraPosition()`, `ac.getCameraForward()`, `ac.getCameraUp()`, `ac.getCameraSide()`,
    `ac.getCameraFOV()`;
  - `ac.getCameraPositionRelativeToCar()`.
  - Note: `ac.getCameraDirection()` points **backwards**.
- **Clip planes:** `ac.overrideCameraClipPlanes(near?, far?)`, useful for a phone held right
  against bodywork.
- **Track detail far away:** `ac.setExtraTrackLODMultiplier(m)`.
- **First-person FOV:** `ac.setFirstPersonCameraFOV(deg)` and `ac.resetFirstPersonCameraFOV()`.
- `script.update` runs every frame, even with the window closed. The wiki also mentions a
  `FRAME_BEGIN` callback, called "right before scene has started rendering (can be used to move
  camera around)": a candidate if `update` turns out to be one frame late (**verify**).

**Online and offline**
- Nothing in the API marks the camera calls as offline-only.
- They are client-side: AcHandCam and the russozl camera apps run online (CSP 0.3.0 preview or
  later).
- The limit online is the user's free-camera setting (A) and other scripts holding the camera
  (B).
- A server can't block the camera through `csp_extra_options`: no such option was found.
- `manifest.ini` `HIDDEN_ONLINE=1` exists if we ever need to hide a window online.

**Minimum CSP:** 0.3.0 preview or later, to be safe (what the comparable apps require). All of
the above is present in 0.3.2-preview2.

**Suggested commands:** `cam.engage {method: free|grab}`, `cam.release`, `cam.pose {pos, look, up, fov}`
(the pose stream itself stays in the existing pose block), `cam.fov`, `cam.dof {dist}`,
`cam.exposure`, `cam.clip {near, far}`.

---

## 2. Car controls

The app script type (`ac_apps`) loads `ac_car_control`, `ac_car_control_physics` and
`ac_car_control_switch`. So do `ac_tools` and `ac_new_modes`. Car display scripts and car physics
scripts have a subset, but those belong to the car's author, not to us.

The `ac.set*` car functions without a car index act on the **target car**. That is car 0 (the
player) by default, and `ac.setTargetCar(carIndex) -> bool` changes it (lib.lua 16080). The SDK
note: "most functions called for remote cars will not have any effect. Also, effect of some
functions on AI cars might be cancelled out a few frames later." Read the state back from
`ac.getCar(i)`.

| Want | API (verified in lib.lua) | State to read back | Depends on the car? | Online |
|---|---|---|---|---|
| Headlights on/off | `ac.setHeadlights(active)` | `car.headlightsActive`, `car.lowBeams` | All cars have them. `car.headlightsAreHeadlights == false` means the key does something else (display mode) | Own car: works like a key press (**verify**). Remote cars: no |
| High beams | `ac.setHighBeams(active)`. Note: it only changes things when the car has separate low beams (`car.hasLowBeams`) | `car.highBeams` | yes | as above |
| Flash high beams | `ac.flashLights()` | | | as above |
| DRL | `ac.setDaytimeLights(active)` | `car.daytimeRunningLights` | needs a DRL setup in the car's CSP config | as above |
| Turn signals / hazards | `ac.setTurningLights(ac.TurningLights.None/Left/Right/Hazards)` (0..3) | `car.turningLeftLights`, `car.turningRightLights`, `car.hazardLights`, `car.turningLightsActivePhase` | visible only if `car.hasTurningLights` (CSP config emissives) | as above |
| Horn | No setter. Use `local c = ac.overrideCarControls(carIndex)`, then `c.horn = true` while held and `false` to stop | `car.hornActive`, `car.hasHornAudioEvent`, `car.sirenHorn` (siren cars: press toggles) | needs a horn sound event; siren cars behave differently | own car; **verify** |
| Wipers | `ac.setWiperMode(i)` with `0..car.wiperModes-1` (`ac.setWiperSpeed` is deprecated) | `car.wiperMode`, `car.wiperSelectedMode` | needs wipers set up (most Kunos cars and CSP-configured mods) | own car |
| Police / flashing lights | **No setter.** `car.hasFlashingLights` and `car.flashingLightsActive` are read-only | | the car's config usually ties them to an extra switch or the siren horn, so we'd need a per-car mapping | |
| Extra switches A-T | `ac.setExtraSwitch(index0to19, value) -> bool` (ignores availability checks unless the car physics script disabled it; hold-mode switches reset after a few frames). Also `ac.isExtraSwitchAvailable(i, availableToCarControl?)`, `ac.getExtraSwitchName(i)` (may be nil), `ac.accessExtraSwitchParams(i)` | `car.extraA..extraT`, `ac.getCarExtraSwitchState(car, i)` | **yes, entirely**: what A-T do (doors, hood, spoiler, light bars) is up to the car's CSP config | own car |
| Driver door | `ac.setDriverDoorOpen(carIndex, isOpen, instant)` (CspDebug uses it) | `car.isDriverDoorOpen` | needs a driver-door animation set up for the car (most Kunos cars, plus CSP configs) | visual; **verify** online |
| Hood / trunk / other doors | **No dedicated API.** Two ways: (a) the car's extra switch, if its config binds one; (b) play the car's own `.ksanim` ourselves, as CspDebug does: read `ac.INIConfig.carData(i, 'extra_animations.ini')` `[ANIMATION_n] FILE/MIN/MAX/TIME`, then call `ac.findNodes('carRoot:' .. i):setAnimation(contentCars .. '/' .. carId .. '/animations/' .. file, pos0to1)` every frame while it moves | | yes: only cars that ship these animations | (b) is local only and not synced online |
| Driver model hidden | `ac.setDriverVisible(carIndex, visible)` ("could also be used for screenshots") | `car.isDriverVisible` | | local |
| Car name tags | `ac.hideCarLabels(carIndex, hide)` | | | local |
| Engine start | No direct call. `ac.simulateCustomHotkeyPress('__EXT_STARTER', frames)` presses CSP's starter hotkey; the `controls.ini` section exists on this PC. Vanilla `[STARTER]` is not a CSP hotkey | `car.rpm` | AC cars normally never stall | own car |
| Engine off | `physics.setEngineStallEnabled(car, true)` + `physics.setEngineRPM(car, 0)` ("use it together ... to shut off the engine"; below 400 rpm the lap stays valid). Restart with the starter | | | **physics: only where `physics.allowed()`** |
| Handbrake | `physics.forceUserHandbrakeFor(timeS, value?)` (invalidates the lap), or `ac.overrideCarControls(i).handbrake = 0..1` (combined with the user's input by max) | `car.handbrake` | | override: own car; physics: see below |
| Neutral / gear | `ac.switchToNeutralGear()`, `physics.engageGear(car, g)` | `car.gear` | | |
| Freeze car | `physics.setCarNoInput(true)` (parked), `physics.setGentleStop(car, true)` | | | physics |

- **`ac.overrideCarControls(carIndex?) -> ac.CarControlsInput?`** (lib.lua 18246). It works for
  AI and user cars and has these fields:
  - `gas`, `brake`, `handbrake`, `clutch`, `steer` (`math.huge` keeps the user's value),
    `requestedGearIndex`, `gearUp`, `gearDown`;
  - `headlights` (toggle), `headlightsFlash`, `horn`, `changeCamera`, `lookLeft`, `lookRight`,
    `lookBack`;
  - `combineAxis` (0.3.0-preview125 or later).
  - Call `:active()` to check that AC physics has read it.
- **`physics.*` rule:** `physics.allowed() -> bool`. If false, "only `physics.raycastTrack()` is
  available". In practice that means offline single-player only, unless the server or online
  script allows it. Check it at runtime and grey out the buttons.
- **The CSP hotkey fallback:** `ac.simulateCustomHotkeyPress(key, frames=3)` covers anything with
  a CSP ("Patch") hotkey. Section names found in this PC's `controls.ini`:
  - lights: `__EXT_HAZARDS`, `__EXT_TURNSIGNAL_LEFT`, `__EXT_TURNSIGNAL_RIGHT`,
    `__EXT_TURNSIGNAL_CANCEL`, `__EXT_LOW_BEAM`, `__EXT_HEADLIGHTS_SWITCH`, `__EXT_DRL`,
    `__EXT_LIGHT_A` ... `__EXT_LIGHT_T` (the extra switches);
  - wipers: `__EXT_WIPERS_OFF`, `__EXT_WIPERS_1` ... `__EXT_WIPERS_4`, `__EXT_WIPERS_MORE`,
    `__EXT_WIPERS_LESS`;
  - other: `__EXT_STARTER`, `__EXT_SIM_PAUSE`, `__EXT_HIDE_UI`, `__EXT_SAVE_CLIP`,
    `__EXT_LOOK_BACK`, `__EXT_SWAP_SEATS`, `__EXT_SEAT_*`, `__EXT_FREECAM_*`.
  - Vanilla AC keys (`[ACTION_HORN]`, `[PAUSE_REPLAY]`, `[SLOWMO]`, `[FFWD]`, `[REV]`) are **not**
    CSP hotkeys, so this function can't press them.

**Suggested commands:** `car.lights {on}`, `car.highbeam {on}`, `car.flash`, `car.drl {on}`,
`car.signal {none|left|right|hazard}`, `car.horn {down|up}`, `car.wipers {mode}`,
`car.extra {index, on}`, `car.door {open, instant}`, `car.anim {name, open}`,
`car.driver {visible}`, `car.starter`, `car.engine_off`, `car.handbrake {value, seconds}`,
`car.neutral`, and `car.state?` (the app writes back what the car has and what is on).

---

## 3. Camera presets

**Let AC do it (best match with the user's own settings):**
- `ac.setCurrentCamera(ac.CameraMode.Cockpit)`: driver seat.
- `ac.setCurrentCamera(ac.CameraMode.Drivable)` + `ac.setCurrentDrivableCamera(ac.DrivableCamera.Chase|Chase2|Bonnet|Bumper|Dash)`
  (values 0..4).
- `ac.CameraMode.Car` + `ac.setCurrentCarCamera(i)`: the F6 cameras; there are
  `ac.getCar(i).carCamerasCount` of them.
- `ac.CameraMode.OnBoardFree` (F5); `ac.setOrbitOnboardCamera(true)` makes it orbit.
- `ac.CameraMode.Track` + `ac.setCurrentTrackCamera(set)` (`sim.trackCamerasSetsCount`).
- `ac.CameraMode.Free`.
- Read back with `sim.cameraMode`, `sim.driveableCameraMode`, `sim.carCameraIndex`.
- `ac.focusCar(index)` picks which car they follow.

**Compute it ourselves (for handheld shots anchored to a preset):** use `ac.getCar(i)`
(`ac.StateCar`).

- **Car frame:**
  - `position`, `look`, `up`, `side` (normalised);
  - `transform` (physics) and `bodyTransform` (the drawn body; it doesn't match the physics
    transform), `worldToLocal`;
  - `velocity`, `localVelocity`, `angularVelocity`;
  - `aabbCenter`/`aabbSize` (car-local), `bsCenter`/`bsRadius`;
  - `wheels[0..3].position`;
  - `graphicsToPhysicsTransform`.
  - Car-local to world: `car.bodyTransform:transformPoint(vec3)` and `:transformVector(vec3)`
    (`mat4x4` methods, lib.lua 5803/5821).
- **Driver seat:**
  - `car.driverEyesPosition` (car-local, user-adjustable);
  - or `ac.getOnboardCameraParams(i) -> ac.SeatParams {position, pitch, yaw}`, with defaults
    from `ac.getOnboardCameraDefaultParams(i)` (car.ini `[GRAPHICS] DRIVEREYES`,
    `ON_BOARD_PITCH_ANGLE`).
  - `car.isLeftHandDrive` says which side the driver is on.
- **Passenger seat:** no API. Mirror the driver's eye position in local X.
  - A mod car on this PC has `DRIVEREYES=0.369,1.28,-0.17` (LHD), so **+X is the car's left**
    there. Check the sign per car the way AcHandCam does (wheels 0 and 1).
- **Hood / bumper:** `ac.INIConfig.carData(i, 'car.ini')` (it reads `data.acd` too, see
  lib.lua 7054). Use `[GRAPHICS] BONNET_CAMERA_POS`, `BONNET_CAMERA_PITCH`, `BUMPER_CAMERA_POS`,
  `BUMPER_CAMERA_PITCH` (keys seen in a mod car's car.ini).
- **F6 cameras:** `ac.accessCarCamera(cameraIndex) -> ac.CarCameraParams {transform (car-relative; .look faces backwards), fov, exposure, clipNear}`.
- **Chase / orbit:** no data API for the chase rig. Build our own from `look`/`up`,
  `aabbSize` and `velocity`, or switch AC to `Drivable` + `Chase`.
- **Pick a car:**
  - `ac.getSim().focusedCar` (also works with the track camera), `closelyFocusedCar`;
  - `ac.iterateCars()`; `ac.getCarID(i)`; `ac.getDriverName(i)`;
  - `car.isCameraOnBoard`, `sim.isFocusedOnInterior`.

**Suggested commands:** `cam.preset {cockpit|bonnet|bumper|dash|chase|chase2|f5|orbit|f6:n|track:n|free}`,
`cam.anchor {car, seat: driver|passenger|hood|bumper|roof|rear|wheel_fl..., offset}`,
`cam.follow {car, tilt}`, `focus.car {index}`, `focus.next`, `focus.prev`.

---

## 4. Replays

- **Detect a replay:**
  - `ac.getSim().isReplayActive` (true in replays; "with CSP tweaks, replays might be active
    online too");
  - `ac.isInReplayMode()`;
  - `sim.isReplayOnlyMode` (AC started just to watch a replay) and `ac.getReplayFilename()`;
  - `sim.isLive` (false in replays);
  - `ac.onReplay(cb('start'|'stop'|'jump'))`.
- **Read the position:** `sim.replayFrames`, `sim.replayCurrentFrame`, `sim.replayFrameMs`,
  `sim.replayPlaybackRate` (read-only; 0.5 = 50 % slow motion).
- **Start/stop the instant replay:** `ac.tryToToggleReplay(active?, rewindS=30) -> bool`
  (CspDebug uses `ac.tryToToggleReplay(not sim.isReplayActive, 30)`).
- **Seek:** `ac.setReplayPosition(frameIndex, playCounter0to1)`.
- **Play / pause / speed:** no setter for playback rate or replay pause.
  - **Our approach:** run the replay clock ourselves. Each frame, call
    `ac.setReplayPosition(floor(t), frac(t))` with `t += dt * rate * 1000 / replayFrameMs`.
    That gives pause (rate 0), any slow motion, reverse and scrubbing.
  - **Verify** that this holds smoothly and that AC's own replay playback doesn't fight it (it
    may need AC's replay to be paused by the user once).
  - `ac.tryToPause(bool)` pauses the simulation; whether it pauses replay playback is
    **unverified**.
- **Follow a car:** `ac.focusCar(index)`. Cameras that follow `sim.focusedCar` keep working.
- **Camera override in replays:** free camera and grab both work in principle: F7 is available in
  AC replays, and the grab API has nothing replay-specific. **Verify** with AcHandCam's method.
  Note `sim.isMakingScreenshot`: during accumulation screenshots, a grabbed camera is paused and
  replay seeking is postponed.
- **Bonus: record our camera into the replay.**
  - `ac.ReplayStream(layoutTable, onStopCb?, frameDivisor?)` stores up to 256 bytes per frame,
    interpolated on playback. Write while live and read while `isReplayActive`.
  - With it, a handheld take recorded live plays back in the saved replay with the same camera.
  - `ac.writeReplayBlob(key, data)` and `ac.readReplayBlob(key)` store data that isn't per frame
    (settings).
- **Weather in replays:** `ac.overrideReplayConditions(set|nil)`.

**Suggested commands:** `replay.enter {rewind_s}`, `replay.exit`, `replay.seek {frame|seconds|fraction}`,
`replay.rate {x}` (0 = pause), `replay.play`, `replay.pause`, `replay.step {frames}`,
`replay.state?` (frames, current, frameMs, active).

---

## 5. Slow motion and simulation speed (offline)

- **There is no API for live physics slow motion.**
  - Checked every `ac.*` and `physics.*` function in `ac_apps`, `ac_new_modes`, `ac_tools` and
    `ac_online_script`: nothing sets a simulation time scale.
  - `sim.timeMultiplier` and `ac.setWeatherTimeMultiplier()` only change the time of day.
  - AC itself has slow motion only in replays (`[SLOWMO]` key).
- **What's possible:**
  - **Pause:** `ac.tryToPause(true|false) -> bool` (offline; refused online with a moving car),
    or `ac.simulateCustomHotkeyPress('__EXT_SIM_PAUSE')`. While paused, the free or grabbed
    camera still moves, so it works as a frozen-time photo mode (**verify**).
  - **Slow motion:** film live, then replay with our own replay clock (section 4) at any rate.
    Combined with `ac.ReplayStream`, the handheld path also plays back slowed down.
- **Impossible:** slowing a live session, offline or online.

**Suggested commands:** `sim.pause {on}`, `replay.rate {x}`. No `sim.timescale`.

---

## 6. Hiding the UI for clean filming

- **Apps:** `ac.setAppsHidden(hidden=true)` hides AC and Lua apps, the same as Ctrl+H. Read the
  state from `ac.getUI().appsHidden`. CSP's `driver-interview` mode uses it.
- **Whole UI:**
  - `ui.onExclusiveHUD(cb(mode) -> true|'apps'|'debug'..., noPadding?)`. Returning `true` stops
    the rest of the UI being drawn for that frame, **including AC's own UI**.
  - `mode` is one of `'menu'|'pause'|'results'|'replay'|'game'`. Apps only, not car or track
    scripts.
  - This is the strongest clean-frame switch, but only one script can hold it, and `uiHidden`
    reports it.
  - Alternatively, use the CSP hotkey `ac.simulateCustomHotkeyPress('__EXT_HIDE_UI')`.
- **HUD pieces:**
  - `ac.disableExtraHUDElements('sessionTime'|'fuel'|'proximity'|'virtualMirror'|'quickPitsMenu'|'leaderboard'|'startingLights'|'wrongWay'|'ping'|'damage' or a list, disable)`;
  - `ac.disableVirtualMirror(true)`;
  - `ac.hideCarLabels(carIndex, true)` (name tags);
  - `ac.hideMouseCursor()` (call every frame);
  - `ac.setDriverVisible(i, false)`.
- **Caveat:** our own app window must hide too. Use `ac.setAppsHidden`, or draw nothing in
  `windowMain` while filming.

**Suggested commands:** `ui.clean {on}` (exclusive HUD + labels + mirror + cursor),
`ui.apps {hidden}`, `ui.hud {element, hidden}`.

---

## 7. Channel between the PC receiver and the AC app

| Channel | API | Verdict |
|---|---|---|
| **Shared memory** | `ac.writeMemoryMappedFile(name, layout, persist?)`, `ac.readMemoryMappedFile(name, layout, persist?)` (layout = C struct body string, or a byte count for raw), `ac.disposeMemoryMappedFile(ref)`. The name has no `Local\`, and the `AcTools.CSP.Limited.` prefix works for every script type. Reader warning: "Do not attempt to modify any of its contents ... would result in Assetto Corsa ... crashing". A new block starts zeroed; an existing one keeps its contents | **Use this.** Proven by AcHandCam with Python `mmap(tagname="Local\\...")`. No ports, firewall or AV prompts; lowest latency; survives either side restarting (reopen with retry) |
| UDP/TCP inside AC | LuaSocket ships with CSP: `extension/internal/lua-shared/socket.lua` + `socket.core`, `local socket = require('shared/socket')`, then `socket.udp()`, `:setsockname()`, `:settimeout(0)`, `:receivefrom()` | Available to apps (they have I/O), but no CSP app using UDP was found, and a blocking call would stall the game. **Verify** first. Keep it as a fallback |
| WebSocket | `web.socket(url, headers?, cb, params{reconnect, encoding='binary'|'json'})` (lib.lua 8053) | Works if the receiver runs a WS server. More moving parts; fine for a debug or remote panel |
| Lua ↔ Lua | `ac.connect(layout)`, shared events | Inside AC only; not for the PC |

**Recommended design (matches AcHandCam and extends it):**

1. **Pose block, PC → app.** Keep it as is (seqlock, 108 B, every phone sample).
2. **Command block, PC → app, new**, e.g. `AcTools.CSP.Limited.TSSCam.Cmd`.
   - A ring of N slots (e.g. 32 × 64 B): `{uint32 seq; uint16 cmdId; uint16 flags; float args[12]; char str[8]}`
     plus a header `{magic, version, writeSeq}`.
   - The app processes every slot with `seq > lastDone` once and in order, so button presses are
     never lost the way they can be in a latest-value block.
3. **Status/ack block, app → PC.**
   - `lastDoneSeq`, plus an error code per command (e.g. `not_supported_by_car`,
     `physics_not_allowed`, `online`, `free_cam_disabled`).
   - A capability bitmask computed every second from `physics.allowed()`, `sim.isOnlineRace`,
     `sim.freeCameraAllowed`, `car.hasTurningLights`, `hasLowBeams`, `wiperModes`,
     `hasHornAudioEvent`, `ac.isExtraSwitchAvailable(i)` and the extra switch names. The phone
     UI uses it to grey out buttons.
   - The car state (lights, signals, wipers, door, extras A-T bits, rpm, gear) and the replay
     state (active, frames, current, frameMs).
4. **View block.** Keep it as is (for timewarp).

---

## What's impossible or depends on the car (summary)

- **Impossible with Lua:**
  - live slow motion (offline or online);
  - setting the replay playback rate or pausing it directly (only seek: emulate it with our own
    clock);
  - a horn setter (use `overrideCarControls().horn`);
  - a police-lights setter;
  - hood or trunk as a generic API;
  - pressing vanilla AC keys such as `[PAUSE_REPLAY]` or `[ACTION_HORN]`;
  - controlling remote players' cars online.
- **Offline only (`physics.allowed()`):** engine off, forced handbrake, gear, car freeze,
  teleport.
- **Depends on the car:**
  - extra switches A-T (meaning varies per car);
  - driver door (needs an animation);
  - hood, trunk and other doors (only through the car's own `.ksanim` files or a bound switch);
  - turn signals visible (`hasTurningLights`);
  - high beams distinct from low beams (`hasLowBeams`);
  - wipers (`wiperModes`);
  - flashing lights and siren horn;
  - hood and bumper camera positions (car.ini);
  - left/right sign of car-local X.
- **Depends on the user's setup:**
  - free-camera method A needs "allow free camera"
    (`sim.freeCameraAllowed`, `ALLOW_FREE_CAMERA`); fall back to `ac.grabCamera`;
  - only one script can grab the camera or own the exclusive HUD at a time.
- **To verify in game first:**
  - car-control setters on your own car online;
  - camera override in replays;
  - a smooth replay clock driven by `setReplayPosition`;
  - the camera moving while paused;
  - the `shared/socket` UDP path;
  - whether `FRAME_BEGIN` removes the one-frame lag.
