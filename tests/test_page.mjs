// The page is one file with no build step, so there is nothing to import.
// Boot it the way AGENTS.md describes: pull the script out of index.html and
// run it in a vm context against a small DOM stub. Booting is itself the test
// most worth having, because it catches start-up errors that a syntax check
// misses; the rest asserts the pure helpers the map's geometry depends on.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, "..", "web", "index.html"), "utf8");

function extractScript(source) {
  const blocks = [...source.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)];
  assert.equal(blocks.length, 1, "expected exactly one inline script in index.html");
  return blocks[0][1];
}

function elementStub() {
  const element = {
    style: {}, dataset: {}, textContent: "", innerHTML: "", value: "",
    hidden: false, checked: false, width: 0, height: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {}, appendChild() {}, append() {},
    removeChild() {}, remove() {}, insertBefore() {}, setAttribute() {},
    removeAttribute() {}, getAttribute: () => null, focus() {}, blur() {}, click() {},
    scrollIntoView() {}, closest: () => null, contains: () => false,
    querySelector: () => elementStub(), querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100 }),
    getContext: () => contextStub(),
    children: [], firstChild: null, parentNode: null,
  };
  return element;
}

function contextStub() {
  const noop = () => {};
  return new Proxy({
    canvas: { width: 1280, height: 720 },
    measureText: () => ({ width: 10 }),
    createImageData: (w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    getImageData: (x, y, w, h) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
    createPattern: () => null,
    setLineDash: noop,
  }, {
    get: (target, name) => (name in target ? target[name] : noop),
    set: (target, name, value) => { target[name] = value; return true; },
  });
}

function boot() {
  const timers = [];
  const eventChannels = [];
  const context = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    JSON, Math, Date, Object, Array, String, Number, Boolean, Map, Set,
    Promise, Error, isNaN, isFinite, parseInt, parseFloat,
    Uint8ClampedArray, Uint8Array, Float32Array, Float64Array, Int32Array,
    ArrayBuffer, TextDecoder, TextEncoder, URL, URLSearchParams, Proxy, Symbol,
    performance: { now: () => 0 },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => {},
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {}, setInterval: () => 1, clearInterval: () => {},
    fetch: () => new Promise(() => {}),           // never settles: no network here
    location: { protocol: "http:", host: "localhost:8080", href: "http://localhost:8080/" },
    devicePixelRatio: 1,
    innerWidth: 1280,
    innerHeight: 720,
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    Image: class { set src(_v) {} constructor() { this.onload = null; this.onerror = null; } },
    WebSocket: class {
      static OPEN = 1;
      constructor(url) { this.url = url; this.readyState = 0; }
      send() {} close() {}
    },
    EventSource: class {
      constructor(url) { this.url = url; }
      addEventListener(channel) { eventChannels.push(channel); }
      close() {}
    },
  };
  context.eventChannels = eventChannels;
  context.window = context;
  context.globalThis = context;
  context.self = context;
  const body = elementStub();
  // One stub per id, kept, so a test can read back what the page wrote into an
  // element the way a browser would.
  const byId = new Map();
  context.document = {
    body, documentElement: elementStub(), head: elementStub(),
    getElementById: (id) => {
      if (!byId.has(id)) byId.set(id, elementStub());
      return byId.get(id);
    },
    createElement: () => elementStub(),
    createElementNS: () => elementStub(),
    querySelector: () => elementStub(),
    querySelectorAll: () => [],
    addEventListener() {}, removeEventListener() {},
    getElementsByClassName: () => [], getElementsByTagName: () => [],
  };

  vm.createContext(context);
  // `let` and `const` at the top level of a script do not become properties of
  // the context the way `function` and `var` do, so the few the tests need are
  // handed out by an epilogue running in the same scope.
  const probe = "\n;globalThis.__page = { view, MAX_ZOOM, CHUNK_TILES, layers,"
              + " SPRITE_PIXELS_PER_TILE, spritesWanted, beltRow, spriteLayers, spriteCell, spriteKey,"
              + " __setSpritesAvailable(v) { spritesAvailable = v; },"
              + " get transport() { return transport; } };";
  vm.runInContext(extractScript(html) + probe, context, { filename: "index.html" });
  // Keep the accessors as accessors, so `transport` stays live, and let
  // everything else fall through to the script's own globals.
  return Object.defineProperties(Object.create(context),
                                 Object.getOwnPropertyDescriptors(context.__page));
}

