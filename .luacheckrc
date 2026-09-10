-- The scenario runs inside Factorio, which supplies these. Without the list
-- every one of them reads as an undefined global and the real findings drown.
std = "lua54"
globals = { "storage" }          -- the scenario writes its own state here
read_globals = {
  "game", "script", "defines", "helpers", "commands", "settings", "rcon",
}
files["scenario/control.lua"] = {
  -- Factorio's API is wide and a handler often takes an event it ignores.
  ignore = { "212" },            -- unused argument
}
max_line_length = 100
