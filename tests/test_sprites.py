#!/usr/bin/env python3
"""Tests for finding an entity's in-world sprite in data.raw.

Factorio has no single field for "the picture of this thing": a furnace, a
belt, a tree and a cliff are each declared by different machinery, and the
shapes below are the ones that actually appear in base. Every case here is
taken from a real prototype that went missing on the first attempt.
"""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "chartorio_sprites", os.path.join(ROOT, "render", "sprites.py"))
sprites = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sprites)


def layer(name="__base__/graphics/entity/x/x.png", **extra):
    base = {"filename": name, "width": 64, "height": 64}
    base.update(extra)
    return base


class FindingTheSprite(unittest.TestCase):
    def test_a_plain_layered_animation(self):
        # stone-furnace
        found = sprites.sprite_for({"graphics_set": {"animation": {"layers": [layer()]}}})
        self.assertEqual(found["filename"], "__base__/graphics/entity/x/x.png")

    def test_the_shadow_layer_is_never_the_entity(self):
        found = sprites.sprite_for({"graphics_set": {"animation": {"layers": [
            layer("shadow.png", draw_as_shadow=True),
            layer("real.png"),
        ]}}})
        self.assertEqual(found["filename"], "real.png")

    def test_a_square_sheet_declares_size_not_width(self):
        # transport-belt: this is why belts were missing at first.
        found = sprites.sprite_for({"belt_animation_set": {"animation_set": {
            "filename": "belt.png", "size": 128, "scale": 0.5, "frame_count": 16}}})
        self.assertEqual((found["width"], found["height"]), (128, 128))

    def test_a_size_pair_is_width_then_height(self):
        found = sprites.sprite_for({"picture": {"filename": "a.png", "size": [64, 128]}})
        self.assertEqual((found["width"], found["height"]), (64, 128))

    def test_a_direction_table_with_a_structure(self):
        # boiler
        found = sprites.sprite_for({"pictures": {"north": {"structure": {
            "layers": [layer("boiler-N.png")]}}}})
        self.assertEqual(found["filename"], "boiler-N.png")

    def test_a_tree_hides_its_sprite_in_variations(self):
        found = sprites.sprite_for({"variations": [{"trunk": layer("trunk.png")}]})
        self.assertEqual(found["filename"], "trunk.png")

    def test_a_prototype_with_no_picture_resolves_to_nothing(self):
        self.assertIsNone(sprites.sprite_for({"type": "item", "stack_size": 50}))

    def test_glow_and_light_layers_are_not_the_entity(self):
        self.assertIsNone(sprites.sprite_for({"picture": layer(draw_as_glow=True)}))

    def test_recursion_gives_up_rather_than_looping(self):
        node = {}
        node["layers"] = node          # a cycle must not hang the extractor
        self.assertIsNone(sprites.sprite_for({"picture": node}))


class ResolvingPaths(unittest.TestCase):
    def test_a_mod_path_points_into_the_data_directory(self):
        self.assertEqual(
            sprites.resolve("__base__/graphics/entity/lab/lab.png", "/game/data"),
            os.path.join("/game/data", "base", "graphics/entity/lab/lab.png"))

    def test_a_path_without_a_mod_prefix_is_refused(self):
        self.assertIsNone(sprites.resolve("graphics/entity/lab.png", "/game/data"))

    def test_an_unterminated_prefix_is_refused(self):
        self.assertIsNone(sprites.resolve("__base/graphics/lab.png", "/game/data"))


if __name__ == "__main__":
    unittest.main()
