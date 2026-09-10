#!/usr/bin/env python3
"""Tests for the save patcher.

Patching writes into somebody's save, so the parts worth pinning down are the
ones that decide whether their world survives: the backup, and refusing to
guess when the save does not look the way it should.
"""
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "patch_save",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools", "patch-save.py"))
patch_save = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_save)


class PatchSave(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.save = os.path.join(self.dir, "world.zip")
        self.script = os.path.join(self.dir, "control.lua")
        with open(self.script, "w") as handle:
            handle.write("-- chartorio\n")

    def write_save(self, names):
        with zipfile.ZipFile(self.save, "w") as out:
            for name in names:
                out.writestr(name, "original %s" % name)

    def test_the_script_lands_in_the_save(self):
        self.write_save(["world/control.lua", "world/level.dat", "world/blueprint.dat"])
        patch_save.patch(self.save, self.script)
        with zipfile.ZipFile(self.save) as saved:
            self.assertEqual(saved.read("world/control.lua").decode(), "-- chartorio\n")

    def test_everything_else_in_the_save_is_left_alone(self):
        self.write_save(["world/control.lua", "world/level.dat", "world/level-init.dat"])
        patch_save.patch(self.save, self.script)
        with zipfile.ZipFile(self.save) as saved:
            self.assertEqual(sorted(saved.namelist()),
                             ["world/control.lua", "world/level-init.dat", "world/level.dat"])
            self.assertEqual(saved.read("world/level.dat").decode(), "original world/level.dat")

    def test_a_backup_is_made_before_the_first_patch(self):
        self.write_save(["world/control.lua"])
        patch_save.patch(self.save, self.script)
        with zipfile.ZipFile(self.save + ".pre-chartorio") as backup:
            self.assertEqual(backup.read("world/control.lua").decode(),
                             "original world/control.lua")

    def test_patching_twice_keeps_the_original_backup(self):
        # The second run must not overwrite the backup with an already patched
        # save, or the way back to a vanilla scenario is gone.
        self.write_save(["world/control.lua"])
        patch_save.patch(self.save, self.script)
        with open(self.script, "w") as handle:
            handle.write("-- chartorio v2\n")
        patch_save.patch(self.save, self.script)
        with zipfile.ZipFile(self.save + ".pre-chartorio") as backup:
            self.assertEqual(backup.read("world/control.lua").decode(),
                             "original world/control.lua")
        with zipfile.ZipFile(self.save) as saved:
            self.assertEqual(saved.read("world/control.lua").decode(), "-- chartorio v2\n")

    def test_a_save_without_a_control_lua_is_refused(self):
        self.write_save(["world/level.dat"])
        with self.assertRaises(SystemExit):
            patch_save.patch(self.save, self.script)

    def test_an_ambiguous_save_is_refused(self):
        self.write_save(["world/control.lua", "other/control.lua"])
        with self.assertRaises(SystemExit):
            patch_save.patch(self.save, self.script)


if __name__ == "__main__":
    unittest.main()
