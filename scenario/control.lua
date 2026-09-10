-- Freeplay's own script. Keep whatever your scenario already had on this line;
-- for a save made with the default scenario this is exactly right.
require('__base__/script/freeplay/control.lua')

-- ===========================================================================
-- Everything below this line is Chartorio. To add it to a different scenario,
-- paste this section at the end of that scenario's control.lua instead.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- chartorio: live map data for the browser front end.
--
-- The bridge process calls the commands below over RCON. They are custom
-- commands rather than `/silent-command`, so the save is never flagged as
-- having used cheat commands. This script lives inside the save itself, so
-- joining clients receive it with the map and need nothing installed.
--
--   /chartorio            live player and train positions
--   /chartorio_palette    colour table for every tile and entity seen so far
--   /chartorio_chunks     charted chunks with their revision numbers
--   /chartorio_chunk X Y  one chunk rastered to palette indices, run length coded
--   /chartorio_dirty      chunks changed since the previous call
--   /chartorio_units      biters in view, only where a chunk is currently seen
--   /chartorio_signals    rail signals in view, with the state the game shows
--   /chartorio_pollution  one pollution value per charted chunk in view
--   /chartorio_alerts     recent losses on the player force
--   /chartorio_tags       map tags placed in game
--   /chartorio_resource   the total of the ore patch under a point
--   /chartorio_events     which change events this build registered
--
-- tests/test_contract.py checks this list against what the bridge calls: an
-- unknown command comes back as a failed call and simply leaves a layer blank,
-- which is indistinguishable from a world with nothing to report.
-- ---------------------------------------------------------------------------

local SUBPIXELS = 2 -- raster cells per map tile, so 64x64 cells per chunk
local CHUNK_SIZE = 32

local train_state_names = {}
for name, value in pairs(defines.train_state) do
  train_state_names[value] = name
end

local function initialise()
  -- Carry over state from when this script was still called webmap, so the
  -- palette and the per chunk revisions survive the rename.
  if storage.webmap and not storage.chartorio then
    storage.chartorio = storage.webmap
    storage.webmap = nil
  end
  storage.chartorio = storage.chartorio or {}
  storage.chartorio.palette = storage.chartorio.palette or {}
  storage.chartorio.palette_order = storage.chartorio.palette_order or {}
  storage.chartorio.revisions = storage.chartorio.revisions or {}
  storage.chartorio.dirty = storage.chartorio.dirty or {}
  storage.chartorio.alerts = storage.chartorio.alerts or {}
  storage.chartorio.signals = storage.chartorio.signals or {}
  storage.chartorio.signal_scanned = storage.chartorio.signal_scanned or {}
  storage.chartorio.alert_serial = storage.chartorio.alert_serial or 0
end

script.on_init(initialise)
script.on_configuration_changed(initialise)

-- Colours arrive as floats in 0..1 or bytes in 0..255 depending on how the
-- prototype declared them; normalise to bytes here so the bridge cannot guess wrong.
local function to_bytes(color)
  if not color then return nil end
  local r, g, b = color.r or 0, color.g or 0, color.b or 0
  local scale = (r <= 1 and g <= 1 and b <= 1) and 255 or 1
  return {
    math.floor(math.min(255, r * scale) + 0.5),
    math.floor(math.min(255, g * scale) + 0.5),
    math.floor(math.min(255, b * scale) + 0.5),
  }
end

-- Palette indices are handed out on first sight and kept in the save, so the
-- bridge only has to fetch the colour table when it grows.
local function palette_index(key, color)
  local chartorio = storage.chartorio
  local existing = chartorio.palette[key]
  if existing then return existing end
  if not color then return 0 end
  local index = #chartorio.palette_order + 1
  chartorio.palette[key] = index
  chartorio.palette_order[index] = {key = key, color = color}
  return index
end

local function tile_index(name)
  local key = "t:" .. name
  if storage.chartorio.palette[key] then return storage.chartorio.palette[key] end
  local prototype = prototypes.tile[name]
  return palette_index(key, prototype and to_bytes(prototype.map_color) or nil)
end

-- The in-game map paints hostile buildings with the enemy colour, so a captured
-- or friendly copy of the same prototype has to land on a different palette entry.
local function entity_index(prototype, hostile)
  local key = (hostile and "x:" or "e:") .. prototype.name
  if storage.chartorio.palette[key] then return storage.chartorio.palette[key] end
  local color
  if hostile then
    color = to_bytes(prototype.enemy_map_color) or to_bytes(prototype.map_color) or {214, 63, 63}
  else
    color = to_bytes(prototype.map_color) or to_bytes(prototype.friendly_map_color)
  end
  return palette_index(key, color)
