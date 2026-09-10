#!/usr/bin/env python3
"""Tests for the parts of the bridge that need no game.

Standard library only, like the bridge itself, so this runs anywhere Python 3
does. Nothing here opens a socket or talks RCON: the pieces under test are the
pure ones, and the two that are not are stubbed.
"""
import faulthandler
import os
import struct
import sys
import threading
import unittest
import zlib

# The hub is threaded and its lock is not reentrant, so a mistake there does not
# fail a test, it stops the thread that touched it. unittest has no per-test
# timeout, and the first test to call hub.add() runs on the main thread, so the
# whole suite would hang until CI eventually kills the job. This turns any hang
# into a failure with a traceback pointing at the line that is stuck. The suite
# takes well under a second, so this can only fire on a real hang.
faulthandler.dump_traceback_later(60, exit=True)

# The bridge reads its configuration at import time and has no default for the
# password, so this has to be set before the import.
os.environ.setdefault("RCON_PASSWORD", "test")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bridge"))

import bridge  # noqa: E402


def read_png_chunks(png):
    """Walk a PNG into (tag, payload), checking every CRC on the way."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "missing PNG signature"
    chunks = []
    offset = 8
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        tag = png[offset + 4:offset + 8]
        payload = png[offset + 8:offset + 8 + length]
        stored = struct.unpack(">I", png[offset + 8 + length:offset + 12 + length])[0]
        assert stored == zlib.crc32(tag + payload) & 0xFFFFFFFF, "bad CRC on %r" % tag
        chunks.append((tag, payload))
        offset += 12 + length
    return chunks


class WebSocketHandshake(unittest.TestCase):
    def test_accept_matches_the_rfc_example(self):
        # The worked example from RFC 6455 section 1.3. A browser refuses the
        # connection outright when this value is wrong.
        self.assertEqual(bridge.websocket_accept("dGhlIHNhbXBsZSBub25jZQ=="),
                         "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class PngEncoding(unittest.TestCase):
    def test_header_reports_the_size_and_colour_type(self):
        png = bridge.encode_png(2, 3, bytes(2 * 3 * 4), channels=4)
        tag, payload = read_png_chunks(png)[0]
        self.assertEqual(tag, b"IHDR")
        width, height, depth, colour = struct.unpack(">IIBB", payload[:10])
        self.assertEqual((width, height, depth), (2, 3, 8))
        self.assertEqual(colour, 6, "four channels must be declared as RGBA")

    def test_pixels_survive_the_round_trip(self):
        pixels = bytes(range(2 * 2 * 4))
        png = bridge.encode_png(2, 2, pixels, channels=4)
        data = b"".join(payload for tag, payload in read_png_chunks(png) if tag == b"IDAT")
        raw = zlib.decompress(data)
        stride = 2 * 4
        for row in range(2):
            start = row * (stride + 1)
            self.assertEqual(raw[start], 0, "rows are written with filter type none")
            self.assertEqual(raw[start + 1:start + 1 + stride],
                             pixels[row * stride:(row + 1) * stride])

    def test_three_channel_tiles_declare_rgb(self):
        png = bridge.encode_png(1, 1, bytes(3), channels=3)
        _, payload = read_png_chunks(png)[0]
        self.assertEqual(payload[9], 2, "three channels must be declared as RGB")

    def test_ends_with_iend(self):
        png = bridge.encode_png(1, 1, bytes(4))
        self.assertEqual(read_png_chunks(png)[-1][0], b"IEND")


class QueryDecoding(unittest.TestCase):
    """A browser percent encodes the commas in "chunks=-15,-9,15,9". Reading
    the value raw produced a viewport the server could not parse, and an empty
    map with every other channel working."""

    def query(self, path):
        handler = bridge.Handler.__new__(bridge.Handler)
        handler.path = path
        return bridge.Handler._query(handler)

    def test_percent_encoded_commas_are_decoded(self):
        self.assertEqual(self.query("/chunks?chunks=-15%2C-9%2C15%2C9")["chunks"],
                         "-15,-9,15,9")

    def test_plain_commas_still_work(self):
        self.assertEqual(self.query("/chunks?chunks=-15,-9,15,9")["chunks"],
                         "-15,-9,15,9")

    def test_plus_is_a_space_in_a_value(self):
        self.assertEqual(self.query("/tags?surface=my+base")["surface"], "my base")

    def test_no_query_gives_nothing(self):
        self.assertEqual(self.query("/chunks"), {})

    def test_a_valueless_key_is_kept(self):
        self.assertEqual(self.query("/x?debug")["debug"], "")


class FakeClient:
    def __init__(self, layers=None, identifier=None):
        self.open = True
        self.layers = layers or {}
        self.identifier = identifier
        self.sent = []

    def send(self, channel, payload, force=False):
        self.sent.append((channel, payload))

    def close(self):
        self.open = False


class HubBehaviour(unittest.TestCase):
    def setUp(self):
        self.hub = bridge.Hub()

    def test_viewers_counts_open_clients(self):
        first, second = FakeClient(), FakeClient()
        self.hub.add(first)
        self.hub.add(second)
        self.assertEqual(self.hub.viewers(), 2)

    def test_a_closed_client_stops_counting(self):
        client = FakeClient()
        self.hub.add(client)
        client.open = False
        self.assertEqual(self.hub.viewers(), 0)

    def test_remove_closes_the_client(self):
        client = FakeClient()
        self.hub.add(client)
        self.hub.remove(client)
        self.assertFalse(client.open)
        self.assertEqual(self.hub.viewers(), 0)

    def test_removing_twice_is_harmless(self):
        client = FakeClient()
        self.hub.add(client)
        self.hub.remove(client)
        self.hub.remove(client)

    def test_broadcast_skips_closed_clients(self):
        live, dead = FakeClient(), FakeClient()
        self.hub.add(live)
        self.hub.add(dead)
        dead.open = False
        # Joining is itself a broadcast now, so count what this one delivered
        # rather than what each client has ever been sent.
        before = len(live.sent)
        self.hub.broadcast("state", {"players": []})
        self.assertEqual(len(live.sent), before + 1)
        self.assertNotIn(("state", {"players": []}), dead.sent)

    def test_any_layer_is_true_when_one_client_wants_it(self):
        self.hub.add(FakeClient(layers={"signals": False}))
        self.hub.add(FakeClient(layers={"signals": True}))
        self.assertTrue(self.hub.any_layer("signals"))
        self.assertFalse(self.hub.any_layer("units"))

    def test_any_layer_ignores_a_closed_client(self):
        client = FakeClient(layers={"units": True})
        self.hub.add(client)
        client.open = False
        self.assertFalse(self.hub.any_layer("units"))

    def test_by_identifier_finds_the_right_client(self):
        wanted = FakeClient(identifier="abc")
        self.hub.add(FakeClient(identifier="xyz"))
        self.hub.add(wanted)
        self.assertIs(self.hub.by_identifier("abc"), wanted)
        self.assertIsNone(self.hub.by_identifier("nobody"))


class ViewerCount(unittest.TestCase):
    """How many browsers have the map open. The hub knows the moment that
    changes, so the number is pushed on join and on leave and never polled."""

    def setUp(self):
        self.hub = bridge.Hub()

    def counts(self, client):
        return [payload["viewers"] for channel, payload in client.sent if channel == "viewers"]

    def test_a_joining_client_is_told_the_count(self):
        client = FakeClient()
        self.hub.add(client)
        self.assertEqual(self.counts(client), [1],
                         "a browser must not wait for the next change to learn the count")

    def test_everyone_hears_a_client_join(self):
        first, second = FakeClient(), FakeClient()
        self.hub.add(first)
        self.hub.add(second)
        self.assertEqual(self.counts(first), [1, 2])
        self.assertEqual(self.counts(second), [2])

    def test_everyone_left_hears_a_client_leave(self):
        first, second = FakeClient(), FakeClient()
        self.hub.add(first)
        self.hub.add(second)
        self.hub.remove(second)
        self.assertEqual(self.counts(first), [1, 2, 1])
        self.assertEqual(self.counts(second), [2], "a client that left is not told anything")

    def test_a_closed_client_stops_counting(self):
        # A browser that went away without the socket closing cleanly is still
        # in the list, so the count has to come from the open clients only.
        stale, client = FakeClient(), FakeClient()
        self.hub.add(stale)
        stale.open = False
        self.hub.add(client)
        self.assertEqual(self.counts(client), [1])

    def test_the_lock_is_free_while_the_count_goes_out(self):
        # broadcast() takes the hub lock again through snapshot(), and a plain
        # Lock is not reentrant: announcing inside it would hang the connection
        # thread, and with it every browser. Run on a worker so this one reports
        # the deadlock itself rather than leaning on the module's watchdog.
        done = threading.Event()

        def join_and_leave():
            client = FakeClient()
            self.hub.add(client)
            self.hub.remove(client)
            done.set()

        threading.Thread(target=join_and_leave, daemon=True).start()
        self.assertTrue(done.wait(5), "add() or remove() deadlocked announcing the count")


class SpritePaths(unittest.TestCase):
    """Sprites are addressed by prototype name. The name is validated rather
    than joined on and hoped for, so a request cannot walk out of the sprite
    directory."""

    def setUp(self):
        self.saved = bridge.SPRITE_DIR
        bridge.SPRITE_DIR = "/srv/sprites"

    def tearDown(self):
        bridge.SPRITE_DIR = self.saved

    def test_a_prototype_name_resolves(self):
        self.assertEqual(bridge.sprite_path("stone-furnace.png"),
                         os.path.join("/srv/sprites", "stone-furnace.png"))

    def test_underscores_and_digits_are_fine(self):
        self.assertIsNotNone(bridge.sprite_path("assembling_machine_1.png"))

    def test_traversal_is_refused(self):
        for name in ("../../etc/passwd.png", "..%2f..%2fetc.png", "a/b.png",
                     "a\\b.png", "....png", ".png", "sub.dir.png"):
            self.assertIsNone(bridge.sprite_path(name), name)

    def test_a_non_png_is_refused(self):
        self.assertIsNone(bridge.sprite_path("stone-furnace.lua"))
        self.assertIsNone(bridge.sprite_path("stone-furnace"))

    def test_nothing_resolves_when_sprites_are_not_configured(self):
        bridge.SPRITE_DIR = ""
        self.assertIsNone(bridge.sprite_path("stone-furnace.png"),
                          "an unset sprite directory must serve nothing at all")

    def test_an_absurdly_long_name_is_refused(self):
        self.assertIsNone(bridge.sprite_path("a" * 300 + ".png"))


class WorldCharting(unittest.TestCase):
    def setUp(self):
        self.world = bridge.World()

    def test_an_uncharted_chunk_has_no_revision(self):
        self.world.set_index("nauvis", {"chunks": [[0, 0, 7]]})
        self.assertEqual(self.world.revision("nauvis", 0, 0), 7)
        self.assertIsNone(self.world.revision("nauvis", 5, 5),
                          "strict fog must refuse to render an uncharted chunk")

    def test_a_dirty_chunk_is_charted_before_the_index_catches_up(self):
        self.world.set_index("nauvis", {"chunks": []})
        self.world.note_charted("nauvis", 3, 4, 12)
        self.assertEqual(self.world.revision("nauvis", 3, 4), 12)

    def test_dropping_a_column_clears_every_zoom_built_from_it(self):
        for zoom in range(bridge.MAX_ZOOM + 1):
            self.world.put_tile(("nauvis", zoom, 8 >> zoom, 8 >> zoom), "sig", b"")
        # A neighbour that shares no ancestry must survive.
        self.world.put_tile(("nauvis", 0, 100, 100), "sig", b"")
        self.world.drop_tile_column("nauvis", 8, 8)
        self.assertEqual(list(self.world.tiles), [("nauvis", 0, 100, 100)])

    def test_the_tile_cache_evicts_the_least_recently_used(self):
        original = bridge.TILE_CACHE_SIZE
        bridge.TILE_CACHE_SIZE = 2
        try:
            self.world.put_tile(("nauvis", 0, 1, 1), "a", b"")
            self.world.put_tile(("nauvis", 0, 2, 2), "b", b"")
            self.world.get_tile(("nauvis", 0, 1, 1))     # touch, so 2,2 is oldest
            self.world.put_tile(("nauvis", 0, 3, 3), "c", b"")
            self.assertEqual(sorted(self.world.tiles),
                             [("nauvis", 0, 1, 1), ("nauvis", 0, 3, 3)])
        finally:
            bridge.TILE_CACHE_SIZE = original


GROUND = (60, 60, 60)
RAIL = (200, 200, 200)


class TilePyramid(unittest.TestCase):
    """Zooming out halves four child tiles into one. A rail is a single tile
    wide, so averaging or taking the top left pixel would erase the track at
    every zoom step; the merge prefers a built thing over bare ground."""

    def setUp(self):
        self.charted = {(x, y) for x in range(2) for y in range(2)}
        self.leaf = self.ground_leaf()

        self.saved_world = bridge.WORLD
        self.saved_revision = bridge.chunk_revision
        self.saved_render = bridge.render_leaf
        self.saved_structure = bridge.PALETTE["structure"]

        bridge.WORLD = bridge.World()
        bridge.chunk_revision = lambda surface, x, y: 1 if (x, y) in self.charted else None
        bridge.render_leaf = lambda surface, x, y: self.leaf
        bridge.PALETTE["structure"] = {bytes(RAIL)}

    def tearDown(self):
        bridge.WORLD = self.saved_world
        bridge.chunk_revision = self.saved_revision
        bridge.render_leaf = self.saved_render
        bridge.PALETTE["structure"] = self.saved_structure

    def ground_leaf(self):
        pixels = bytearray()
        for _ in range(bridge.TILE_PX * bridge.TILE_PX):
            pixels += bytes(GROUND) + b"\xff"
        return pixels

    def pixel(self, pixels, x, y):
        base = (y * bridge.TILE_PX + x) * 4
        return tuple(pixels[base:base + 3])

    def paint(self, pixels, x, y, colour):
        base = (y * bridge.TILE_PX + x) * 4
        pixels[base:base + 4] = bytes(colour) + b"\xff"

    def test_nothing_charted_means_nothing_to_draw(self):
        self.charted = set()
        signature, pixels = bridge.tile_pixels("nauvis", 0, 0, 0)
        self.assertIsNone(signature)
        self.assertIsNone(pixels)

    def test_a_partly_charted_parent_still_renders(self):
        self.charted = {(0, 0)}
        signature, pixels = bridge.tile_pixels("nauvis", 1, 0, 0)
        self.assertIsNotNone(signature)
        self.assertIn("-", signature, "uncharted children are recorded as holes")

    def test_a_one_pixel_rail_survives_a_zoom_step(self):
        self.paint(self.leaf, 5, 0, RAIL)
        _, pixels = bridge.tile_pixels("nauvis", 1, 0, 0)
        self.assertEqual(self.pixel(pixels, 2, 0), RAIL,
                         "a rail must not be averaged away when zooming out")

    def test_bare_ground_stays_ground(self):
        _, pixels = bridge.tile_pixels("nauvis", 1, 0, 0)
        self.assertEqual(self.pixel(pixels, 2, 0), GROUND)

    def test_a_rail_survives_every_zoom_step(self):
        self.charted = {(x, y) for x in range(8) for y in range(8)}
        self.paint(self.leaf, 5, 0, RAIL)
        _, pixels = bridge.tile_pixels("nauvis", 3, 0, 0)
        row = [self.pixel(pixels, x, 0) for x in range(bridge.TILE_PX)]
        self.assertIn(RAIL, row, "the rail is gone by zoom 3")

    def test_the_signature_changes_when_a_chunk_changes(self):
        before = bridge.tile_signature("nauvis", 1, 0, 0)
        bridge.chunk_revision = lambda surface, x, y: 2 if (x, y) in self.charted else None
        self.assertNotEqual(bridge.tile_signature("nauvis", 1, 0, 0), before)

    def test_an_unchanged_tile_is_served_from_cache(self):
        signature, first = bridge.tile_pixels("nauvis", 1, 0, 0)
        calls = []
        bridge.render_leaf = lambda surface, x, y: calls.append((x, y)) or self.leaf
        again, second = bridge.tile_pixels("nauvis", 1, 0, 0)
        self.assertEqual(again, signature)
        self.assertIs(second, first)
        self.assertEqual(calls, [], "a cached tile must not re-ask the game")


if __name__ == "__main__":
    unittest.main()