test("the page boots without throwing", () => {
  const page = boot();
  assert.equal(typeof page.draw, "function", "the script defined nothing, so it did not really run");
});

test("the page opens a websocket before falling back", () => {
  // The event stream is the fallback, not the default. If this ever reads
  // "events" straight after boot, the websocket path is dead again.
  const page = boot();
  assert.equal(page.transport, "websocket");
});

test("world and screen coordinates round trip", () => {
  const page = boot();
  const back = page.screenToWorld(...Object.values(page.worldToScreen(120, -45)));
  assert.ok(Math.abs(back.x - 120) < 1e-9, `x drifted to ${back.x}`);
  assert.ok(Math.abs(back.y + 45) < 1e-9, `y drifted to ${back.y}`);
});

test("zoom level steps down as the view scales out", () => {
  const page = boot();
  const levels = [2, 0.6, 0.3, 0.05].map((scale) => {
    page.view.scale = scale;
    return page.zoomForScale();
  });
  assert.deepEqual(levels, [...levels].sort((a, b) => a - b), "zoom must not go backwards");
  assert.equal(levels[0], 0, "a close view uses full detail tiles");
  assert.equal(levels.at(-1), page.MAX_ZOOM, "a far view uses the coarsest tiles");
});

test("the viewport box pads beyond the screen so panning has tiles ready", () => {
  const page = boot();
  page.view.x = 0;
  page.view.y = 0;
  page.view.scale = 1;
  const box = page.viewportBox();
  assert.ok(box.x2 - box.x1 > 1280, "the requested box is no wider than the screen");
  assert.equal(box.chunks.length, 4, "chunks must be the four numbers the bridge parses");
  assert.ok(box.chunks[0] < box.chunks[2] && box.chunks[1] < box.chunks[3]);
});

test("units are paired to their nearest previous position", () => {
  const page = boot();
  // Two biters that swapped reading order between updates must still pair to
  // the position they actually came from, or they teleport across the screen.
  const previous = [10, 10, 40, 40];
  const next = [41, 41, 11, 11];
  const pairs = page.pairUnits(previous, next);
  assert.deepEqual([pairs[0], pairs[1]], [40, 40], "the first biter came from 40,40");
  assert.deepEqual([pairs[4], pairs[5]], [10, 10], "the second biter came from 10,10");
});

test("a biter with nobody near it starts where it is", () => {
  const page = boot();
  const pairs = page.pairUnits([], [7, 9]);
  assert.deepEqual([pairs[0], pairs[1]], [7, 9], "a new biter must not slide in from elsewhere");
});

test("the viewer count shows what the hub pushed", () => {
  const page = boot();
  page.handleMessage(JSON.stringify({ channel: "viewers", payload: { viewers: 3 } }));
  assert.equal(String(page.document.getElementById("viewer-count").textContent), "3");
});

test("the event stream fallback listens for the viewer count too", () => {
  // Every channel has to work on both transports, and the fallback subscribes
  // per channel by name: one missing here is a number that never updates.
  const page = boot();
  page.useEventStream();
  assert.ok(page.eventChannels.includes("viewers"),
            `/events subscribed to ${page.eventChannels.join(", ")}`);
});

test("a sprite covers the world size its scale implies", () => {
  // Factorio draws at 32 pixels to a world tile, so a 151px sprite at scale
  // 0.5 is 151*0.5/32 = 2.36 tiles across. Getting this wrong by the factor of
  // 32 draws one chest where a whole building belongs.
  const page = boot();
  assert.equal(page.SPRITE_PIXELS_PER_TILE, 32);
  const tiles = 151 * 0.5 / page.SPRITE_PIXELS_PER_TILE;
  assert.ok(Math.abs(tiles - 2.359) < 0.01, `stone furnace came out ${tiles} tiles wide`);
});

