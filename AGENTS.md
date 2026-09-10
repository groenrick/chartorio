# Working on Chartorio

Chartorio is a live web map for a Factorio headless server. Read this before
changing anything: most of it is knowledge that cost a debugging session to
acquire and is not obvious from the code.

## The three parts

| Part | Runs where | Language |
| --- | --- | --- |
| `scenario/control.lua` | inside the save, on the game thread | Lua |
| `bridge/bridge.py` | next to the server | Python, standard library only |
| `web/index.html` | the browser | one file, no build step, no libraries |

The game code is a **scenario script inside the save**, not a mod. Factorio has
no server-only mods: any mod changes the checksum and every client must install
it, while a save carries its own `control.lua` and sends it to clients on join.
Keep it that way.

`tools/patch-save.py` writes the script into a save. The server must be stopped
first, because a running Factorio holds the save in memory and writes it back
over the patch.

## Rules that come from real failures

**An uncaught error in a command kills the server.** Not the command, the
server: it goes to `Failed` and quits. Wrap anything that touches an API whose
shape you are not certain of in `pcall`. A probe using `entity.help()`, removed
in 2.0, took a live server down.

**`script.on_event` replaces the previous handler for that event.** Registering
`on_entity_died` twice silently disables the first registration. Events that
serve two purposes need one handler that does both.

**A `nil` in a table literal makes a hole**, and iterating can skip everything
behind it. Register events by name (`defines.events[name]`) so a define missing
from the running build cannot silently drop the rest.

**Entity searches cost time proportional to the area searched**, not to what
they find. A viewport-sized search for rail signals took 328ms per call and ate
a third of the game thread. Nothing may scan the whole surface, and nothing may
scan a screen-sized area on a timer:

- Rail signals never move, so they live in a per chunk registry and only their
  state is read.
- Biters do move, so they are searched per chunk, only in chunks that are both
  on screen and currently visible.
- The chunk index and pollution take a rectangle and are clamped server side.

**Charted is not visible.** The in-game map keeps showing nests in charted
chunks but only draws moving biters where radar or a player gives current
vision. `is_chunk_charted` and `is_chunk_visible` are both used deliberately.
Uncharted chunks are not rendered at all; `CHARTORIO_FOG=open` lifts that.

**Custom commands, never `/silent-command`.** A cheat command flags the save.
Everything the bridge calls is a registered command.

## Factorio 2.0 specifics

- `helpers.write_file`, `helpers.table_to_json` (moved off `game`)
- `game.train_manager.get_trains{surface=}` (not `surface.get_trains`)
- `player.physical_position` is the character; `player.position` follows
  whatever the player controls, so it moves when they pan the in-game map
- Hostile entities use `enemy_map_color`; `map_color` is nil on nests, and
  `friendly_map_color` is blue, which is how nests once ended up blue
- Rails: `get_rail_end(defines.rail_direction.front).location.position` gives
  the real endpoints. Painting a rail's bounding box loses its direction and
  turns diagonals into staircases
- Sixteen directions, so one step is a sixteenth of a turn

## The bridge

- **RCON responses**: a long answer may be one large packet or several
  fragments, and nothing in the protocol says which. After a long packet, wait
  briefly and treat silence as the end; do not block waiting for a fragment
  that never comes.
- **Percent decode query values.** A browser sends the comma in
  `chunks=-15,-9,15,9` as `%2C`; reading it raw produced a viewport the server
  could not parse and an empty map with every other channel working.
- **Raise the listen backlog.** Python's default of five drops connections when
  a page opens a socket plus parallel tile requests, which reaches the browser
  as a websocket that cannot connect.
- **Keep the event-stream fallback.** Some networks pass ordinary HTTP but
  refuse a plain `ws://` upgrade. `/events` carries the same channels.
