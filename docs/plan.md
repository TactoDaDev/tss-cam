# TSS Cam: plan (2026-11-06)

Our own phone-as-handheld-camera kit for BeamNG.drive and Assetto Corsa, to match and beat Kujees Cam Hub.
Research: `docs/research/` (kujees-features, receiver-and-app, beamng-apis, assetto-csp-apis, ui-design).
ReSkate (EA *Skate.* via the ReSkate mod platform) comes last, after the main kit works.

## The kit
| Piece | Built from | Job |
|---|---|---|
| `app/` iPhone app | ours, Swift + SwiftUI + ARKit + VideoToolbox; built unsigned on GitHub Actions `macos-26`, installed with Sideloadly | tracking, live viewfinder, thumbsticks, zoom, camera modes, car controls, recording |
| `receiver/` PC receiver | AcHandCam's receiver (Python, MIT, notice kept) extended | phone link, live view encode, game picker (Auto / BeamNG / Assetto Corsa), routes commands to the game |
| `games/beamng/` | our TSS Handcam mod, grown | camera modes, car controls, replays, slow-mo |
| `games/assetto/` | new CSP Lua app of ours | camera, car controls, replays, UI hiding |

Repo: `TactoDaDev/tss-cam`, public while building (free Mac builds), private when done.

## Protocol (stays compatible with AcHandCam)
- Phone keeps sending byte-exact AcHandCam v3 pose packets (`ACHC`, UDP 47820, ~60 Hz): the stock receiver keeps working.
- Our extra packets on the same port, new magics (the stock receiver ignores them):
  `TSCH` hello/version, `TSCP` capabilities (receiver -> phone: game, car, list of buttons and modes),
  `TSCM` command (phone -> receiver: id + args, acked), `TSCS` sticks (move x/y, up/down, look).
- Receiver -> game: BeamNG over local UDP to the mod (the separate bridge folds into the receiver);
  Assetto Corsa over shared memory (AcHandCam's pose/status/view blocks + our command ring and capability block).

## Features (Kujees parity first, then where we beat him)
- **Camera modes:** Handheld (walk), Seated stick mode (phone aims, stick moves), Hood, Passenger, Chase, Drone,
  Head, Lock-on (keeps the car framed), Next car / any car, Free.
- **Car controls, per car, from the game:** BeamNG lists each car's real doors/hood/trunk/tailgate
  (`vdata.actionsEnabled`), plus lights, hazards, signals, fogs, horn, siren, engine, parking brake.
  Assetto Corsa: lights, high beams, hazards, horn, extras A-T, doors/wipers where the car is animated.
- **Replays:** film any car in a replay; BeamNG play/pause/seek/speed from the phone; AC follow + seek.
- **Film tools (Kujees has none):** live slow motion (BeamNG bullet time), smoothing, lens (focal length),
  handheld shake amount, horizon lock, torch/lamp, clean HUD.
- **Connection that just works:** QR code in the receiver (app scans it), firewall rule set up by the receiver,
  clear diagnostics, no account, no licence, offline.
- **UI:** the user's own design language (Backtrack + BBG Terminal, `docs/research/ui-design.md`).
  Receiver UI = HTML/CSS in a WebView2 window (pywebview) over the Python core.

## Phases (one at a time, each tested before the next)
1. **Receiver core:** import AcHandCam's receiver (MIT), add `TSC*` packets, game-hook layer (BeamNG UDP direct,
   AC shared memory), command routing. Tests with a fake phone and fake games (no hardware).
2. **BeamNG mod v2:** commands + capability report, camera modes, follow any car, slow-mo, replay control.
   Tested in game through MCP with the fake phone (screenshots of each mode and door/light).
3. **Receiver UI:** Backtrack-style window, game picker with auto-detect, QR code, firewall helper, status pills.
4. **iPhone app v0 + build pipeline:** XcodeGen project, GitHub Actions unsigned `.ipa`, ARKit poses (v3), connect,
   in-app log. Green CI build = done; first phone test needs the user (Sideloadly).
5. **iPhone app v1:** live viewfinder (H.264, port the receiver's reference code 1:1, shared test vectors),
   thumbsticks, mode tabs, car-control sheet from capabilities, zoom chips, shutter/recording.
6. **Assetto Corsa app:** camera, command ring, capabilities, replays, UI hiding; tested in AC.
7. **Polish:** packaging (receiver installer), docs, film tools tuning; repo goes private.
8. **ReSkate:** research what its mod platform allows (camera control, offline/private servers only).

## Overnight scope (user asleep, PC free, game launches OK)
Phases 1-4: receiver core, BeamNG mod v2 tested in game, receiver UI, app skeleton building green in CI.
The phone side needs the user in the morning (install with Sideloadly, first live test).

## Risks
- Live video over Wi-Fi is the hardest part: port AcHandCam's reference code, test vectors, in-app log from day one.
- No Mac: no debugger; every app test is a CI build + sideload. Free Apple ID: 7 days, 3 apps, bundle ID fixed.
- AC car controls depend on each car mod's animations; online servers allow less.