end

local function is_hostile(entity)
  local force = entity.force
  return force ~= nil and force.name == "enemy"
end

local function chunk_key(surface_name, x, y)
  return surface_name .. ":" .. x .. ":" .. y
end

local function touch_chunk(surface_name, x, y)
  local key = chunk_key(surface_name, x, y)
  storage.chartorio.revisions[key] = (storage.chartorio.revisions[key] or 0) + 1
  storage.chartorio.dirty[key] = {surface = surface_name, x = x, y = y, revision = storage.chartorio.revisions[key]}
end

local function touch_position(surface, position)
  touch_chunk(surface.name, math.floor(position.x / CHUNK_SIZE), math.floor(position.y / CHUNK_SIZE))
end

-- ---------------------------------------------------------------------------
-- Change tracking: only chunks that actually changed get re-rendered.
-- ---------------------------------------------------------------------------

-- Defined further down, once the signal registry exists.
local remember_signal_hook

local function on_entity_changed(event)
  local entity = event.entity or event.destination
  if entity and entity.valid then
    touch_position(entity.surface, entity.position)
    if remember_signal_hook then remember_signal_hook(entity) end
  end
end

-- Looked up by name: a define missing from this build must not leave a nil
-- hole in a table literal, which would silently drop the entries behind it.
local function register(names, handler)
  for _, name in ipairs(names) do
    local event = defines.events[name]
    if event then script.on_event(event, handler) end
  end
end

register({
  "on_built_entity",
  "on_robot_built_entity",
  "on_space_platform_built_entity",
  "script_raised_built",
  "script_raised_revive",
  "on_player_mined_entity",
  "on_robot_mined_entity",
  "on_space_platform_mined_entity",
  "script_raised_destroy",
  "on_biter_base_built",
  "on_entity_spawned",
}, on_entity_changed)

local function on_tiles_changed(event)
  local surface = event.surface or (event.robot and event.robot.surface)
  if not surface or not event.tiles then return end
  for _, tile in pairs(event.tiles) do
    touch_position(surface, tile.position)
  end
end

-- on_entity_died feeds both the tile refresh and the alert feed. Registering
-- it twice would silently replace the first handler, so it is combined here.
local ALERT_LIMIT = 60

script.on_event(defines.events.on_entity_died, function(event)
  on_entity_changed(event)

  local entity = event.entity
  if not entity or not entity.valid then return end
  if entity.force.name ~= "player" then return end
  if entity.type == "character" and not entity.player then return end

  initialise()
  local alerts = storage.chartorio.alerts
  storage.chartorio.alert_serial = storage.chartorio.alert_serial + 1
  local position = entity.position
  alerts[#alerts + 1] = {
    id = storage.chartorio.alert_serial,
    tick = game.tick,
    surface = entity.surface.name,
    x = position.x,
    y = position.y,
    name = entity.name,
    cause = event.cause and event.cause.name or nil,
  }
  while #alerts > ALERT_LIMIT do table.remove(alerts, 1) end
end)

register({
  "on_player_built_tile",
  "on_robot_built_tile",
  "on_player_mined_tile",
  "on_robot_mined_tile",
  "on_space_platform_built_tile",
  "on_space_platform_mined_tile",
  "script_raised_set_tiles",
}, on_tiles_changed)

script.on_event(defines.events.on_chunk_charted, function(event)
  touch_chunk(game.surfaces[event.surface_index].name, event.position.x, event.position.y)
end)

script.on_event(defines.events.on_chunk_generated, function(event)
  touch_chunk(event.surface.name, event.position.x, event.position.y)
end)

-- ---------------------------------------------------------------------------
-- Rastering
-- ---------------------------------------------------------------------------

-- A rail's bounding box is an axis aligned square, so filling it throws the
-- rail's direction away and diagonals come out as blocky staircases. Paint the
-- rail along its own direction instead, one tile wide, the way the in-game map
-- draws track.
local RAIL_TYPES = {
  ["straight-rail"] = true,
  ["half-diagonal-rail"] = true,
  ["curved-rail-a"] = true,
  ["curved-rail-b"] = true,
  ["elevated-straight-rail"] = true,
  ["elevated-half-diagonal-rail"] = true,
  ["elevated-curved-rail-a"] = true,
  ["elevated-curved-rail-b"] = true,
  ["legacy-straight-rail"] = true,
  ["legacy-curved-rail"] = true,
  ["rail-ramp"] = true,
}

local function direction_vector(direction)
  -- Factorio 2.0 counts sixteen directions, so one step is a sixteenth turn.
  local angle = (direction or 0) * math.pi / 8
  return math.sin(angle), -math.cos(angle)
