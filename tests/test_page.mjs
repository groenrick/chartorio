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
    style: {}, dataset: {}, textContent: "", value: "",
    hidden: false, checked: false, width: 0, height: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener() {}, removeEventListener() {},
    appendChild(child) { element.children.push(child); return child; }, append() {},
    removeChild() {}, remove() {}, insertBefore() {}, setAttribute() {},
    removeAttribute() {}, getAttribute: () => null, focus() {}, blur() {}, click() {},
    scrollIntoView() {}, closest: () => null, contains: () => false,
    querySelector: () => elementStub(), querySelectorAll: () => [],
    getBoundingClientRect: () => ({ left: 0, top: 0, right: 200, bottom: 100, width: 200, height: 100 }),
    getContext: () => contextStub(),
    children: [], firstChild: null, parentNode: null,
  };
  // A browser empties an element when its innerHTML is set, so the stub has to
  // as well: a list that is refilled every tick would otherwise only ever grow.
  let markup = "";
  Object.defineProperty(element, "innerHTML", {
    get: () => markup,
    set(value) { markup = value; element.children.length = 0; },
  });
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
  let clock = 0;
  const eventChannels = [];
  const context = {
    console: { log() {}, warn() {}, error() {}, info() {} },
    JSON, Math, Date, Object, Array, String, Number, Boolean, Map, Set,
    Promise, Error, isNaN, isFinite, parseInt, parseFloat,
    Uint8ClampedArray, Uint8Array, Float32Array, Float64Array, Int32Array,
    ArrayBuffer, TextDecoder, TextEncoder, URL, URLSearchParams, Proxy, Symbol,
    performance: { now: () => context.__clock },
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => {},
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    clearTimeout: () => {}, setInterval: () => 1, clearInterval: () => {},
    fetch: () => new Promise(() => {}),           // never settles: no network here
    location: { protocol: "http:", host: "localhost:8080", href: "http://localhost:8080/", hash: "" },
    devicePixelRatio: 1,
    innerWidth: 1280,
    innerHeight: 720,
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    localStorage: (() => {
      const saved = new Map();
      return {
        getItem: (k) => (saved.has(k) ? saved.get(k) : null),
        setItem(k, v) { saved.set(k, String(v)); },
        removeItem(k) { saved.delete(k); },
      };
    })(),
    history: { replaceState(_s, _t, url) { context.__lastUrl = url; }, pushState() {} },
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
  context.__clock = 0;
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
              + " __now(t) { globalThis.__clock = t; },"
              + " SPRITE_PIXELS_PER_TILE, spritesWanted, beltRow, spriteLayers, spriteCell, spriteKey,"
              + " expandRuns, tileVariant, terrainKey, terrainCovers, spriteDepth,"
              + " encodeView, decodeView, applyView, viewPrecision, LAYER_BITS,"
              + " itemsWanted, __setItemIndex(v) { itemIndex = v; }, itemPosition, beltItemsMoving, expandBeltItems,"
              + " __setItemsSeen(list, at) { previousItems = new Map();"
              + "   for (const i of list) previousItems.set(i.i, i);"
              + "   itemsArrivedAt = at; itemsInterval = 300; },"
              + " rollingStockFrame, drawRollingStock,"
              + " get follow() { return follow; }, set follow(v) { follow = v; },"
              + " __setSpriteIndex(v) { spriteIndex = v; },"
              + " __setTerrain(x, y, rev) { terrainCache.set(terrainKey(x, y), { revision: rev, canvas: {} }); },"
              + " __setCharted(x, y, rev) { charted.set(key(x, y), rev); },"
              + " __setSpritesAvailable(v) { spritesAvailable = v; },"
              + " PANEL_SECTIONS, LIST_SHOWN, fillList, followedFirst, rememberFolding, recallFolding,"
              + " get foldedSections() { return foldedSections; },"
              + " set foldedSections(v) { foldedSections = v; },"
              + " set expandedLists(v) { expandedLists = v; },"
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

test("the chunk raster expands from its run length coding", () => {
  const page = boot();
  const indices = page.expandRuns([3, 7, 2, 9], 5);
  assert.deepEqual(Array.from(indices), [7, 7, 7, 9, 9]);
});

test("a short run list leaves the tail as index zero", () => {
  // An edge the game has not rastered comes back short, and must not be read
  // off the end of the array.
  const page = boot();
  assert.deepEqual(Array.from(page.expandRuns([2, 4], 5)), [4, 4, 0, 0, 0]);
});

test("a run longer than the chunk is truncated, not overflowed", () => {
  const page = boot();
  assert.deepEqual(Array.from(page.expandRuns([99, 3], 4)), [3, 3, 3, 3]);
});

test("an odd run list is ignored rather than read past its end", () => {
  const page = boot();
  assert.deepEqual(Array.from(page.expandRuns([2, 5, 3], 4)), [5, 5, 0, 0]);
});

test("a tile always picks the same variant for the same position", () => {
  // Terrain is rebuilt whenever a chunk changes, and the ground must not
  // shimmer when it is.
  const page = boot();
  const first = page.tileVariant(120, -44, 16);
  assert.equal(page.tileVariant(120, -44, 16), first, "not deterministic");
  assert.ok(first >= 0 && first < 16, `variant ${first} is off the sheet`);
});

test("tile variants stay on the sheet for every count", () => {
  const page = boot();
  for (const count of [1, 4, 16]) {
    for (let n = 0; n < 40; n++) {
      const v = page.tileVariant(n * 7 - 100, n * -3 + 55, count);
      assert.ok(v >= 0 && v < count, `count ${count} gave ${v}`);
    }
  }
});

test("neighbouring tiles do not all land on the same variant", () => {
  // A hash that collapses would tile the ground with one texture and look
  // worse than the flat colour it replaced.
  const page = boot();
  const seen = new Set();
  for (let y = 0; y < 8; y++) {
    for (let x = 0; x < 8; x++) seen.add(page.tileVariant(x, y, 16));
  }
  assert.ok(seen.size >= 6, `only ${seen.size} variants across 64 tiles`);
});

test("a chunk the sprites cover needs no colour tile under it", () => {
  // The colour map bakes entities over the ground, so leaving it underneath is
  // what put a blue square around every sprite.
  const page = boot();
  page.__setSpritesAvailable(true);
  page.layers.terrain = true;
  page.view.scale = 24;
  page.__setCharted(3, 4, 7);
  assert.equal(page.terrainCovers(3, 4), false, "nothing cached yet");
  page.__setTerrain(3, 4, 7);
  assert.equal(page.terrainCovers(3, 4), true, "cached at the current revision");
});

test("a stale terrain chunk does not suppress its colour tile", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.layers.terrain = true;
  page.view.scale = 24;
  page.__setCharted(3, 4, 8);
  page.__setTerrain(3, 4, 7);          // cached before the chunk changed
  assert.equal(page.terrainCovers(3, 4), false,
               "a changed chunk must fall back to the colour tile, not go blank");
});

