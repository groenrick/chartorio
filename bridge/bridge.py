#!/usr/bin/env python3
"""Chartorio: bridges the Factorio RCON interface to the browser map.

Three things happen here. Live player and train positions are polled and
pushed to browsers over server-sent events. Chunk rasters are fetched on
demand and baked into PNG tiles. Chunks the game reports as changed have
their tiles dropped from the cache so the browser refetches them.

Standard library only, so the container needs no extra packages.
"""
import json
import os
import socket
import struct
import threading
import time
import zlib
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RCON_HOST = os.environ.get("RCON_HOST", "127.0.0.1")
RCON_PORT = int(os.environ.get("RCON_PORT", "27015"))
RCON_PASSWORD = os.environ["RCON_PASSWORD"]
LISTEN_HOST = os.environ.get("CHARTORIO_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("CHARTORIO_PORT", "8080"))
STATE_INTERVAL = float(os.environ.get("CHARTORIO_STATE_INTERVAL", "0.25"))
DIRTY_INTERVAL = float(os.environ.get("CHARTORIO_DIRTY_INTERVAL", "2"))
INDEX_INTERVAL = float(os.environ.get("CHARTORIO_INDEX_INTERVAL", "10"))
TILE_CACHE_SIZE = int(os.environ.get("CHARTORIO_TILE_CACHE", "3000"))
MAX_ZOOM = int(os.environ.get("CHARTORIO_MAX_ZOOM", "3"))
TILE_PX = 64
# strict keeps the fog honest: only charted chunks can be rendered at all.
FOG = os.environ.get("CHARTORIO_FOG", "strict")


def _static_dir():
    """Web assets sit in ../web in the repository and next to the script once
    installed, so accept either without configuration."""
    override = os.environ.get("CHARTORIO_WEB")
    if override:
        return override
    here = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.join(os.path.dirname(here), "web")
    if os.path.isfile(os.path.join(sibling, "index.html")):
        return sibling
    return here


STATIC_DIR = _static_dir()

SOCKET_TIMEOUT = 20.0
FRAGMENT_TIMEOUT = 0.3   # how long to wait for a possible continuation packet

AUTH = 3
AUTH_RESPONSE = 2
EXEC_COMMAND = 2
RESPONSE_VALUE = 0


class RconError(Exception):
    pass


class RconClient:
    """Minimal Source RCON client, enough for single command round trips."""

    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.socket = None
        self.request_id = 0
        self.lock = threading.Lock()

    def command(self, body):
        """Run one command. Serialised, because a single socket is shared."""
        with self.lock:
            for attempt in (1, 2):
                try:
                    if self.socket is None:
                        self._connect()
                    return self._command(body)
                except (OSError, RconError):
                    self.close()
                    if attempt == 2:
                        raise
        raise RconError("unreachable")

    def close(self):
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def _connect(self):
        self.socket = socket.create_connection((self.host, self.port), timeout=SOCKET_TIMEOUT)
        request_id = self._write(AUTH, self.password)
        while True:
            packet_id, packet_type, _ = self._read_packet()
            if packet_type != AUTH_RESPONSE:
                continue
            if packet_id == -1:
                raise RconError("RCON authentication rejected")
            if packet_id != request_id:
                raise RconError("unexpected auth response id %d" % packet_id)
            return

    def _command(self, body):
        """Read one response, which may or may not be split.

        A short packet is the whole answer. A long one may be the first of
        several fragments, or it may be a single large packet, and nothing in
        the protocol says which. So after a long packet, wait briefly for a
        follow-up and treat silence as the end. Waiting unconditionally would
        add that delay to every poll.
        """
        request_id = self._write(EXEC_COMMAND, body)
        parts = []
        try:
            while True:
                try:
                    packet_id, packet_type, payload = self._read_packet()
                except (socket.timeout, TimeoutError):
                    if parts:
                        break
                    raise
                if packet_type != RESPONSE_VALUE or packet_id != request_id:
                    continue
                parts.append(payload)
                if len(payload) < 4000:
                    break
                self.socket.settimeout(FRAGMENT_TIMEOUT)
        finally:
            if self.socket is not None:
                self.socket.settimeout(SOCKET_TIMEOUT)
        return "".join(parts)

    def _write(self, packet_type, body):
        self.request_id += 1
        payload = struct.pack("<ii", self.request_id, packet_type) + body.encode("utf-8") + b"\x00\x00"
        self.socket.sendall(struct.pack("<i", len(payload)) + payload)
        return self.request_id

    def _read_exactly(self, count):
        data = b""
        while len(data) < count:
            chunk = self.socket.recv(count - len(data))
            if not chunk:
                raise RconError("RCON connection closed by the server")
            data += chunk
        return data

    def _read_packet(self):
        length = struct.unpack("<i", self._read_exactly(4))[0]
        if length < 10 or length > 4 * 1024 * 1024:
            raise RconError("implausible RCON packet length %d" % length)
        body = self._read_exactly(length)
        packet_id, packet_type = struct.unpack("<ii", body[:8])
        return packet_id, packet_type, body[8:-2].decode("utf-8", "replace")


RCON = RconClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)


