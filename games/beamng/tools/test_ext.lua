-- Runs the real tssHandcam extension against stand-ins: a fake receiver (the shared memory, through a fake ffi),
-- a fake core_camera and camera data. From tools/:  ~/.rokit/bin/luau.exe test_ext.lua
-- Checks: no phone = the game's view is kept; engaging lands exactly on the game's view; moving the phone moves
-- the camera; recenter requests reach the status block; the view block is written; release gives the camera back.
local realRequire = require
local rig = realRequire("../mod/tss_handcam/lua/common/tssHandcam/rig")

local fails = 0
local function check(ok, what) if ok then print("ok   " .. what) else fails = fails + 1 print("FAIL " .. what) end end
local function near(a, b, e) return math.abs(a - b) <= (e or 1e-4) end

-- (run through python tools/run_tests.py)
-- ---- BeamNG stand-ins
local V = {}
V.__index = V
function vec3(x, y, z) return setmetatable({ x = x or 0, y = y or 0, z = z or 0 }, V) end
function V:set(x, y, z) self.x, self.y, self.z = x, y, z end
function quatFromDir(d, u) return { dir = d, up = u } end
local messages = {}
function ui_message(t) table.insert(messages, t) end
function log(_, _, t) print("log: " .. tostring(t)) end
local saved = nil
function jsonReadFile() return nil end
function jsonWriteFile(_, t) saved = t end
function castRayStatic(_, _, len) return len end -- no ground anywhere (flat void)
GFXDevice = { getVideoMode = function() return { width = 2560, height = 1440 } end }
core_vehicle_manager = { getVehicleData = function() return { mainPartName = "car" } end }
local function need(ok, what) if not ok then error("imgui: " .. what, 2) end end
ui_imgui = { BoolPtr = function(b) return { [0] = b } end, FloatPtr = function(f) return { [0] = f } end,
  ImVec4 = function() return {} end, ImVec2 = function() return {} end, Cond_FirstUseEver = 4,
  SetNextWindowSize = function() end, End = function() end, Separator = function() end, SameLine = function() end,
  Begin = function(name, open) need(type(name) == "string" and type(open) == "table", "Begin") return true end,
  TextColored = function(c, fmt, a) need(type(c) == "table" and fmt == "%s" and type(a) == "string", "TextColored") end,
  Button = function(l) need(type(l) == "string", "Button") return false end,
  Checkbox = function(l, p) need(type(l) == "string" and type(p) == "table" and type(p[0]) == "boolean", "Checkbox") return false end,
  SliderFloat = function(l, p, lo, hi, f) need(type(l) == "string" and type(p[0]) == "number" and lo < hi and type(f) == "string", "SliderFloat") return false end }

local active = nil
local gameView = { pos = vec3(100, 200, 5), fwd = vec3(0, 1, 0), up = vec3(0, 0, 1), fov = 60 }
core_camera = {
  getGlobalCameras = function() return { tssHandcam = {}, free = {} } end,
  getActiveGlobalCameraName = function() return active end,
  setGlobalCameraByName = function(n) active = n end,
  getPosition = function() return gameView.pos end,
  getForward = function() return gameView.fwd end,
  getUp = function() return gameView.up end,
  getFovDeg = function() return gameView.fov end,
  startCameraTransition = function() end,
  globalCameraFunction = function() end,
}

-- ---- the fake receiver + bridge: the receiver's blocks as plain tables; a "packet" is a table copy whose # is its
-- byte size, and the fake ffi copies fields where the real one copies bytes
local mem = { pose = { magic = 0x31434D48, version = 3, seqBegin = 1, seqEnd = 1, heartbeat = 1, outState = 1, generation = 1,
  px = 0, py = 1.5, pz = 0, fx = 0, fy = 0, fz = 1, ux = 0, uy = 1, uz = 0, latencyMs = 20, rxHz = 60, lossPct = 0,
  zoom = 1, joyX = 0, joyY = 0, mode = 0, viewOrientation = 0, vfAspect = 0 }, status = {}, view = {} }
local function packet(t, size)
  local c = {}
  for k, x in pairs(t) do c[k] = x end
  return setmetatable(c, { __len = function() return size end })
