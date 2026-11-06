-- Offline checks for the TSC packet module and the car controls: from tools/, run  ~/.rokit/bin/luau.exe test_control.lua
-- The reference packets come from the Python receiver (receiver/tsscam/protocol.py), so both sides agree byte for byte.
local tsc = require("../mod/tss_handcam/lua/common/tssHandcam/tsc")
local control = require("../mod/tss_handcam/lua/common/tssHandcam/control")

local fails = 0
local function check(ok, what)
  if not ok then fails = fails + 1 print("FAIL " .. what) else print("ok   " .. what) end
end
local function unhex(h) return (h:gsub("..", function(b) return string.char(tonumber(b, 16)) end)) end
local function floats(p) return string.unpack("<ffff", p) end

-- ------------------------------------------------------------------ packets
check(tsc.crc32("abc") == 891568578, "crc32('abc') matches zlib")
local pyHello = unhex("5453434801000d007b22766572223a22302e31227d66703948")
check(tsc.encode("TSCH", '{"ver":"0.1"}') == pyHello, "hello encodes exactly like Python's")
local m, payload = tsc.decode(pyHello)
check(m == "TSCH" and payload == '{"ver":"0.1"}', "decodes Python's hello")
local bad = pyHello:sub(1, 10) .. "X" .. pyHello:sub(12)
check(tsc.decode(bad) == nil, "a damaged packet is refused")
check(tsc.decode("ACHC" .. string.rep("\0", 96)) == nil, "an AcHandCam pose is not a TSC packet")
local m2, p2 = tsc.decode(unhex("54534353010010000000003f000080be000000000000803ff3dc8b56"))
local s = tsc.sticks(p2, floats)
check(m2 == "TSCS" and s and s.lift == 0.5 and s.lookX == -0.25 and s.speed == 1, "sticks from Python")
check(tsc.sticks(string.pack("<ffff", 5, 0, 0, 0), floats) == nil, "out-of-range stick refused")

-- ------------------------------------------------------------------ controls
local vdata = {
  actionsEnabled = { { name = "trunk" }, { name = "door_FR" }, { name = "door_FL" }, { name = "hoodCatch" },
                     { name = "toggle_siren" }, { name = "weird_mod_flap_door" }, { name = "lightbar_value" } },
  inputActions = {
    door_FL = { onDown = "controller.getControllerSafe('door_FL_coupler').toggleGroup()", title = "x.door_FL" },
    door_FR = { onDown = "controller.getControllerSafe('door_FR_coupler').toggleGroup()" },
    trunk = { onDown = "controller.getControllerSafe('trunkCoupler').toggleGroup()" },
    hoodCatch = { onDown = "controller.getControllerSafe('hoodCatchCoupler').toggleGroupConditional({})" },
    toggle_siren = { onDown = "electrics.set_lightbar_signal(2)" },
    weird_mod_flap_door = { onDown = "controller.getControllerSafe('flap').toggleGroup()" },
    lightbar_value = { onChange = "x(VALUE)" },
  },
}
local panels = control.panels(vdata, function(k) return k end)
local ids = {}
for i, p in ipairs(panels) do ids[i] = p.id end
check(table.concat(ids, ",") == "door_FL,door_FR,hoodCatch,trunk,weird_mod_flap_door", "panels in order: " .. table.concat(ids, ","))
check(panels[1].label == "Front left door", "vanilla panel label")
check(panels[5].label == "Weird mod flap door", "mod panel gets a readable label: " .. panels[5].label)

local list = control.controls(vdata, nil, { lights = 2, hazard = 1 })
local byId = {}
for _, c in ipairs(list) do byId[c.id] = c end
check(byId["panel:door_FL"] and byId["panel:door_FL"].group == "doors", "panel control in the doors group")
check(byId.lights.value == 2 and byId.hazard.value == 1, "state values filled in")
check(byId.horn.kind == "hold", "horn is a hold button")
check(control.FIXED[1].value == nil, "the shared fixed list is not modified")

check(control.luaFor("panel:door_FL", nil, vdata) == vdata.inputActions.door_FL.onDown, "door runs the game's own binding")
check(control.luaFor("panel:door_RR", nil, vdata) == nil, "a door the car doesn't have is refused")
check(control.luaFor("lights", { to = 1 }) == "electrics.setLightsState(1)", "lights to low")
check(control.luaFor("lights", {}) == "electrics.toggle_lights()", "lights cycle")
check(control.luaFor("horn", { down = false }) == "electrics.horn(false)", "horn release")
check(control.luaFor("engine", {}):find("^if ") ~= nil, "engine toggle is an if/else, not and/or")
check(control.luaFor("nope") == nil, "unknown control refused")
check(control.STATE_LUA:find("tssHandcam.onCarState", 1, true) ~= nil, "state report calls back into GE")

print(fails == 0 and "ALL OK" or (fails .. " FAILED"))
