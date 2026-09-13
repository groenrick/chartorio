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



class RailDirections(unittest.TestCase):
    """Rails declare all eight directions and are drawn from five stacked
    pictures. Both were new: everything before them had four directions and one
    picture."""

    def rail(self, **directions):
        return {"pictures": {name: value for name, value in directions.items()}}

    def parts(self, tag):
        return {part: layer("%s-%s.png" % (tag, part))
                for part in ("stone_path_background", "stone_path", "ties",
                             "backplates", "metals")}

    def test_all_eight_directions_are_taken(self):
        names = ("north", "northeast", "east", "southeast",
                 "south", "southwest", "west", "northwest")
        found = sprites.directional_layers(self.rail(**{n: self.parts(n) for n in names}))
        self.assertEqual(sorted(found), [0, 2, 4, 6, 8, 10, 12, 14])

    def test_the_diagonals_are_not_lost_to_the_four_cardinals(self):
        # Matching on north/east/south/west first would take those and drop the
        # diagonals, leaving every diagonal rail drawn as a straight one.
        names = ("north", "northeast", "east", "southeast",
                 "south", "southwest", "west", "northwest")
        found = sprites.directional_layers(self.rail(**{n: self.parts(n) for n in names}))
        self.assertIn(2, found, "northeast")
        self.assertIn(14, found, "northwest")

    def test_a_rail_is_five_pictures_in_drawing_order(self):
        # Ballast, its inner fill, sleepers, the plates, then the rails. Any
        # other order puts the stone bed over the metals.
        # A rail always declares all eight keys, even where the picture is
        # empty, so the fixture does too.
        named = {n: self.parts(n) for n in ("north", "northeast", "east", "southeast")}
        named.update({n: {} for n in ("south", "southwest", "west", "northwest")})
        found = sprites.directional_layers(self.rail(**named))
        files = [l["filename"].split("-", 1)[1] for l in found[0]]
        self.assertEqual(files, ["stone_path_background.png", "stone_path.png",
                                 "ties.png", "backplates.png", "metals.png"])

    def test_a_straight_rail_keeps_only_the_directions_it_defines(self):
        # Factorio defines four for a straight rail, because a north-south rail
        # is the same picture whichever end you look from, and the game only
        # ever reports 0, 2, 4 and 6 for one.
        found = sprites.directional_layers(self.rail(
            north=self.parts("north"), northeast=self.parts("northeast"),
            east=self.parts("east"), southeast=self.parts("southeast"),
            south={}, southwest={}, west={}, northwest={}))
        self.assertEqual(sorted(found), [0, 2, 4, 6])

    def test_the_segment_visualisation_is_not_drawn(self):
        # The game draws it for debugging; it is not part of the track.
        parts = self.parts("north")
        parts["segment_visualisation_middle"] = layer("debug.png")
        named = {"north": parts, "northeast": self.parts("ne"),
                 "east": self.parts("e"), "southeast": self.parts("se")}
        named.update({n: {} for n in ("south", "southwest", "west", "northwest")})
        found = sprites.directional_layers(self.rail(**named))
        self.assertTrue(all("debug" not in l["filename"] for l in found[0]))

    def test_a_four_direction_entity_is_unchanged(self):
        found = sprites.directional_layers({"graphics_set": {"animation": {
            "north": layer("n.png"), "east": layer("e.png"),
            "south": layer("s.png"), "west": layer("w.png")}}})
        self.assertEqual(sorted(found), [0, 4, 8, 12])



if __name__ == "__main__":
    unittest.main()