def call(command):
    return json.loads(RCON.command(command))


class Broadcaster:
    """Fan-out of named events to every open server-sent events stream."""

    def __init__(self, backlog=64):
        self.condition = threading.Condition()
        self.sequence = 0
        self.messages = []
        self.backlog = backlog

    def publish(self, name, payload):
        with self.condition:
            self.sequence += 1
            self.messages.append((self.sequence, name, json.dumps(payload)))
            del self.messages[:-self.backlog]
            self.condition.notify_all()

    def current(self):
        with self.condition:
            return self.sequence

    def since(self, seen, timeout):
        with self.condition:
            if self.sequence == seen:
                self.condition.wait(timeout)
            pending = [m for m in self.messages if m[0] > seen]
            return self.sequence, pending


EVENTS = Broadcaster()


class World:
    """Everything the browser can ask for, kept warm by the pollers."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"connected": False, "players": [], "trains": []}
        self.index = {}          # surface -> {"size": px, "chunks": [[x, y, revision], ...]}
        self.revisions = {}      # surface -> {(x, y): revision} for fast lookups
        self.tiles = OrderedDict()  # (surface, zoom, x, y) -> (signature, RGBA pixels)
        self.pngs = OrderedDict()   # (surface, zoom, x, y) -> (signature, png bytes)
        self.tags = {}           # surface -> [tag, ...]
        self.pollution = {}      # surface -> {"peak": n, "chunks": [x, y, amount, ...]}
        self.alerts = []         # recent losses, newest last

    def set_state(self, state):
        with self.lock:
            self.state = state

    def get_state(self):
        with self.lock:
            return self.state

    def set_index(self, surface, payload):
        revisions = {(entry[0], entry[1]): entry[2] for entry in payload.get("chunks", [])}
        with self.lock:
            self.index[surface] = payload
            self.revisions[surface] = revisions

    def get_index(self, surface):
        with self.lock:
            return self.index.get(surface)

    def revision(self, surface, chunk_x, chunk_y):
        """None means the chunk is not charted, so there is nothing to draw."""
        with self.lock:
            revisions = self.revisions.get(surface)
            if revisions is None:
                return 0 if FOG != "strict" else None
            found = revisions.get((chunk_x, chunk_y))
            if found is None and FOG != "strict":
                return 0
            return found

    def note_charted(self, surface, chunk_x, chunk_y, revision):
        """A chunk reported dirty is charted, even if the index has not caught up."""
        with self.lock:
            self.revisions.setdefault(surface, {})[(chunk_x, chunk_y)] = revision

    def get_tile(self, key):
        with self.lock:
            entry = self.tiles.get(key)
            if entry is not None:
                self.tiles.move_to_end(key)
            return entry

    def put_tile(self, key, signature, pixels):
        with self.lock:
            self.tiles[key] = (signature, pixels)
            self.tiles.move_to_end(key)
            while len(self.tiles) > TILE_CACHE_SIZE:
                self.tiles.popitem(last=False)

    def drop_tile(self, key):
        with self.lock:
            self.tiles.pop(key, None)

    def drop_tile_column(self, surface, chunk_x, chunk_y):
        """Drop a chunk's tile and every zoomed out tile built from it."""
        with self.lock:
            for zoom in range(MAX_ZOOM + 1):
                self.tiles.pop((surface, zoom, chunk_x >> zoom, chunk_y >> zoom), None)
            for zoom in range(MAX_ZOOM + 1):
                self.pngs.pop((surface, zoom, chunk_x >> zoom, chunk_y >> zoom), None)

    def get_png(self, key):
        with self.lock:
            entry = self.pngs.get(key)
            if entry is not None:
                self.pngs.move_to_end(key)
            return entry

    def put_png(self, key, signature, png):
        with self.lock:
            self.pngs[key] = (signature, png)
            self.pngs.move_to_end(key)
            while len(self.pngs) > TILE_CACHE_SIZE:
                self.pngs.popitem(last=False)