test("below the sprite threshold the colour tile always stands", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.layers.terrain = true;
  page.__setCharted(3, 4, 7);
  page.__setTerrain(3, 4, 7);
  page.view.scale = 4;
  assert.equal(page.terrainCovers(3, 4), false);
});

test("an entity with no sprite still has something drawn for it", () => {
  // The terrain layer paints over the colour map, so anything the extract has
  // never seen would otherwise vanish. A radar built after the extract was
  // taken is the case that found this.
  const page = boot();
  const meta = { kind: "none", tiles: [3, 3] };
  // Length rather than deepEqual: an array built inside the vm context has
  // that context's prototype, which deepStrictEqual rejects.
  assert.equal(page.spriteLayers(meta, {}).length, 0,
               "kind none has no layers, so the caller must fall back");
});

test("a sprited prototype still reports its footprint", () => {
  const page = boot();
  const meta = { kind: "static", layers: [{ file: "a.png" }], tiles: [2, 2] };
  assert.equal(page.spriteLayers(meta, {}).length, 1, "art wins over the fallback");
});

test("ore is painted under the things built on top of it", () => {
  // A belt across a coal patch disappeared beneath the coal.
  const page = boot();
  page.__setSpriteIndex({
    coal: { kind: "ore", category: "resource" },
    "transport-belt": { kind: "belt", category: "transport-belt" },
  });
  assert.equal(page.spriteDepth({ n: "coal" }), 0, "ore is ground");
  assert.equal(page.spriteDepth({ n: "transport-belt" }), 1, "a belt is built on it");
  assert.ok(page.spriteDepth({ n: "coal" }) < page.spriteDepth({ n: "transport-belt" }));
});