end

-- The ends of a rail, in world coordinates, with the direction of travel there.
-- Guarded: an uncaught error inside a command takes the server down with it.
local function rail_ends(entity)
  local ok, ends = pcall(function()
    local front = entity.get_rail_end(defines.rail_direction.front).location
    local back = entity.get_rail_end(defines.rail_direction.back).location
    return {
      {x = front.position.x, y = front.position.y, direction = front.direction},
      {x = back.position.x, y = back.position.y, direction = back.direction},
    }
  end)
  if ok and ends and ends[1] and ends[2] then return ends end
  return nil
end

local function stamp(cells, size, origin_x, origin_y, world_x, world_y, index)
  -- Two by two, so consecutive steps overlap and a diagonal run stays solid
  -- instead of touching only at its corners.
  local cell_x = math.floor((world_x - origin_x) * SUBPIXELS)
  local cell_y = math.floor((world_y - origin_y) * SUBPIXELS)
  for offset_y = 0, 1 do
    for offset_x = 0, 1 do
      local x, y = cell_x + offset_x, cell_y + offset_y
      if x >= 0 and y >= 0 and x < size and y < size then
        cells[y * size + x + 1] = index
      end
    end
  end
end

-- Draw the track between its real ends. Straight rails come out straight
-- because their tangents are parallel; a curve bends through the point where
-- its two end tangents meet, which is the arc's control point.
local function paint_rail(cells, size, origin_x, origin_y, entity, index)
  local ends = rail_ends(entity)
  if not ends then
    -- Older or unexpected rail types: fall back to a line through the centre.
    local step_x, step_y = direction_vector(entity.direction)
    local box = entity.selection_box or entity.bounding_box
    local length = math.max(
      box.right_bottom.x - box.left_top.x,
      box.right_bottom.y - box.left_top.y)
    if length <= 0 then length = 2 end
    local position = entity.position
    local samples = math.ceil(length * SUBPIXELS * 2)
    for sample = 0, samples do
      local along = -length / 2 + length * sample / samples
      stamp(cells, size, origin_x, origin_y,
            position.x + step_x * along, position.y + step_y * along, index)
    end
    return
  end

  local ax, ay = ends[1].x, ends[1].y
  local bx, by = ends[2].x, ends[2].y
  local a_dx, a_dy = direction_vector(ends[1].direction)
  local b_dx, b_dy = direction_vector(ends[2].direction)

  local control_x, control_y = (ax + bx) / 2, (ay + by) / 2
  local denominator = a_dx * b_dy - a_dy * b_dx
  if math.abs(denominator) > 0.0001 then
    local along = ((bx - ax) * b_dy - (by - ay) * b_dx) / denominator
    local candidate_x, candidate_y = ax + a_dx * along, ay + a_dy * along
    -- Near parallel tangents put the meeting point far away; keep the chord.
    local span = math.max(math.abs(bx - ax), math.abs(by - ay)) + 2
    if math.abs(candidate_x - ax) <= span * 2 and math.abs(candidate_y - ay) <= span * 2 then
      control_x, control_y = candidate_x, candidate_y
    end
  end

  local chord = math.max(math.abs(bx - ax), math.abs(by - ay))
  local samples = math.max(4, math.ceil(chord * SUBPIXELS * 3))
  for sample = 0, samples do
    local t = sample / samples
    local inverse = 1 - t
    local x = inverse * inverse * ax + 2 * inverse * t * control_x + t * t * bx
    local y = inverse * inverse * ay + 2 * inverse * t * control_y + t * t * by
    stamp(cells, size, origin_x, origin_y, x, y, index)
  end
end


