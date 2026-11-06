-- TSS Cam packets (receiver/tsscam/protocol.py): magic(4) version u8 flags u8 length u16 payload crc32(zlib).
-- Plain Lua + the bit library (LuaJIT `bit` in game, Luau `bit32` in the offline tests). The game side never
-- compresses (localhost has no MTU problem; the receiver compresses for the phone) and never receives compressed.
local M = {}
local bit = bit or bit32
local band, bxor, rshift = bit.band, bit.bxor, bit.rshift
local schar, sbyte = string.char, string.byte

M.HELLO, M.COMMAND, M.ANSWER, M.CAPS, M.STICKS = 'TSCH', 'TSCM', 'TSCA', 'TSCP', 'TSCS'
local MAGICS = { TSCH = true, TSCM = true, TSCA = true, TSCP = true, TSCS = true }
local VERSION = 1

local crcTable = {}
for i = 0, 255 do
  local c = i
  for _ = 1, 8 do
    if band(c, 1) == 1 then c = bxor(0xEDB88320, rshift(c, 1)) else c = rshift(c, 1) end
  end
  crcTable[i] = c
end

function M.crc32(s)
  local c = 0xFFFFFFFF
  for i = 1, #s do
    c = bxor(crcTable[band(bxor(c, sbyte(s, i)), 255)], rshift(c, 8))
  end
  return bxor(c, 0xFFFFFFFF)
end

local function u16(x) return schar(band(x, 255), band(rshift(x, 8), 255)) end
local function u32(x) return schar(band(x, 255), band(rshift(x, 8), 255), band(rshift(x, 16), 255), band(rshift(x, 24), 255)) end
M.u32 = u32

function M.encode(magic, payload)
  assert(MAGICS[magic], 'unknown magic')
  local body = magic .. schar(VERSION, 0) .. u16(#payload) .. payload
  return body .. u32(M.crc32(body))
end

-- magic, payload, flags; or nil, reason
function M.decode(data)
  if type(data) ~= 'string' or #data < 12 then return nil, 'too short' end
  local magic = data:sub(1, 4)
  if not MAGICS[magic] then return nil, 'not a TSC packet' end
  if sbyte(data, 5) ~= VERSION then return nil, 'unsupported version' end
  local len = sbyte(data, 7) + 256 * sbyte(data, 8)
  if #data ~= 8 + len + 4 then return nil, 'length mismatch' end
  if u32(M.crc32(data:sub(1, -5))) ~= data:sub(-4) then return nil, 'bad CRC' end
  local flags = sbyte(data, 6)
  if band(flags, 1) ~= 0 then return nil, 'compressed payloads are for the phone link only' end
  return magic, data:sub(9, 8 + len), flags
end

-- four little-endian floats (the sticks packet); `toFloats` comes from the caller (ffi in game, string.unpack in Luau)
function M.sticks(payload, toFloats)
  if #payload ~= 16 then return nil end
  local a, b, c, d = toFloats(payload)
  local function ok(x) return x == x and x >= -1.0001 and x <= 1.0001 end
  if not (ok(a) and ok(b) and ok(c) and ok(d)) then return nil end
  return { lift = a, lookX = b, lookY = c, speed = d }
end

return M