WORLD = World()


class UnitCache:
    """Biters move constantly; one lookup per interval is shared by all viewers."""

    def __init__(self, ttl=0.15):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.entries = {}

    def get(self, box):
        now = time.time()
        with self.lock:
            entry = self.entries.get(box)
            if entry and now - entry[0] < self.ttl:
                return entry[1]
        payload = call("/chartorio_units %s %d %d %d %d" % box)
        if not isinstance(payload.get("units"), list):
            payload["units"] = []
        with self.lock:
            self.entries[box] = (now, payload)
            if len(self.entries) > 64:
                for key in [k for k, v in self.entries.items() if now - v[0] > 5]:
                    self.entries.pop(key, None)
        return payload


UNITS = UnitCache()


class RegionCache:
    """Viewport scoped lookups. A played save holds tens of thousands of
    chunks, so nothing here may ever walk the whole map."""

    def __init__(self, command, ttl):
        self.command = command
        self.ttl = ttl
        self.lock = threading.Lock()
        self.entries = {}

    def get(self, surface, box):
        now = time.time()
        cache_key = (surface,) + box
        with self.lock:
            entry = self.entries.get(cache_key)
            if entry and now - entry[0] < self.ttl:
                return entry[1]
        payload = call("%s %s %d %d %d %d" % ((self.command, surface) + box))
        chunks = payload.get("chunks")
        payload["chunks"] = chunks if isinstance(chunks, list) else []
        with self.lock:
            self.entries[cache_key] = (now, payload)
            if len(self.entries) > 64:
                for key in [k for k, v in self.entries.items() if now - v[0] > self.ttl * 4]:
                    self.entries.pop(key, None)
        return payload


INDEX = RegionCache("/chartorio_chunks", 10.0)
POLLUTION = RegionCache("/chartorio_pollution", 20.0)


