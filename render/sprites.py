#!/usr/bin/env python3
"""Extract in-world entity sprites from a graphical Factorio install.

A headless server ships no artwork — 872 KB of stubs against a gigabyte on a
full install — so the sprites the map draws have to come from somebody's own
licensed copy of the game. Nothing extracted here may be committed: it is
Wube's art, and each installation produces its own copy.

    python3 render/sprites.py --out /srv/chartorio-sprites --only-from-map http://host:8080

Writes:

    <out>/index.json          what to draw for each prototype, and where
    <out>/<prototype>.png     the source spritesheet, copied unchanged

Placement is left to the browser. Factorio draws at 32 pixels to a world tile,
so a sprite `width` pixels across at `scale` s covers width*s/32 world tiles,
centred on the entity's position plus `shift`, which is already in world tiles.
"""
import argparse
import json
import os
import shutil
import subprocess
import urllib.request

# Where a prototype hides its main picture. Tried in order; the first that
# resolves to a real layer wins. Factorio has no single field for this because
# a belt, a furnace and a pole are drawn by quite different machinery.
PICTURE_PATHS = [
    ("graphics_set", "animation"),
    ("graphics_set", "picture"),
    ("picture",),
    ("pictures",),
    ("animation",),
    ("animations",),
    ("structure", "direction_in"),
    ("structure",),
    ("belt_animation_set", "animation_set"),
    ("graphics_set", "animations"),
    ("stages",),
    ("orientations",),
    ("folded_animation",),
    ("platform_picture",),
    ("variations",),
    ("sprite",),
    ("sprites",),
    ("vertical_animation",),
    ("off_animation",),
    ("base_picture",),
    ("idle_animation",),
    ("connection_sprites",),
    ("pipe_covers",),
]


def dig(node, path):
    for step in path:
        if not isinstance(node, dict):
            return None
        node = node.get(step)
        if node is None:
            return None
    return node


def first_layer(node, depth=0):
    """Walk down to something carrying a filename, skipping shadows.

    Sprite definitions nest in several shapes: a plain sprite, `layers`, a
    direction table such as `north`/`east`, or `sheets`. Shadows are skipped
    because they are drawn separately and would otherwise be picked as the
    entity itself."""
    if depth > 6 or node is None:
        return None
    if isinstance(node, list):
        for item in node:
            found = first_layer(item, depth + 1)
            if found:
                return found
        return None
    if not isinstance(node, dict):
        return None
    if node.get("draw_as_shadow") or node.get("draw_as_glow") or node.get("draw_as_light"):
        return None
    if node.get("filename"):
        # A square sheet gives `size` instead of a width and a height, which is
        # how belts are declared and why they went missing at first.
        size = node.get("size")
        if isinstance(size, list) and len(size) == 2:
            node = dict(node, width=size[0], height=size[1])
        elif isinstance(size, int):
            node = dict(node, width=size, height=size)
        if node.get("width") and node.get("height"):
            return node
    for key in ("layers", "sheets", "sheet", "north", "up", "east", "right",
                "west", "south", "down", "left", "direction_in", "hr_version",
                "structure", "straight_vertical", "sprite", "picture",
                "trunk", "leaves", "stages", "pictures", "north_to_south",
                "animations", "filename_prefix"):
        found = first_layer(node.get(key), depth + 1)
        if found:
            return found
    return None


def sprite_for(prototype):
    for path in PICTURE_PATHS:
        layer = first_layer(dig(prototype, path))
        if layer:
            # A belt sheet is a grid of frames by belt orientation, and the
            # orientation is not the entity's direction: there are twenty rows
            # covering four straight runs, eight curves and eight sideloads.
            if path[0] == "belt_animation_set":
                layer = dict(layer, chartorio_kind="belt")
            return layer
    return None


def layer_info(node):
    """One drawable layer, normalised. None when the node is not one."""
    if not isinstance(node, dict) or not node.get("filename"):
        return None
    if node.get("draw_as_shadow") or node.get("draw_as_glow") or node.get("draw_as_light"):
        return None
    size = node.get("size")
    width, height = node.get("width"), node.get("height")
    if isinstance(size, list) and len(size) == 2:
        width, height = size
    elif isinstance(size, int):
        width = height = size
    if not width or not height:
        return None
    shift = node.get("shift") or [0, 0]
    if isinstance(shift, dict):
        shift = [shift.get("x", 0), shift.get("y", 0)]
    return {
        "filename": node["filename"],
        "width": width,
        "height": height,
        "scale": node.get("scale", 1),
        "shift": shift,
        "x": node.get("x", 0),
        "y": node.get("y", 0),
        "frames": node.get("frame_count", 1),
        "line_length": node.get("line_length", 0),
        "directions": node.get("direction_count", 1),
    }


