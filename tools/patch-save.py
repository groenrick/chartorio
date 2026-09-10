#!/usr/bin/env python3
"""Inject the Chartorio scenario script into an existing Factorio save.

A save carries its own scenario script at world/control.lua, and joining
clients receive it with the map. Replacing it there is what makes Chartorio
work without anyone installing a mod.

Usage:
    python3 patch-save.py <save.zip> [control.lua]

The server must be stopped: a running Factorio holds the save in memory and
would write it back out over the patch.
"""
import os
import shutil
import sys
import zipfile


def patch(save_path, script_path):
    with open(script_path) as handle:
        script = handle.read()

    backup_path = save_path + ".pre-chartorio"
    if not os.path.exists(backup_path):
        shutil.copy2(save_path, backup_path)

    source = zipfile.ZipFile(save_path)
    targets = [name for name in source.namelist() if name.endswith("/control.lua")]
    if len(targets) != 1:
        source.close()
        raise SystemExit("expected exactly one control.lua in the save, found: %r" % targets)
    target = targets[0]

    items = [(item, source.read(item.filename)) for item in source.infolist()]
    source.close()

    with zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED) as out:
        for item, data in items:
            out.writestr(item, script.encode() if item.filename == target else data)

    print("patched %s in %s (backup: %s)" % (target, save_path, backup_path))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    default_script = os.path.join(os.path.dirname(here), "scenario", "control.lua")
    patch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else default_script)
