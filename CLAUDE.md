# TSS Cam

The user's own phone-as-handheld-camera kit for BeamNG.drive and Assetto Corsa (competitor: Kujees Cam Hub).
Plan and phases: `docs/plan.md`. Research: `docs/research/` (read the one for the part you touch).
Design: the user's Backtrack + BBG Terminal language (`docs/research/ui-design.md`); no green accents.

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
- `app/`: the iPhone app (phase 4+). No Mac: built unsigned on GitHub Actions, installed with Sideloadly.

## Tests
`cd receiver && python tests/test_tsscam.py` (fake phone + fake game over real local UDP; no hardware).

## Rules
- Repo `TactoDaDev/tss-cam`: public while building (free macOS CI minutes), the user makes it private when done.
- Phone pose packets stay byte-exact AcHandCam v3 so the stock receiver keeps working.
- Never copy Kujees' code, art or UI; features only.
