-- Car controls for the phone: which buttons a car has, and the vehicle Lua each one runs.
--
-- Every command string is what the game's own bindings run (docs/research/beamng-apis.md, with the source lines):
-- lua/vehicle/electrics.lua (lights, hazards, signals, fog, light bar, horn, ignition), lua/vehicle/input.lua
-- (parking brake), lua/vehicle/controller/advancedCouplerControl.lua (doors, hood, trunk: one controller per panel).
-- A car's openable panels come from the car itself: vdata.actionsEnabled lists the actions its parts enable and
-- vdata.inputActions[name].onDown is the exact Lua the game's key binding runs (core/input/bindings.lua:525-529),
-- so mod cars with their own *.interaction.json work too.
local M = {}

-- panels first: the names vanilla's interaction json uses (common_default.interaction.json)
local PANEL_LABELS = {
  door_FL = 'Front left door', door_FR = 'Front right door', door_RL = 'Rear left door', door_RR = 'Rear right door',
  door_L = 'Left door', door_R = 'Right door', hoodCatch = 'Hood', hoodRelease = 'Hood release',
  trunk = 'Trunk', tailgate = 'Tailgate', tailgate_upper = 'Upper tailgate', tailgate_lower = 'Lower tailgate',
  hatch = 'Hatch', decklid = 'Decklid',
}
local PANEL_ORDER = { 'door_FL', 'door_FR', 'door_RL', 'door_RR', 'door_L', 'door_R', 'hoodCatch', 'hoodRelease',
  'trunk', 'tailgate', 'tailgate_upper', 'tailgate_lower', 'hatch', 'decklid' }
local function isPanel(name)
  local n = name:lower()
  return n:find('door') or n:find('hood') or n:find('trunk') or n:find('tailgate') or n:find('hatch')
      or n:find('decklid') or n:find('spare') or n:find('bonnet') or n:find('boot')
end

-- fixed controls every car gets (state keys match what readState reports)
M.FIXED = {
  { id = 'lights', label = 'Lights', group = 'lights', kind = 'cycle', state = 'lights', states = { 'Off', 'Low', 'High' } },
  { id = 'highbeam', label = 'High beams', group = 'lights', kind = 'toggle' },
  { id = 'hazard', label = 'Hazards', group = 'lights', kind = 'toggle', state = 'hazard' },
  { id = 'signal_left', label = 'Left signal', group = 'lights', kind = 'toggle', state = 'signalL' },
  { id = 'signal_right', label = 'Right signal', group = 'lights', kind = 'toggle', state = 'signalR' },
  { id = 'fog', label = 'Fog lights', group = 'lights', kind = 'toggle', state = 'fog' },
  { id = 'lightbar', label = 'Light bar', group = 'lights', kind = 'cycle', state = 'lightbar', states = { 'Off', 'Lights', 'Siren' } },
  { id = 'horn', label = 'Horn', group = 'horn', kind = 'hold' },
  { id = 'engine', label = 'Engine', group = 'car', kind = 'toggle', state = 'engine' },
  { id = 'parkbrake', label = 'Parking brake', group = 'car', kind = 'toggle', state = 'parkbrake' },
  { id = 'open_all', label = 'Open everything', group = 'doors', kind = 'press' },
  { id = 'close_all', label = 'Close everything', group = 'doors', kind = 'press' },
}

local FIXED_LUA = {
  highbeam = 'electrics.toggle_highbeams()',
  hazard = 'electrics.toggle_warn_signal()',
  signal_left = 'electrics.toggle_left_signal()',
  signal_right = 'electrics.toggle_right_signal()',
  fog = 'electrics.toggle_fog_lights()',
  lightbar = 'electrics.toggle_lightbar_signal()',
  parkbrake = "input.toggleEvent('parkingbrake')",
  open_all = "for _,c in pairs(controller.getControllersByType('advancedCouplerControl')) do c.detachGroup() end",
  close_all = "for _,c in pairs(controller.getControllersByType('advancedCouplerControl')) do c.tryAttachGroupImpulse() end",
}