test("an unknown prototype is painted with the built things, not the ground", () => {
  // Better a radar over some ore than a radar hidden under it.
  const page = boot();
  page.__setSpriteIndex({});
  assert.equal(page.spriteDepth({ n: "whatever" }), 1);
});

test("depth beats position, so a belt on ore stays visible", () => {
  const page = boot();
  page.__setSpriteIndex({
    coal: { kind: "ore", category: "resource" },
    "transport-belt": { kind: "belt", category: "transport-belt" },
  });
  // The belt is further north, so sorting by y alone would put it underneath.
  const belt = { n: "transport-belt", y: -10 };
  const ore = { n: "coal", y: 10 };
  const ordered = [belt, ore].sort((a, b) => page.spriteDepth(a) - page.spriteDepth(b) || a.y - b.y);
  assert.equal(ordered[0].n, "coal", "ore must be drawn first");
});

test("the view round trips through the URL", () => {
  // Within the precision the zoom justifies, not exactly: at 2 pixels to a
  // tile one decimal is already finer than a pixel, and that rounding is the
  // point of the scheme.
  const page = boot();
  page.view.x = -12.5; page.view.y = 40.25; page.view.scale = 2;
  const hash = page.encodeView();
  page.view.x = 0; page.view.y = 0; page.view.scale = 8;
  page.applyView(page.decodeView(hash));
  assert.ok(Math.abs(page.view.x + 12.5) <= 0.05, `x came back ${page.view.x}`);
  assert.ok(Math.abs(page.view.y - 40.25) <= 0.05, `y came back ${page.view.y}`);
  assert.equal(page.view.scale, 2);
});

test("zoomed in, the position keeps the precision it needs", () => {
  const page = boot();
  page.view.x = 96.25; page.view.y = -45.75; page.view.scale = 24;
  page.applyView(page.decodeView(page.encodeView()));
  assert.equal(page.view.x, 96.25, "at 24 px a tile two decimals survive");
  assert.equal(page.view.y, -45.75);
});

test("layers survive as a bitmask, not a list of names", () => {
  const page = boot();
  page.layers.terrain = true; page.layers.biters = false; page.layers.pollution = true;
  const hash = page.encodeView();
  assert.ok(hash.length < 40, `too long to share: ${hash}`);
  page.layers.terrain = false; page.layers.pollution = false; page.layers.biters = true;
  page.applyView(page.decodeView(hash));
  assert.equal(page.layers.terrain, true);
  assert.equal(page.layers.biters, false);
  assert.equal(page.layers.pollution, true, "pollution is off by default, so it must be carried explicitly");
});

test("position is rounded to what the zoom can distinguish", () => {
  const page = boot();
  assert.equal(page.viewPrecision(0.05), 0, "zoomed right out, whole tiles are enough");
  assert.equal(page.viewPrecision(2), 1);
  assert.equal(page.viewPrecision(24), 2);
  page.view.scale = 0.05; page.view.x = -1234.56789; page.view.y = 1.5;
  assert.ok(!page.encodeView().includes(".56789"), "far out, decimals are noise");
});

test("a scale beyond the map's limits is clamped, not honoured", () => {
  const page = boot();
  page.applyView(page.decodeView("#1/0/0/9999/ff"));
  assert.equal(page.view.scale, 24);
  page.applyView(page.decodeView("#1/0/0/0.0001/ff"));
  assert.equal(page.view.scale, 0.05);
});

test("a mangled hash is ignored rather than fatal", () => {
  const page = boot();
  for (const bad of ["", "#", "#garbage", "#1", "#1//", "#9/0/0/2/ff", "#1/x/y/z/!!"]) {
    const asked = page.decodeView(bad);
    if (asked) page.applyView(asked);          // must not throw
  }
  assert.ok(Number.isFinite(page.view.x), "the view survived every mangled link");
});

