-- The scenario runs inside Factorio, which supplies these. Without the list
-- every one of them reads as an undefined global and the real findings drown.
std = "lua54"
globals = { "storage" }          -- the scenario writes its own state here
read_globals = {
  "game", "script", "defines", "helpers", "commands", "settings", "rcon",
  "prototypes",
}
-- Line length is left alone on purpose: the scenario builds long API calls and
-- wrapping them reads worse than the long line does.
max_line_length = false