end
local inbox = {}
local function push() table.insert(inbox, packet(mem.pose, 108)) end -- the bridge forwards the receiver's pose
local fakeFfi = {
  cdef = function() end,
  new = function() return {} end,
  sizeof = function() return 48 end,
  copy = function(dst, src) for k, x in pairs(src) do dst[k] = x end end,
  string = function(buf, size) return packet(buf, size) end,
}
socket = { udp = function()
  return {
    setsockname = function(_, host, port) assert(host == "127.0.0.1" and port == 47830) return 1 end,
    settimeout = function() end,
    close = function() end,
    receive = function() return table.remove(inbox, 1) end,
    sendto = function(_, p, host, port)
      assert(host == "127.0.0.1" and port == 47831 and #p == 48)
      if p.magic == 0x31534D48 then mem.status = p elseif p.magic == 0x31564D48 then mem.view = p end
    end,
  }
end }
function __require(name) -- the extension's require (run_tests.py renames it in tools/.build_ext.lua)
  if name == "ffi" then return fakeFfi end
  if name == "tssHandcam/rig" then return rig end
  if name == "tssHandcam/tsc" then return realRequire("../mod/tss_handcam/lua/common/tssHandcam/tsc") end
  if name == "tssHandcam/control" then return realRequire("../mod/tss_handcam/lua/common/tssHandcam/control") end
  error("unexpected require " .. tostring(name))
end

local uiShown = true
ui_visibility = { get = function() return uiShown end, set = function(b) uiShown = b end }

local ext = realRequire("./.build_ext")({ __require = __require, ui_imgui = ui_imgui, vec3 = vec3, quatFromDir = quatFromDir,
  ui_message = ui_message, jsonReadFile = jsonReadFile, jsonWriteFile = jsonWriteFile, castRayStatic = castRayStatic,
  GFXDevice = GFXDevice, core_vehicle_manager = core_vehicle_manager, core_camera = core_camera, log = log, socket = socket,
  ui_visibility = ui_visibility })
tssHandcam = ext
ext.onExtensionLoaded()

local function beat() mem.pose.heartbeat = mem.pose.heartbeat + 1 mem.pose.seqBegin = mem.pose.seqBegin + 1 mem.pose.seqEnd = mem.pose.seqBegin push() end
local function frame()
  beat()
  ext.onUpdate(1 / 60)
  local data = { dtReal = 1 / 60, veh = 0, vid = 0, vehPos = vec3(), res = { pos = vec3(), targetPos = vec3(), rot = nil, fov = 60 } }
  local ran = (active == "tssHandcam") and ext.cameraUpdate(data)
  return data, ran
end

-- receiver found, phone live, not engaged
local d, ran = frame()
check(not ran, "not engaged: the game's camera runs")
check(mem.status.magic == 0x31534D48 and mem.status.engaged == 0, "status block written (idle)")

-- engage: the first frame lands exactly on the game's view
ext.engage()
check(active == "tssHandcam", "engage switches to the handheld camera")
d, ran = frame()
check(ran and near(d.res.pos.x, 100) and near(d.res.pos.y, 200) and near(d.res.pos.z, 5), string.format("engaged: starts on the game's view (%.2f %.2f %.2f)", d.res.pos.x, d.res.pos.y, d.res.pos.z))
check(near(d.res.rot.dir.y, 1) and near(d.res.rot.up.z, 1), "engaged: same direction")
check(mem.view.magic == 0x31564D48 and mem.view.live == 1 and mem.view.poseHeartbeat == mem.pose.heartbeat, "view block: live, with the pose it drew")

-- step 1 m forward with the phone (filming frame +z): the camera moves 1 m along the view (+Y here)
mem.pose.pz = 1
for _ = 1, 60 do d = frame() end
check(near(d.res.pos.y, 201, 1e-3) and near(d.res.pos.x, 100, 1e-3), string.format("1 m forward with the phone = 1 m forward in game (y %.3f)", d.res.pos.y))
-- step right
mem.pose.px = 0.5
for _ = 1, 60 do d = frame() end
check(near(d.res.pos.x, 100.5, 1e-3), string.format("0.5 m right with the phone = 0.5 m right in game (x %.3f)", d.res.pos.x))
-- crouch
mem.pose.py = 1.0
for _ = 1, 60 do d = frame() end
check(near(d.res.pos.z, 4.5, 1e-3), string.format("crouch 0.5 m = camera 0.5 m lower (z %.3f)", d.res.pos.z))
-- zoom 2x on a phone that sends its screen shape: the FOV narrows
mem.pose.vfAspect, mem.pose.zoom = 19.5 / 9, 2
for _ = 1, 120 do d = frame() end
check(d.res.fov < 45, string.format("2x zoom narrows the FOV (%.1f deg)", d.res.fov))

-- recenter: a request reaches the receiver; when it re-anchors (new generation), the view doesn't jump
local before = mem.status.recenterRequests
ext.recenter()
frame()
check(mem.status.recenterRequests == before + 1, "recenter request in the status block")
local p0 = { x = d.res.pos.x, y = d.res.pos.y, z = d.res.pos.z }
mem.pose.generation, mem.pose.px, mem.pose.py, mem.pose.pz = 2, 0, 1.5, 0 -- the receiver's new origin
d = frame()
check(near(d.res.pos.x, p0.x, 1e-3) and near(d.res.pos.y, p0.y, 1e-3) and near(d.res.pos.z, p0.z, 1e-3), "re-anchor: no jump")

-- walk mode: full stick forward walks along the view
mem.pose.mode, mem.pose.joyY = 1, 1
local y0 = d.res.pos.y
for _ = 1, 120 do d = frame() end
check(d.res.pos.y - y0 > 1.5, string.format("walk mode: 2 s of full stick walks forward (%.2f m)", d.res.pos.y - y0))
mem.pose.mode, mem.pose.joyY = 0, 0

-- a torn read (seqBegin ~= seqEnd) is skipped, not drawn
local yT = d.res.pos.y
ext.onUpdate(1 / 60) -- settle
mem.pose.pz, mem.pose.seqBegin = 50, mem.pose.seqBegin + 7 -- writer mid-update
push()
ext.onUpdate(1 / 60)
d = { dtReal = 1 / 60, veh = 0, vid = 0, vehPos = vec3(), res = { pos = vec3(), targetPos = vec3(), fov = 60 } }
ext.cameraUpdate(d)
check(near(d.res.pos.y, yT, 0.05), "torn read ignored")
mem.pose.pz = 0

-- release: back to the vehicle camera
ext.release()
check(active == nil, "release gives the camera back")
d, ran = frame()
check(not ran, "released: the game's camera runs again")

-- ride with the car: the camera keeps its place on the car while the car drives and turns
ext.toggleFollow() -- on
local carPos, carFwd = vec3(100, 195, 0.5), vec3(0, 1, 0)
local car = { getDirectionVector = function() return carFwd end, getDirectionVectorUp = function() return vec3(0, 0, 1) end }
local function carFrame()
  beat()
  ext.onUpdate(1 / 60)
  local dd = { dtReal = 1 / 60, veh = car, vid = 7, vehPos = carPos, res = { pos = vec3(), targetPos = vec3(), fov = 60 } }
  ext.cameraUpdate(dd)
  return dd
end
ext.engage()
d = carFrame()
local onCar = { x = d.res.pos.x - carPos.x, y = d.res.pos.y - carPos.y }
carPos = vec3(100, 215, 0.5) -- 20 m on
d = carFrame()
check(near(d.res.pos.y - carPos.y, onCar.y, 1e-3) and near(d.res.pos.x - carPos.x, onCar.x, 1e-3), "riding: the camera moves with the car")
carFwd = vec3(-1, 0, 0) -- the car turns 90 deg left: the camera, ahead of the car, swings round with it
d = carFrame()
check(near(d.res.pos.x - carPos.x, -onCar.y, 1e-3) and near(d.res.pos.y - carPos.y, onCar.x, 1e-3), "riding: the camera turns with the car")
check(near(d.res.rot.dir.x, -1, 1e-3), "riding: and so does its view")
ext.release()
active = nil

-- film clean: one key starts filming and hides the UI, the next stops and brings it back
ext.toggleClean()
check(active == "tssHandcam" and uiShown == false, "film clean: filming, UI hidden")
ext.toggleClean()
check(active == nil and uiShown == true, "film clean again: released, UI back")
uiShown = false -- UI already hidden by the player: filming clean and stopping leaves it hidden
ext.toggleClean() ext.toggleClean()
check(uiShown == false, "film clean: a UI the player hid stays hidden")
uiShown = true
ext.toggleClean()
active = "free" -- the player picks another camera while filming clean
ext.onUpdate(1 / 60)
check(uiShown == true and not ext.isEngaged(), "another camera picked: UI back")
active = nil

-- settings were saved after a change
ext.toggleFollow() -- off
ext.toggleFollow() -- on
for _ = 1, 70 do ext.onUpdate(1 / 60) end
check(saved and saved.followCar == true, "settings saved after a change")

print(fails == 0 and "ALL OK" or (fails .. " FAILED"))
