#!/usr/bin/env python3
"""Turn a sprite extract into something worth shipping.

`sprites.py` copies the game's files out unchanged, which keeps an extract
diffable against the install it came from. This trades bytes over a finished
extract, so extraction stays honest and the trade can be rerun with different
settings without extracting again.

    python3 render/build.py --in extract --out built --verify

Never writes to the input. Records what it did in `build.json`, so a built
directory can say where it came from and refuse to be built twice.

Standard library only, like the rest.
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import png  # noqa: E402

MANIFEST = "build.json"

# These address a grid at draw time — belts step through sixteen frames across
# twenty orientations, ore across richness by variation, underground belts
# offset by direction — so their sheets must survive whole.
# "rotated" is here for a reason worth remembering: a locomotive's sheet is one
# frame per angle, and its layer names the first frame's rectangle. Cropping to
# that rectangle is pixel-perfect and threw away 255 rotations, and --verify
# passed, because the crop did match the source rectangle. Verification can
# only check what it is told to look at.
GRID_KINDS = {"belt", "ore", "underground", "rotated"}


def read_cached(path, cache):
    """Decode a sheet once.

    A rail's five layers all come out of 4096x8192 sheets, and there are eight
    directions of four rail types. Decoding each sheet per crop is 160 full
    decodes of a 33 megapixel image in pure Python, which does not finish in
    any useful time.
    """
    if path not in cache:
        with open(path, "rb") as handle:
            cache[path] = png.read(handle.read())
    return cache[path]


def crop_decoded(decoded, x, y, width, height):
    full_width, full_height, channels, pixels = decoded
    if x < 0 or y < 0 or x + width > full_width or y + height > full_height:
        raise png.UnsupportedPNG("crop outside the sheet")
    stride = full_width * channels
    out = bytearray()
    for row in range(y, y + height):
        start = row * stride + x * channels
        out += pixels[start:start + width * channels]
    return channels, bytes(out)


def flatten_group(source, target, layers, copied, cache):
    """Stack a group of layers into one image.

    A rail is five pictures at fixed offsets — ballast, inner fill, sleepers,
    backplates, metals — and every one of them is sampled out of a 4096x8192
    sheet that the browser decodes to 134 MB. Five such sheets is 670 MB of
    image memory to draw track, and five drawImage calls for every rail on
    screen.

    Stacked once here instead, a rail direction becomes a single image a few
    hundred pixels square: one call to draw it, and the giant sheets are never
    loaded at all.

    Only for layers that are drawn together every time. Anything selected at
    draw time — a belt's frame, an ore's richness, a locomotive's angle — has
    to stay separate.
    """
    boxes = []
    for layer in layers:
        shift = layer.get("shift") or [0, 0]
        # Shift is in world tiles; a layer's own pixels are `scale` of a tile.
        left = shift[0] * 32 / layer["scale"] - layer["width"] / 2
        top = shift[1] * 32 / layer["scale"] - layer["height"] / 2
        boxes.append((left, top, left + layer["width"], top + layer["height"]))
    if any(abs(l["scale"] - layers[0]["scale"]) > 1e-9 for l in layers):
        return None                      # mixed scales: not safely stackable
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    width = int(round(max(b[2] for b in boxes) - left))
    height = int(round(max(b[3] for b in boxes) - top))
    if width <= 0 or height <= 0 or width * height > 4096 * 4096:
        return None

    canvas = png.blank(width, height)
    for layer, box in zip(layers, boxes):
        try:
            decoded = read_cached(os.path.join(source, layer["file"]), cache)
            channels, pixels = crop_decoded(decoded, layer.get("x", 0),
                                            layer.get("y", 0),
                                            layer["width"], layer["height"])
        except (png.UnsupportedPNG, OSError, KeyError):
            return None
        if channels != 4:
            return None
        canvas = png.over(canvas, width, height, pixels,
                          layer["width"], layer["height"],
                          int(round(box[0] - left)), int(round(box[1] - top)))

    name = "flat-%s.png" % hashlib.sha256(
        ("|".join("%s@%s,%s" % (l["file"], l.get("x", 0), l.get("y", 0))
                  for l in layers)).encode()).hexdigest()[:16]
    if name not in copied:
        with open(os.path.join(target, name), "wb") as handle:
            handle.write(png.write(width, height, 4, canvas))
        copied.add(name)
    scale = layers[0]["scale"]
    return {
        "file": name, "width": width, "height": height, "scale": scale,
        # The union's own centre, back in world tiles.
        "shift": [(left + width / 2) * scale / 32, (top + height / 2) * scale / 32],
        "x": 0, "y": 0,
    }


def digest(path):
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            hasher.update(block)
    return hasher.hexdigest()


def layer_groups(entry):
    """Every list of layers in an index entry, whatever shape it is in."""
    return list((entry.get("by") or {}).values()) or [entry.get("layers") or []]


def plan_crops(index):
    """Which file can be cut to which rectangle.

    A file several prototypes share is still croppable when they all want the
    same rectangle of it — eight spawner variants point at one sheet and draw
    the same corner. One that is wanted at two different rectangles, or that
    belongs to a grid kind, is left whole.
    """
    regions = {}
    for entry in index.get("sprites", {}).values():
        grid = entry.get("kind") in GRID_KINDS
        for group in layer_groups(entry):
            for layer in group:
                box = None if grid else (layer.get("x", 0), layer.get("y", 0),
                                         layer["width"], layer["height"])
                for sheet in sheets_of(layer):
                    # A layer spread over several files is only croppable to one
                    # rectangle if it is a single file in the first place.
                    regions.setdefault(sheet, set()).add(
                        box if len(sheets_of(layer)) == 1 else None)
    # A tile draws only its size one variants, one row of a sheet that also
    # carries the two, four and eight tile patches.
    for info in index.get("tiles", {}).values():
        across = min(info["count"], info.get("line_length") or info["count"])
        regions.setdefault(info["file"], set()).add(
            (info.get("x", 0), info.get("y", 0), info["cell"] * across, info["cell"]))
    # An icon carries its mipmaps beside it; only the leftmost square draws.
    for info in index.get("items", {}).values():
        regions.setdefault(info["file"], set()).add((0, 0, info["size"], info["size"]))

    plan = {}
    for name, boxes in regions.items():
        if len(boxes) == 1 and None not in boxes:
            plan[name] = next(iter(boxes))
    return plan


def sheets_of(layer):
    """Every file a layer draws from. Rolling stock spreads its rotations over
    several, and reading only `file` dropped seven of a locomotive's eight."""
    return list(layer.get("files") or [layer["file"]])


