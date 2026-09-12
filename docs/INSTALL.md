# Installing Chartorio

Two parts, and the second one is optional:

1. **The map.** Runs next to your headless server. Nothing to download but this
   repository, nothing to install but Python 3.
2. **Real game art.** Makes the map draw Factorio's own sprites when you zoom
   in, instead of flat colours. Needs a graphical Factorio somewhere. **The map
   is complete without it** — skip it if you like.

---

## 1. The map

### What you need

| | |
| --- | --- |
| Factorio | **2.0** headless server. The API names this uses changed in 2.0 |
| Python | **3.9 or newer** on the same host. No packages — standard library only |
| RCON | enabled on your Factorio server, bound to localhost |
| Root | to create a service user and a systemd unit |

### Install

```bash
git clone https://github.com/groenrick/chartorio.git
cd chartorio
sudo systemctl stop factorio          # the save is patched on disk
sudo ./install.sh
```

Point it somewhere else if your paths differ:

```bash
sudo FACTORIO_DIR=/srv/factorio SAVE=/srv/factorio/saves/my-world.zip ./install.sh
```

The installer copies the bridge to `/opt/chartorio`, creates a `chartorio`
service user, generates an RCON password in `/etc/chartorio/rcon.env`, patches
your save with the scenario script, and installs a systemd unit.

**It does not edit your Factorio unit.** It prints the two RCON flags you need
to add yourself, because silently rewriting somebody's server unit is not a
thing an installer should do.

Add them, start both, and open `http://your-server:8080`.

### Why it patches your save

The game-side code is a **scenario script inside the save**, not a mod. Factorio
has no server-only mods: a mod changes the checksum and every client has to
install it, while a save carries its own `control.lua` and hands it to clients
when they join. Your players connect to a vanilla-compatible server and see
nothing unusual.

The original save is kept as `world.zip.pre-chartorio`.

**Stop the server before patching, always.** A running Factorio holds the save
in memory and writes it back over your patch when it exits.

### If you do not use the freeplay scenario

Do not replace your `control.lua`. Open `scenario/control.lua`, copy everything
below the marker line, and paste it at the end of your own scenario script.

### Updating

```bash
git pull
sudo systemctl stop factorio
python3 tools/patch-save.py /opt/factorio/saves/world.zip
sudo install -o root -g chartorio -m 640 bridge/bridge.py /opt/chartorio/bridge.py
sudo install -o root -g chartorio -m 640 web/index.html /opt/chartorio/index.html
sudo systemctl start factorio chartorio
```

Only patch the save again when `scenario/control.lua` has changed. The bridge
and the page can be updated on their own without interrupting anyone.

---

## 2. Real game art, if you want it

Zoomed in past eight pixels to a world tile, the map can draw Factorio's actual
sprites — belts pointing the right way and animating, trees as themselves, ore
thinning as it is mined, terrain textures underneath. Zoomed out, and wherever
there is no art for something, it falls back to the colour map.

### Why you have to do this yourself

**The artwork is Wube's, and this project cannot ship it.** Chartorio is MIT
licensed; that licence is ours to give over our own code and emphatically not
over Factorio's assets. So nothing extracted is committed here, attached to a
release, or distributed in any form. Every installation takes its own copy from
its own licensed game.

This is the same reason [Mapshot](https://github.com/Palats/mapshot) works the
way it does. Mods sidestep the problem by running inside the game, where the
art already is; a web map lives outside it.

A headless server ships **no artwork at all** — 872 KB of stubs against about a
gigabyte on a full install — so the extraction has to happen on a machine with
the real game, which is usually not your server.

### What you need

- A **graphical** Factorio install, the same major version as your server
- The same mods enabled as the server, so the prototypes match
- Your map already running, so the extractor knows which prototypes to take

### Extract

On the machine with the game:

```bash
mkdir -p ~/chartorio-scratch/config ~/chartorio-scratch/mods
cat > ~/chartorio-scratch/config/config.ini <<'INI'
[path]
read-data=/Applications/factorio.app/Contents/data
write-data=/Users/you/chartorio-scratch
INI
echo '{"mods":[{"name":"base","enabled":true}]}' > ~/chartorio-scratch/mods/mod-list.json

python3 render/sprites.py \
  --out ./sprites-extract \
  --config ~/chartorio-scratch/config/config.ini \
  --mods ~/chartorio-scratch/mods \
  --only-from-map http://your-server:8080
```

The scratch directory keeps the extractor out of your own Factorio's data
directory, so it does not fight your game for the lock — you can keep playing
while it runs.

`--only-from-map` reads your running map's palette and takes only the
prototypes your world actually contains. Without it you would extract
everything the game has.

> This is more steps than it should be, and is being fixed — see
> [#31](https://github.com/groenrick/chartorio/issues/31).

### Build

An extract is the game's files copied out unchanged, which keeps it checkable
against the install it came from. Most of what those files hold is never drawn:
a static machine is drawn from frame zero while the other thirty-one frames
come along for nothing.

```bash
python3 render/build.py --in ./sprites-extract --out ./sprites --verify
```

On a typical world that is about 160 MB in and 48 MB out. `--verify` decodes
every cropped sprite and compares it against the same rectangle of its source,
because smaller is worthless if it draws the wrong thing.

### Copy it over and switch it on

```bash
scp -r ./sprites/* root@your-server:/opt/chartorio/sprites/
ssh root@your-server '
  chown -R root:chartorio /opt/chartorio/sprites
  chmod 750 /opt/chartorio/sprites; chmod 640 /opt/chartorio/sprites/*'
```

Add one line to `/etc/systemd/system/chartorio.service`:

```
Environment=CHARTORIO_SPRITES=/opt/chartorio/sprites
```

Then `systemctl daemon-reload && systemctl restart chartorio`. Zoom in past
eight pixels to a tile and the sprites appear.

Sprites must live somewhere the service can read. The unit sets
`ProtectHome=true`, so `/root` and `/home` are invisible to it — put them under
`/opt/chartorio`.

### Keeping it current

An extract is a snapshot of the prototypes your world had when it was taken.
Build something whose kind is new to the world and it will draw as a coloured
block until you extract again. Nothing breaks; it just falls back.

---

## Troubleshooting

**The map is blank and every layer is empty.** The bridge cannot reach RCON.
Check `journalctl -u chartorio`, and that the RCON flags were added to your
Factorio unit.

**Everything works but one layer is always empty.** A command the bridge calls
may be missing from the scenario, which can happen if the save was patched with
an older `control.lua`. Compare what the save is running:

```bash
python3 - <<'PY'
import zipfile
z = zipfile.ZipFile("/opt/factorio/saves/world.zip")
name = [n for n in z.namelist() if n.endswith("/control.lua")][0]
print(name, len(z.read(name).splitlines()), "lines")
PY
```

Note that `scenario/control.lua` on disk is only a copy. **The script that
actually runs is the one inside the save.**

**Biters, and anything drawn from sprites, never appear.** Those need current
vision — radar or a player — not merely charted ground, exactly like the
in-game map. With nobody online and no radar, the game itself can see nothing.

**`/status` tells you what the game is being asked to do**: calls, rate, and
the share of wall time spent inside them. It is the first place to look at
anything that feels slow.

```bash
curl -s http://your-server:8080/status
```