class ResourceCache:
    """A patch walk is expensive, and every pixel of a patch gives the same
    answer, so cache per chunk rather than per hovered tile."""

    def __init__(self, ttl=30.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.entries = {}

    def get(self, surface, x, y):
        chunk = (surface, x // 32, y // 32)
        now = time.time()
        with self.lock:
            entry = self.entries.get(chunk)
            if entry and now - entry[0] < self.ttl:
                return entry[1]
        payload = call("/chartorio_resource %s %d %d" % (surface, x, y))
        with self.lock:
            # A miss is only cached for the tile it came from: one chunk can
            # hold both ore and empty ground.
            if payload.get("found"):
                self.entries[chunk] = (now, payload)
                if len(self.entries) > 256:
                    for key in [k for k, v in self.entries.items() if now - v[0] > self.ttl]:
                        self.entries.pop(key, None)
        return payload


RESOURCES = ResourceCache()


def encode_png(width, height, pixels, channels=4):
    """Write a PNG. `pixels` holds `channels` bytes per pixel, row major.
    Four channels keeps unexplored ground transparent inside a zoomed out tile."""
    raw = bytearray()
    stride = width * channels
    for row in range(height):
        raw.append(0)  # filter type: none
        raw += pixels[row * stride:(row + 1) * stride]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
            + chunk(b"IEND", b""))


PALETTE = {"size": 0, "colors": {0: (26, 22, 18)}, "keys": {}, "structure": set()}
PALETTE_LOCK = threading.Lock()


def refresh_palette(expected_size):
    """The palette only grows, so refetch it when the game reports new entries."""
    with PALETTE_LOCK:
        if expected_size <= PALETTE["size"]:
            return PALETTE["colors"]
        payload = call("/chartorio_palette")
        colors = {0: (26, 22, 18)}
        keys = {}
        for entry in payload.get("colors", []):
            color = entry.get("color")
            if color:
                colors[entry["index"]] = tuple(color)
                keys[entry["key"]] = list(color)
        PALETTE["colors"] = colors
        PALETTE["keys"] = keys
        PALETTE["size"] = len(payload.get("colors", []))
        # Entity colours, as raw RGB triples, so zooming out can keep thin
        # structures such as rails instead of averaging them away.
        PALETTE["structure"] = {
            bytes(color) for key, color in keys.items() if not key.startswith("t:")
        }
        return colors


def render_leaf(surface, chunk_x, chunk_y):
    """One chunk, straight from the game, as RGBA pixels."""
    payload = call("/chartorio_chunk %s %d %d" % (surface, chunk_x, chunk_y))
    if "runs" not in payload:
        raise RconError(payload.get("error", "malformed chunk response"))
    colors = refresh_palette(payload.get("palette_size", 0))
    size = payload["size"]
    fallback = colors[0]
    pixels = bytearray()
    runs = payload["runs"]
    for position in range(0, len(runs), 2):
        count, index = runs[position], runs[position + 1]
        color = colors.get(index, fallback)
        pixels += bytes((color[0], color[1], color[2], 255)) * count
    expected = size * size * 4
    if len(pixels) < expected:
        pixels += bytes((fallback[0], fallback[1], fallback[2], 255)) * ((expected - len(pixels)) // 4)
    return pixels[:expected]


def chunk_revision(surface, chunk_x, chunk_y):
    """The charted set is learned from viewport queries, so a tile can be asked
    for before anything told us about its chunk. Ask the game about that one
    chunk rather than refusing to draw it."""
    revision = WORLD.revision(surface, chunk_x, chunk_y)
    if revision is not None:
        return revision
    try:
        payload = INDEX.get(surface, (chunk_x, chunk_y, chunk_x, chunk_y))
    except (OSError, RconError, ValueError):
        return None
    chunks = payload.get("chunks") or []
    if len(chunks) >= 3:
        WORLD.note_charted(surface, chunks[0], chunks[1], chunks[2])
        return chunks[2]
    return None


def tile_signature(surface, zoom, tile_x, tile_y):
    """None when nothing under this tile is charted, so there is nothing to draw.
    Otherwise a string that changes whenever any chunk beneath it changes."""
    if zoom == 0:
        revision = chunk_revision(surface, tile_x, tile_y)
        return None if revision is None else str(revision)
    parts = []
    for offset_y in (0, 1):
        for offset_x in (0, 1):
            parts.append(tile_signature(surface, zoom - 1, tile_x * 2 + offset_x, tile_y * 2 + offset_y))
    if all(part is None for part in parts):
        return None
    return "(" + ",".join("-" if part is None else part for part in parts) + ")"


def tile_pixels(surface, zoom, tile_x, tile_y):
    signature = tile_signature(surface, zoom, tile_x, tile_y)
    if signature is None:
        return None, None

    key = (surface, zoom, tile_x, tile_y)
    cached = WORLD.get_tile(key)
    if cached is not None and cached[0] == signature:
        return signature, cached[1]

    if zoom == 0:
        pixels = render_leaf(surface, tile_x, tile_y)
    else:
        # Four children, each halved, dropped into their quadrant. Nearest
        # neighbour on purpose: palette colours must survive so that hovering
        # a zoomed out tile still identifies what it is.
        pixels = bytearray(TILE_PX * TILE_PX * 4)
        half = TILE_PX // 2
        structure = PALETTE["structure"]
        for offset_y in (0, 1):
            for offset_x in (0, 1):
                _, child = tile_pixels(surface, zoom - 1, tile_x * 2 + offset_x, tile_y * 2 + offset_y)
                if child is None:
                    continue
                for row in range(half):
                    source_row = row * 2
                    for column in range(half):
                        base = (source_row * TILE_PX + column * 2) * 4
                        # Of the four pixels being merged, keep a built thing
                        # over bare ground: a rail is one tile wide and would
                        # otherwise disappear at every zoom step.
                        chosen = base
                        for candidate in (base, base + 4, base + TILE_PX * 4, base + TILE_PX * 4 + 4):
                            if child[candidate + 3] and bytes(child[candidate:candidate + 3]) in structure:
                                chosen = candidate
                                break
                        target = ((offset_y * half + row) * TILE_PX + offset_x * half + column) * 4
                        pixels[target:target + 4] = child[chosen:chosen + 4]

    WORLD.put_tile(key, signature, pixels)
    return signature, pixels


def render_tile(surface, zoom, tile_x, tile_y):
    key = (surface, zoom, tile_x, tile_y)
    signature, pixels = tile_pixels(surface, zoom, tile_x, tile_y)
    if pixels is None:
        return None, None

    cached = WORLD.get_png(key)
    if cached is not None and cached[0] == signature:
        return signature, cached[1]

    png = encode_png(TILE_PX, TILE_PX, pixels, channels=4)
    WORLD.put_png(key, signature, png)
    return signature, png


def state_poller():
    failures = 0
    while True:
        try:
            payload = call("/chartorio")
            for key in ("players", "trains"):
                if not isinstance(payload.get(key), list):
                    payload[key] = []
            payload["connected"] = True
            payload["interval"] = STATE_INTERVAL
            WORLD.set_state(payload)
            EVENTS.publish("state", payload)
            failures = 0
            time.sleep(STATE_INTERVAL)
        except (OSError, RconError, ValueError) as error:
            failures += 1
            offline = {
                "connected": False,
                "error": "%s: %s" % (type(error).__name__, error),
                "players": [],
                "trains": [],
            }
            WORLD.set_state(offline)
            EVENTS.publish("state", offline)
            time.sleep(min(30, 2 ** min(failures, 4)))


def dirty_poller():
    while True:
        time.sleep(DIRTY_INTERVAL)
        try:
            payload = call("/chartorio_dirty")
        except (OSError, RconError, ValueError):
            continue
        changed = []
        for entry in payload.get("chunks", []) or []:
            WORLD.note_charted(entry["surface"], entry["x"], entry["y"], entry.get("revision", 0))
            WORLD.drop_tile_column(entry["surface"], entry["x"], entry["y"])
            changed.append({"surface": entry["surface"], "x": entry["x"], "y": entry["y"],
                            "revision": entry.get("revision", 0)})
        if changed:
            EVENTS.publish("tiles", {"chunks": changed})

        try:
            alerts = call("/chartorio_alerts")
        except (OSError, RconError, ValueError):
            continue
        current = alerts.get("alerts")
        current = current if isinstance(current, list) else []
        if current != WORLD.alerts:
            WORLD.alerts = current
            EVENTS.publish("alerts", {"tick": alerts.get("tick", 0), "alerts": current})


def index_poller():
    """Only map tags are cheap enough to poll globally; chunks and pollution
    are fetched per viewport when a browser asks for them."""
    while True:
        surface = "nauvis"
        try:
            tags = call("/chartorio_tags %s" % surface)
            found = tags.get("tags")
            found = found if isinstance(found, list) else []
            if found != WORLD.tags.get(surface):
                WORLD.tags[surface] = found
                EVENTS.publish("tags", {"surface": surface, "tags": found})
        except (OSError, RconError, ValueError):
            pass
        time.sleep(INDEX_INTERVAL)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif path == "/state":
            self._send_json(WORLD.get_state())
        elif path == "/palette":
            if PALETTE["size"] == 0:  # nothing rendered yet, fetch it once up front
                try:
                    refresh_palette(10 ** 9)
                except (OSError, RconError, ValueError):
                    pass
            with PALETTE_LOCK:
                colors = {str(index): list(color) for index, color in PALETTE["colors"].items()}
            self._send_json({"colors": colors, "keys": PALETTE.get("keys", {})})
        elif path == "/tags":
            self._send_json({"surface": self._query().get("surface", "nauvis"),
                             "tags": WORLD.tags.get(self._query().get("surface", "nauvis"), [])})
        elif path == "/pollution":
            self._serve_region(POLLUTION, remember=False)
        elif path == "/alerts":
            self._send_json({"alerts": WORLD.alerts})
        elif path == "/resource":
            self._serve_resource()
        elif path == "/units":
            self._serve_units()
        elif path == "/chunks":
            self._serve_region(INDEX, remember=True)
        elif path.startswith("/tile/"):
            self._serve_tile(path)
        elif path == "/events":
            self._serve_events()
        else:
            self.send_error(404)

    def _query(self):
        _, _, raw = self.path.partition("?")
        query = {}
        for part in raw.split("&"):
            key, _, value = part.partition("=")
            if key:
                query[key] = value
        return query

    def _serve_region(self, cache, remember):
        query = self._query()
        surface = query.get("surface", "nauvis")
        try:
            box = (int(float(query.get("x1", -8))), int(float(query.get("y1", -8))),
                   int(float(query.get("x2", 8))), int(float(query.get("y2", 8))))
        except ValueError:
            return self.send_error(400, "region needs chunk coordinates x1, y1, x2, y2")
        try:
            payload = cache.get(surface, box)
        except (OSError, RconError, ValueError) as error:
            return self._send_json({"chunks": [], "error": str(error)})

        if remember:
            # Tile serving refuses anything not known to be charted, so feed
            # what the game just told us back into that gate.
            chunks = payload["chunks"]
            for position in range(0, len(chunks) - 2, 3):
                WORLD.note_charted(surface, chunks[position], chunks[position + 1], chunks[position + 2])
        self._send_json(payload)

    def _serve_resource(self):
        query = self._query()
        try:
            surface = query.get("surface", "nauvis")
            x = int(float(query["x"]))
            y = int(float(query["y"]))
        except (KeyError, ValueError):
            return self.send_error(400, "resource needs x and y")
        try:
            self._send_json(RESOURCES.get(surface, x, y))
        except (OSError, RconError, ValueError) as error:
            self._send_json({"found": False, "error": str(error)})

    def _serve_units(self):
        query = self._query()
        try:
            box = (query.get("surface", "nauvis"),
                   int(float(query["x1"])), int(float(query["y1"])),
                   int(float(query["x2"])), int(float(query["y2"])))
        except (KeyError, ValueError):
            return self.send_error(400, "units needs x1, y1, x2 and y2")
        try:
            self._send_json(UNITS.get(box))
        except (OSError, RconError, ValueError) as error:
            self._send_json({"units": [], "error": str(error)})

    def _serve_tile(self, path):
        # /tile/<surface>/<x>/<y>.png is zoom 0; /tile/<surface>/<z>/<x>/<y>.png
        # asks for a zoomed out tile covering 2^z chunks per side.
        parts = path[len("/tile/"):].split("/")
        if len(parts) not in (3, 4) or not parts[-1].endswith(".png"):
            return self.send_error(404)
        surface = parts[0]
        try:
            zoom = int(parts[1]) if len(parts) == 4 else 0
            tile_x = int(parts[-2])
            tile_y = int(parts[-1][:-4])
        except ValueError:
            return self.send_error(404)
        if zoom < 0 or zoom > MAX_ZOOM:
            return self.send_error(404, "zoom out of range")

        try:
            signature, png = render_tile(surface, zoom, tile_x, tile_y)
        except (OSError, RconError, ValueError, KeyError) as error:
            return self.send_error(503, "chunk render failed: %s" % error)
        if png is None:
            return self.send_error(404, "nothing charted here")

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", '"%s"' % signature)
        self.send_header("Content-Length", str(len(png)))
        self.end_headers()
        self.wfile.write(png)

    def _serve_file(self, name, content_type):
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as handle:
                body = handle.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        seen = EVENTS.current()  # a fresh client starts at now, not at the backlog
        try:
            self.wfile.write(("event: state\ndata: %s\n\n" % json.dumps(WORLD.get_state())).encode())
            self.wfile.flush()
            while True:
                seen, pending = EVENTS.since(seen, timeout=15)
                if not pending:
                    self.wfile.write(b": keepalive\n\n")
                for _, name, payload in pending:
                    self.wfile.write(("event: %s\ndata: %s\n\n" % (name, payload)).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def main():
    for worker in (state_poller, dirty_poller, index_poller):
        threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
