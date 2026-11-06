-- TSS Handcam: your phone as a handheld camera in BeamNG.
--
-- phone (AcHandCam app, ARKit/ARCore) --UDP--> AcHandCam PC receiver (Python, unchanged) --shared memory-->
--   our bridge (bridge/handcam_bridge.py) --local UDP--> this extension --> the "tssHandcam" global camera mode
--   (lua/ge/extensions/core/cameraModes/tssHandcam.lua), drawn every frame by core/cameraUpdate.lua.
--
-- The receiver was written for Assetto Corsa (github.com/jxrdxn011/AcHandcam, MIT). It publishes the phone's
-- pose in three named session-local file mappings (receiver/handheld_receiver/shm.py): Pose (it writes), Status
-- and View (the game writes; it reads them for recenter requests and to timewarp the phone's live view).
-- BeamNG's FFI accepts struct types but refuses C function declarations from mods ("invalid C type", tested in game
-- 2026-11-03), so the mod can't map that memory itself: the bridge relays the same bytes over 127.0.0.1 UDP
-- (BeamNG's luasocket, as core/remoteController.lua uses it). The packets are the mappings' exact layouts.
--
-- Camera: a global camera mode (isGlobal) is updated right before rendering with the frame's vehicle data, so a
-- camera riding with a car (follow-car) is placed with that frame's car pose (no lag). Vertical FOV, as every
-- BeamNG camera mode (core/cameraModes/gameengine.lua: "Camera modes choose the base vertical FOV").
local M = {}
M.dependencies = { 'ui_imgui' }

local im = ui_imgui
local rig = require('tssHandcam/rig')
local v = rig.v

local CAM_NAME = 'tssHandcam'
local SETTINGS = '/settings/tss_handcam.json'
local GAME_PORT, BRIDGE_PORT = 47830, 47831 -- bridge -> game (pose), game -> bridge (status, view)
local POSE_SIZE = 108
local POSE_MAGIC, POSE_VERSION = 0x31434D48, 3
local STATUS_MAGIC, STATUS_VERSION = 0x31534D48, 1
local VIEW_MAGIC, VIEW_VERSION = 0x31564D48, 2
local MODE_WALK = 1
local STATE_NAMES = {
  [0] = 'waiting for the phone', [1] = 'live', [2] = 'holding (tracking lost)', [3] = 'frozen (phone/receiver)',
  [4] = 'paused', [5] = 'holding (no packets)', [6] = 'playing a recorded path',
}
local RECEIVER_TIMEOUT = 1.0 -- s without a new heartbeat: the receiver isn't running
local BRIDGE_TIMEOUT = 1.0   -- s without any packet: the bridge isn't running (it resends every 0.25 s)
local STICK_TIMEOUT = 0.3    -- s without fresh data: the joystick counts as released
local MIN_CLEARANCE = 0.15   -- m: the camera never goes closer than this to the ground

local defaults = {
  fov = 46,             -- vertical FOV at 1x for phone apps that don't send their screen shape
  zoomFromPhone = true,
  smoothing = 0.03,     -- s, on top of the receiver's own filtering
  blendBack = 0.5,      -- s, blend back to the game's camera on release
  followCar = false,    -- ride with the player's car (in-car / chase shots)
  followTilt = true,    -- ...and pitch and roll with it
  walkSpeed = 1.6,      -- m/s at full stick (walk mode)
  walkSmoothing = 0.35, -- s
  headBob = 1,          -- 0 off, 1 subtle, 2 strong
  terrainAlways = false, -- keep above the ground in handheld mode too (walk mode always does)
}
local cfg = {}

-- ------------------------------------------------------------ the link
-- the receiver's layouts as FFI structs (types only: BeamNG allows those) to unpack and pack the packets
local ffi = require('ffi')
for _, decl in ipairs({ -- each on its own: after a Lua reload they exist already and ffi.cdef refuses to redeclare
  'typedef struct { uint32_t magic, version, seqBegin, heartbeat; int32_t outState; uint32_t flags, generation, trackingState; '
    .. 'float px, py, pz, fx, fy, fz, ux, uy, uz, latencyMs, rxHz, lossPct, zoom, joyX, joyY; '
    .. 'uint32_t mode, viewOrientation; float vfAspect; uint32_t seqEnd; } tsshc_pose;',
  'typedef struct { uint32_t magic, version, heartbeat, engaged, grabbed, lastGeneration, appliedCount; int32_t errorCode; '
    .. 'float fps, lookSign, sideSign; uint32_t recenterRequests; } tsshc_status;',
  'typedef struct { uint32_t magic, version, frame, live; float fovV, smoothing; uint32_t poseHeartbeat, hasRot; '
    .. 'float rx, ry, rz, rw; } tsshc_view;',
}) do pcall(ffi.cdef, decl) end
local poseBuf, statusBuf, viewBuf = ffi.new('tsshc_pose'), ffi.new('tsshc_status'), ffi.new('tsshc_view')

local udp = nil
local udpError = nil
local packetAge = 99 -- s since the bridge's last packet

local function openSocket()
  if udp then return true end
  if not socket or not socket.udp then udpError = 'no UDP sockets in this game version' return false end
  local s = socket.udp()
  if not s then udpError = 'could not make a UDP socket' return false end
  local ok, err = s:setsockname('127.0.0.1', GAME_PORT)
  if not ok then
    s:close()
    udpError = 'port ' .. GAME_PORT .. ' is busy (' .. tostring(err) .. ')'
    return false
  end
  s:settimeout(0)
  udp, udpError = s, nil
  return true
end

local function closeSocket()
  if udp then pcall(function() udp:close() end) end
  udp = nil
end

local handleTsc -- TSS Cam's own packets (commands, sticks), defined with the car controls below

-- newest whole pose packet this frame (the receiver may have sent a few), or nil; TSS Cam packets are handled
local function receive()
  if not udp then return nil end
  local last = nil
  for _ = 1, 64 do
    local d = udp:receive(8192)
    if not d then break end
    if #d == POSE_SIZE then
      last = d
    elseif type(d) == 'string' and d:sub(1, 3) == 'TSC' then
      packetAge = 0
      handleTsc(d)
    end
  end
  return last
end

local function send(buf)
  if udp then pcall(function() udp:sendto(ffi.string(buf, ffi.sizeof(buf)), '127.0.0.1', BRIDGE_PORT) end) end
end

-- ------------------------------------------------------------------- state
local sample = nil            -- last good pose {p, f, u (BeamNG order), state, zoom, joyX, joyY, mode, heartbeat, ...}
local lastHeartbeat, heartbeatAge = -1, 99
local lastGeneration = -1
local engaged, frozen = false, false
local prevGlobal = nil        -- the global camera before we took over (nil = the vehicle's camera)
local orig = nil              -- the game's view when we took over (world)
local rigF, rigMode = rig.WORLD, nil
local anchor, anchor0 = nil, nil
local needAnchor = true
local cur, curFov = nil, nil  -- shown pose (rig frame) and FOV
local lastWorld = nil         -- last pose drawn (world), handed to the free camera on release
local walkVel, bobPhase, walking = v(0, 0, 0), 0, false
local groundPrev, groundRaw, terrainWas = nil, nil, false
local tempSmooth, tempSmoothTime = 0, 0
local effectiveSmoothing = 0
local fps, appliedCount, statusBeat, viewFrame, recenterRequests = 60, 0, 0, 0, 0
local errorText = ''
local windowOpen = im.BoolPtr(false)
local autoOpened = false
local dirty, dirtyTime = false, 0

local function message(text) ui_message(text, 4, 'tssHandcam') end

-- --------------------------------------------------------------- settings
local function loadSettings()
  local s = jsonReadFile(SETTINGS) or {}
  for k, d in pairs(defaults) do
    if type(s[k]) == type(d) then cfg[k] = s[k] else cfg[k] = d end
  end
end
local function saveSettings() jsonWriteFile(SETTINGS, cfg, true) dirty = false end
local function changed() dirty, dirtyTime = true, 1 end

-- ------------------------------------------------------------------ reading
local function readPose()
  local d = receive()
  if not d then return nil end
  packetAge = 0
  local p = poseBuf
  ffi.copy(p, d, POSE_SIZE)
  local e = p.seqEnd
  local s = {
    magic = p.magic, version = p.version, heartbeat = p.heartbeat, state = p.outState, generation = p.generation,
    p = rig.fromFilm(p.px, p.py, p.pz), f = rig.fromFilm(p.fx, p.fy, p.fz), u = rig.fromFilm(p.ux, p.uy, p.uz),
    latency = p.latencyMs, rxHz = p.rxHz, loss = p.lossPct,
    zoom = p.zoom, joyX = p.joyX, joyY = p.joyY, mode = p.mode, portrait = p.viewOrientation == 1, vfAspect = p.vfAspect,
  }
  if p.seqBegin ~= e or s.magic ~= POSE_MAGIC or s.version ~= POSE_VERSION then return nil end -- torn or not ours
  if not (s.zoom == s.zoom and s.zoom > 0) then s.zoom = 1 end
  if not (s.joyX == s.joyX and s.joyY == s.joyY) then s.joyX, s.joyY = 0, 0 end
  return s, s.state ~= 0 and rig.validSample(s)
end

local function updateSample(dt)
  packetAge = packetAge + dt
  local s, valid = readPose()
  if not s then heartbeatAge = heartbeatAge + dt return end
  if s.heartbeat ~= lastHeartbeat then lastHeartbeat, heartbeatAge = s.heartbeat, 0 else heartbeatAge = heartbeatAge + dt end
  if valid then
    if s.generation ~= lastGeneration then
      if lastGeneration ~= -1 then needAnchor = true end -- the receiver re-anchored (recenter, tracking back)
      lastGeneration = s.generation
    end
    if not frozen or not sample then
      sample = s
    else -- frozen: hold the pose; zoom, mode and the stick still follow the phone
      sample.zoom, sample.mode, sample.joyX, sample.joyY = s.zoom, s.mode, s.joyX, s.joyY
      sample.state, sample.rxHz, sample.loss, sample.latency = s.state, s.rxHz, s.loss, s.latency
    end
    if not autoOpened and s.state == 1 then autoOpened = true windowOpen[0] = true end -- the phone just connected
  elseif sample then
    sample.state, sample.joyX, sample.joyY = s.state, 0, 0
  end
end

local function bridgeUp() return packetAge < BRIDGE_TIMEOUT end
local function receiverUp() return bridgeUp() and heartbeatAge < RECEIVER_TIMEOUT end

-- ------------------------------------------------------------------ writing
local function writeStatus()
  if not bridgeUp() then return end
  local st = statusBuf
  statusBeat = (statusBeat + 1) % 4294967296
  st.magic, st.version, st.heartbeat = STATUS_MAGIC, STATUS_VERSION, statusBeat
  st.engaged = engaged and 1 or 0
  st.grabbed = engaged and 1 or 0
  st.lastGeneration = math.max(0, lastGeneration)
  st.appliedCount = appliedCount % 4294967296
  st.errorCode = errorText ~= '' and 3 or 0
  st.fps = fps
  st.lookSign, st.sideSign = 1, 1
  st.recenterRequests = recenterRequests
  send(st)
end

-- what the receiver needs to timewarp the phone's live view: the pose this frame was drawn from, the FOV, and the
-- rotation from that pose's view to the one drawn (smoothing, head bob)
local function writeView(live, fov, poseHeartbeat, rot)
  if not bridgeUp() then return end
  local vw = viewBuf
  viewFrame = (viewFrame + 1) % 4294967296
  vw.magic, vw.version, vw.frame = VIEW_MAGIC, VIEW_VERSION, viewFrame
  vw.live = live and 1 or 0
  vw.fovV, vw.smoothing = fov or cfg.fov, effectiveSmoothing
  vw.poseHeartbeat = poseHeartbeat or 0
  if rot and rot[4] == rot[4] then
    vw.hasRot, vw.rx, vw.ry, vw.rz, vw.rw = 1, rot[1], rot[2], rot[3], rot[4]
  else
    vw.hasRot = 0
  end
  send(vw)
end

-- ------------------------------------------------------------------ camera
local function t3(a) return v(a.x, a.y, a.z) end

local function gameAspect()
  local ok, a = pcall(function() local vm = GFXDevice.getVideoMode() return vm.width / vm.height end)
  if ok and a and a == a and a > 0.2 and a < 10 then return a end
  return 16 / 9
end

local function isUnicycle(vid)
  local d = vid and core_vehicle_manager and core_vehicle_manager.getVehicleData(vid)
  return d ~= nil and d.mainPartName == 'unicycle'
end

-- ---------------------------------------------- TSS Cam: camera modes, car controls, phone commands
-- Camera modes: hand (the world), ride (ride with the car, as you hold it), hood / passenger / chase / drone (ride
-- with the car from that mount), lockon (handheld, always looking at the car). The camera can follow any car
-- (followVid) without taking control of it: the player keeps driving their own (docs/research/beamng-apis.md s8).
local tsc = require('tssHandcam/tsc')
local control = require('tssHandcam/control')
local MODES = { 'hand', 'ride', 'hood', 'passenger', 'chase', 'drone', 'lockon' }
local MODE_SET, RIDE = {}, { ride = true, hood = true, passenger = true, chase = true, drone = true }
for _, m in ipairs(MODES) do MODE_SET[m] = true end
local camMode, pendingMount, followVid = 'hand', nil, nil
local sticks, sticksAge = nil, 99
local carState, carStateVid = {}, nil
local capsRev, capsTimer, stateTimer, hornTimer, tscCount = 0, 0, 0, 0, 0
local lockLook = nil
local hidUi = false -- we hid the UI (film clean / the phone's clean button) and must bring it back

local stickFloats = ffi.new('float[4]')
local function toFloats(p) ffi.copy(stickFloats, p, 16) return stickFloats[0], stickFloats[1], stickFloats[2], stickFloats[3] end
local function sendRaw(s) if udp then pcall(function() udp:sendto(s, '127.0.0.1', BRIDGE_PORT) end) end end
local function sendJson(magic, t)
  local ok, s = pcall(jsonEncode, t)
  if ok and type(s) == 'string' then sendRaw(tsc.encode(magic, s)) end
end

-- the car the camera is about: the one picked on the phone, else the player's
local function targetVeh()
  if followVid then
    local o = getObjectByID and getObjectByID(followVid)
    if o then return o, followVid end
    followVid = nil
  end
  local veh = getPlayerVehicle and getPlayerVehicle(0)
  if veh then return veh, veh:getId() end
  return nil, nil
end

local function carList()
  local list = {}
  if not be then return list end
  for i = 0, be:getObjectCount() - 1 do
    local o = be:getObject(i)
    if o and o:getActive() and o.playerUsable ~= false and not isUnicycle(o:getId()) then list[#list + 1] = o end
  end
  return list
end

local function carName(veh)
  local jb = veh and veh:getJBeamFilename() or ''
  local ok, m = pcall(function() return core_vehicles.getModel(jb).model end)
  if ok and type(m) == 'table' and m.Name then return (m.Brand and (m.Brand .. ' ') or '') .. m.Name end
  return jb
end

local function sendCaps()
  local veh, vid = targetVeh()
  local vd = vid and core_vehicle_manager.getVehicleData(vid)
  local tr = translateLanguage and function(k) return translateLanguage(k, k) end
  local rs = core_replay and core_replay.state or {}
  capsRev = capsRev + 1
  sendJson(tsc.CAPS, {
    rev = capsRev, car = veh and carName(veh) or '', vid = vid or -1, followingOther = followVid ~= nil,
    cars = #carList(), engaged = engaged, frozen = frozen, camMode = camMode, modes = MODES,
    controls = vd and vd.vdata and control.controls(vd.vdata, tr, carStateVid == vid and carState or nil) or {},
    time = simTimeAuthority and simTimeAuthority.get() or 1, paused = simTimeAuthority and simTimeAuthority.getPause() or false,
    replay = { state = rs.state or 'inactive', pos = rs.positionSeconds or 0, total = rs.totalSeconds or 0,
               speed = rs.speed or 1, paused = rs.paused or false },
    clean = hidUi,
  })
end

local function answer(seq, ok, err) sendJson(tsc.ANSWER, { seq = seq, ok = ok and true or false, err = err or '' }) end

local function cycleCar(step)
  local list = carList()
  if #list == 0 then return false, 'no cars' end
  local _, cur = targetVeh()
  local idx = 1
  for i, o in ipairs(list) do if o:getId() == cur then idx = i end end
  local nxt = list[((idx - 1 + step) % #list) + 1]
  local player = getPlayerVehicle and getPlayerVehicle(0)
  followVid = (player and nxt:getId() == player:getId()) and nil or nxt:getId()
  pendingMount = camMode -- put the camera on the new car
  return true
end

local commands = {}
commands['car'] = function(a)
  if core_replay and core_replay.state and core_replay.state.state == 'playback' then
    return false, 'car controls are off during a replay'
  end
  local veh, vid = targetVeh()
  if not veh then return false, 'no car' end
  local vd = core_vehicle_manager.getVehicleData(vid)
  local lua, err = control.luaFor(a.id, a, vd and vd.vdata)
  if not lua then return false, err end
  veh:queueLuaCommand(lua)
  if a.id == 'horn' then hornTimer = (a.down == false) and 0 or 3 end -- a lost "up" can't leave it blaring
  stateTimer = 0.15
  return true
end
commands['cam.toggle'] = function() M.toggle() return true end
commands['cam.engage'] = function() M.engage() return engaged, errorText end
commands['cam.release'] = function() M.release() return true end
commands['cam.recenter'] = function() M.recenter() return true end
commands['cam.freeze'] = function() M.freeze() return true end
commands['cam.mode'] = function(a)
  if not MODE_SET[a.mode] then return false, 'unknown camera mode' end
  camMode, pendingMount, lockLook = a.mode, a.mode, nil
  return true
end
commands['car.next'] = function() return cycleCar(1) end
commands['car.prev'] = function() return cycleCar(-1) end
commands['car.player'] = function() followVid, pendingMount = nil, camMode return true end
commands['time'] = function(a) -- live slow motion (lua/ge/simTimeAuthority.lua: 0.001..1, no fast motion live)
  local s = tonumber(a.speed)
  if not (simTimeAuthority and s) then return false, 'no time control' end
  simTimeAuthority.set(math.max(0.001, math.min(1, s)))
  return true
end
commands['pause'] = function() if not simTimeAuthority then return false end simTimeAuthority.togglePause() return true end
commands['replay'] = function(a)
  if not core_replay then return false, 'no replays' end
  local op, val = a.op, tonumber(a.value)
  if op == 'toggle' then core_replay.togglePlay()
  elseif op == 'stop' then core_replay.stop()
  elseif op == 'seek' and val then core_replay.seek(math.max(0, math.min(1, val)))
  elseif op == 'jump' and val then core_replay.jumpTime(val)
  elseif op == 'speed' and val then core_replay.setSpeed(math.max(0.001, math.min(8, val)))
  else return false, 'unknown replay op' end
  return true
end
commands['clean'] = function(a)
  if not ui_visibility then return false, 'no UI control' end
  local on = a.on
  if on == nil then on = not hidUi end
  if on and ui_visibility.get() then ui_visibility.set(false) hidUi = true
  elseif not on and hidUi then ui_visibility.set(true) hidUi = false end
  return true
end

handleTsc = function(d)
  local magic, payload = tsc.decode(d)
  if not magic then return end
  tscCount = tscCount + 1
  if magic == tsc.STICKS then
    local s = tsc.sticks(payload, toFloats)
    if s then sticks, sticksAge = s, 0 end
  elseif magic == tsc.COMMAND then
    local ok, msg = pcall(jsonDecode, payload)
    if not ok or type(msg) ~= 'table' then return end
    local seq, fn = tonumber(msg.seq) or 0, commands[msg.cmd]
    if not fn then answer(seq, false, 'unknown command ' .. tostring(msg.cmd)) return end
    local okc, res, err = pcall(fn, type(msg.args) == 'table' and msg.args or {})
    if okc then answer(seq, res, err) else answer(seq, false, tostring(res)) end
    capsTimer = math.min(capsTimer, 0.1) -- the phone sees the change soon
  end
end

local function linkTick(dt)
  sticksAge = sticksAge + dt
  if hornTimer > 0 then
    hornTimer = hornTimer - dt
    if hornTimer <= 0 then
      local veh = targetVeh()
      if veh then veh:queueLuaCommand('electrics.horn(false)') end
    end
  end
  if not bridgeUp() then return end
  stateTimer = stateTimer - dt
  if stateTimer <= 0 then
    stateTimer = 0.5
    local veh = targetVeh()
    if veh then veh:queueLuaCommand(control.STATE_LUA) end
  end
  capsTimer = capsTimer - dt
  if capsTimer <= 0 then
    capsTimer = 1
    sendCaps()
  end
end

function M.onCarState(vid, lights, hazard, sl, sr, fog, lightbar, pb, engine) -- from the car's own Lua
  carStateVid = vid
  carState = { lights = lights, hazard = hazard, signalL = sl, signalR = sr, fog = fog, lightbar = lightbar,
               parkbrake = pb, engine = engine }
end

-- a mount point on a car, in the world: the node of one of its cameras (core/camera.lua: getCameraDataById,
-- cameraModes/onboard.lua:70-72 for the formula)
local function camNode(veh, vid, name)
  local ok, cd = pcall(function() return core_camera.getCameraDataById(vid) end)
  local c = ok and cd and cd[name]
  if not (c and c.camNodeID) then return nil end
  local x, y, z = veh:getPositionXYZ()
  local nx, ny, nz = veh:getNodePositionXYZ(c.camNodeID)
  if not (x and nx) then return nil end
  return v(x + nx, y + ny, z + nz)
end

-- where a mode puts the camera (world pos, look, up), or nil to keep the view as it is
local function mountWorld(mode, veh, vid, F)
  if not veh then return nil end
  local f, u, r, o = F.f, F.u, F.r, F.o
  local function at(df, dr, du) return rig.add(o, rig.add(rig.scale(f, df), rig.add(rig.scale(r, dr), rig.scale(u, du)))) end
  if mode == 'hood' then
    return camNode(veh, vid, 'onboard.hood') or at(1.2, 0, 1.1), rig.normalize(rig.sub(f, rig.scale(u, 0.06)), f), u
  elseif mode == 'passenger' then
    local ok, _, rhd = pcall(function() return core_camera.getDriverDataById(vid) end)
    local seat = (ok and rhd) and 'occupant.FL' or 'occupant.FR'
    return camNode(veh, vid, seat) or at(0, (ok and rhd) and -0.45 or 0.45, 0.9), f, u
  elseif mode == 'chase' then
    local p = at(-6, 0, 1.8)
    return p, rig.normalize(rig.sub(at(0, 0, 0.8), p), f), u
  elseif mode == 'drone' then
    local p = at(-8, 0, 5)
    return p, rig.normalize(rig.sub(at(0, 0, 0.5), p), f), u
  end
  return nil
end

-- the rig this frame: the world, or the followed car with this frame's pose (on foot there is no car to ride)
local function currentRig(data)
  local veh, vid = data.veh, data.vid
  if followVid then
    local o = getObjectByID and getObjectByID(followVid)
    if o then veh, vid = o, followVid else followVid = nil end
  end
  if not (RIDE[camMode] or cfg.followCar) then return rig.WORLD, 'world', veh, vid end
  if not veh or veh == 0 or isUnicycle(vid) then return rig.WORLD, 'world', veh, vid end
  local fwd, up = t3(veh:getDirectionVector()), t3(veh:getDirectionVectorUp())
  if not cfg.followTilt then
    fwd = rig.horizontal(fwd) or rig.Y
    up = rig.Z
  end
  local pos = (veh == data.veh) and t3(data.vehPos) or t3(veh:getPosition()) -- the player's car: this frame's pose
  return rig.frame(pos, fwd, up), 'car', veh, vid
end

local function buildAnchor()
  local cp, cl, cu
  if cur then
    cp, cl, cu = cur.pos, cur.look, cur.up
  else
    cp, cl, cu = rig.fromWorld(rigF, orig.pos), rig.fromWorldDir(rigF, orig.look), rig.fromWorldDir(rigF, orig.up)
  end
  anchor = rig.makeAnchor(cp, cl, cu, sample)
  if not anchor0 then anchor0 = { pos = anchor.pos, fwd = anchor.fwd, mode = rigMode } end
  groundPrev = nil
  needAnchor = false
end

-- ground height under (x, y): rays only reach down from a little above the last ground, so the camera climbs
-- kerbs and slopes but never jumps onto a roof or bridge overhead
local function groundAt(x, y, refZ, stepUp)
  local len = stepUp + 100
  local ok, d = pcall(castRayStatic, vec3(x, y, refZ + stepUp), vec3(0, 0, -1), len)
  if not ok or not d or d >= len then return nil end
  return refZ + stepUp - d
end

-- walk mode (and optionally handheld): keep the same height above the ground over hills and kerbs, never below it
local function followTerrain(tpos, dt)
  if rigMode ~= 'world' then return tpos end
  local active = (sample.mode == MODE_WALK) or cfg.terrainAlways
  if active ~= terrainWas then groundPrev = nil end
  terrainWas = active
  if not active then return tpos end
  local g = groundAt(tpos.x, tpos.y, groundPrev or (tpos.z - 0.3), groundPrev and 2.0 or 0.6)
  groundRaw = g
  if not g then return tpos end
  local gF = groundPrev and (groundPrev + (g - groundPrev) * rig.smoothK(dt, 0.08)) or g
  if groundPrev and gF ~= groundPrev then
    local dz = v(0, 0, gF - groundPrev)
    anchor.pos, tpos = rig.add(anchor.pos, dz), rig.add(tpos, dz)
  end
  groundPrev = gF
  if tpos.z < g + MIN_CLEARANCE then
    local lift = v(0, 0, g + MIN_CLEARANCE - tpos.z)
    anchor.pos, tpos = rig.add(anchor.pos, lift), rig.add(tpos, lift)
  end
  return tpos
end

local function setRes(data, pos, look, up, fov)
  data.res.pos:set(pos.x, pos.y, pos.z)
  data.res.targetPos:set(pos.x + look.x * 5, pos.y + look.y * 5, pos.z + look.z * 5)
  data.res.rot = quatFromDir(vec3(look.x, look.y, look.z), vec3(up.x, up.y, up.z))
  data.res.fov = fov
  lastWorld = { pos = pos, look = look, up = up, fov = fov }
end

-- Called by the camera mode every frame while it is the active global camera. Returning false hands the view
-- back to the game (core/cameraUpdate.lua clears the global camera).
function M.cameraUpdate(data)
  if not engaged then return false end
  local dt = math.max(1e-4, data.dtReal or data.dt or 1 / 60)
  fps = fps + (1 / dt - fps) * 0.05

  local F, mode, tveh, tvid = currentRig(data)
  if rigMode ~= nil and mode ~= rigMode then
    -- follow-car switched on or off while filming: carry the view over unchanged
    if cur then
      cur.pos = rig.fromWorld(F, rig.toWorld(rigF, cur.pos))
      cur.look = rig.fromWorldDir(F, rig.toWorldDir(rigF, cur.look))
      cur.up = rig.fromWorldDir(F, rig.toWorldDir(rigF, cur.up))
    end
    needAnchor, anchor0, groundPrev = true, nil, nil
  end
  rigF, rigMode = F, mode

  -- a camera mode or another car was picked on the phone: start from that mount (the phone moves on from there)
  if pendingMount and sample then
    local wp, wl, wu = mountWorld(pendingMount, tveh, tvid, F)
    if wp then
      local l, u = rig.orthonormal(rig.fromWorldDir(F, wl), rig.fromWorldDir(F, wu))
      cur = { pos = rig.fromWorld(F, wp), look = l, up = u }
      needAnchor, anchor0, groundPrev = true, nil, nil
    end
    pendingMount = nil
  end

  if not sample then -- nothing from the phone yet: keep the game's view
    setRes(data, orig.pos, orig.look, orig.up, orig.fov)
    writeView(false, orig.fov)
    return true
  end
  if needAnchor then buildAnchor() end

  -- walk mode: the phone's shutter becomes a joystick that moves the whole rig, easing in and out
  local target = v(0, 0, 0)
  if sample.mode == MODE_WALK and not frozen and cur and heartbeatAge < STICK_TIMEOUT then
    target = rig.walkVelocity(cur.look, cur.up, sample.joyX, sample.joyY, cfg.walkSpeed)
  end
  if frozen or not cur then
    walkVel = v(0, 0, 0)
  else
    walkVel = rig.approach(walkVel, target, dt, cfg.walkSmoothing, cfg.walkSmoothing * 0.7)
  end
  local speed = rig.len(walkVel)
  walking = speed > 0.05
  if speed > 0 then
    anchor.pos = rig.add(anchor.pos, rig.scale(walkVel, dt))
    bobPhase = (bobPhase + 2 * math.pi * rig.cadence(speed) * dt) % (4 * math.pi)
  end

  -- the phone's thumbsticks (seated filming): lift moves the rig up and down, look turns it about the camera
  if sticks and sticksAge < STICK_TIMEOUT and not frozen then
    local k = sticks.speed > 0 and sticks.speed or 1
    if math.abs(sticks.lift) > 0.05 then anchor.pos = rig.add(anchor.pos, v(0, 0, sticks.lift * 1.5 * k * dt)) end
    if math.abs(sticks.lookX) > 0.05 then
      local camPos = rig.toRig(anchor, sample)
      local a, f = -sticks.lookX * math.rad(90) * k * dt, anchor.fwd
      anchor.fwd = rig.normalize(v(f.x * math.cos(a) - f.y * math.sin(a), f.x * math.sin(a) + f.y * math.cos(a), 0), f)
      anchor.pos = rig.sub(camPos, rig.dir(anchor, sample.p)) -- turn about the camera, not the rig's origin
    end
  end

  local tpos, tlook, tup = rig.toRig(anchor, sample)
  tpos = followTerrain(tpos, dt)

  local targetFov = cfg.fov
  if cfg.zoomFromPhone then
    if (sample.vfAspect or 0) > 0 then
      targetFov = rig.fovForView(24 * sample.zoom, sample.vfAspect, sample.portrait, gameAspect())
    else
      targetFov = rig.fovForZoom(cfg.fov, sample.zoom)
    end
  end
  curFov = curFov and (curFov + (targetFov - curFov) * rig.smoothK(dt, 0.12)) or orig.fov

  if not cur then
    cur = { pos = tpos, look = tlook, up = tup }
  elseif not frozen then
    local tau = cfg.smoothing
    if tempSmoothTime > 0 then
      tempSmoothTime = tempSmoothTime - dt
      tau = math.max(tau, tempSmooth)
    end
    effectiveSmoothing = tau
    local k = rig.smoothK(dt, tau)
    cur.pos = rig.lerp(cur.pos, tpos, k)
    cur.look, cur.up = rig.orthonormal(rig.lerp(cur.look, tlook, k), rig.lerp(cur.up, tup, k))
  end

  -- head bob while walking: added to what is shown, never to the rig
  local showPos, showUp = cur.pos, cur.up
  if speed > 0 and cfg.headBob > 0 then
    local up, side, roll = rig.headBob(bobPhase, speed, cfg.headBob)
    local right = rig.rightOf(rig.headingOf(cur.look, cur.up))
    showPos = rig.add(cur.pos, rig.add(v(0, 0, up), rig.scale(right, side)))
    showUp = rig.normalize(rig.add(rig.scale(cur.up, math.cos(roll)), rig.scale(rig.cross(cur.look, cur.up), math.sin(roll))), cur.up)
  end

  local wPos, wLook, wUp = rig.toWorld(rigF, showPos), rig.toWorldDir(rigF, cur.look), rig.toWorldDir(rigF, showUp)
  local warp = { rig.viewRotation(tlook, tup, cur.look, showUp) }
  if camMode == 'lockon' and tveh and tveh ~= 0 then -- the phone moves the camera, the car stays in frame
    local tx, ty, tz = tveh:getPositionXYZ()
    local to = rig.normalize(v(tx - wPos.x, ty - wPos.y, tz + 0.5 - wPos.z), wLook)
    lockLook = lockLook and rig.normalize(rig.lerp(lockLook, to, rig.smoothK(dt, 0.12)), to) or to
    wLook, wUp = rig.orthonormal(lockLook, rig.Z)
    warp = nil -- the view no longer follows the phone's rotation: no timewarp rotation to report
  end
  setRes(data, wPos, wLook, wUp, curFov)
  appliedCount = appliedCount + 1
  writeView(not frozen and sample.state == 1, curFov, sample.heartbeat, warp)
  return true
end

-- ------------------------------------------------------------------ control
function M.engage()
  if engaged then return end
  if not (core_camera and core_camera.getGlobalCameras and core_camera.getGlobalCameras()[CAM_NAME]) then
    errorText = 'camera mode missing: restart the game with the mod enabled'
    message('TSS Handcam: ' .. errorText)
    return
  end
  prevGlobal = core_camera.getActiveGlobalCameraName()
  orig = {
    pos = t3(core_camera.getPosition()), look = t3(core_camera.getForward()), up = t3(core_camera.getUp()),
    fov = core_camera.getFovDeg() or cfg.fov,
  }
  if not (orig.fov > 1) then orig.fov = cfg.fov end
  rigF, rigMode, anchor, anchor0, needAnchor = rig.WORLD, nil, nil, nil, true
  cur, curFov, frozen = nil, nil, false
  walkVel, bobPhase, groundPrev = v(0, 0, 0), 0, nil
  errorText = ''
  engaged = true
  core_camera.setGlobalCameraByName(CAM_NAME)
  if core_camera.getActiveGlobalCameraName() ~= CAM_NAME then
    engaged = false
    errorText = 'the game refused the camera'
    message('TSS Handcam: ' .. errorText)
    return
  end
  message(receiverUp() and 'TSS Handcam: filming' or 'TSS Handcam: filming (start the AcHandCam receiver and the phone app)')
end

function M.release()
  if not engaged then return end
  engaged = false
  M.restoreUi()
  local last = lastWorld
  if prevGlobal then
    core_camera.setGlobalCameraByName(prevGlobal)
  else
    core_camera.setGlobalCameraByName(nil)
    if core_camera.getActiveGlobalCameraName() == CAM_NAME then core_camera.setGlobalCameraByName('free') prevGlobal = 'free' end
  end
  if prevGlobal == 'free' and last then -- the free camera carries on from where the phone left it
    core_camera.globalCameraFunction('free', 'setPosition', vec3(last.pos.x, last.pos.y, last.pos.z))
    core_camera.globalCameraFunction('free', 'setRotation', quatFromDir(vec3(last.look.x, last.look.y, last.look.z), vec3(last.up.x, last.up.y, last.up.z)))
  elseif cfg.blendBack > 0 then
    core_camera.startCameraTransition(cfg.blendBack)
  end
  message('TSS Handcam: camera back to the game')
end

function M.toggle() if engaged then M.release() else M.engage() end end

-- film clean: filming with the HUD and every ImGui window hidden (the game's own Alt+U, ui/visibility.lua), and
-- everything back when filming stops. Only what we hid comes back: if the UI was already off, it stays off.
function M.toggleClean()
  if engaged then
    M.release()
  else
    M.engage()
    if engaged and ui_visibility and ui_visibility.get() then
      ui_visibility.set(false)
      hidUi = true
    end
  end
end
function M.restoreUi() -- called whenever filming stops
  if hidUi and ui_visibility then ui_visibility.set(true) end
  hidUi = false
end
function M.recenter() recenterRequests = (recenterRequests + 1) % 4294967296 end -- the receiver recenters, we re-anchor
function M.freeze()
  frozen = not frozen
  if not frozen then needAnchor = true end
end
function M.backToStart()
  if anchor0 and anchor0.mode == rigMode then
    anchor = { pos = anchor0.pos, fwd = anchor0.fwd }
    groundPrev = nil
    tempSmooth, tempSmoothTime = 0.35, 1.5 -- glide back instead of cutting
  end
end
function M.toggleFollow() cfg.followCar = not cfg.followCar changed() end
function M.toggleWindow() windowOpen[0] = not windowOpen[0] end
function M.isEngaged() return engaged end
-- one line for checks from outside (MCP): link, receiver, phone and camera state
function M.statusLine()
  return string.format('socket=%s bridge=%s receiver=%s phone=%s zoom=%s engaged=%s fov=%s err=%s', tostring(udp ~= nil),
    tostring(bridgeUp()), tostring(receiverUp()), sample and (STATE_NAMES[sample.state] or tostring(sample.state)) or 'none',
    sample and string.format('%.1f', sample.zoom) or '-', tostring(engaged), curFov and string.format('%.1f', curFov) or '-',
    udpError or errorText)
end

-- --------------------------------------------------------------------- UI
local col = { ok = im.ImVec4(0.35, 0.9, 0.45, 1), warn = im.ImVec4(1, 0.72, 0.2, 1), bad = im.ImVec4(1, 0.38, 0.32, 1), dim = im.ImVec4(0.65, 0.68, 0.72, 1) }
local ptr = {}
local function slider(key, label, lo, hi, fmt)
  ptr[key] = ptr[key] or im.FloatPtr(cfg[key])
  ptr[key][0] = cfg[key]
  if im.SliderFloat(label, ptr[key], lo, hi, fmt) then cfg[key] = ptr[key][0] changed() end
end
local bptr = {}
local function checkbox(key, label)
  bptr[key] = bptr[key] or im.BoolPtr(cfg[key])
  bptr[key][0] = cfg[key]
  if im.Checkbox(label, bptr[key]) then cfg[key] = bptr[key][0] changed() end
end

local function stateLine()
  if not udp then return 'No link: ' .. tostring(udpError), col.bad end
  if not bridgeUp() then return 'Bridge not running: start TSS Handcam\'s bridge/run_bridge.bat', col.bad end
  if heartbeatAge > RECEIVER_TIMEOUT then return 'Receiver not running: start AcHandCam\'s run_receiver.bat', col.bad end
  if not sample then return 'Receiver running: waiting for the phone', col.warn end
  if frozen then return 'Frozen (in game)', col.warn end
  local st = sample.state or 0
  return 'Phone: ' .. (STATE_NAMES[st] or ('state ' .. st)), (st == 1 or st == 6) and col.ok or col.warn
end

local function drawWindow()
  im.SetNextWindowSize(im.ImVec2(430, 520), im.Cond_FirstUseEver)
  if im.Begin('TSS Handcam', windowOpen) then
    local text, c = stateLine()
    im.TextColored(c, '%s', text)
    if sample and sample.rxHz then
      im.TextColored(col.dim, '%s', string.format('%.0f Hz, loss %.1f%%, latency %s   %s %s  zoom %.1fx  FOV %.0f',
        sample.rxHz, sample.loss or 0, (sample.latency and sample.latency >= 0) and string.format('%.0f ms', sample.latency) or 'n/a',
        sample.mode == MODE_WALK and 'WALK' or 'HANDHELD', walking and '(moving)' or '', sample.zoom or 1, curFov or cfg.fov))
    end
    if errorText ~= '' then im.TextColored(col.bad, '%s', errorText) end
    im.Separator()
    if im.Button(engaged and 'Release camera' or 'Film with the phone') then M.toggle() end
    im.SameLine()
    if im.Button('Recenter') then M.recenter() end
    im.SameLine()
    if im.Button(frozen and 'Unfreeze' or 'Freeze') then M.freeze() end
    im.SameLine()
    if im.Button('Back to start') then M.backToStart() end
    im.Separator()
    checkbox('followCar', 'Ride with my car (in-car and chase shots)')
    if cfg.followCar then checkbox('followTilt', '   ...and tilt with it') end
    checkbox('zoomFromPhone', 'Zoom from the phone')
    slider('fov', '1x FOV (older phone apps)', 20, 90, '%.0f deg')
    slider('walkSpeed', 'Walk speed', 0.2, 8, '%.1f m/s')
    slider('walkSmoothing', 'Walk start/stop', 0, 1.5, '%.2f s')
    slider('headBob', 'Head bob', 0, 2, '%.1f')
    checkbox('terrainAlways', 'Stay above the ground when not walking')
    slider('smoothing', 'Extra smoothing', 0, 0.3, '%.3f s')
    slider('blendBack', 'Blend back to the game', 0, 2, '%.2f s')
    im.Separator()
    im.TextColored(col.dim, '%s', 'Keys: Options > Controls > "TSS Handcam" (film, recenter, freeze, window).')
  end
  im.End()
end

-- ------------------------------------------------------------------ hooks
local function onUpdate(dtReal)
  local dt = math.max(1e-4, dtReal or 1 / 60)
  if not udp then
    M._retry = (M._retry or 0) - dt
    if M._retry <= 0 then M._retry = 2 openSocket() end
  end
  updateSample(dt)
  linkTick(dt)
  if engaged and core_camera.getActiveGlobalCameraName() ~= CAM_NAME then
    engaged = false -- another camera was picked: leave the game in it
    M.restoreUi()
    message('TSS Handcam: released (another camera was picked)')
  end
  if not engaged then fps = fps + (1 / dt - fps) * 0.05 end
  writeStatus()
  if dirty then
    dirtyTime = dirtyTime - dt
    if dirtyTime <= 0 then saveSettings() end
  end
  if windowOpen[0] then drawWindow() end
end

local function onExtensionLoaded() loadSettings() end
local function onExtensionUnloaded()
  if engaged then M.release() end
  if dirty then saveSettings() end
  closeSocket()
end

M.onUpdate = onUpdate
M.onExtensionLoaded = onExtensionLoaded
M.onExtensionUnloaded = onExtensionUnloaded

return M