local function render_chunk(surface, chunk_x, chunk_y)
  local origin_x, origin_y = chunk_x * CHUNK_SIZE, chunk_y * CHUNK_SIZE
  local area = {{origin_x, origin_y}, {origin_x + CHUNK_SIZE, origin_y + CHUNK_SIZE}}
  local size = CHUNK_SIZE * SUBPIXELS
  local cells = {}
  for cell = 1, size * size do cells[cell] = 0 end

  for _, tile in pairs(surface.find_tiles_filtered({area = area})) do
    local index = tile_index(tile.name)
    local base_x = math.floor((tile.position.x - origin_x) * SUBPIXELS)
    local base_y = math.floor((tile.position.y - origin_y) * SUBPIXELS)
    for offset_y = 0, SUBPIXELS - 1 do
      local row = (base_y + offset_y) * size
      for offset_x = 0, SUBPIXELS - 1 do
        cells[row + base_x + offset_x + 1] = index
      end
    end
  end

  for _, entity in pairs(surface.find_entities_filtered({area = area})) do
    if entity.valid and entity.type ~= "character" and entity.type ~= "item-entity" then
      local index = entity_index(entity.prototype, is_hostile(entity))
      if index ~= 0 and RAIL_TYPES[entity.type] then
        paint_rail(cells, size, origin_x, origin_y, entity, index)
      elseif index ~= 0 then
        local box = entity.selection_box or entity.bounding_box
        local left = math.max(0, math.floor((box.left_top.x - origin_x) * SUBPIXELS))
        local top = math.max(0, math.floor((box.left_top.y - origin_y) * SUBPIXELS))
        local right = math.min(size - 1, math.ceil((box.right_bottom.x - origin_x) * SUBPIXELS) - 1)
        local bottom = math.min(size - 1, math.ceil((box.right_bottom.y - origin_y) * SUBPIXELS) - 1)
        for cell_y = top, bottom do
          local row = cell_y * size
          for cell_x = left, right do
            cells[row + cell_x + 1] = index
          end
        end
      end
    end
  end

  -- Run length encode; a chunk is mostly large stretches of one colour.
  local runs = {}
  local current, count = cells[1], 0
  for cell = 1, size * size do
    if cells[cell] == current then
      count = count + 1
    else
      runs[#runs + 1] = count
      runs[#runs + 1] = current
      current, count = cells[cell], 1
    end
  end
  runs[#runs + 1] = count
  runs[#runs + 1] = current
  return runs, size
end

-- ---------------------------------------------------------------------------
-- Visibility
--
-- Charted is not the same as visible. The in-game map keeps showing nests in
-- charted chunks, but only draws moving biters where radar or a player gives
-- current vision. The chunk list changes slowly, so it is cached.
-- ---------------------------------------------------------------------------

local UNIT_LIMIT = 600
local MAX_QUERY_TILES = 512

-- ---------------------------------------------------------------------------
-- Live entities
-- ---------------------------------------------------------------------------

local function get_trains(surface)
  if game.train_manager and game.train_manager.get_trains then
    return game.train_manager.get_trains({surface = surface})
  end
  return surface.get_trains()
end

-- player.position follows whatever the player is controlling, so opening the
-- in-game map and panning moves it with the view. The character's own position
-- is what belongs on a map.
local function physical_position_of(player)
  local ok, position = pcall(function() return player.physical_position end)
  if ok and position then return position end
  if player.character and player.character.valid then return player.character.position end
  return player.position
end

local function collect_players()
  local players = {}
  for _, player in pairs(game.connected_players) do
    local position = physical_position_of(player)
    players[#players + 1] = {
      name = player.name,
      surface = player.surface.name,
      x = position.x,
      y = position.y,
      color = {r = player.color.r, g = player.color.g, b = player.color.b},
      in_vehicle = player.vehicle ~= nil,
      afk_ticks = player.afk_time,
    }
  end
  return players
end

local function collect_trains()
  local trains = {}
  for _, surface in pairs(game.surfaces) do
    for _, train in pairs(get_trains(surface)) do
      local stock = train.front_stock or train.back_stock
      if stock and stock.valid then
        local destination = train.path_end_stop
        trains[#trains + 1] = {
          id = train.id,
          surface = surface.name,
          x = stock.position.x,
          y = stock.position.y,
          orientation = stock.orientation,
          speed = train.speed,
          state = train_state_names[train.state] or "unknown",
          destination = destination and destination.backer_name or nil,
          carriages = #train.carriages,
          passengers = #train.passengers,
        }
      end
    end
  end
  return trains
end

-- ---------------------------------------------------------------------------
-- Commands
-- ---------------------------------------------------------------------------

local function respond(value)
  if rcon then rcon.print(helpers.table_to_json(value)) end
end

commands.add_command("chartorio", "Live player and train positions for Chartorio.", function()
  initialise()
  respond({
    tick = game.tick,
    speed = game.speed,
    surfaces = (function()
      local names = {}
      for _, surface in pairs(game.surfaces) do names[#names + 1] = surface.name end
      return names
    end)(),
    players = collect_players(),
    trains = collect_trains(),
    palette_size = #storage.chartorio.palette_order,
  })
end)

commands.add_command("chartorio_palette", "Colour table for every tile and entity the Chartorio has seen.", function()
  initialise()
  local colors = {}
  for index, entry in pairs(storage.chartorio.palette_order) do
    colors[#colors + 1] = {index = index, key = entry.key, color = entry.color}
  end
  respond({colors = colors})
end)

-- Charted chunks inside a rectangle of chunk coordinates. A world wide scan
-- is not an option: a played save holds tens of thousands of chunks and
-- walking all of them blows straight through the RCON timeout.
local MAX_INDEX_CHUNKS = 6000

commands.add_command("chartorio_chunks", "Charted chunks in view: chartorio_chunks <surface> <cx1> <cy1> <cx2> <cy2>.", function(command)
  initialise()
  local surface_name, x1, y1, x2, y2 = string.match(
    command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then
    return respond({error = "usage: chartorio_chunks <surface> <cx1> <cy1> <cx2> <cy2>"})
  end
  x1, y1, x2, y2 = tonumber(x1), tonumber(y1), tonumber(x2), tonumber(y2)
  if x2 < x1 then x1, x2 = x2, x1 end
  if y2 < y1 then y1, y2 = y2, y1 end

  -- Clamp around the centre rather than refusing, so a zoomed out browser
  -- still gets the middle of what it asked for.
  local clamped = false
  while (x2 - x1 + 1) * (y2 - y1 + 1) > MAX_INDEX_CHUNKS do
    clamped = true
    if (x2 - x1) >= (y2 - y1) then
      x1, x2 = x1 + 1, x2 - 1
    else
      y1, y2 = y1 + 1, y2 - 1
    end
  end

  local seed = 0
  local settings = surface.map_gen_settings
  if settings and settings.seed then seed = settings.seed end

  local force = game.forces.player
  local revisions = storage.chartorio.revisions
  local chunks = {}
  for chunk_y = y1, y2 do
    for chunk_x = x1, x2 do
      if force.is_chunk_charted(surface, {chunk_x, chunk_y}) then
        chunks[#chunks + 1] = chunk_x
        chunks[#chunks + 1] = chunk_y
        chunks[#chunks + 1] = revisions[chunk_key(surface.name, chunk_x, chunk_y)] or 0
      end
    end
  end

  respond({
    surface = surface.name,
    size = CHUNK_SIZE * SUBPIXELS,
    seed = seed,
    clamped = clamped,
    area = {x1, y1, x2, y2},
    chunks = chunks,
  })
end)

commands.add_command("chartorio_chunk", "Raster one chunk: chartorio_chunk <surface> <x> <y>.", function(command)
  initialise()
  local surface_name, chunk_x, chunk_y = string.match(command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then return respond({error = "usage: chartorio_chunk <surface> <x> <y>"}) end
  chunk_x, chunk_y = tonumber(chunk_x), tonumber(chunk_y)
  local runs, size = render_chunk(surface, chunk_x, chunk_y)
  local key = chunk_key(surface.name, chunk_x, chunk_y)
  storage.chartorio.dirty[key] = nil
  respond({
    surface = surface.name,
    x = chunk_x,
    y = chunk_y,
    size = size,
    revision = storage.chartorio.revisions[key] or 0,
    palette_size = #storage.chartorio.palette_order,
    runs = runs,
  })
end)

commands.add_command("chartorio_units", "Visible biters in a viewport: chartorio_units <surface> <x1> <y1> <x2> <y2>.", function(command)
  initialise()
  local surface_name, x1, y1, x2, y2 = string.match(
    command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then
    return respond({error = "usage: chartorio_units <surface> <x1> <y1> <x2> <y2>"})
  end
  x1, y1, x2, y2 = tonumber(x1), tonumber(y1), tonumber(x2), tonumber(y2)

  local center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
  local half = MAX_QUERY_TILES / 2
  local clamped = (x2 - x1) > MAX_QUERY_TILES or (y2 - y1) > MAX_QUERY_TILES
  if clamped then
    x1, x2 = center_x - half, center_x + half
    y1, y2 = center_y - half, center_y + half
  end

  -- Only chunks that are both on screen and currently seen are worth looking
  -- at. Searching the whole viewport instead meant scanning a quarter of a
  -- million tiles for a handful of biters.
  local force = game.forces.player
  local points = {}
  local truncated = false
  local scanned = 0
  for chunk_y = math.floor(y1 / CHUNK_SIZE), math.floor(y2 / CHUNK_SIZE) do
    for chunk_x = math.floor(x1 / CHUNK_SIZE), math.floor(x2 / CHUNK_SIZE) do
      -- Asked per chunk in view. Building a visibility set for the whole
      -- surface meant walking every generated chunk a few times a minute,
      -- which is the same world wide scan that made the map expensive before.
      if force.is_chunk_visible(surface, {chunk_x, chunk_y}) then
        scanned = scanned + 1
        local area = {
          {chunk_x * CHUNK_SIZE, chunk_y * CHUNK_SIZE},
          {chunk_x * CHUNK_SIZE + CHUNK_SIZE, chunk_y * CHUNK_SIZE + CHUNK_SIZE},
        }
        for _, unit in pairs(surface.find_entities_filtered({area = area, type = "unit", force = "enemy"})) do
          if #points >= UNIT_LIMIT * 2 then
            truncated = true
            break
          end
          local position = unit.position
          points[#points + 1] = math.floor(position.x * 4) / 4
          points[#points + 1] = math.floor(position.y * 4) / 4
        end
      end
      if truncated then break end
    end
    if truncated then break end
  end

  respond({
    surface = surface.name,
    units = points,
    truncated = truncated,
    clamped = clamped,
    scanned = scanned,
  })
end)

-- ---------------------------------------------------------------------------
-- Resource patches
--
-- Patches have no API of their own, so walk outward chunk by chunk from the one
-- under the cursor for as long as neighbours hold the same ore. Patches are
-- contiguous, so this covers one patch and stops at its edge.
-- ---------------------------------------------------------------------------

local PATCH_CHUNK_LIMIT = 256

local function patch_summary(surface, resource_name, start_x, start_y)
  local visited = {}
  local queue = {{start_x, start_y}}
  local head = 1
  local total, entity_count, chunks = 0, 0, 0
  local min_x, min_y, max_x, max_y = math.huge, math.huge, -math.huge, -math.huge
  local truncated = false

  while head <= #queue do
    local position = queue[head]
    head = head + 1
    local chunk_x, chunk_y = position[1], position[2]
    local id = chunk_x .. ":" .. chunk_y
    if not visited[id] then
      visited[id] = true
      if chunks >= PATCH_CHUNK_LIMIT then
        truncated = true
        break
      end
      local area = {
        {chunk_x * CHUNK_SIZE, chunk_y * CHUNK_SIZE},
        {chunk_x * CHUNK_SIZE + CHUNK_SIZE, chunk_y * CHUNK_SIZE + CHUNK_SIZE},
      }
      local found = surface.find_entities_filtered({area = area, name = resource_name})
      if #found > 0 then
        chunks = chunks + 1
        entity_count = entity_count + #found
        for _, entity in pairs(found) do
          total = total + entity.amount
          local entity_position = entity.position
          if entity_position.x < min_x then min_x = entity_position.x end
          if entity_position.y < min_y then min_y = entity_position.y end
          if entity_position.x > max_x then max_x = entity_position.x end
          if entity_position.y > max_y then max_y = entity_position.y end
        end
        queue[#queue + 1] = {chunk_x + 1, chunk_y}
        queue[#queue + 1] = {chunk_x - 1, chunk_y}
        queue[#queue + 1] = {chunk_x, chunk_y + 1}
        queue[#queue + 1] = {chunk_x, chunk_y - 1}
      end
    end
  end

  return {
    total = total,
    entities = entity_count,
    chunks = chunks,
    truncated = truncated,
    bounds = entity_count > 0 and {min_x, min_y, max_x, max_y} or nil,
  }
end

commands.add_command("chartorio_resource", "Patch total under a point: chartorio_resource <surface> <x> <y>.", function(command)
  initialise()
  local surface_name, x, y = string.match(command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then
    return respond({error = "usage: chartorio_resource <surface> <x> <y>"})
  end
  x, y = tonumber(x), tonumber(y)

  -- The cursor lands on a tile, not exactly on an entity, so look in its square.
  local here = surface.find_entities_filtered({
    area = {{x, y}, {x + 1, y + 1}},
    type = "resource",
  })
  if #here == 0 then
    return respond({found = false})
  end

  local resource = here[1]
  local prototype = resource.prototype
  local summary = patch_summary(surface, resource.name, math.floor(x / CHUNK_SIZE), math.floor(y / CHUNK_SIZE))

  -- Infinite resources such as crude oil are shown as a yield percentage,
  -- derived from the prototype rather than a hard coded divisor.
  local infinite = prototype.infinite_resource or false
  local normal = prototype.normal_resource_amount
  local percent = nil
  if infinite and normal and normal > 0 then
    percent = summary.total / normal * 100
  end

  respond({
    found = true,
    name = resource.name,
    localised = resource.name,
    infinite = infinite,
    total = summary.total,
    percent = percent,
    entities = summary.entities,
    chunks = summary.chunks,
    truncated = summary.truncated,
    bounds = summary.bounds,
  })
end)

commands.add_command("chartorio_tags", "Map tags placed in game: chartorio_tags <surface>.", function(command)
  initialise()
  local surface = game.surfaces[command.parameter or "nauvis"]
  if not surface then return respond({error = "unknown surface"}) end
  local tags = {}
  for _, tag in pairs(game.forces.player.find_chart_tags(surface)) do
    local position = tag.position
    tags[#tags + 1] = {
      id = tag.tag_number,
      x = position.x,
      y = position.y,
      text = tag.text,
      icon = tag.icon and (tag.icon.type .. "/" .. (tag.icon.name or "")) or nil,
      author = tag.last_user and tag.last_user.name or nil,
    }
  end
  respond({surface = surface.name, tags = tags})
end)

commands.add_command("chartorio_pollution", "Pollution in view: chartorio_pollution <surface> <cx1> <cy1> <cx2> <cy2>.", function(command)
  initialise()
  local surface_name, x1, y1, x2, y2 = string.match(
    command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then
    return respond({error = "usage: chartorio_pollution <surface> <cx1> <cy1> <cx2> <cy2>"})
  end
  x1, y1, x2, y2 = tonumber(x1), tonumber(y1), tonumber(x2), tonumber(y2)
  if x2 < x1 then x1, x2 = x2, x1 end
  if y2 < y1 then y1, y2 = y2, y1 end
  -- Clamped here as well as in the bridge: one value per chunk is cheap, but
  -- an unbounded rectangle is not.
  while (x2 - x1 + 1) * (y2 - y1 + 1) > MAX_INDEX_CHUNKS do
    if (x2 - x1) >= (y2 - y1) then x1, x2 = x1 + 1, x2 - 1 else y1, y2 = y1 + 1, y2 - 1 end
  end

  local force = game.forces.player
  local values = {}
  local peak = 0
  for chunk_y = y1, y2 do
    for chunk_x = x1, x2 do
      if force.is_chunk_charted(surface, {chunk_x, chunk_y}) then
        local amount = surface.get_pollution({chunk_x * CHUNK_SIZE + 16, chunk_y * CHUNK_SIZE + 16})
        if amount > 1 then
          values[#values + 1] = chunk_x
          values[#values + 1] = chunk_y
          values[#values + 1] = math.floor(amount)
          if amount > peak then peak = amount end
        end
      end
    end
  end
  respond({surface = surface.name, peak = math.floor(peak), chunks = values})
end)

commands.add_command("chartorio_alerts", "Recent losses on the player force.", function()
  initialise()
  local alerts = {}
  for _, alert in pairs(storage.chartorio.alerts) do
    alerts[#alerts + 1] = alert
  end
  respond({tick = game.tick, alerts = alerts})
end)

-- ---------------------------------------------------------------------------
-- Rail signals
--
-- Searching a viewport for signals cost 328ms a call, because an entity search
-- costs time proportional to the area searched and not to what it finds.
-- Signals never move, so a chunk only has to be searched once: the result is
-- kept per chunk in `storage`, and a query reads state off entities that are
-- already known. Building or destroying a signal marks that one chunk for a
-- rescan, which is a single chunk search rather than a screenful.
-- ---------------------------------------------------------------------------

local SIGNAL_LIMIT = 800
local SIGNAL_TYPES = {"rail-signal", "rail-chain-signal"}

local signal_state_names = {}
for name, value in pairs(defines.signal_state or {}) do
  signal_state_names[value] = name
end

local chain_state_names = {}
for name, value in pairs(defines.chain_signal_state or {}) do
  chain_state_names[value] = name
end

local IS_SIGNAL = {["rail-signal"] = true, ["rail-chain-signal"] = true}

-- Assigns the forward declaration made next to `on_entity_changed`, which is
-- where build and destroy already arrive. A plain `local function` here would
-- make a second local and leave the hook nil, so the map would keep showing
-- signals that had been removed.
remember_signal_hook = function(entity)
  if not storage.chartorio or not storage.chartorio.signal_scanned then return end
  local ok, kind = pcall(function() return entity.type end)
  if not ok or not IS_SIGNAL[kind] then return end
  local position = entity.position
  local key = chunk_key(entity.surface.name,
                        math.floor(position.x / CHUNK_SIZE),
                        math.floor(position.y / CHUNK_SIZE))
  storage.chartorio.signal_scanned[key] = nil
end

local function signals_in_chunk(surface, chunk_x, chunk_y)
  local key = chunk_key(surface.name, chunk_x, chunk_y)
  local known = storage.chartorio.signals[key]
  if storage.chartorio.signal_scanned[key] and known then
    return known
  end
  local area = {
    {chunk_x * CHUNK_SIZE, chunk_y * CHUNK_SIZE},
    {chunk_x * CHUNK_SIZE + CHUNK_SIZE, chunk_y * CHUNK_SIZE + CHUNK_SIZE},
  }
  local found = surface.find_entities_filtered({area = area, type = SIGNAL_TYPES})
  storage.chartorio.signals[key] = found
  storage.chartorio.signal_scanned[key] = true
  return found
end

commands.add_command("chartorio_signals", "Rail signals in view: chartorio_signals <surface> <x1> <y1> <x2> <y2>.", function(command)
  initialise()
  local surface_name, x1, y1, x2, y2 = string.match(
    command.parameter or "", "^(%S+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)%s+(-?%d+)$")
  local surface = surface_name and game.surfaces[surface_name]
  if not surface then
    return respond({error = "usage: chartorio_signals <surface> <x1> <y1> <x2> <y2>"})
  end
  x1, y1, x2, y2 = tonumber(x1), tonumber(y1), tonumber(x2), tonumber(y2)

  local center_x, center_y = (x1 + x2) / 2, (y1 + y2) / 2
  local half = MAX_QUERY_TILES / 2
  local clamped = (x2 - x1) > MAX_QUERY_TILES or (y2 - y1) > MAX_QUERY_TILES
  if clamped then
    x1, x2 = center_x - half, center_x + half
    y1, y2 = center_y - half, center_y + half
  end

  local force = game.forces.player
  local signals = {}
  local truncated = false
  local scanned = 0

  for chunk_y = math.floor(y1 / CHUNK_SIZE), math.floor(y2 / CHUNK_SIZE) do
    for chunk_x = math.floor(x1 / CHUNK_SIZE), math.floor(x2 / CHUNK_SIZE) do
      -- Signals belong to the factory, which the map only shows where the
      -- chunk is charted.
      if force.is_chunk_charted(surface, {chunk_x, chunk_y}) then
        local key = chunk_key(surface.name, chunk_x, chunk_y)
        local was_known = storage.chartorio.signal_scanned[key]
        local known = signals_in_chunk(surface, chunk_x, chunk_y)
        if not was_known then scanned = scanned + 1 end

        -- Entities destroyed since the chunk was scanned go invalid rather
        -- than disappearing, so the list is compacted as it is read.
        local live = {}
        for _, entity in pairs(known) do
          if entity.valid then
            live[#live + 1] = entity
            if #signals >= SIGNAL_LIMIT then
              truncated = true
            else
              local chain = entity.type == "rail-chain-signal"
              local ok, value = pcall(function()
                if chain then return chain_state_names[entity.chain_signal_state] end
                return signal_state_names[entity.signal_state]
              end)
              local position = entity.position
              signals[#signals + 1] = {
                x = position.x,
                y = position.y,
                direction = entity.direction,
                chain = chain,
                state = (ok and value) or "unknown",
              }
            end
          end
        end
        if #live ~= #known then storage.chartorio.signals[key] = live end
      end
      if truncated then break end
    end
    if truncated then break end
  end

  respond({
    surface = surface.name,
    signals = signals,
    truncated = truncated,
    clamped = clamped,
    scanned = scanned,
  })
end)

commands.add_command("chartorio_events", "Which change events this build registered.", function()
  local names = {
    "on_built_entity", "on_robot_built_entity", "on_space_platform_built_entity",
    "script_raised_built", "script_raised_revive", "on_entity_died",
    "on_player_mined_entity", "on_robot_mined_entity", "on_space_platform_mined_entity",
    "script_raised_destroy", "on_player_built_tile", "on_robot_built_tile",
    "on_player_mined_tile", "on_robot_mined_tile", "on_space_platform_built_tile",
    "on_space_platform_mined_tile", "script_raised_set_tiles",
    "on_chunk_charted", "on_chunk_generated",
  }
  local present, missing = {}, {}
  for _, name in ipairs(names) do
    if defines.events[name] then present[#present + 1] = name else missing[#missing + 1] = name end
  end
  -- Colour probe: shows which map colour hostile prototypes will be drawn with.
  local probes = {}
  for _, name in ipairs({"biter-spawner", "spitter-spawner", "small-worm-turret", "big-worm-turret"}) do
    local prototype = prototypes.entity[name]
    if prototype then
      probes[name] = {
        enemy = to_bytes(prototype.enemy_map_color),
        map = to_bytes(prototype.map_color),
        friendly = to_bytes(prototype.friendly_map_color),
      }
    end
  end
  respond({present = present, missing = missing, hostile_colors = probes})
end)

commands.add_command("chartorio_dirty", "Chunks changed since the previous call.", function()
  initialise()
  local dirty = {}
  for _, entry in pairs(storage.chartorio.dirty) do
    dirty[#dirty + 1] = entry
  end
  storage.chartorio.dirty = {}
  respond({chunks = dirty})
end)
