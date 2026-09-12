#!/usr/bin/env python3
"""Just enough PNG to crop a spritesheet.

The bridge writes PNGs by hand over `zlib` and has never needed to read one.
Cropping does, so this reads and writes the one shape the game's art uses:
eight bits a channel, RGB or RGBA, non-interlaced. Anything else is refused
rather than guessed at, because a wrongly decoded sheet draws the wrong thing
and this project's sprite failures have all been silent ones.

Standard library only, like everything else here.
"""
import struct
import zlib


class UnsupportedPNG(Exception):
    pass


SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunks(data):
    if data[:8] != SIGNATURE:
        raise UnsupportedPNG("not a PNG")
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        tag = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        yield tag, payload
        offset += 12 + length


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read(data):
    """Return (width, height, channels, pixel bytes), row major."""
    header = None
    body = bytearray()
    for tag, payload in _chunks(data):
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload[:13])
        elif tag == b"IDAT":
            body += payload
        elif tag == b"IEND":
            break
    if header is None:
        raise UnsupportedPNG("no header")
    width, height, depth, colour, compression, filtering, interlace = header
    if depth != 8 or compression != 0 or filtering != 0 or interlace != 0:
        raise UnsupportedPNG("only 8 bit, non interlaced")
    channels = {2: 3, 6: 4}.get(colour)
    if channels is None:
        raise UnsupportedPNG("only RGB and RGBA")

    raw = zlib.decompress(bytes(body))
    stride = width * channels
    out = bytearray(stride * height)
    previous = bytearray(stride)
    at = 0
    for row in range(height):
        filter_type = raw[at]
        at += 1
        line = bytearray(raw[at:at + stride])
        at += stride
        if filter_type == 1:                       # Sub
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:                     # Up
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:                     # Average
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:                     # Paeth
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                upleft = previous[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(left, previous[i], upleft)) & 0xFF
        elif filter_type != 0:
            raise UnsupportedPNG("unknown filter %d" % filter_type)
        out[row * stride:(row + 1) * stride] = line
        previous = line
    return width, height, channels, bytes(out)


def write(width, height, channels, pixels):
    raw = bytearray()
    stride = width * channels
    for row in range(height):
        raw.append(0)                              # filter type: none
        raw += pixels[row * stride:(row + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8,
                         6 if channels == 4 else 2, 0, 0, 0)
    return (SIGNATURE
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def crop(data, x, y, width, height):
    """A rectangle of a sheet, as a PNG. Asking for anything outside it is a
    mistake worth hearing about rather than clamping silently."""
    full_width, full_height, channels, pixels = read(data)
    if x < 0 or y < 0 or x + width > full_width or y + height > full_height:
        raise UnsupportedPNG("crop %dx%d+%d+%d outside %dx%d"
                             % (width, height, x, y, full_width, full_height))
    stride = full_width * channels
    out = bytearray()
    for row in range(y, y + height):
        start = row * stride + x * channels
        out += pixels[start:start + width * channels]
    return write(width, height, channels, bytes(out))