test("an old format version is not misread as the current one", () => {
  // The version exists so a future scheme can be told apart rather than
  // silently decoded as nonsense.
  const page = boot();
  assert.equal(page.decodeView("#9/1/2/3/ff"), null);
});

test("following someone is carried, and is absent when nobody is followed", () => {
  const page = boot();
  page.follow = null;
  assert.ok(!page.encodeView().includes(":"), "no follow, no field");
  page.follow = { kind: "player", id: "rikkert996" };
  const hash = page.encodeView();
  assert.ok(hash.includes("p:rikkert996"));
  page.follow = null;
  page.applyView(page.decodeView(hash));
  assert.deepEqual({ ...page.follow }, { kind: "player", id: "rikkert996" });
});

test("a followed train id comes back as a number", () => {
  // Trains are looked up by id, and "12" would never match 12.
  const page = boot();
  page.applyView(page.decodeView("#1/0/0/2/ff/nauvis/t:12"));
  assert.equal(page.follow.kind, "train");
  assert.equal(page.follow.id, 12);
  assert.equal(typeof page.follow.id, "number");
});

test("items on belts stay off until well past the sprite threshold", () => {
  // An item is a quarter of a tile; at the sprite threshold it is noise.
  const page = boot();
  page.__setSpritesAvailable(true);
  page.__setItemIndex({ coal: { file: "coal.png", size: 64 } });
  page.layers.items = true;
  page.view.scale = 8;
  assert.equal(page.itemsWanted(), false, "8 px a tile is the sprite threshold, too far for items");
  page.view.scale = 16;
  assert.equal(page.itemsWanted(), true);
});

test("items are off unless the layer is switched on", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.__setItemIndex({ coal: { file: "coal.png", size: 64 } });
  page.view.scale = 24;
  page.layers.items = false;
  assert.equal(page.itemsWanted(), false, "the layer defaults off and must stay off");
});

test("items need an icon index, not just the layer", () => {
  const page = boot();
  page.__setSpritesAvailable(true);
  page.__setItemIndex(null);
  page.layers.items = true;
  page.view.scale = 24;
  assert.equal(page.itemsWanted(), false, "no icons extracted means nothing to draw");
});

test("the items layer appends to the bitmask without shifting the others", () => {
  // Bit order is a promise to every link already shared.
  const page = boot();
  assert.equal(page.LAYER_BITS[0], "terrain");
  assert.equal(page.LAYER_BITS[7], "signals");
  assert.equal(page.LAYER_BITS[8], "items", "a ninth layer appends, never inserts");
});

test("an old link without the items bit leaves the layer off", () => {
  const page = boot();
  page.layers.items = true;
  // "6n" is an eight bit mask from before the items layer existed.
  page.applyView(page.decodeView("#1/0/0/2/6n"));
  assert.equal(page.layers.items, false, "a link that predates the layer must not switch it on");
});

test("a symmetric wagon covers half a turn with its frames", () => {
  // A cargo wagon has 128 frames for 180 degrees, because it looks the same
  // from either end; a locomotive has 256 for the whole turn. Both step by a
  // 256th of a turn. Reading 128 as a whole turn drew every wagon at twice the
  // angle it should be.
  const page = boot();
  const wagon = { files: ["w1.png","w2.png","w3.png","w4.png"], width: 442, height: 408,
                  frames: 128, line_length: 4, lines_per_file: 8 };
  const loco  = { files: ["l1.png","l2.png","l3.png","l4.png","l5.png","l6.png","l7.png","l8.png"],
                  width: 474, height: 458, frames: 256, line_length: 4, lines_per_file: 8 };
  // A quarter turn is 64 steps for both; the wagon wraps at 128 and so lands
  // on the same cell as the locomotive does.
  const w = page.rollingStockFrame({}, wagon, 0.25);
  const l = page.rollingStockFrame({}, loco, 0.25);
  assert.equal(w.x, l.x, "same angle, same column");
  assert.equal(w.y, l.y, "same angle, same row");
  // Half a turn later the wagon is back where it started; the locomotive is not.
  const wHalf = page.rollingStockFrame({}, wagon, 0.75);
  assert.equal(wHalf.x, w.x, "a wagon looks the same from either end");
  assert.equal(wHalf.y, w.y);
  const lHalf = page.rollingStockFrame({}, loco, 0.75);
  assert.ok(lHalf.file !== l.file || lHalf.y !== l.y, "a locomotive has a front");
});

