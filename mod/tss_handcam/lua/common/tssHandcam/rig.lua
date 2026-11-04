-- TSS Handcam: the maths, on plain {x, y, z} tables so it runs (and is tested) outside the game
-- (tools/test_rig.lua, with LuaJIT or Luau).
--
-- Frames
--   Filming frame (what the AcHandCam receiver sends): x = right, y = up, z = forward, metres for the
--     position, unit vectors for the camera's forward and up. "Forward" is where the phone pointed
--     (horizontally) when you pressed Recenter; pitch and roll are absolute (gravity).
--   BeamNG: x = right, y = forward, z = up, right-handed (right = forward x up).
--   Rig frame: where the camera is anchored and smoothed: the world, or (follow-car) the car itself.
--     Rig coordinates use BeamNG's order too, so a frame is {o, r, f, u} in world space.
--   Anchor {pos, fwd}: places the filming frame in the rig frame, fwd horizontal:
--     rig(v) = pos + right(fwd) * v.x + fwd * v.y + up * v.z      (v in BeamNG order)
--
-- The camera maths of AcHandCam's Assetto Corsa app (github.com/jxrdxn011/AcHandcam, MIT) was the
-- reference for the anchor, walk, lens and timewarp-rotation ideas; this is a fresh implementation in
-- BeamNG's axes (no handedness probing needed: BeamNG is right-handed by definition).
local M = {}

local sqrt, cos, sin, atan2, abs, exp = math.sqrt, math.cos, math.sin, math.atan2 or math.atan, math.abs, math.exp

local function v(x, y, z) return { x = x, y = y, z = z } end
local function add(a, b) return v(a.x + b.x, a.y + b.y, a.z + b.z) end
local function sub(a, b) return v(a.x - b.x, a.y - b.y, a.z - b.z) end
local function scale(a, s) return v(a.x * s, a.y * s, a.z * s) end
local function dot(a, b) return a.x * b.x + a.y * b.y + a.z * b.z end
local function cross(a, b) return v(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x) end
local function len(a) return sqrt(dot(a, a)) end
local function normalize(a, fallback)
  local l = len(a)
  if l < 1e-9 then return fallback end
  return scale(a, 1 / l)
end
local function lerp(a, b, t) return v(a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t) end

M.v, M.add, M.sub, M.scale, M.dot, M.cross, M.len, M.normalize, M.lerp = v, add, sub, scale, dot, cross, len, normalize, lerp
M.X, M.Y, M.Z = v(1, 0, 0), v(0, 1, 0), v(0, 0, 1)

function M.isFinite(a)
  return a.x == a.x and a.y == a.y and a.z == a.z and abs(a.x) < 1e30 and abs(a.y) < 1e30 and abs(a.z) < 1e30
end

--- Filming frame (x right, y up, z forward) -> BeamNG order (x right, y forward, z up).
function M.fromFilm(x, y, z) return v(x, z, y) end

--- Right of a horizontal forward vector: forward x up.
function M.rightOf(fwd) return v(fwd.y, -fwd.x, 0) end

local function horizontal(a) return normalize(v(a.x, a.y, 0), nil) end
M.horizontal = horizontal

