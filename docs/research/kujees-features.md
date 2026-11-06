# Kujees Cam Hub: feature inventory (competitor research)

Researched 2026-11-06 from public sources only (no sign-ups, no purchases, no downloads). We do not
copy their code, art or UI; this is a functional feature list.

**Sources and how much to trust them**
- **GitHub release notes** (best source): `github.com/kujees/kujees-cam-downloads/releases`. A
  binary-only repo for the Windows installer, with detailed notes for PC 1.2.4 to 1.3.4. Tags list
  day and month only; the year (2026) comes from the Patreon post dates.
  https://github.com/kujees/kujees-cam-downloads/releases
- **Patreon public API** (titles, dates, tiers; post bodies are locked):
  https://www.patreon.com/api/campaigns?filter[vanity]=kujees (campaign 8331232),
  https://www.patreon.com/api/posts?filter[campaign_id]=8331232
- **Social captions** (Snapchat Spotlight, Threads, TikTok via search snippets, YouTube titles).
  TikTok and YouTube pages did not render through the fetcher, so only captions/snippets were read.
- **The user's own viewing** of TikTok video 7699158589172108565 (v1.3.0), given in the brief.
- No Reddit, forum or review threads about Cam Hub turned up. Their Discord (22,457 members,
  https://discord.gg/hE8FzgEh5a) has a patron-only area, so user complaints are not public.
  Everything under "complaints/gaps" is therefore inferred from what the changelog had to fix.

Legend: **[confirmed]** stated in a source; **[seen]** seen in the TikTok video (per the brief);
**[uncertain]** inferred or ambiguous.

---

## What it is

A Windows PC app ("Kujees Cam Hub") plus a phone app (Android, iPhone). The phone is the in-game
camera: "Move the phone = the game camera moves", with the live game picture as the viewfinder on the
phone. Launched 2026-10-04 as "Your phone is now a handheld camera for Assetto Corsa & BeamNG".
[confirmed] Sources: Patreon post list (API above); YouTube https://www.youtube.com/watch?v=1-9q9SnU_-k
and https://www.youtube.com/shorts/vAIAE2r-vNw (titles).

Creator background: Kujees is an Assetto Corsa graphics-mod creator ("Kujees Realism", PP filters,
CSP configs) with earlier AC apps on the same Patreon: "CamKujees" (2026-03-14), "Kujees Camera APP",
"Kujees Teleport APP", "Kujees Weather APP" (2026-09-16). Whether "Kujees Camera APP" was a
predecessor of Cam Hub is **[uncertain]** (post bodies are locked).

---

## 1. Camera

| Feature | What it does | Games | Phone control | Version | Source |
|---|---|---|---|---|---|
| Phone as handheld camera | Phone orientation and position drive the game camera; live game picture on the phone | AC, BeamNG (launch); ETS2, FH6 (1.2) | Move the phone | 1.0 (2026-10-04) | Patreon post list; YouTube titles |
| Full camera-app UI | Viewfinder, iPhone-like shutter/record button, zoom, mode tabs along the bottom | All | Buttons, tabs | seen in 1.3.0 | [seen] TikTok video |
| Save videos to the phone | Recording saved on the phone | All [uncertain which] | Record button | 1.2 | TikTok profile caption via search snippet ("NEW in Kujees Cam Hub 1.2 ... saving videos to the phone"), https://www.tiktok.com/@kujees |
| Zoom | Zoom in the viewfinder | All | Pinch zoom; **one-finger zoom** added in 1.3.0 | 1.2 (zoom), 1.3.0 (one-finger) | TikTok 1.2 caption; https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.3.0 |
| Pinch-zoom fix for FH6 Steam build | Pinch zoom works again on FH6 6.479.874.0 | FH6 | Pinch | 1.3.3 | https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.3.3 |
| Camera modes (tabs) | Seen: **Hood, Passenger, Free**, plus two more | All [uncertain per game] | Bottom tab bar | seen in 1.3.0 | [seen] TikTok |
| Lock options / start positions | **Lock on your car**, **Next car** (switch target car), **Head** (rides with the car). **Free** and **Drone** modes start outside the driver's door | General; FH6 also got Lock on your car and better Free/Drone | Tabs/buttons [uncertain] | 1.3.0 | v1.3.0 notes |
| Lamp | A light that shines where you look (a handheld torch) | BeamNG, AC (1.3.1); FH6 through an optional ReShade add-on package (1.3.3) that fixes lens projection and makes colour neutral; ReShade not included | A switch, remembered from 1.3.2 | 1.3.1 | https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.3.1, v1.3.2, v1.3.3, v1.3.4 ("Lamp says when its add-on is missing") |
| Recenter | Re-zero the phone's orientation | All | Recenter button | by 1.3.1 | v1.3.1 notes (two-phones item) |
| Two phones | Only one phone moves the camera at a time; tap Recenter on the phone you hold to take over | All | Recenter | 1.3.1 | v1.3.1 notes |
| Camera release | Closing the phone app hands the camera back to the game in ~1 s | All | Close the app | 1.3.2 | https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.3.2 |
| Game logo while loading | Phone shows the game's logo instead of a frozen picture while the game opens, loads or sits in a menu | All | none | 1.3.0 | v1.3.0 notes |
| Start order free | Start the game or the hub first; the camera links up either way | All | none | 1.3.1 | v1.3.1 notes |

## 2. Movement / thumbsticks

| Feature | What it does | Phone control | Version | Source |
|---|---|---|---|---|
| Walk (AR tracking) | Walking with the phone moves the camera through the world (6-DoF). Android uses **ARCore**; iPhone presumably ARKit **[uncertain]** | Physically walk | by 1.2.7 (Android 1.2.5) | https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.2.7 |
| Tracking-loss fallback | When AR tracking is lost (phone set down, camera covered, plain wall), the gyro keeps rotating the camera and the screen explains why; walking resumes when tracking returns | automatic | Android 1.2.5 (PC 1.2.7) | v1.2.7 notes |
| No drift on tracking loss | Camera no longer moves or turns by itself in a dark room or during a Wi-Fi hiccup | automatic | 1.3.1 | v1.3.1 notes |
| On-screen stick(s) | Move the camera with a virtual stick. **One-stick controls** added in 1.3.0 (implies a two-stick layout before, **[uncertain]**). The user saw on-screen thumbsticks | Thumbstick(s) | 1.3.0 (one-stick) | v1.3.0 notes; user |
| Walk: Off | For seated play: the phone only aims, the stick moves the camera | Toggle + stick | 1.3.1 | v1.3.1 notes |
| Walking collision | Walking camera no longer climbs walls onto roofs, and walks back out of a pit (so the walk camera follows the ground and collides) | automatic | 1.3.2 | v1.3.2 notes (AC and BeamNG only) |

## 3. Car controls (the "car button")

| Feature | Games | Version | Source |
|---|---|---|---|
| "Car button in every game": doors, hood, trunk, lights | All four (AC, BeamNG, ETS2, FH6) | 1.3.0 | v1.3.0 notes; Threads post 2026-10-21 "open doors, lights and your trailer from the phone" https://www.threads.com/@kujees |
| Headlights, parking lights, high beams, hazards, cabin light, horn | Seen in the video; per-game coverage **[uncertain]** | 1.3.0 | [seen] TikTok |
| Wipers ("wipers that really wipe") | ETS2 confirmed; Snapchat caption says "even wipers" generally | 1.3.0 | v1.3.0 notes; Snapchat Spotlight https://www.snapchat.com/@kujees |
| Truck lights and trailer hookup | ETS2 | 1.3.0 | v1.3.0 notes |
| Doors / hood / trunk toggles | FH6, only on suitable **parked** cars; auto-retry after small errors (1.3.4) | 1.3.3 | v1.3.3, v1.3.4 notes |

Control method: buttons on the phone behind a "car" button (a panel). Exact layout **[uncertain]**.

## 4. Replays

| Feature | Games | Version | Source |
|---|---|---|---|
| Replay controls: open doors and switch "extras" on **any car** in a replay; each car keeps its own settings ("Open any car in a replay") | Assetto Corsa | 1.3.0 | v1.3.0 notes; [seen] TikTok |
| Filming replays with the phone camera ("Replays, zoom, a smoother picture") | AC confirmed; BeamNG/others **[uncertain]** | 1.3.0 | [seen] TikTok; Snapchat 1.3.0 caption ("including replays and one-finger zoom") |
| "Next car" lock: jump the camera to another car | General | 1.3.0 | v1.3.0 notes |

## 5. Video quality / smoothing / connection

| Feature | What it does | Version | Source |
|---|---|---|---|
| Live View sharper/smoother | Better phone picture and smoother tracking | 1.2 | TikTok 1.2 caption (snippet) |
| Smoother, sharper picture | incl. BeamNG.drive 0.40 | 1.3.0 | v1.3.0 notes |
| Smoother picture in BeamNG | BeamNG-specific streaming improvement | 1.3.1 | v1.3.1 notes |
| Wi-Fi (default) | Phone and PC on the same network | 1.0 | v1.2.7-1.2.9 notes |
| Internet fallback | If Wi-Fi can't reach the PC, the phone connects over the internet by itself; the picture is kept lighter over the internet (1.3.4). Implies a relay server **[uncertain how]** | 1.3.1 | v1.3.1, v1.3.4 notes |
| Backup address | Sign-in, updates and phone connection work on networks that block them | 1.3.1 | v1.3.1 notes |
| USB cable | Steadier picture and motion; falls back to Wi-Fi when unplugged. iPhone needs Apple's free "Apple Devices" app; Android needs USB debugging on | 1.3.2 | v1.3.2 notes |
| Light PC mode | On weaker PCs the phone picture is smaller and 30 fps so the game keeps its frame rate; turns on by itself; Settings > Live View | 1.3.2 | v1.3.2 notes |
| Auto reconnect / picture recovery | Detects a stalled connection and reconnects; a frozen or black picture recovers itself | 1.3.4 | https://github.com/kujees/kujees-cam-downloads/releases/tag/v1.3.4 |
| Re-find the PC | If the PC's address changes the phone finds it again | 1.3.1 | v1.3.1 notes |

Not found: resolution, bitrate, codec, latency figures, stabilisation/smoothing settings for the motion
itself. **[uncertain]**

## 6. Per-game support

| Game | Status | How it hooks in (functional) | Notes / limits | Source |
|---|---|---|---|---|
| **Assetto Corsa** | Since 1.0 | Needs **Custom Shaders Patch** (hub warns if missing) | Replay controls (doors/extras on any car), Lamp, walking collision fix | v1.3.0, v1.3.1, v1.3.2 notes |
| **BeamNG.drive** | Since 1.0 | A BeamNG **mod** installed by the hub; the hub switches it back on if the player disables it | Lamp, smoother picture (1.3.1), walking collision fix, 0.40 supported (1.3.0) | v1.3.0, v1.3.1, v1.3.2 notes |
| **Euro Truck Simulator 2** | Since 1.2 | Needs an ETS2 profile/"controller slot" set up (hub reports when missing) **[uncertain mechanism]** | Truck lights, wipers, trailer | v1.3.0, v1.3.1 notes; 1.2 post title |
| **Forza Horizon 6** | Since 1.2, **experimental** | Uses FH6 "Data Out"; tied to specific game builds (broke with a Steam update, fixed in 1.3.3) | **Steam only, Solo only**; no Microsoft Store / Game Pass; doors/hood/trunk only on parked cars; Lamp needs ReShade add-on | v1.3.3, v1.3.4 notes |

## 7. Setup / pricing / platform

- **Price:** Patreon. "Kujees — Full Access" **US$3/month** (CA$4.50) unlocks all Kujees apps,
  graphics packs, etc., incl. Cam Hub; "Kujees — Supporter" **US$6/month** (same access, extra
  support); Free tier. Cam Hub is not sold separately. 8,524 patrons incl. free; the API reported
  **133 paid members [uncertain: one fetch only]**. Source: Patreon campaign API (above).
- **Free trial:** the hub "starts your free trial" on first start (length unknown). v1.3.1 notes.
- **Licence:** Patreon sign-in + licence server; a "Copy PC ID" access code works without the server
  (1.2.7); stays signed in if Patreon is down (1.3.4); app locks itself if its files are changed
  (1.3.0). Sources: v1.2.7, v1.3.0, v1.3.4 notes.
- **PC:** Windows only. Installer `KujeesCamHub-1.3.4-Setup.exe` (70 MB) with a `.sha256` file;
  **unsigned** executables; updates in-app (green dot on Settings, 1.2.9) or by running the new Setup
  over the old one (settings, access and pairing kept). Opens in the browser if its own window fails
  (1.3.4). Sources: v1.2.4, v1.2.9, v1.3.4 notes; expanded assets list.
- **Phones:** Android app (1.2.5 → 1.2.6 → 1.3.2) shipped with the PC release (sideloaded APK
  **[uncertain]**; no Play Store listing found). iPhone app via **TestFlight** (1.2 build 8), "free on
  TestFlight". App codes for both are on the PC's Phone tab (1.3.1). Black-and-white UI to match the PC
  app (1.2 / Android 1.2.6). Sources: v1.2.9, v1.3.1, v1.3.3 notes; TikTok 1.2 caption.
- **Pairing:** scan a **QR / pairing code** on the PC's Phone tab; the code holds all the PC's
  addresses and the phone tries each (1.2.9); "Scan again" keeps the old pairing as "Back to my PC"
  (1.3.2); manual address entry possible (1.2.8).
- **First-start helpers:** one-time "Let your phone connect to this PC?" firewall prompt (1.2.9);
  Phone tab status line with one Fix it button (Public network, missing firewall rule, hidden Block
  rule, VPN/virtual adapter address, antivirus firewalls named: Kaspersky, Norton, Bitdefender);
  browser test to tell router problems apart; per-game "what's missing" messages on PC and phone;
  "Create report file" for support. Sources: v1.2.7, v1.2.8, v1.2.9, v1.3.1 notes.
- **Timeline:** 1.0 (2026-10-04) → 1.2 (10-17: ETS2, FH6, iPhone, new app) → 1.2.4-1.2.9 (10-18, six
  builds in a day, mostly connection fixes) → 1.3.0 (10-21: car button, AC replays, one stick) →
  1.3.1 (10-27: internet fallback, Walk Off, Lamp) → 1.3.2 (11-02: USB, Light PC) → 1.3.3 (11-03: FH6
  Steam fix) → 1.3.4 (11-05: reconnect/recovery). No 1.1 found.

## 8. User complaints / gaps we could beat

No public user complaints were found (Discord is patron-gated). The items below are **inferred**
from what the changelog had to fix or still states as limits.

1. **Connecting is their biggest pain.** Six builds in one day (1.2.4-1.2.9) and most later
   releases fix firewall rules, Public networks, VPN/virtual adapters, antivirus firewalls,
   licence-server blocks, stalled or black pictures. A connection that just works (and a clear
   one-screen diagnosis) is a real edge.
2. **Online licence + Patreon dependency.** Sign-in, licence server, a self-locking app; users
   behind VPN/ad-blockers hit "one moment" loops (fixed in 1.3.1). An offline-first licence would
   be friendlier.
3. **Unsigned Windows installer** (SmartScreen warnings, "downloaded from the internet" mark).
4. **iPhone only through TestFlight** (builds expire after 90 days; not on the App Store). A
   proper App Store app is a clear advantage for iPhone users.
5. **Performance cost on the game PC:** they needed a "Light PC mode" (smaller, 30 fps picture).
   Low-latency encoding (hardware encoder) and a measured cost budget would beat it.
6. **AR walking problems:** camera drift on tracking loss, climbing walls onto roofs, stuck in pits
   (all patched). Solid ground-following and collision from the start, per game.
7. **FH6 is fragile** (breaks on game updates, Steam only, Solo only). Not relevant to us
   (BeamNG + AC), but shows memory-hook approaches break.
8. **Car controls are shallow and game-generic** (doors/hood/trunk/lights/wipers/horn). In BeamNG
   we can go much deeper through vehicle Lua (any light, any door/coupler, ignition, handbrake,
   per-car functions) and in AC through CSP extras.
9. **Replay support is AC-only** for car controls. BeamNG replays with the phone camera and car
   state are open ground **[uncertain whether they film BeamNG replays at all]**.
10. **Not found anywhere:** camera settings like focal length in mm, aperture/depth of field, shake
    or stabilisation amount, exposure, horizon lock, follow/smoothing tuning, audio, frame-rate or
    bitrate choice, multi-camera switching for streams. All are candidate differentiators.

## Related tools (not Kujees)

- **RealCam Hub** by Grow1nly (Patreon, 3,185 patrons): phone-as-camera (ARKit) plugin for AC,
  BeamNG, FH6; €6.50 and €10 tiers. https://www.patreon.com/cw/Grow1nly
- **Russozl** AC camera tools (CSP Lua apps; phone as camera over Wi-Fi or USB, "Mobile Cam" with
  G-force physics and subject tracking). https://www.patreon.com/russozl/about
- **SineMatic's "Rotation tracker and handheld camera"** for BeamNG (experimental, Android app sends
  rotation over USB tethering/Wi-Fi; rotation only, FOV presets).
  https://www.beamng.com/threads/rotation-tracker-and-handheld-camera.110983/