- **The handshake GUID is a fixed constant and a typo in it is silent.** A
  wrong `Sec-WebSocket-Accept` makes every browser refuse the upgrade, the page
  falls back to `/events`, and the map works, so nothing looks broken. The
  bridge shipped a mistyped GUID from the day WebSockets were added until a
  test compared it against the value in RFC 6455. Fall back on purpose, never
  by accident: if the status line says event stream on a network you control,
  the websocket is broken, not the network.
- **Serve the page with `no-store`**, or browsers keep an old page across a
  redeploy and appear to poll endpoints that no longer exist.
- Tiles are PNGs written by hand over `zlib`; there is no image library.
- Zoomed out tiles halve cached child tiles and prefer a built thing over bare
  ground when merging, so one-tile-wide rails survive every zoom step.

## Measure before optimising

`/status` reports what the game is being asked to do: calls, rate, and the
share of wall time spent inside them. Use it. The transport was rewritten to
WebSockets on the assumption that polling was the load; measurement showed
polling cost nothing and two entity searches cost half the game thread.

## Testing without a browser

- Pull functions out of `index.html` with a regex and run them in Node against
  stubs. The whole page boots that way with a small DOM stub, which catches
  start-up errors that a syntax check misses.
- Verify rendering by fetching tiles and stitching them into one PNG, then
  looking at it. Colours are exact palette values, so this is reliable.
- When a test fails, check the test's assumptions first. Three failures in a
  row during this project were the test, not the code: a pan into unexplored
  ground, counting requests that a callback had already refilled, and asserting
  on totals instead of deltas.

## Issues

Reports from outside come in through the forms in `.github/ISSUE_TEMPLATE`,
which ask for the things that decide where a fault lives: which of the three
parts, websocket or `/events`, and the output of `/status`. Those forms do not
apply when writing an issue by hand or with `gh`, so use this shape instead:

```
<one paragraph: what the situation is today, and why it is that way>

## Approach
## Constraints that shape it
## Done when
```

**Constraints is the section that earns its place.** The interesting changes in
this project are nearly all a fix for something non-obvious, and an issue is
where that knowledge lands before any code exists. Cost on the game thread,
charted versus visible, what cannot be committed here: write it down when the
issue is opened, not when the pull request is reviewed.

**Done when** is a test someone else can apply, not a restatement of the title.

Do not ask an outside reporter for constraints they cannot know. Take what
their form gives and write the shaped issue yourself.

## Never commit to main

Work on a branch and open a pull request, always, including for a one word
change and including your own work. `main` is protected on GitHub: it takes no
direct push from anyone outside the admin, no force push and no deletion, and
both CI jobs have to be green before a merge.

The admin can still push straight to `main`, because a locked out maintainer
with a broken deployment is worse than a bypassed rule. That escape hatch is
for a hotfix, not for ordinary work. Use a pull request.

The reason is not ceremony. This repository force pushed over a commit and
carried a mistyped WebSocket handshake for a day without noticing, and the
review a pull request forces is where both of those get caught. A branch also
means CI has somewhere to fail that is not `main`.

## Commits

Conventional Commits, `type(scope): subject`.

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `chore`.
Scopes are the parts of the project: `scenario`, `bridge`, `web`, `tools`,
`install`, `docs`.

Subject in the imperative, no trailing full stop, under about 60 characters.
Use the body for **why**, since this project's interesting changes are usually
a fix for something non-obvious, and say what was measured when a change was
made for performance. Breaking changes get a `!` after the scope and a
`BREAKING CHANGE:` footer.

```
perf(scenario): keep rail signals in a per chunk registry

A viewport search for signals cost 328ms a call, half the game thread with
one browser open. Signals never move, so only their state is read now: 15ms.
```

```
fix(bridge): percent decode query values
fix(scenario): report physical_position for players
feat(web): pan with wasd and the arrow keys
docs(readme): show how rails are drawn
```

History before this convention was adopted is plain prose; leave it be.

## Conventions

- Comments explain why, not what, and are worth writing where the reason is not
  obvious from the code.
- Keep the page dependency-free and the bridge standard-library only. Both are
  features: this installs on a bare server with nothing but Python 3.
- Prefer measuring over guessing, and say what was measured.