test("the wagon orientation seen on the live map lands on the right frame", () => {
  // Observed: locomotives at 0.375 and wagons at 0.875 on the same train —
  // half a turn apart, which is the same line for a symmetric wagon.
  const page = boot();
  const wagon = { files: ["w1.png","w2.png","w3.png","w4.png"], width: 442, height: 408,
                  frames: 128, line_length: 4, lines_per_file: 8 };
  const at875 = page.rollingStockFrame({}, wagon, 0.875);
  const at375 = page.rollingStockFrame({}, wagon, 0.375);
  assert.deepEqual([at875.file, at875.x, at875.y], [at375.file, at375.x, at375.y],
                   "a wagon half a turn round draws the same way");
});

test("a locomotive picks the frame for the way it is pointing", () => {
  // 256 rotations over eight files of a four by eight grid. Rotating one
  // top-down image would look flat: these are drawn in perspective.
  const page = boot();
  const layer = { files: ["a.png","b.png","c.png","d.png","e.png","f.png","g.png","h.png"],
                  width: 474, height: 458, frames: 256, line_length: 4, lines_per_file: 8 };
  const north = page.rollingStockFrame({}, layer, 0);
  assert.equal(north.file, "a.png");
  assert.equal(north.x, 0);
  assert.equal(north.y, 0);
  // A quarter turn is frame 64: the third file, first cell.
  const east = page.rollingStockFrame({}, layer, 0.25);
  assert.equal(east.file, "c.png", "64 frames in is the third file of 32");
  assert.equal(east.x, 0);
  assert.equal(east.y, 0);
});

test("a frame lands on the right cell of its grid", () => {
  // Sheets step by a 256th of a turn, so the angle that picks frame 5 is
  // 5/256 of a turn, not 5 of however many frames the sheet happens to hold.
  const page = boot();
  const layer = { files: ["a.png"], width: 100, height: 50, frames: 32,
                  line_length: 4, lines_per_file: 8 };
  const cell = page.rollingStockFrame({}, layer, 5 / 256);
  assert.equal(cell.x, 100, "column 1");
  assert.equal(cell.y, 50, "row 1");
});

test("orientation wraps rather than running off the sheet", () => {
  // orientation is a fraction of a turn and 1.0 is the same as 0.
  const page = boot();
  const layer = { files: ["a.png","b.png"], width: 10, height: 10, frames: 64,
                  line_length: 4, lines_per_file: 8 };
  for (const o of [0, 0.5, 0.999, 1, 1.5, -0.25]) {
    const cell = page.rollingStockFrame({}, layer, o);
    assert.ok(layer.files.includes(cell.file), `orientation ${o} chose ${cell.file}`);
    assert.ok(cell.x >= 0 && cell.x < 40, `orientation ${o} x=${cell.x}`);
    assert.ok(cell.y >= 0 && cell.y < 80, `orientation ${o} y=${cell.y}`);
  }
});

test("a train with no art falls back to its marker", () => {
  const page = boot();
  page.__setSpriteIndex({ locomotive: { kind: "none", tiles: [1, 1] } });
  assert.equal(page.drawRollingStock({ n: "locomotive", x: 0, y: 0, o: 0 }), false);
});

test("an item slides from where it was to where it is", () => {
  const page = boot();
  page.__setItemsSeen([{ i: 7, n: "coal", x: 10, y: 20 }], 1000);
  page.__now(1000);
  const start = page.itemPosition({ i: 7, n: "coal", x: 12, y: 20 });
  assert.ok(Math.abs(start.x - 10) < 0.01, `at the moment it arrives it is still at the old spot, got ${start.x}`);
  page.__now(1150);                      // half of a 300ms gap
  const middle = page.itemPosition({ i: 7, n: "coal", x: 12, y: 20 });
  assert.ok(Math.abs(middle.x - 11) < 0.05, `halfway should be 11, got ${middle.x}`);
  page.__now(1300);
  const end = page.itemPosition({ i: 7, n: "coal", x: 12, y: 20 });
  assert.ok(Math.abs(end.x - 12) < 0.01, `at the end it has arrived, got ${end.x}`);
});