test("sprites stay off until the view is zoomed in far enough", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.layers.terrain = true;
  page.view.scale = 4;
  assert.equal(page.spritesWanted(), false, "4 px a tile is too far out for sprites");
  page.view.scale = 8;
  assert.equal(page.spritesWanted(), true, "8 px a tile is the threshold, inclusive");
  page.view.scale = 24;
  assert.equal(page.spritesWanted(), true);
});

test("sprites are off entirely when the server has no artwork", () => {
  const page = boot();
  page.__setSpritesAvailable(false);
  page.layers.terrain = true;
  page.view.scale = 24;
  assert.equal(page.spritesWanted(), false,
               "a bridge with no sprite directory must not make the page ask");
});

test("turning the terrain layer off takes its sprites with it", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.view.scale = 24;
  page.layers.terrain = false;
  assert.equal(page.spritesWanted(), false);
});

test("a straight belt picks the row Factorio uses for its direction", () => {
  // From base/prototypes/entity/transport-belts.lua the indices are ordered
  // east=1, west=2, north=3, south=4 — not north first — and they are 1 based,
  // so the row is one less. Getting this wrong points every belt the wrong way.
  const page = boot();
  assert.equal(page.beltRow({ d: 4 }), 0, "east");
  assert.equal(page.beltRow({ d: 12 }), 1, "west");
  assert.equal(page.beltRow({ d: 0 }), 2, "north");
  assert.equal(page.beltRow({ d: 8 }), 3, "south");
});

test("a curved belt uses the row for where it is fed from", () => {
  // A curve is decided by neighbours, not by the belt's own direction, so the
  // shape arrives separately. west_to_north is index 7, east_to_north is 5.
  const page = boot();
  assert.equal(page.beltRow({ d: 0, s: "left" }), 6, "to north, fed from west");
  assert.equal(page.beltRow({ d: 0, s: "right" }), 4, "to north, fed from east");
  assert.equal(page.beltRow({ d: 8, s: "left" }), 9, "to south, fed from east");
  assert.equal(page.beltRow({ d: 12, s: "right" }), 7, "to west, fed from north");
});

test("every belt row lands inside the twenty on the sheet", () => {
  const page = boot();
  for (const d of [0, 4, 8, 12]) {
    for (const s of [undefined, "left", "right"]) {
      const row = page.beltRow({ d, s });
      assert.ok(row >= 0 && row < 20, `direction ${d} shape ${s} gave row ${row}`);
    }
  }
});

test("an unknown belt direction still draws something", () => {
  // Diagonals should not happen on a belt, but a bad row would crop off the
  // sheet and throw away the frame.
  const page = boot();
  const row = page.beltRow({ d: 6 });
  assert.ok(row >= 0 && row < 20);
});

test("a tree draws the variation the game gave it, trunk and leaves", () => {
  const page = boot();
  const meta = { kind: "variations", by: { 1: ["a-trunk", "a-leaves"], 2: ["b-trunk", "b-leaves"] } };
  assert.deepEqual(page.spriteLayers(meta, { v: 2 }), ["b-trunk", "b-leaves"]);
});

test("a tree with no variation reported falls back to the first", () => {
  // The scenario only sends `v` when it is not 1, to keep the payload small.
  const page = boot();
  const meta = { kind: "variations", by: { 1: ["a-trunk"], 2: ["b-trunk"] } };
  assert.deepEqual(page.spriteLayers(meta, {}), ["a-trunk"]);
});

test("a key with no sheet still draws something", () => {
  // A family the game gains later must degrade, not leave holes in the map.
  const page = boot();
  const meta = { kind: "variations", by: { 1: ["a"], 2: ["b"] } };
  assert.deepEqual(page.spriteLayers(meta, { v: 9 }), ["a"]);
});