--- Horizontal heading of a camera; still defined when it looks straight down or up (then the
--- camera's up vector points along, or against, the way it faces).
function M.headingOf(look, up)
  if look.x * look.x + look.y * look.y > 0.0025 then return horizontal(look) end
  local hu = horizontal(up)
  if hu then return look.z < 0 and hu or scale(hu, -1) end
  return v(0, 1, 0)
end

--- Heading angle of a sample's camera (BeamNG order), from forward towards right.
function M.headingAngle(f, u)
  if f.x * f.x + f.y * f.y > 0.0025 then return atan2(f.x, f.y) end
  if f.z < 0 then return atan2(u.x, u.y) end
  return atan2(-u.x, -u.y)
end

--- A filming-frame vector (BeamNG order) in rig directions.
function M.dir(anchor, w)
  local r = M.rightOf(anchor.fwd)
  return v(r.x * w.x + anchor.fwd.x * w.y, r.y * w.x + anchor.fwd.y * w.y, w.z)
end

--- Camera pose in the rig frame for a sample {p, f, u} (BeamNG order).
function M.toRig(anchor, s)
  return add(anchor.pos, M.dir(anchor, s.p)), normalize(M.dir(anchor, s.f), M.Y), normalize(M.dir(anchor, s.u), M.Z)
end

--- The anchor that puts this sample exactly on the given camera's position and heading
--- (pitch and roll stay the phone's own: gravity is shared).
function M.makeAnchor(camPos, camLook, camUp, s)
  local fc = M.headingOf(camLook, camUp)
  local rc = M.rightOf(fc)
  local a = M.headingAngle(s.f, s.u)
  local anchor = { fwd = normalize(sub(scale(fc, cos(a)), scale(rc, sin(a))), fc) }
  anchor.pos = sub(camPos, M.dir(anchor, s.p))
  return anchor
end

-- ---------------------------------------------------------------- rig frames
M.WORLD = { o = v(0, 0, 0), r = M.X, f = M.Y, u = M.Z }

--- A frame from an origin, a forward and an up (up is made perpendicular to forward).
function M.frame(o, fwd, up)
  local f = normalize(fwd, M.Y)
  local u = normalize(sub(up, scale(f, dot(up, f))), M.Z)
  return { o = o, r = cross(f, u), f = f, u = u }
end
function M.toWorldDir(F, w) return add(add(scale(F.r, w.x), scale(F.f, w.y)), scale(F.u, w.z)) end
function M.toWorld(F, p) return add(F.o, M.toWorldDir(F, p)) end
function M.fromWorldDir(F, w) return v(dot(w, F.r), dot(w, F.f), dot(w, F.u)) end
function M.fromWorld(F, p) return M.fromWorldDir(F, sub(p, F.o)) end

--- Look kept, up made perpendicular to it.
function M.orthonormal(look, up)
  local l = normalize(look, M.Y)
  local u = normalize(sub(up, scale(l, dot(up, l))), nil)
  if not u then u = normalize(sub(M.Z, scale(l, l.z)), M.X) end
  return l, u
end

--- Frame-rate independent smoothing factor for time constant tau (s).
function M.smoothK(dt, tau)
  if tau <= 1e-4 then return 1 end
  return 1 - exp(-dt / tau)
end

-- ---------------------------------------------------------------- lenses
-- An iPhone lens's "mm" is a 35 mm equivalent on the 4:3 photo diagonal (43.27 mm): the sensor's long
-- half-side is 17.3 mm-equivalent, the short one 0.75 of that. The phone shows the 4:3 sensor area for the
-- ultra-wide and fills its whole screen from 1x (24 mm) up, easing in between 20 and 24 mm.
local HALF_LONG = 17.3

--- Vertical FOV (deg) the game must render so its frame covers what the phone's viewfinder shows.
function M.fovForView(focalMm, screenAspect, portrait, gameAspect)
  local tShort = HALF_LONG / math.max(1, focalMm) * 0.75
  local c = math.min(1, math.max(0, (focalMm - 20) / 4))
  c = c * c * (3 - 2 * c)
  local a = math.max(4 / 3, screenAspect or 4 / 3)
  local tLong = tShort * ((4 / 3) * (1 - c) + a * c)
  local ex, ey = tLong, tShort
  if portrait then ex, ey = tShort, tLong end
  return math.deg(2 * math.atan(math.max(ey, ex / math.max(0.1, gameAspect))))
end

--- Vertical FOV (deg) for a zoom factor on a base FOV: zoom 2 halves tan(fov / 2), like a real lens.
function M.fovForZoom(baseDeg, zoom)
  return math.deg(2 * math.atan(math.tan(math.rad(baseDeg) / 2) / math.max(0.1, zoom or 1)))
end

-- ---------------------------------------------------------------- walking
--- Dead zone, then a curve: fine control near the centre, full speed at the edge.
function M.shapeStick(x, y, dead, power)
  dead, power = dead or 0.08, power or 1.6
  local m = sqrt(x * x + y * y)
  if m <= dead then return 0, 0 end
  local k = math.min(1, (m - dead) / (1 - dead)) ^ power
  return x / m * k, y / m * k
end

--- Walking velocity (rig frame, m/s) for the stick: up walks where the camera looks, right strafes.
function M.walkVelocity(look, up, jx, jy, speed)
  local sx, sy = M.shapeStick(jx, jy)
  if sx == 0 and sy == 0 then return v(0, 0, 0) end
  local h = M.headingOf(look, up)
  return scale(add(scale(M.rightOf(h), sx), scale(h, sy)), speed)
end

--- Eases a velocity towards a target: starting takes ~tauUp, stopping ~tauDown.
function M.approach(vel, target, dt, tauUp, tauDown)
  local out = lerp(vel, target, M.smoothK(dt, len(target) >= len(vel) and tauUp or tauDown))
  if len(target) == 0 and len(out) < 1e-3 then return v(0, 0, 0) end
  return out
end

--- Steps per second at a walking speed (about 1.9 at 1.4 m/s).
function M.cadence(speed) return math.max(1.4, math.min(2.8, 1.2 + 0.5 * speed)) end

--- Head bob for a step phase (2 pi per step; the sway repeats every two steps): up (m), side (m), roll (rad).
--- amount 1 is subtle (about 1 cm at walking pace), growing a little with speed.
function M.headBob(phase, speed, amount)
  local s = math.min(math.max(speed, 0) / 1.4, 1.6) * (amount or 1)
  if s <= 0 then return 0, 0, 0 end
  return -0.012 * s * cos(phase), 0.008 * s * sin(phase * 0.5), 0.004 * s * sin(phase * 0.5)
end

-- ---------------------------------------------------------------- live view
--- Quaternion (x, y, z, w) of a rotation matrix m[row][col].
function M.quatFromMatrix(m)
  local tr = m[1][1] + m[2][2] + m[3][3]
  local x, y, z, w
  if tr > 0 then
    local s = sqrt(tr + 1) * 2
    w, x, y, z = 0.25 * s, (m[3][2] - m[2][3]) / s, (m[1][3] - m[3][1]) / s, (m[2][1] - m[1][2]) / s
  elseif m[1][1] > m[2][2] and m[1][1] > m[3][3] then
    local s = sqrt(1 + m[1][1] - m[2][2] - m[3][3]) * 2
    w, x, y, z = (m[3][2] - m[2][3]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s, (m[1][3] + m[3][1]) / s
  elseif m[2][2] > m[3][3] then
    local s = sqrt(1 + m[2][2] - m[1][1] - m[3][3]) * 2
    w, x, y, z = (m[1][3] - m[3][1]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s, (m[2][3] + m[3][2]) / s
  else
    local s = sqrt(1 + m[3][3] - m[1][1] - m[2][2]) * 2
    w, x, y, z = (m[2][1] - m[1][2]) / s, (m[1][3] + m[3][1]) / s, (m[2][3] + m[3][2]) / s, 0.25 * s
  end
  local n = sqrt(x * x + y * y + z * z + w * w)
  if w < 0 then n = -n end
  return x / n, y / n, z / n, w / n
end

--- Rotation from the view the phone asked for (tLook, tUp) to the view actually drawn (sLook, sUp), in the
--- camera's own axes (x screen right, y up, z back), which the receiver uses to timewarp the phone's picture.
function M.viewRotation(tLook, tUp, sLook, sUp)
  local function basis(look, up)
    local l, u = M.orthonormal(look, up)
    return { cross(l, u), u, scale(l, -1) }
  end
  local a, b = basis(tLook, tUp), basis(sLook, sUp)
  local m = {}
  for i = 1, 3 do
    m[i] = {}
    for j = 1, 3 do m[i][j] = dot(a[i], b[j]) end
  end
  return M.quatFromMatrix(m)
end

--- A sample the camera can use: finite, unit directions, sane position.
function M.validSample(s)
  if not (M.isFinite(s.p) and M.isFinite(s.f) and M.isFinite(s.u)) then return false end
  if abs(len(s.f) - 1) > 0.05 or abs(len(s.u) - 1) > 0.05 then return false end
  return abs(s.p.x) < 10000 and abs(s.p.y) < 10000 and abs(s.p.z) < 10000
end

return M