test("an item nobody saw before is drawn where it is", () => {
  // New items appear constantly as a belt feeds; they must not slide in from
  // the origin.
  const page = boot();
  page.__setItemsSeen([], 1000);
  page.__now(1150);
  const at = page.itemPosition({ i: 99, n: "coal", x: 5, y: 6 });
  assert.equal(at.x, 5);
  assert.equal(at.y, 6);
});

test("an item that jumped across the map is not slid", () => {
  // Picked up and put down somewhere else, or an id reused. A belt runs about
  // two tiles a second; anything much larger is not travel.
  const page = boot();
  page.__setItemsSeen([{ i: 3, n: "coal", x: 0, y: 0 }], 1000);
  page.__now(1150);
  const at = page.itemPosition({ i: 3, n: "coal", x: 200, y: 200 });
  assert.equal(at.x, 200, "drawn where it now is, not halfway across the map");
});

test("an item on a stopped belt does not drift", () => {
  // A backed up belt reports the same position twice; extrapolating would make
  // items creep forward and snap back.
  const page = boot();
  page.__setItemsSeen([{ i: 1, n: "coal", x: 4, y: 4 }], 1000);
  page.__now(1250);
  const at = page.itemPosition({ i: 1, n: "coal", x: 4, y: 4 });
  assert.equal(at.x, 4);
  assert.equal(at.y, 4);
});

test("items without an identity are drawn where they are", () => {
  // An older scenario does not send one; the layer must still work.
  const page = boot();
  page.__setItemsSeen([{ i: 1, n: "coal", x: 0, y: 0 }], 1000);
  page.__now(1150);
  const at = page.itemPosition({ n: "coal", x: 9, y: 9 });
  assert.equal(at.x, 9);
});

test("the flat wire format expands to items", () => {
  // name index, x, y, identity — repeated. The name table is 1 based, the way
  // Lua counts.
  const page = boot();
  const out = page.expandBeltItems({ names: ["coal", "iron-plate"],
                                     items: [1, 10.5, 20.25, 7, 2, 11, 21, 8] });
  assert.equal(out.length, 2);
  assert.equal(out[0].n, "coal");
  assert.equal(out[0].x, 10.5);
  assert.equal(out[0].i, 7);
  assert.equal(out[1].n, "iron-plate");
});

test("a records-style answer from an older scenario still reads", () => {
  const page = boot();
  const out = page.expandBeltItems({ items: [{ n: "coal", x: 1, y: 2, i: 3 }] });
  assert.equal(out.length, 1);
  assert.equal(out[0].n, "coal");
});

test("a truncated flat list does not produce a half item", () => {
  const page = boot();
  const out = page.expandBeltItems({ names: ["coal"], items: [1, 5, 6, 9, 1, 7] });
  assert.equal(out.length, 1, "the trailing partial record is dropped");
});

test("an empty answer is empty, not a crash", () => {
  const page = boot();
  assert.equal(page.expandBeltItems({ items: [], names: [] }).length, 0);
  assert.equal(page.expandBeltItems({}).length, 0);
});

test("ore totals read the way the game writes them", () => {
  const page = boot();
  assert.equal(page.formatAmount(950), "950");
  assert.equal(page.formatAmount(12_500), "12.5k");
  assert.equal(page.formatAmount(3_400_000), "3.40M");
  assert.equal(page.formatAmount(2_000_000_000), "2.00G");
});

// ---------------------------------------------------------------------------
// The side panel: folding, and lists that are cut short instead of growing
// until the legend is off the bottom of the screen (#35).
// ---------------------------------------------------------------------------

test("the controls come before the lists that grow", () => {
  const page = boot();
  const order = Array.from(page.PANEL_SECTIONS);
  // Layers and the legend are what somebody reaches for; players, trains and
  // alerts are what pushes them off screen, so they may never come first.
  assert.ok(order.indexOf("layers") < order.indexOf("players"));
  assert.ok(order.indexOf("legend") < order.indexOf("trains"));
  assert.ok(order.indexOf("legend") < order.indexOf("alerts"));
});

