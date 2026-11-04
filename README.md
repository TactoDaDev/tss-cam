# TSS Handcam

Your phone as a handheld camera in BeamNG.drive: walk, crouch, pan and tilt, and the game camera does the same.

It uses the free [AcHandCam](https://github.com/jxrdxn011/AcHandcam) phone app and PC receiver (MIT), which were
made for Assetto Corsa. The receiver isn't tied to Assetto Corsa: it turns the phone's tracking into a camera pose
and publishes it in shared memory. This mod reads that shared memory in BeamNG and drives a camera of its own.
Nothing of AcHandCam is changed or shipped here.

## Setup

1. **Receiver (PC):** download `AcHandCam-PC-<version>.zip` from AcHandCam's releases, unzip it somewhere it can stay,
   double-click `run_receiver.bat` (needs Python 3.10+ with "Add python.exe to PATH"). Allow it on Private networks.
   Live view on the phone: AMD, set *Live view > Backend = native*; NVIDIA/Intel, `winget install Gyan.FFmpeg` once.
2. **Phone:** iPhone: `AcHandCam-iPhone-<version>.zip` holds an `.ipa`; install it with [Sideloadly](https://sideloadly.io)
   and your Apple ID (needs iTunes and iCloud from apple.com, not the Microsoft Store). Turn on Developer Mode and trust
   your Apple ID on the phone. A free Apple ID: re-sign every 7 days. Android: the `.apk` (ARCore phones).
3. **Game:** this mod (`mod/tss_handcam`) in `mods/unpacked/`. Bind the keys in *Options > Controls > Camera*:
   "TSS Handcam: film with the phone / release", recenter, freeze, ride with my car, settings window.
4. Open the phone app, enter the PC address the receiver shows, tap *Start tracking*. The **TSS Handcam** window opens
   in game once the phone is live. Press **Film with the phone** (or your key).

On the phone: hold the shutter to freeze while you reposition, **WALK** turns the shutter into a joystick, **.5 / 1 /
2 / 3** or pinch to zoom, **Recenter** makes where you point "straight ahead" without moving the view.

## How it works

- `lua/ge/extensions/tssHandcam.lua`: opens the receiver's three file mappings through LuaJIT's FFI (kernel32
  `OpenFileMappingA` / `MapViewOfFile`), reads the pose (torn reads skipped), writes the status block (engaged,
  recenter requests) and the view block (FOV, the pose each frame was drawn from, the smoothing rotation) that the
  receiver uses to timewarp the phone's live view. Layouts: AcHandCam's `receiver/handheld_receiver/shm.py`.
- `lua/ge/extensions/core/cameraModes/tssHandcam.lua`: a global camera mode; the game builds one of every file in that
  folder and calls `update()` right before rendering, with that frame's vehicle data, so a camera riding a car stays
  glued to it.
- `lua/common/tssHandcam/rig.lua`: the maths, on plain tables (tested offline). The phone's filming frame
  (x right, y up, z forward, from Recenter) is anchored to the game's view when you start; pitch and roll stay
  absolute (gravity), heading and position are relative. Ride-with-car keeps everything in the car's frame.
- Vertical FOV from the phone's lens and screen shape, as the receiver's timewarp expects
  (`core/cameraModes/gameengine.lua`: camera modes choose the vertical FOV).
- Settings: `settings/tss_handcam.json`.

## Testing

`python tools/run_tests.py` runs the maths tests and the real extension against a fake receiver and fake game camera
(Luau, `~/.rokit/bin/luau.exe`). The shared memory itself (FFI to kernel32) is only exercised in the game.
