# TSS Cam

The user's own phone-as-handheld-camera kit for BeamNG.drive and Assetto Corsa (competitor: Kujees Cam Hub).
Plan and phases: `docs/plan.md`. Research: `docs/research/` (read the one for the part you touch).
Design: TSS Cam's OWN look (the user doesn't want every app in Backtrack's style): viewfinder HUD (full-bleed game
image, corner brackets, focus box) + race livery type (slanted red tally/labels, heavy italic race-number readouts;
NO stripes, the user removed them) + glass (floating translucent card, white primary key with a ring). Directions
explored in `docs/design/tsscam-styles.figcraft`. No green accents. Craft/perf rules: `docs/research/ui-design.md`.

## Layout
- `receiver/handheld_receiver/`: AcHandCam's PC receiver (MIT, `LICENSE-AcHandCam.txt`), vendored. Only change:
  `service.py` hands `TSC*` datagrams to `extra_handler` (2 marked lines). Keep it that way: their pose/live-view
  code is the reference our phone app must match.
- `receiver/tsscam/`: ours. `protocol.py` (TSCH/TSCM/TSCA/TSCP/TSCS packets, CRC like ACHC), `hub.py` (phone <->
  game switchboard, game picker auto/beamng/assetto), `games/beamng.py` (the old bridge: shm <-> UDP 47830/47831,
  plus our packets), `games/assetto.py` (stub until phase 6), `games/detect.py` (tasklist).
  Run: `cd receiver && python -m tsscam --headless [--game auto|beamng|assetto]`.
- `games/beamng/`: the TSS Handcam mod (history kept via `git subtree` from D:\beamng-tss-handcam).
  Its old `bridge/` is replaced by the receiver's BeamNG hook: don't run both (port 47831).
- `app/`: the iPhone app (SwiftUI + ARKit). No Mac: `.github/workflows/ios.yml` (macos-26) generates the project
  with XcodeGen (`app/project.yml`), runs the packet tests on a simulator, uploads an unsigned `.ipa`
  (`gh run download <id> -R TactoDaDev/tss-cam -n TSSCam-ipa -D dist`); the user installs it with Sideloadly.
  Swift 5 language mode (Swift 6 strict concurrency would fail the ARKit delegate). Bundle id fixed: `app.tss.cam`.
- `desktop/`: the Tauri 2 app (window, tray, single instance) that starts the receiver (`--no-window`) in a
  kill-on-close job object and shows its page. `cargo build` -> `desktop/target/debug/tsscam.exe`.

## Tests
`cd receiver && python tests/test_tsscam.py` (fake phone + fake game over real local UDP; no hardware).

## Rules
- Repo `TactoDaDev/tss-cam`: public while building (free macOS CI minutes), the user makes it private when done.
- Phone pose packets stay byte-exact AcHandCam v3 so the stock receiver keeps working.
- Never copy Kujees' code, art or UI; features only.

## Status (2026-11-06)
Phases 1-4 done: receiver core (12 tests), BeamNG mod v2 checked in game (doors, lights, modes, follow any car,
slow-mo, lift stick; replays not tried live yet), receiver window + Tauri app, iPhone app v0 building green in CI.
Next: the user's first phone test (Sideloadly), then phase 5 (live viewfinder: port liveview/pacing/timewarp 1:1).
