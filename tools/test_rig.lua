-- Offline checks for the camera maths: from tools/, run  ~/.rokit/bin/luau.exe test_rig.lua
local rig = require("../mod/tss_handcam/lua/common/tssHandcam/rig")
local v = rig.v

local fails = 0
local function near(a, b, eps) return math.abs(a - b) <= (eps or 1e-6) end
local function vnear(a, b, eps) return near(a.x, b.x, eps) and near(a.y, b.y, eps) and near(a.z, b.z, eps) end
local function check(ok, what)
  if not ok then fails = fails + 1 print("FAIL " .. what) else print("ok   " .. what) end
end
local function s3(a) return string.format("(%.3f, %.3f, %.3f)", a.x, a.y, a.z) end

-- BeamNG is right-handed: right = forward x up
check(vnear(rig.rightOf(v(0, 1, 0)), v(1, 0, 0)), "right of +Y is +X")
check(vnear(rig.cross(rig.Y, rig.Z), rig.X), "Y x Z = X")

-- filming frame order
check(vnear(rig.fromFilm(1, 2, 3), v(1, 3, 2)), "film (right, up, fwd) -> (right, fwd, up)")

-- an anchor puts the sample exactly on the camera, whatever the phone's own heading
local function sample(px, py, pz, yawDeg, pitchDeg)
  local y, p = math.rad(yawDeg), math.rad(pitchDeg)
  local f = v(math.sin(y) * math.cos(p), math.cos(y) * math.cos(p), math.sin(p))
  local u = rig.normalize(rig.sub(rig.Z, rig.scale(f, rig.dot(rig.Z, f))), rig.X)
  return { p = v(px, py, pz), f = f, u = u }
end
local camPos, camLook, camUp = v(100, -50, 3), rig.normalize(v(-1, 1, 0), nil), rig.Z
local s0 = sample(0.4, -0.2, 1.5, 70, 0)
local a = rig.makeAnchor(camPos, camLook, camUp, s0)
local p, l, u = rig.toRig(a, s0)
check(vnear(p, camPos, 1e-9), "anchor: position lands on the camera " .. s3(p))
check(vnear(l, camLook, 1e-9), "anchor: heading lands on the camera " .. s3(l))
check(near(math.abs(rig.len(a.fwd)), 1) and near(a.fwd.z, 0), "anchor fwd is horizontal and unit")

-- walking the phone 1 m forward (its own forward at anchor time) moves the camera 1 m along the camera's view
local s1 = { p = rig.add(s0.p, s0.f), f = s0.f, u = s0.u }
p = rig.toRig(a, s1)
check(vnear(p, rig.add(camPos, camLook), 1e-9), "1 m along the phone's view = 1 m along the camera's view")

-- stepping right moves right of the view; tilting down looks down
local right = rig.rightOf(camLook)
local sr = { p = rig.add(s0.p, rig.rightOf(rig.horizontal(s0.f))), f = s0.f, u = s0.u }
check(vnear(rig.toRig(a, sr), rig.add(camPos, right), 1e-9), "1 m to the phone's right = 1 m to the camera's right")
local sd = sample(0.4, -0.2, 1.5, 70, -30)
local _, ld = rig.toRig(a, sd)
check(near(ld.z, -math.sin(math.rad(30)), 1e-9), "pitch is absolute: 30 deg down stays 30 deg down")
check(near(rig.dot(rig.horizontal(ld), camLook), 1, 1e-9), "pitching keeps the heading")

-- straight down still has a heading (from the up vector)
check(vnear(rig.headingOf(v(0, 0, -1), v(0, 1, 0)), v(0, 1, 0)), "looking straight down: heading from up")
check(vnear(rig.headingOf(v(0, 0, 1), v(0, -1, 0)), v(0, 1, 0)), "looking straight up: heading from -up")

-- rig frames round-trip, and a car frame turned 90 deg left maps its forward onto world -X
local F = rig.frame(v(10, 20, 1), v(-1, 0, 0), rig.Z)
check(vnear(F.r, v(0, 1, 0)), "car facing -X: its right is +Y")
local w = v(3, -4, 2)
check(vnear(rig.toWorld(F, rig.fromWorld(F, w)), w, 1e-9), "frame round-trip")
check(vnear(rig.toWorldDir(F, v(0, 1, 0)), v(-1, 0, 0)), "car frame forward")

-- lenses: 1x on a 19.5:9 phone in landscape, 16:9 game: the phone's screen is wider than the game's, so the
-- game renders taller (~67 deg) to cover the phone's full width; 2x about halves tan(fov/2)
local f1 = rig.fovForView(24, 19.5 / 9, false, 16 / 9)
local f2 = rig.fovForView(48, 19.5 / 9, false, 16 / 9)
check(f1 > 60 and f1 < 72 and near(math.tan(math.rad(f2) / 2), math.tan(math.rad(f1) / 2) / 2, 1e-9),
  string.format("lens FOV 1x %.1f, 2x %.1f deg", f1, f2))
check(rig.fovForView(13, 19.5 / 9, false, 16 / 9) > f1, "ultra-wide is wider than 1x")
check(near(rig.fovForZoom(60, 1), 60) and near(math.tan(math.rad(rig.fovForZoom(60, 2)) / 2), math.tan(math.rad(30)) / 2), "zoom halves tan(fov/2)")

-- walking: stick up = along the view, full stick = full speed
local wv = rig.walkVelocity(camLook, camUp, 0, 1, 2)
check(vnear(wv, rig.scale(camLook, 2), 1e-9), "stick up walks along the view")
check(vnear(rig.walkVelocity(camLook, camUp, 0.05, 0, 2), v(0, 0, 0)), "dead zone")

-- the live-view rotation is the identity when nothing differs, and turns the right way for a small yaw
local qx, qy, qz, qw = rig.viewRotation(camLook, camUp, camLook, camUp)
check(near(qw, 1) and near(qx, 0) and near(qy, 0) and near(qz, 0), "view rotation: identity")
local yawed = rig.normalize(rig.add(camLook, rig.scale(right, 0.1)), nil)
qx, qy, qz, qw = rig.viewRotation(camLook, camUp, yawed, camUp)
check(math.abs(qy) > 0.04 and math.abs(qx) < 1e-9 and math.abs(qz) < 1e-9, string.format("view rotation: a yaw turns about the camera's up (y %.3f)", qy))

-- sample validation
check(rig.validSample(s0), "valid sample")
check(not rig.validSample({ p = v(0 / 0, 0, 0), f = s0.f, u = s0.u }), "NaN rejected")

print(fails == 0 and "ALL OK" or (fails .. " FAILED"))