def referenced(index):
    names = set()
    for entry in index.get("sprites", {}).values():
        for group in layer_groups(entry):
            for layer in group:
                names.update(sheets_of(layer))
    for info in index.get("tiles", {}).values():
        names.add(info["file"])
    for info in index.get("items", {}).values():
        names.add(info["file"])
    return names


def rewrite_offsets(index, plan):
    """A cropped sheet starts at its own origin now. Forgetting this is how a
    sprite ends up cut correctly and drawn from somewhere else."""
    for entry in index.get("sprites", {}).values():
        for group in layer_groups(entry):
            for layer in group:
                if layer["file"] in plan:
                    layer["x"] = 0
                    layer["y"] = 0
    for info in index.get("tiles", {}).values():
        if info["file"] in plan:
            info["x"] = 0
            info["y"] = 0


def flatten(source, target, index):
    """Replace every stack of layers that is always drawn together with one
    image. Returns how many groups were flattened."""
    made = set()
    cache = {}
    done = 0
    for entry in index.get("sprites", {}).values():
        if entry.get("kind") in GRID_KINDS:
            continue
        groups = entry.get("by")
        if groups:
            for key, layers in list(groups.items()):
                if len(layers) < 2:
                    continue
                flat = flatten_group(source, target, layers, made, cache)
                if flat:
                    groups[key] = [flat]
                    done += 1
        else:
            layers = entry.get("layers") or []
            if len(layers) < 2:
                continue
            flat = flatten_group(source, target, layers, made, cache)
            if flat:
                entry["layers"] = [flat]
                done += 1
    return done


def build(source, target, crop=True, drop_unreferenced=True, flatten_layers=True):
    with open(os.path.join(source, "index.json")) as handle:
        index = json.load(handle)

    if os.path.exists(target):
        shutil.rmtree(target)
    os.makedirs(target)

    keep = referenced(index)
    plan = plan_crops(index) if crop else {}
    record = {"files": {}, "dropped": [], "kept_whole": []}
    before = after = 0

    for name in sorted(os.listdir(source)):
        if not name.endswith(".png"):
            continue
        origin = os.path.join(source, name)
        size = os.path.getsize(origin)
        before += size
        if drop_unreferenced and name not in keep:
            record["dropped"].append(name)
            continue
        box = plan.get(name)
        written = None
        if box:
            try:
                with open(origin, "rb") as handle:
                    data = handle.read()
                written = png.crop(data, *box)
            except (png.UnsupportedPNG, OSError) as error:
                record.setdefault("refused", {})[name] = str(error)
                written = None
        if written is None:
            shutil.copy2(origin, os.path.join(target, name))
            if name not in plan:
                record["kept_whole"].append(name)
        else:
            with open(os.path.join(target, name), "wb") as handle:
                handle.write(written)
        grown = os.path.getsize(os.path.join(target, name))
        after += grown
        record["files"][name] = {"from": size, "to": grown,
                                 "crop": list(box) if box and written else None,
                                 "sha256": digest(origin)}

    if crop:
        rewrite_offsets(index, {k: v for k, v in plan.items()
                                if record["files"].get(k, {}).get("crop")})

    # Flattening happens after cropping, so a stacked layer is cut from an
    # already trimmed sheet, and before the index is written.
    if flatten_layers:
        record["flattened"] = flatten(target, target, index)
        # Sheets that only existed to be stacked are now dead weight.
        if drop_unreferenced:
            keep_now = referenced(index)
            for name in list(record["files"]):
                if name not in keep_now and os.path.isfile(os.path.join(target, name)):
                    os.remove(os.path.join(target, name))
                    record["dropped"].append(name)
                    record["files"].pop(name)
            after = sum(os.path.getsize(os.path.join(target, f))
                        for f in os.listdir(target) if f.endswith(".png"))
            record["bytes_out"] = after
    with open(os.path.join(target, "index.json"), "w") as handle:
        json.dump(index, handle, indent=1)

    record.update({
        "version": 2,
        "built": int(time.time()),
        "source": os.path.abspath(source),
        "settings": {"crop": crop, "drop_unreferenced": drop_unreferenced,
                     "flatten": flatten_layers},
        "bytes_in": before,
        "bytes_out": after,
    })
    with open(os.path.join(target, MANIFEST), "w") as handle:
        json.dump(record, handle, indent=1)
    return record