def tree_variations(prototype):
    """Trees carry a dozen variations, each its own trunk and leaves files, and
    an entity picks one with `graphics_variation`. Taking the first variation's
    trunk — which is what happened at first — draws every tree in the world as
    the same bare stump."""
    variations = prototype.get("variations")
    if not isinstance(variations, list) or not variations:
        return None
    out = []
    for variation in variations:
        if not isinstance(variation, dict):
            continue
        layers = [layer_info(variation.get(part)) for part in ("trunk", "leaves")]
        layers = [layer for layer in layers if layer]
        if layers:
            out.append(layers)
    return out or None


def resolve(filename, data_dir):
    """`__base__/graphics/...` points into the game's data directory."""
    if not filename.startswith("__"):
        return None
    end = filename.find("__", 2)
    if end < 0:
        return None
    return os.path.join(data_dir, filename[2:end], filename[end + 2:].lstrip("/"))


def dump_data(binary, config, mods, data_dir):
    out = os.path.join(os.path.dirname(config), "..", "script-output", "data-raw-dump.json")
    out = os.path.normpath(out)
    if not os.path.exists(out):
        subprocess.run([binary, "--config", config, "--mod-directory", mods, "--dump-data"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    with open(out) as handle:
        return json.load(handle)


def wanted_from_map(url):
    """Only the prototypes this world actually contains. The scenario's palette
    already tracks every entity the map has seen, so the extract stays small."""
    with urllib.request.urlopen(url.rstrip("/") + "/palette", timeout=15) as handle:
        palette = json.load(handle)
    return sorted({key[2:] for key in palette.get("keys", {})
                   if key.startswith("e:") or key.startswith("x:")})


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--binary", default="/Applications/factorio.app/Contents/MacOS/factorio")
    parser.add_argument("--config", required=True, help="config.ini with a private write-data")
    parser.add_argument("--mods", required=True)
    parser.add_argument("--data-dir", default=None, help="the game's data directory")
    parser.add_argument("--only-from-map", default=None,
                        help="a running bridge, so only prototypes this world has are extracted")
    args = parser.parse_args()

    data_dir = args.data_dir or os.path.normpath(
        os.path.join(os.path.dirname(args.binary), "..", "data"))

    raw = dump_data(args.binary, args.config, args.mods, data_dir)
    wanted = set(wanted_from_map(args.only_from_map)) if args.only_from_map else None

    os.makedirs(args.out, exist_ok=True)
    index, copied, skipped = {}, {}, set()

    for category, entries in raw.items():
        if not isinstance(entries, dict):
            continue
        if category in ("item", "recipe", "fluid", "technology", "tool",
                        "ammo", "capsule", "module", "gun", "armor"):
            continue
        for name, prototype in entries.items():
            if not isinstance(prototype, dict) or "type" not in prototype:
                continue
            if name in index:
                continue
            if wanted is not None and name not in wanted:
                continue
            def take(layer):
                """Copy a layer's sheet and return it with a served filename."""
                source = resolve(layer["filename"], data_dir)
                if not source or not os.path.isfile(source):
                    return None
                if source not in copied:
                    target = os.path.basename(source)
                    # Two prototypes can name the same file; keep them apart.
                    while target in copied.values():
                        target = "_" + target
                    shutil.copy2(source, os.path.join(args.out, target))
                    copied[source] = target
                served = dict(layer)
                served.pop("filename")
                served["file"] = copied[source]
                return served

            entry = None
            variations = tree_variations(prototype) if category == "tree" else None
            if variations:
                taken = []
                for layers in variations:
                    served = [take(layer) for layer in layers]
                    served = [layer for layer in served if layer]
                    if served:
                        taken.append(served)
                if taken:
                    entry = {"kind": "variations", "variations": taken}
            else:
                layer = sprite_for(prototype)
                info = layer_info(layer) if layer else None
                served = take(info) if info else None
                if served:
                    entry = {"kind": layer.get("chartorio_kind", "static"),
                             "layers": [served]}
                    if entry["kind"] == "belt":
                        # Frames advance with the belt's own speed, so a fast
                        # belt visibly runs faster than a yellow one.
                        entry["speed"] = prototype.get("speed", 0.03125)
                        entry["speed_coefficient"] = prototype.get(
                            "animation_speed_coefficient", 1)

            if not entry:
                if wanted is not None:
                    skipped.add(name)
                continue
            entry["category"] = category
            index[name] = entry

    with open(os.path.join(args.out, "index.json"), "w") as handle:
        json.dump({"version": 1, "pixels_per_tile": 32, "sprites": index}, handle, indent=1)

    size = sum(os.path.getsize(os.path.join(args.out, f)) for f in os.listdir(args.out))
    print("%d sprites, %.1f MB in %s" % (len(index), size / 1e6, args.out))
    missing = sorted(skipped - set(index))
    if missing:
        print("no sprite resolved for %d: %s" % (len(missing), ", ".join(missing[:14])))


if __name__ == "__main__":
    main()