test("folded sections survive a reload, and the link does not carry them", () => {
  const page = boot();
  page.foldedSections = new Set(["trains", "alerts"]);
  page.rememberFolding();

  const url = page.encodeView(page.view);
  assert.ok(!url.includes("trains"), "folding belongs in this browser, not the link");
  // A link written before any of this still means what it meant: the fields
  // after the layer mask kept their positions.
  const old = page.decodeView("#1/0/0/2/ff/nauvis/t:12");
  // Field by field: an object built inside the vm has a different prototype.
  assert.equal(old.follow.kind, "train");
  assert.equal(old.follow.id, 12);

  page.foldedSections = new Set();
  page.recallFolding();
  assert.deepEqual([...page.foldedSections].sort(), ["alerts", "trains"]);
});

test("a section that no longer exists is dropped on the way back in", () => {
  const page = boot();
  page.foldedSections = new Set(["trains", "biters"]);   // "biters" was renamed
  page.rememberFolding();
  page.foldedSections = new Set();
  page.recallFolding();
  assert.deepEqual([...page.foldedSections], ["trains"]);
});

test("a long list is cut short and says how much is hidden", () => {
  const page = boot();
  const list = page.document.createElement("ul");
  const rows = Array.from({ length: 10 }, () => page.document.createElement("li"));
  page.expandedLists = new Set();
  page.fillList(list, rows, "trains");

  assert.equal(list.children.length, page.LIST_SHOWN + 1, "the shown rows plus one 'more'");
  assert.equal(list.children.at(-1).textContent, `and ${10 - page.LIST_SHOWN} more`);
});

test("a list that fits is left whole, with nothing to click", () => {
  const page = boot();
  const list = page.document.createElement("ul");
  const rows = Array.from({ length: page.LIST_SHOWN }, () => page.document.createElement("li"));
  page.expandedLists = new Set();
  page.fillList(list, rows, "trains");
  assert.equal(list.children.length, page.LIST_SHOWN);
});

test("once expanded a list shows everything", () => {
  const page = boot();
  const list = page.document.createElement("ul");
  const rows = Array.from({ length: 10 }, () => page.document.createElement("li"));
  page.expandedLists = new Set(["trains"]);
  page.fillList(list, rows, "trains");
  assert.equal(list.children.length, 10, "no 'more' row once it is open");
});

test("an empty list says so rather than collapsing to nothing", () => {
  const page = boot();
  const list = page.document.createElement("ul");
  page.fillList(list, [], "trains");
  assert.match(list.innerHTML, /no trains/);
  page.fillList(list, [], "players");
  assert.match(list.innerHTML, /nobody online/);
});

test("the followed train stays in a list that is cut short", () => {
  const page = boot();
  page.follow = { kind: "train", id: 47 };
  const trains = [
    { id: 11, speed: 0.9 }, { id: 22, speed: 0.5 }, { id: 47, speed: 0 },
  ];
  const order = trains.slice().sort((a, b) =>
    page.followedFirst(a, b) || Math.abs(b.speed) - Math.abs(a.speed));
  assert.equal(order[0].id, 47, "parked, but followed, so it may not fall off the end");

  page.follow = null;
  const unfollowed = trains.slice().sort((a, b) =>
    page.followedFirst(a, b) || Math.abs(b.speed) - Math.abs(a.speed));
  assert.deepEqual(unfollowed.map((t) => t.id), [11, 22, 47], "otherwise the fastest first");
});

test("the legend starts folded, because it is long and read least", () => {
  const page = boot();
  assert.ok(page.foldedSections.has("legend"));
  assert.ok(!page.foldedSections.has("trains"), "the lists somebody watches stay open");
});

test("a saved empty set means everything open, not the default", () => {
  const page = boot();
  page.foldedSections = new Set();
  page.rememberFolding();
  page.foldedSections = new Set(["legend"]);
  page.recallFolding();
  assert.equal(page.foldedSections.size, 0, "unfolding the legend has to stick");
});