def verify(source, target):
    """Every transformed sprite must hold the pixels it held before.

    Smaller is not the point; drawing the same thing is. Every sprite fault
    this project has had was silent, and a build step that quietly mangles one
    sheet would be more of the same.
    """
    with open(os.path.join(target, MANIFEST)) as handle:
        record = json.load(handle)
    problems = []
    checked = 0

    # Everything the index asks for must still be there. Checking only the files
    # that were transformed missed seven of a locomotive's eight sheets being
    # dropped as unreferenced.
    with open(os.path.join(target, "index.json")) as handle:
        index = json.load(handle)
    for name in sorted(referenced(index)):
        if not os.path.isfile(os.path.join(target, name)):
            problems.append("%s: the index needs it and the build has not got it" % name)
    for name, info in sorted(record["files"].items()):
        if name.startswith("flat-"):
            continue
        built = os.path.join(target, name)
        origin = os.path.join(source, name)
        if not os.path.isfile(built):
            problems.append("%s: missing from the build" % name)
            continue
        if digest(origin) != info["sha256"]:
            problems.append("%s: the extract changed under the build" % name)
            continue
        if not info["crop"]:
            continue
        try:
            with open(origin, "rb") as handle:
                wanted = png.read(png.crop(handle.read(), *info["crop"]))[3]
            with open(built, "rb") as handle:
                got = png.read(handle.read())[3]
        except (png.UnsupportedPNG, OSError) as error:
            problems.append("%s: %s" % (name, error))
            continue
        if wanted != got:
            problems.append("%s: pixels differ from the source rectangle" % name)
        checked += 1
    return checked, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--in", dest="source", required=True, help="an extract; never written to")
    parser.add_argument("--out", dest="target", required=True, help="rebuilt from scratch each time")
    parser.add_argument("--no-crop", action="store_true", help="copy sheets whole")
    parser.add_argument("--keep-unreferenced", action="store_true",
                        help="keep files the index never mentions")
    parser.add_argument("--no-flatten", action="store_true",
                        help="keep stacked layers separate; they will be drawn "
                             "one call each from their original sheets")
    parser.add_argument("--verify", action="store_true",
                        help="check every cropped sprite against its source rectangle")
    args = parser.parse_args()

    if os.path.abspath(args.source) == os.path.abspath(args.target):
        raise SystemExit("build in place would destroy the extract; give --out a new directory")
    if os.path.isfile(os.path.join(args.source, MANIFEST)):
        raise SystemExit("--in is already a build; point it at the extract it came from")

    record = build(args.source, args.target,
                   crop=not args.no_crop,
                   drop_unreferenced=not args.keep_unreferenced,
                   flatten_layers=not args.no_flatten)
    saved = record["bytes_in"] - record["bytes_out"]
    print("%s -> %s" % (args.source, args.target))
    print("  %.1f MB in, %.1f MB out, %.1f MB saved (%.1fx)"
          % (record["bytes_in"] / 1e6, record["bytes_out"] / 1e6, saved / 1e6,
             record["bytes_in"] / max(record["bytes_out"], 1)))
    cropped = sum(1 for f in record["files"].values() if f["crop"])
    print("  %d cropped, %d kept whole, %d dropped"
          % (cropped, len(record["kept_whole"]), len(record["dropped"])))
    if record.get("flattened"):
        print("  %d layer stacks flattened into one image each" % record["flattened"])
    if record.get("refused"):
        print("  %d refused and copied whole" % len(record["refused"]))

    if args.verify:
        checked, problems = verify(args.source, args.target)
        if problems:
            for line in problems[:10]:
                print("  FAIL %s" % line)
            raise SystemExit("%d of %d sprites did not survive the build" % (len(problems), checked))
        print("  verified %d cropped sprites against their source rectangles" % checked)


if __name__ == "__main__":
    main()
