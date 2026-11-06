# BeamNG APIs for phone control of car and camera

Read-only study of the installed game (`E:\Steam\steamapps\common\BeamNG.drive\`), 2026-11-06. Every call below
was found in the game's own source. Paths are relative to the install root. Vehicle jbeams were read out of
`content/vehicles/*.zip` without extracting them. Nothing was run in game. Items marked **(test in game)** are
behaviour that the Lua source can't settle.

## 0. How GE reaches a car

- Run code in a car's vehicle Lua: `veh:queueLuaCommand("<lua>")`. Vanilla does exactly this from GE for doors:
  `lua/ge/extensions/gameplay/taxi.lua:1211`
  (`v:queueLuaCommand('for _, c in pairs(controller.getControllersByType("advancedCouplerControl")) do c.tryAttachGroupImpulse() end')`).
- Find the car:
  - player: `getPlayerVehicle(0)` or `be:getPlayerVehicleID(0)` (`lua/ge/ge_utils.lua:441`);
  - any id: `getObjectByID(id)` (`ge_utils.lua:450`).
- All cars: `be:queueAllObjectLua("<lua>")` (`lua/ge/extensions/core/environment.lua:52`).
- Read back: the vehicle sends GE a string with `obj:queueGameEngineLua("tssHandcam.onCarState(...)")`, the
  pattern `lua/vehicle/extensions/gameplayInterface.lua:30` uses. Alternatively
  `core_vehicleBridge.requestValue(veh, callback, 'electricsValue', 'lights_state')`
  (`lua/ge/extensions/core/vehicleBridge.lua:53`; the lookup is in
  `lua/vehicle/extensions/gameplayInterfaceModules/interactElectrics.lua:55`).
- Vanilla's own vehicle commands are listed in `lua/ge/extensions/core/input/actions/vehicle.json` (ctx `vlua`, so
  the strings are vehicle Lua) and per vehicle in `vehicles/common/common_default.interaction.json`
  (inside `content/vehicles/common.zip`, loaded by `lua/common/jbeam/interaction.lua:34-35`). Those strings are the
  safest commands to send: they are what the game's own key bindings run.

Suggested protocol shape: `{cmd:"<name>", veh:<id or 0=player>, arg:...}`. GE resolves the vehicle and queues
the string.

## 1. Doors, hood, trunk, tailgate

**How vanilla does it.** Every opening panel is an `advancedCouplerControl` controller (latch group) named in
the part's jbeam:

- `lua/vehicle/controller/advancedCouplerControl.lua` exports `toggleGroup()` (line 211),
  `toggleGroupConditional(conds)` (223), `detachGroup()` (175, opens it: unlatch plus a short open force),
  `tryAttachGroupImpulse()` (144, closes it: pull force plus auto-latch) and `getGroupState()` (377).
- States: `attached`, `detached`, `autoCoupling`, `coupling`, `broken`, `desyncedAttached`,
  `desyncedDetached` (lines 10-18).
- It also sets the electrics `<name>_attached` and `<name>_notAttached` (lines 120-121, 449-450), so the state
  can be read as an electrics value.

Controller names seen in vanilla jbeams (etk800, covet, pickup, vivace, sunburst2):

- doors: `door_FL_coupler`, `door_FR_coupler`, `door_RL_coupler`, `door_RR_coupler`, `door_L_coupler`,
  `door_R_coupler` (2-door cars);
- hood: two stages, `hoodLatchCoupler` (interior release) and `hoodCatchCoupler` (outside catch, the one with the
  pop-up force); the etk800 has a variant with `hoodLatchCouplerL`/`R`;
- rear: `trunkCoupler`, `tailgateCoupler`, `tailgateCouplerUpper`, `hatchCoupler`, `decklidCoupler`,
  `rearDoorCoupler`;
- other: `spareholderCoupler`, barn doors, sliding side doors, and so on.

The action strings (all in `common_default.interaction.json`):

- `door_FL`: `controller.getControllerSafe('door_FL_coupler').toggleGroup()`; FR, RL, RR, L and R work the same
  way.
- `hoodRelease`: `controller.getControllerSafe('hoodLatchCoupler').toggleGroup()`.
- `hoodCatch`: `controller.getControllerSafe('hoodCatchCoupler').toggleGroupConditional({{'hoodLatchCoupler','attached','vehicle.advancedCouplerControl.releaseHoodFirstInterior'}})`.
  The catch refuses while the latch is still attached.
- `trunk`, `tailgate`, `tailgate_upper`, `hatch` and `decklid`: the same `toggleGroup()` on their couplers (some
  are conditional, e.g. `tailgate_lower` checks `tailgateCouplerUpper`).

All latches at once: the funstuff radial does it.

- `core_funstuff.openLatches()` and `core_funstuff.closeLatches()` (`lua/ge/extensions/core/funstuff.lua:248-256`)
  act on the player car only.
- They go through `core_vehicleBridge.executeAction(veh, 'latchesOpen')`, which runs
  `lua/vehicle/extensions/gameplayInterfaceModules/interactMisc.lua:18-28`: `detachGroup()` or
  `tryAttachGroupImpulse()` on every `advancedCouplerControl`.

**Commands for us (vehicle Lua strings):**

- One panel, toggle: `controller.getControllerSafe('door_FL_coupler').toggleGroup()`. `getControllerSafe`
  returns a harmless nil-controller when the car has no such panel (`lua/vehicle/controller.lua:236-257`).
- One panel, explicit: `local c=controller.getController('door_FL_coupler') if c then c.detachGroup() end` (open)
  or `... c.tryAttachGroupImpulse()` (close).
- Hood fully open: `detachGroup()` on every controller whose name starts with `hood`, latch first and catch
  second. Hood close: `tryAttachGroupImpulse()` on the same controllers.
- All panels: `for _,c in pairs(controller.getControllersByType('advancedCouplerControl')) do c.detachGroup() end`.
  `tryAttachGroupImpulse()` closes them all. Or from GE: `core_vehicleBridge.executeAction(veh,'latchesOpen')` /
  `'latchesClose'`, which works for any vehicle object.

**Discovering a car's panels (synchronously in GE, no round trip):**

- `core_vehicle_manager.getVehicleData(vid).vdata.controller` is the jbeam controller list. Entries with
  `fileName == 'advancedCouplerControl'` carry `.name`. Read this way by
  `lua/ge/extensions/ui/bindingsLegend.lua:430-434`.
- Better for buttons: `vdata.actionsEnabled` (a list of `{name=...}`) holds the actions this car's parts enable,
  e.g. `door_FR`, `hoodCatch`, `hoodRelease`, `trunk`. `vdata.inputActions[name]` holds the action, with
  `onDown` (the exact vehicle Lua to run) and a `title` translation key. Read this way by
  `lua/ge/extensions/core/input/bindings.lua:525-529` and `core/input/actions.lua:93-108`.
- So a phone "doors" page can be built from the car itself: for each enabled action with an `onDown` (skip
  `onChange` actions, which use `VALUE`), send `veh:queueLuaCommand(action.onDown)`.

Caveats:

- `toggleGroup()` ignores presses while the open or close force is still running (line 213), e.g. for 3 s after a
  door close on the etk800.
- A broken latch (`broken`) only re-latches through `tryAttachGroupImpulse`.
- Mods may use other controller names or the old `beamstate` couplers. The `actionsEnabled` route covers any mod
  that ships its own `*.interaction.json`.
- Mods with no `advancedCouplerControl` at all have no openable panels, and nothing works.

Suggested commands: `door <id|all> open|close|toggle` (ids: `FL FR RL RR L R hood trunk tailgate`),
`panels?` (returns the list from `actionsEnabled`), `action <name>` (run any enabled interaction action).

## 2. Lights

All of these are vehicle Lua, in `lua/vehicle/electrics.lua` (exports at lines 1087-1112). Vanilla bindings are
in `actions/vehicle.json`.

- **Headlights cycle** off, low, high, off: `electrics.toggle_lights()` (968; the `toggle_headlights` action).
- **Set the state** (0 off, 1 low, 2 high): `electrics.setLightsState(n)` (1022). This is what the radial menu's
  off/low/high entries call (lines 532-562). No click sound.
- **High beams**:
  - toggle, only when the lights are on: `electrics.toggle_highbeams()` (983);
  - flash while held: `electrics.light_flash_highbeams(true/false)` (996; the `highbeam` action);
  - interaction toggles: `electrics.setLightsState(1-electrics.values.lowhighbeam)` (low) and
    `electrics.setLightsState(2-electrics.values.lowhighbeam)` (high), in `common_default.interaction.json`.
- **Hazards**: `electrics.toggle_warn_signal()` (964) or `electrics.set_warn_signal(bool)` (955).
- **Turn signals**:
  - toggle: `electrics.toggle_left_signal()` and `toggle_right_signal()` (159, 214);
  - set: `electrics.set_left_signal(bool, autoCancel)` and `set_right_signal(bool, autoCancel)` (187, 242);
  - cancel: `electrics.stop_turn_signal()` (143).
- **Fog lights**: `electrics.toggle_fog_lights()` (1026) or `electrics.set_fog_lights(bool)` (1030).
- **Light bar** (police and other beacons): `electrics.set_lightbar_signal(n)` (1005), where 0 is off, 1 is lights
  and 2 is lights plus siren. `electrics.toggle_lightbar_signal()` (1018) cycles them.
- **Light bar pattern**: `for _,v in pairs(controller.getControllersByType('lightbar')) do v.toggleMode() end`
  (vehicle.json `toggleLightbarMode`), or `v.setModeIndex(i)` (interaction json `lightbar_setmode_1..4`).
- **Underglow**: `electrics.values.underglow = 1 - (electrics.values.underglow or 0)` (vehicle.json
  `toggle_underglow`).
- **Reading state** (electrics, booleans turned into 0/1 at the end of `updateGFX`, line 466):
  - `lights_state` (0/1/2), `lowbeam`, `highbeam`, `hazard_enabled`;
  - `signal_left_input`, `signal_right_input`, `fog`, `lightbar` (0/1/2), `horn`.
- **Parking lights: impossible as a vanilla function.** `vals.parking = 0 -- TODO: input.parkinglights`
  (electrics.lua:419). Nothing sets it.
- **Interior or dome light: none in vanilla.** No electrics or lights for it in the studied car jbeams or in
  electrics.lua.
- **Caveat: ignition 0 forces every light value to 0** (`updateElectricsWithIgnitionLevelEuropean`, lines
  286-340). The set state is remembered but nothing shows until ignition is 1 or higher.

Suggested commands: `lights off|low|high|cycle`, `highbeam toggle|flash_on|flash_off`, `hazard toggle|on|off`,
`signal left|right|off`, `fog toggle`, `lightbar off|lights|siren|cycle`, `lightbar_mode next`.

## 3. Horn, siren

- **Horn** (hold): `electrics.horn(true)` on press and `electrics.horn(false)` on release (electrics.lua:1034;
  vehicle.json `horn` onDown/onUp). The phone must send down and up. If the "up" can get lost, have GE time out
  the horn, e.g. after 3 s with no refresh.
- **Siren** is part of the light bar: `electrics.set_lightbar_signal(2)` gives lights plus siren
  (electrics.lua:1005-1016; `toggle_siren` in the interaction json:
  `electrics.set_lightbar_signal(electrics.values.lightbar == 2 and 1 or 2)`).
- Caveat: a car with no `siren` soundscape sound wraps the state modulo 2, so it gets lights only.

Suggested commands: `horn down|up`, `siren toggle`.

## 4. Wipers

**Impossible in vanilla: no wiper animation and nothing that drives one.**

- No Lua in `lua/` mentions wipers, except unrelated ImGui knob names.
- Wiper blades are static flexbodies (e.g. `etk800_glass.jbeam` `["etk800_wipers", ...]`).
- The only moving wiper part is the **interior stalk prop**: `["wipers", "etk800_wiperstalk", ..., -1, 1, 0, 1]`
  in etk800, etkc, midsize, pessima, sbr, hopper, covet, bx, vivace and sunburst2 interiors. Props read
  `electrics.values[func]` (`lua/vehicle/props.lua:36`). So `electrics.values.wipers = 1` would tilt the stalk on
  those cars, and nothing else happens.
- The nine (`nine_wiper`) and wl40 (`wl40_wiper_F/R`) have wiper node groups, but no controller or electrics
  moves them.

Real wipers would need our own animated mesh or prop per car. Out of scope. Suggest no `wipers` button, or a
stalk-only `wipers` command labelled as such.

## 5. Engine, ignition, parking brake, gears (vehicle Lua)

- **Ignition levels** 0 off, 1 accessory, 2 on, 3 start: `electrics.setIgnitionLevel(n)` (electrics.lua:348).
  - Engine start: `electrics.setIgnitionLevel(3)`. The controller drops it back to 2 once the engine fires
    (`lua/vehicle/controller/vehicleController/vehicleController.lua:307-309`; the comment at electrics.lua:1061
    says "set level 3 from external (eg GE) and have the engine start").
  - The radial "engine" entry does the same (electrics.lua:722-736).
  - Engine stop: `electrics.setIgnitionLevel(0)`, or 1 to keep the accessories on.
  - Cycle like the key: `electrics.toggleIgnitionLevelOnDown()` then `electrics.toggleIgnitionLevelOnUp()`
    (radial, lines 700-705).
- **Caveat:** in arcade gearbox mode, any throttle or brake input brings ignition back to 2 and auto-starts the
  engine (`vehicleController.lua:291-327`). `electrics.values.engineRunning` (vehicleController.lua:569) and
  `ignitionLevel` give the state.
- **Parking brake**:
  - toggle: `input.toggleEvent('parkingbrake')` (vehicle.json `parkingbrake_toggle`;
    `lua/vehicle/input.lua:738`);
  - on: `input.event('parkingbrake', 1, 1)`, off: `input.event('parkingbrake', 0, 1)` (interaction json
    `parkingBrake_on/off`);
  - state: `electrics.values.parkingbrake`.
- **Gears**:
  - `controller.mainController.shiftUp()` and `shiftDown()` (vehicle.json; prefer the `...OnDown` variants when
    present, as the action does);
  - `controller.mainController.shiftToGearIndex(i)`: -1 R, 0 N, 1..8;
  - `controller.mainController.cycleGearboxModes()`;
  - state: `electrics.values.gear` and `gearIndex`.
  - Caveat: index meaning varies by gearbox (automatic, DCT, CVT, electric in `shiftLogic/`).

Suggested commands: `engine start|stop|cycle`, `ignition <0-3>`, `parkingbrake toggle|on|off`,
`gear up|down|<index>`.

## 6. Camera presets

Vehicle cameras are built per vehicle in `lua/ge/extensions/core/camera.lua:1021-1188`, one per file in
`lua/ge/extensions/core/cameraModes/`. `onboard` and `occupant` are multi-cameras named `onboard.<name>` and
`occupant.<seatId>` (line 13). Default menu order (line 1064):

`orbit, driver, onboard.hood, external, relative, chase, droneChase`, then the occupant cameras (inserted before
`onboard.hood`, line 501).

Switching (GE):

- **Vehicle camera by name:** `core_camera.setByName(0, 'chase')` (`camera.lua:838`), or
  `core_camera.setVehicleCameraByName(0, name, withTransition)` (774). This clears a global camera first, so it
  also ends our handcam mode.
- Names: `orbit`, `chase`, `external`, `relative`, `droneChase`, `onboard.hood` (only when the car's jbeam has a
  `camerasInternal` "hood", e.g. `etk800_body_sedan.jbeam`), `driver`, `occupant.FL`, `occupant.FR`, and so on.
- **Slots and cycling:**
  - `core_camera.setBySlotId(0, n)` (977; actions `camera_1..10`);
  - `core_camera.setVehicleCameraByIndexOffset(0, ±1)` (1275; `switch_camera_next/prev`);
  - `core_camera.resetCamera(0)` (`center_camera`).
- **Free camera:** `commands.setFreeCamera(0)` (`lua/ge/server/commands.lua:40`, seeds it from the current view),
  or `core_camera.setGlobalCameraByName('free')`. Back to the vehicle: `commands.setGameCamera(0)` (line 33).
- **Free camera pose:** `core_camera.globalCameraFunction('free','setPosition', vec3)` and `'setRotation'`
  (`camera.lua:1463`; our `release()` already uses them).
- **Drone:** the vehicle camera `droneChase` (`cameraModes/droneChase.lua`).
- **Current camera:** `core_camera.getActiveCamName(0)` (939); `core_camera.getActiveGlobalCameraName()` (1893).
- **Transitions:** `core_camera.startCameraTransition(seconds)` (669).

Mount points:

- In jbeam:
  - `camerasInternal` becomes `cameras.onboard`;
  - `cameraExternal` becomes `orbit`;
  - `camerasRelative` becomes `relative`;
  - `cameraChase` becomes `chase`.
  - Upgraded in `lua/common/jbeam/sections/camera.lua:250-255`.
- Each onboard camera gets a physics node (`camNodeID`, lines 95-112).
- In 0.40 the driver and passenger views are **occupant cameras**. Seats come from `occupantSlots` in
  `vehicles/common/passengers/occupant_generic.jbeam`, with seat ids `FL`, `FR`, `RL`, `RR` from the car's
  `$seatID` (e.g. `etk800_interior.jbeam`). Each gets a camera `occupant.<seatId>` (`sections/camera.lua:116-160`;
  `sections/occupantSlots.lua`, `slot.cameraName = "occupant." .. id`).
- **Driver seat:** `core_camera.getDriverCameraById(vid)` (906) returns the name and the camera (with
  `camNodeID`). `core_camera.getDriverDataById(vid)` (927) returns `camNodeID, rightHandDrive, rightHandDoor`.
  `gameplay_seatOccupants.getDriverSeatId`-style logic: `vdata.occupantSlotsById[*].defaultPermissionMode ==
  'driver'` (`gameplay/seatOccupants.lua:96`).
- **Any camera's mount for any vehicle:** `core_camera.getCameraDataById(vid)[camName].camNodeID` (899). Camera
  data exists for every spawned vehicle, not only the player's (`camera.lua:138-153`).
- World position of that node, as `cameraModes/onboard.lua:70-72` computes it:
  `vec3(veh:getPositionXYZ()) + vec3(veh:getNodePositionXYZ(camNodeID))`.
- Orientation from the ref nodes: `vdata.refNodes[0]` gives `ref`, `left` and `back`, read from
  `core_vehicle_manager.getVehicleData(vid).vdata` (`camera.lua:1032`). Then `dir = ref - back`,
  `left = ref - left` (onboard.lua:82-86). Or more simply `veh:getDirectionVector()` and
  `veh:getDirectionVectorUp()` (`cameraModes/path.lua:133`).

**Passenger seat caveat:** `core_camera.setByName(0,'occupant.FR')` does not stick. The `onCameraChanged` hook in
`gameplay/seatOccupants.lua:1750-1759` resyncs and `syncOccupantCamera` (499-533) switches back to the
player's own seat camera. It also hides the other occupant cameras. Two ways round it:

- `gameplay_seatOccupants.switchSeat(0, 1)` (line 1248, the action `switch_next_seat`). This really moves the
  player, who then may not be allowed to drive.
- **Recommended:** a "passenger" preset in **our own** global camera mode. Place it each frame at the
  `occupant.FR` (or `occupant.FL` for RHD cars) node with the formula above, looking along the car. The handcam
  rig can then add the phone's motion on top.

Suggested commands: `cam orbit|chase|external|relative|drone|hood|driver|free`, `cam passenger` (ours),
`cam next|prev`, `cam handcam` (ours).

## 7. Replays and slow motion

`core_replay` (`lua/ge/extensions/core/replay.lua`, exports at lines 1062-1107):

- State:
  - `core_replay.state`: `{state='inactive'|'playback'|'recording', loadedFile, positionSeconds, totalSeconds,
    speed, paused}` (set in `stateChanged`, line 399);
  - also `getState()`, `isPaused()`, `getPositionSeconds()`, `getTotalSeconds()`, `getLoadedFile()`;
  - hook `onReplayStateChanged(state)` every update, and `onReplayCoreEvent` for start, stop and pause
    (lines 425-450).
- Play and pause:
  - `core_replay.togglePlay()` (552);
  - `core_replay.pause(bool)` (538, only during playback);
  - load and play: `core_replay.loadFile(filename, autoplay)` (631);
  - `core_replay.stop()` (662) and `stopAndUnload()` (672);
  - list: `core_replay.getRecordings()` (261; `replays/*.rpl`, sorted with the current map first).
- Seek:
  - `core_replay.seek(0..1)` (834, normalized);
  - `core_replay.jumpTime(seconds)` (849; ±1 and ±15 in `actions/replay.json`);
  - `core_replay.jumpFrames(n)` (842).
- Speed:
  - `core_replay.setSpeed(x)` (487). Instant, `1` is realtime.
  - `core_replay.toggleSpeed(±1 | 'realtime' | 'slowmotion')` (496) steps through
    `{1/1000 ... 1/8, 1/4, 1/2, 3/4, 1, 1.5, 2, 4, 8}` (line 10).
- Recording: `core_replay.toggleRecording()`, `startRecording()`, `stopRecording()` (774-808).
- **Vehicles in a replay:** the scene's own vehicle objects. The replay destroys vehicles that aren't in the file
  (comment in `core/camera.lua:2202`). So `getAllVehicles()` / `be:getObjectCount()` during playback list the
  replay's cars.
  - Switching which one the camera follows uses the same `be:enterVehicle(0, veh)` or
    `core_input_vehicleSwitching.switchCycleVehicle(0, ±1)` as in section 8.
  - `switch_next_vehicle` stays bound during playback: it's in the Normal action map, and only the vehicle maps
    are disabled (`replay.lua:383-388`).
- **Global camera modes during playback: yes.** Vanilla's `path` camera (a global mode like ours) runs during
  replays and syncs to `core_replay.getPositionSeconds()` (`cameraModes/path.lua:247-248, 532-533`). Our
  `tssHandcam` mode is the same kind of camera.
- **Car controls during a replay: no.**
  - Playback disables `VehicleCommonActionMap` and `VehicleSpecificActionMap` (`replay.lua:383-388`): the game
    itself treats vehicle input as meaningless there.
  - Positions come from the file. A queued `queueLuaCommand` would still reach the car's Lua.
  - Whether lights or doors show in playback isn't decidable from Lua (the recorder is C++). **(test in game)**
    Expect doors not to move. Treat car commands as disabled while `core_replay.state.state == 'playback'`.

Simulation speed outside replays (`lua/ge/simTimeAuthority.lua`, global `simTimeAuthority`):

- `simTimeAuthority.set(x)` (305) eases to the new speed. `setInstant(x)` (306) jumps.
  - Both clamp to `0.001..1`: **no fast motion live** (line 116). Fast motion exists only in replays (up to 8x).
- Presets: `simTimeAuthority.selectPreset('^' | '<' | '>' | 'v')` (122; the `toggle_slow_motion`,
  `slower_motion` and `faster_motion` actions). During playback it forwards to `core_replay.toggleSpeed`.
- Pause:
  - `simTimeAuthority.pause(bool)` (308) or `togglePause()` (311; the `pause` action, also replay-aware);
  - state: `simTimeAuthority.getPause()` (310); speed: `simTimeAuthority.get()`.

Suggested commands: `replay play|pause|toggle|stop`, `replay seek <0-1>`, `replay jump <s>`,
`replay speed <x>|slower|faster`, `replay list`, `replay load <file>`, `replay record`, `time <0.001-1>`,
`time slower|faster|toggle`, `pause toggle`.

## 8. All vehicles; which one the camera follows

- List:
  - `getAllVehicles()` (`ge_utils.lua:503`);
  - `for vid, veh in activeVehiclesIterator() do ... end` (563, active ones only);
  - raw: `for i=0,be:getObjectCount()-1 do local veh=be:getObject(i) ... end` (`core/input/vehicleSwitching.lua:73-80`).
  - Useful per car: `veh:getId()`, `veh:getJBeamFilename()`, `veh:getActive()`, `veh.playerUsable`.
  - Vanilla skips `unicycle` (walking) and `playerUsable == false`.
- Follow, by entering the car: `be:enterVehicle(0, veh)` (`vehicleSwitching.lua:44`), or cycle with
  `core_input_vehicleSwitching.switchCycleVehicle(0, ±1)` (90).
  - The camera system always follows the player's vehicle: `cameraUpdate.lua:125`
    `getPlayerVehicle(ctx.vehiclePlayer or player)` gives `data.veh`.
  - So entering changes the controlled car too. With the seat system, `canEnterVehicle` may refuse a full car
    (vehicleSwitching.lua:14-18).
- **Follow without taking control** (recommended for filming AI or traffic): our global camera mode reads the
  target itself:
  - `getObjectByID(id)`, with `veh:getPositionXYZ()`, `getDirectionVector()`, `getDirectionVectorUp()` and
    `getNodePositionXYZ(n)`;
  - it ignores `data.veh`, and the player keeps driving their own car.

Suggested commands: `cars?` (id, model, active, player flag), `follow <id>` (ours, no control change),
`enter <id>`, `car next|prev`.

## 9. Hiding HUD and UI

- The handcam already does it right.
  - `toggleClean()` calls `ui_visibility.set(false)`, and `restoreUi()` sets it back
    (`mod/tss_handcam/lua/ge/extensions/tssHandcam.lua:440-457`).
  - That's the game's own Alt+U: action `toggleUi` gives `ui_visibility.toggle()`
    (`actions/general.json`).
- `ui_visibility` (`lua/ge/extensions/ui/visibility.lua`):
  - `set(bool)` hides the CEF UI (HUD, apps, messages) and all ImGui windows (`Engine.imgui.setEnabled`), other
    mods' windows included;
  - `setCef(bool)` and `setImgui(bool)` work separately; `get()` reads the state.
  - Hiding CEF also clears the vehicle-interaction crosshair (`core/vehicleTriggers.lua`
    `onCefVisibilityChanged`).
- Not hidden by it: debug drawing (`debugDrawer`) and the world. Photo mode is `pause.photomode`.

Suggested command: `clean on|off|toggle` (reuse `toggleClean` / `restoreUi`; when the phone asks for clean
without filming, call `ui_visibility.set(false)` and remember that we hid it).

## Impossible or not vanilla

- **Wipers:** no animation exists. Only the interior stalk prop follows `electrics.values.wipers` on some cars.
- **Parking lights:** the electrics value is a TODO and hard-wired to 0.
- **Interior or dome light:** none.
- **Fast motion (more than 1x) live:** clamped to 1. Only replays go up to 8x.
- **Driving, doors and lights inside a replay:** vehicle input is disabled and positions come from the file
  (test in game what shows).
- **Passenger view through `core_camera`:** the seat system forces the player's own seat camera back. Use our
  own camera, or really switch seats.
