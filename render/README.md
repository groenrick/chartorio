# Real game sprites

The map draws terrain and entities as flat prototype `map_color` values,
because that is all a headless server can offer: it ships no artwork at all,
872 KB of stubs against a gigabyte on a full install. Zoomed in, those flat
blocks stop being enough.

This extracts the game's real in-world sprites from a **licensed graphical
install** so the map can draw the actual artwork close in.

```bash
python3 render/sprites.py \
  --out /srv/chartorio-sprites \
  --config ~/factorio-scratch/config/config.ini \
  --mods   ~/factorio-scratch/mods \
  --only-from-map http://your-server:8080
```

Then point the bridge at the result:

```
CHARTORIO_SPRITES=/srv/chartorio-sprites
CHARTORIO_SPRITE_SCALE=8      # screen pixels to a world tile, the default
```

**Nothing extracted here may be committed.** It is Wube's art; each
installation produces its own copy, the same rule the scenario follows by
living inside the save.

## How it works

`factorio --dump-data` writes `data.raw` as JSON, which carries what a control
script cannot see: the sprite's filename, its size in pixels, its `shift` in
world tiles, and its `scale`. Placement then falls out of one fact — Factorio
draws at **32 pixels to a world tile** — so a sprite `width` pixels across at
`scale` s covers `width * s / 32` world tiles, centred on the entity's position
plus its shift.

`--only-from-map` reads the running bridge's palette, which already tracks
every prototype the world has ever shown, so only those are extracted. On the
live server that is 56 prototypes and 27 MB. The cost scales with how many
*kinds* of thing exist, not with how big the map is.

## Finding the picture is the hard part

There is no single field for "the picture of this thing". Every one of these
was a real miss:

| prototype | where the sprite hides |
| --- | --- |
| stone furnace | `graphics_set.animation.layers[0]` |
| transport belt | `belt_animation_set.animation_set`, square `size` rather than a width and height |
| boiler | `pictures.north.structure.layers` |
| assembling machine | `graphics_set.animation`, 32 frames on a sheet |
| tree | `variations[].trunk` |
| cliff | `orientations.*.pictures`, **plus an x/y offset into a shared sheet** |
| biter spawner | `graphics_set.animations` |

Shadow, glow and light layers are skipped, or a building would be drawn as its
own shadow. The cliff case is the one to be careful with: miss the sheet offset
and a cliff renders as whatever else happens to sit at 0,0 in that file.

56 of the 57 prototypes in the live world resolve. `small-biter` does not, and
biters are drawn as moving dots from a faster poll anyway.

## What the map does with them

Sprites are drawn only above `CHARTORIO_SPRITE_SCALE` screen pixels to a world
tile — 8 by default, roughly a quarter of native art scale — and only for
chunks the game can **currently see**, by radar or a player. Charted is not
enough: that is the same rule the biters follow, and it keeps the map from
showing more than the game would.

Anything that moves — players, trains, biters, cars — is excluded from the
sprite layer, because each already has its own faster live layer and would
otherwise appear twice, a few hundred milliseconds apart.

A prototype with no extracted sprite is not an error. The colour tile
underneath simply stands, so a partial extract degrades instead of breaking.
