#!/usr/bin/env python3
"""Tests for the PNG reader used to crop spritesheets.

A wrongly decoded sheet draws the wrong thing, and every sprite failure this
project has had was silent, so the decoder is checked against every filter type
PNG can use rather than against one sample that happens to work.
"""
import importlib.util
import os
import unittest
import zlib
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("chartorio_png",
                                               os.path.join(ROOT, "render", "png.py"))
png = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(png)


def encode_with_filter(width, height, channels, pixels, filter_type):
    """Write a PNG using one specific filter, so the reader is tested against
    all five rather than only the one the writer happens to emit."""
    stride = width * channels
    raw = bytearray()
    previous = bytearray(stride)
    for row in range(height):
        line = bytearray(pixels[row * stride:(row + 1) * stride])
        out = bytearray(line)
        if filter_type == 1:
            for i in range(stride - 1, channels - 1, -1):
                out[i] = (line[i] - line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                out[i] = (line[i] - previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                out[i] = (line[i] - ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upleft = previous[i - channels] if i >= channels else 0
                out[i] = (line[i] - png._paeth(left, previous[i], upleft)) & 0xFF
        raw.append(filter_type)
        raw += out
        previous = line

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return (png.SIGNATURE + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))


def gradient(width, height, channels):
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes(((x * 7 + y * 13) % 256, (x * 3) % 256, (y * 5) % 256)[:3])
            if channels == 4:
                out += bytes(((x + y) % 256,))
    return bytes(out)


class Roundtrip(unittest.TestCase):
    def test_every_filter_type_decodes_to_the_same_image(self):
        width, height = 9, 7
        for channels in (3, 4):
            pixels = gradient(width, height, channels)
            for filter_type in range(5):
                data = encode_with_filter(width, height, channels, pixels, filter_type)
                w, h, c, got = png.read(data)
                self.assertEqual((w, h, c), (width, height, channels))
                self.assertEqual(got, pixels, "filter %d, %d channels" % (filter_type, channels))

    def test_what_this_module_writes_it_can_read(self):
        pixels = gradient(4, 4, 4)
        w, h, c, got = png.read(png.write(4, 4, 4, pixels))
        self.assertEqual((w, h, c, got), (4, 4, 4, pixels))


class Cropping(unittest.TestCase):
    def setUp(self):
        self.pixels = gradient(8, 8, 4)
        self.sheet = png.write(8, 8, 4, self.pixels)

    def test_a_crop_holds_the_pixels_that_were_at_that_offset(self):
        out = png.crop(self.sheet, 2, 3, 4, 2)
        w, h, c, got = png.read(out)
        self.assertEqual((w, h, c), (4, 2, 4))
        for row in range(2):
            start = ((row + 3) * 8 + 2) * 4
            self.assertEqual(got[row * 16:(row + 1) * 16], self.pixels[start:start + 16])

    def test_the_whole_sheet_crops_to_itself(self):
        w, h, c, got = png.read(png.crop(self.sheet, 0, 0, 8, 8))
        self.assertEqual(got, self.pixels)

    def test_a_crop_outside_the_sheet_is_refused(self):
        # Clamping silently is how a sheet ends up drawing the wrong thing.
        for box in ((6, 0, 4, 2), (0, 7, 2, 4), (-1, 0, 2, 2), (0, 0, 9, 9)):
            with self.assertRaises(png.UnsupportedPNG, msg=str(box)):
                png.crop(self.sheet, *box)


class Refusals(unittest.TestCase):
    def test_something_that_is_not_a_png_is_refused(self):
        with self.assertRaises(png.UnsupportedPNG):
            png.read(b"not a png at all")

    def test_an_interlaced_png_is_refused_rather_than_misread(self):
        data = bytearray(png.write(2, 2, 4, gradient(2, 2, 4)))
        data[8 + 8 + 12] = 1                     # interlace flag in IHDR
        with self.assertRaises(png.UnsupportedPNG):
            png.read(bytes(data))


class AgainstTheGamesOwnArt(unittest.TestCase):
    """The decoder only matters if it reads Wube's files, which are the ones it
    will ever see. Skipped where the game is not installed."""

    ART = "/Applications/factorio.app/Contents/data/base/graphics/icons/iron-plate.png"

    def test_a_real_icon_decodes_to_its_declared_size(self):
        if not os.path.isfile(self.ART):
            self.skipTest("no Factorio install here")
        with open(self.ART, "rb") as handle:
            data = handle.read()
        width, height, channels, pixels = png.read(data)
        self.assertEqual(len(pixels), width * height * channels)
        # An icon carries its mipmaps beside it, so it is wider than it is tall.
        self.assertGreater(width, height)
        cropped = png.crop(data, 0, 0, height, height)
        w, h, _, _ = png.read(cropped)
        self.assertEqual((w, h), (height, height))


if __name__ == "__main__":
    unittest.main()
