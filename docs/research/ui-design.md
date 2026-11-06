# UI design brief (from the user's own apps: Backtrack and BBG Terminal)

The user asked for the kit's UI to take its design cues from Backtrack (`D:\Clipping software`, screens in
`docs/design/v3-*.png`) and BBG Terminal (`D:\bbgterminal`, screens in `release/shots/*.png`). Both are theirs.

## Shared language
- **Dark, layered, premium.** Near-black warm backgrounds with raised glass/surface panels and thin 1 px borders,
  generous rounding (12-20 px), lots of breathing room. Never flat grey, never "a recolour".
- **Content is the colour.** Backtrack takes its ambient colour from the game art behind the panels (blurred art
  layer + game tint); controls stay white. For TSS Cam the live game picture is the art: the phone UI floats on it.
- **Pills and chips.** Filters, status and metadata are rounded pills: white filled when active, thin-bordered when
  not (Backtrack); tinted background + matching border + coloured text for status (BBG: path orange, branch
  violet, ok green, error red).
- **Keycap hints.** Shortcuts shown as small keycaps next to actions (Save clip `F8`, `Tab complete`).
- **One hero action** per screen, visually special: Backtrack's white "Save clip" pill with the iridescent holo
  ring, the round play button. The holo gradient is for brand moments only (logo, record, export).
- **Status cards** with a live dot ("● RECORDING", game · resolution, encoder badge).
- **Big bold display type** for heroes (tight tracking, heavy weight); small uppercase letter-spaced labels for
  section headers ("YOUR GAMES", "LATEST CLIP · 2 MIN AGO").
- **Icon rail / sidebar** with simple line icons; inline SVG, no UI libraries.

## Palettes
- BBG Terminal tokens: `--bg #14141c`, `--surface #1a1a24`, `--border #2d2d3b`, `--text #f2ede4` (warm white),
  `--muted #8f8a9c`, `--accent #ff8a14` (Build Blast orange), `--violet #b48cff`, `--ok #5fd38d`,
  `--warn #ffb224`, `--danger #ef4b3a`. Focused input gets a glowing accent border.
- Backtrack v3: black glass over the game art, white controls, iridescent `--holo` only for brand moments.
  The user rejected an acid-lime accent as cheap: **no green accents** (green only as an "ok" status colour).

## Performance rules (measured in Backtrack; keep them)
- Idle must draw ~0 frames: no endless CSS animations, no `filter: blur()` on big areas (fake it with a tiny
  canvas stretched), no `backdrop-filter` except a modal scrim. Smooth beats pretty.

## Applying it to TSS Cam
- **Phone app (SwiftUI):** full-bleed live viewfinder; controls on dark glass capsules at the edges; mode tabs as
  pills along the bottom; zoom chips `.5 1 2 3` as small pills; shutter = the one hero control (white with the holo
  ring while filming); car-control sheet as a grid of tinted pill buttons grouped by DOORS / LIGHTS / WIPERS / HORN;
  thumbsticks as translucent glass circles with a white knob; status pill top-left ("● LIVE · BeamNG · 60 fps").
- **PC receiver:** a small Backtrack-style window: left rail (Home, Games, Live view, Settings), a hero card for the
  connected phone and the game picker (BeamNG / Assetto Corsa / Auto) with game art tiles; status pills for receiver,
  phone, game hook; keycap hints for hotkeys.