test("a directional entity picks the sheet for the way it was built", () => {
  const page = boot();
  const meta = { kind: "directional", by: { 0: ["n"], 4: ["e"], 8: ["s"], 12: ["w"] } };
  assert.deepEqual(page.spriteLayers(meta, { d: 8 }), ["s"], "south");
  assert.deepEqual(page.spriteLayers(meta, { d: 12 }), ["w"], "west");
  assert.deepEqual(page.spriteLayers(meta, {}), ["n"], "no direction means north");
});

test("an underground belt tells its entrance from its exit", () => {
  const page = boot();
  const meta = { kind: "underground", by: { input: ["in"], output: ["out"] } };
  assert.deepEqual(page.spriteLayers(meta, { g: "output" }), ["out"]);
  assert.deepEqual(page.spriteLayers(meta, { g: "input" }), ["in"]);
  assert.deepEqual(page.spriteLayers(meta, {}), ["in"], "an unreported end is an entrance");
});

test("an underground belt offsets across the sheet by direction", () => {
  // The four directions sit side by side on one 768px sheet of 192px cells.
  const page = boot();
  const meta = { kind: "underground", by: { input: [{}] } };
  const layer = { width: 192, height: 192, x: 0, y: 192 };
  // Compared field by field: an object built inside the vm context has that
  // context's prototype, which deepStrictEqual rejects.
  for (const [direction, column] of [[0, 0], [4, 1], [8, 2], [12, 3]]) {
    const cell = page.spriteCell(meta, layer, { d: direction }, 0);
    assert.equal(cell.x, column * 192, `direction ${direction} column`);
    assert.equal(cell.y, 192, `direction ${direction} stays on the entrance row`);
  }
});

test("a pipe picks its shape from which sides are connected", () => {
  const page = boot();
  const meta = { kind: "pipe", by: { 0: ["single"], 5: ["vertical"], 10: ["horizontal"], 15: ["cross"] } };
  assert.deepEqual(page.spriteLayers(meta, { c: 5 }), ["vertical"], "north and south");
  assert.deepEqual(page.spriteLayers(meta, { c: 10 }), ["horizontal"], "east and west");
  assert.deepEqual(page.spriteLayers(meta, { c: 15 }), ["cross"]);
  assert.deepEqual(page.spriteLayers(meta, { c: 0 }), ["single"], "connected to nothing");
});

test("ore draws the richness stage its amount has left", () => {
  // stage_counts runs richest to poorest; the first threshold the amount
  // clears is the stage, so a spent patch looks thin.
  const page = boot();
  const meta = { kind: "ore", stage_counts: [15000, 9500, 5500, 2900, 1300, 400, 150, 80] };
  const layer = { width: 128, height: 128, x: 0, y: 0, variation_count: 8 };
  assert.equal(page.spriteCell(meta, layer, { a: 20000 }, 0).x, 0, "richer than the top stage");
  assert.equal(page.spriteCell(meta, layer, { a: 9500 }, 0).x, 128, "second stage");
  assert.equal(page.spriteCell(meta, layer, { a: 100 }, 0).x, 7 * 128, "nearly spent");
  assert.equal(page.spriteCell(meta, layer, { a: 0 }, 0).x, 7 * 128, "empty stays on the last stage");
});

test("ore variation picks a row and stays on the sheet", () => {
  const page = boot();
  const meta = { kind: "ore", stage_counts: [100] };
  const layer = { width: 128, height: 128, x: 0, y: 0, variation_count: 8 };
  assert.equal(page.spriteCell(meta, layer, { a: 100, v: 3 }, 0).y, 2 * 128);
  assert.equal(page.spriteCell(meta, layer, { a: 100, v: 9 }, 0).y, 0,
               "a variation past the sheet must wrap, not crop off it");
});

test("ore totals read the way the game writes them", () => {
  const page = boot();
  assert.equal(page.formatAmount(950), "950");
  assert.equal(page.formatAmount(12_500), "12.5k");
  assert.equal(page.formatAmount(3_400_000), "3.40M");
  assert.equal(page.formatAmount(2_000_000_000), "2.00G");
});