-- The vehicle reports its state back to GE (pattern: lua/vehicle/extensions/gameplayInterface.lua:30)
M.STATE_LUA = "local e=electrics.values obj:queueGameEngineLua(string.format('tssHandcam.onCarState(%d,%d,%d,%d,%d,%d,%d,%d,%d)',"
  .. "obj:getId(),tonumber(e.lights_state) or 0,tonumber(e.hazard_enabled) or 0,tonumber(e.signal_left_input) or 0,"
  .. "tonumber(e.signal_right_input) or 0,tonumber(e.fog) or 0,tonumber(e.lightbar) or 0,"
  .. "(tonumber(e.parkingbrake) or 0)>0.5 and 1 or 0,(tonumber(e.engineRunning) or 0)>0.5 and 1 or 0))"

local function prettify(name)
  local s = name:gsub('_', ' '):gsub('(%l)(%u)', '%1 %2')
  return s:sub(1, 1):upper() .. s:sub(2)
end

-- The car's own openable panels, in a stable order: { {id, label}, ... }
function M.panels(vdata, translate)
  local found, list = {}, {}
  if not (vdata and vdata.actionsEnabled and vdata.inputActions) then return list end
  for _, a in pairs(vdata.actionsEnabled) do
    local name = type(a) == 'table' and a.name or a
    local act = name and vdata.inputActions[name]
    if type(name) == 'string' and act and type(act.onDown) == 'string' and not act.onChange and isPanel(name) then
      found[name] = act
    end
  end
  local seen = {}
  local function add(name)
    if found[name] and not seen[name] then
      seen[name] = true
      local label = PANEL_LABELS[name]
      if not label and translate and found[name].title then label = translate(found[name].title) end
      if not label or label == '' or label == found[name].title then label = prettify(name) end
      list[#list + 1] = { id = name, label = label }
    end
  end
  for _, n in ipairs(PANEL_ORDER) do add(n) end
  local rest = {}
  for n in pairs(found) do if not seen[n] then rest[#rest + 1] = n end end
  table.sort(rest)
  for _, n in ipairs(rest) do add(n) end
  return list
end

-- The phone's button list for this car (state values filled in from the last report, if any)
function M.controls(vdata, translate, state)
  local out = {}
  for _, p in ipairs(M.panels(vdata, translate)) do
    out[#out + 1] = { id = 'panel:' .. p.id, label = p.label, group = 'doors', kind = 'toggle' }
  end
  for _, c in ipairs(M.FIXED) do
    local e = {}
    for k, val in pairs(c) do e[k] = val end
    if c.state and state then e.value = state[c.state] end
    out[#out + 1] = e
  end
  return out
end

-- The vehicle Lua for a command, or nil and why. msg = {cmd = 'car', args = {id = <control id>, down = bool, to = ...}}
function M.luaFor(id, args, vdata)
  args = args or {}
  if type(id) ~= 'string' then return nil, 'no control id' end
  if id:sub(1, 6) == 'panel:' then
    local name = id:sub(7)
    for _, p in ipairs(M.panels(vdata)) do
      if p.id == name then return vdata.inputActions[name].onDown end
    end
    return nil, 'this car has no ' .. name
  end
  if id == 'lights' then
    local to = tonumber(args.to)
    if to and to >= 0 and to <= 2 then return string.format('electrics.setLightsState(%d)', math.floor(to)) end
    return 'electrics.toggle_lights()'
  elseif id == 'horn' then
    return args.down == false and 'electrics.horn(false)' or 'electrics.horn(true)'
  elseif id == 'engine' then
    if args.to == 'stop' then return 'electrics.setIgnitionLevel(0)' end
    if args.to == 'start' then return 'electrics.setIgnitionLevel(3)' end
    return 'if (tonumber(electrics.values.engineRunning) or 0)>0.5 then electrics.setIgnitionLevel(0) else electrics.setIgnitionLevel(3) end'
  end
  local lua = FIXED_LUA[id]
  if lua then return lua end
  return nil, 'unknown control ' .. id
end

return M
