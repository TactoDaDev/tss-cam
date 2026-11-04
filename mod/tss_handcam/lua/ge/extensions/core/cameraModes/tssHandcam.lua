-- TSS Handcam's camera: a global camera mode (core/camera.lua builds one of every file in this folder; isGlobal
-- makes it a camera that exists without a vehicle, like "free"). core/cameraUpdate.lua calls update() right
-- before rendering; the tssHandcam extension does the work. Returning false gives the view back to the game.
local C = {}
C.__index = C

function C:init()
  self.isGlobal = true
  self.hidden = true -- not in the camera cycle; switched on from the tssHandcam extension
end

function C:update(data)
  if not tssHandcam then return false end
  local ok, res = pcall(tssHandcam.cameraUpdate, data)
  if not ok then
    log('E', 'tssHandcam', tostring(res))
    return false
  end
  return res
end

return function(...)
  local o = ... or {}
  setmetatable(o, C)
  o:init()
  return o
end
