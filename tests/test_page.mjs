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
      addEventListener() {} close() {}
    },
  };
  context.window = context;
  context.globalThis = context;
  context.self = context;
  const body = elementStub();
  context.document = {
    body, documentElement: elementStub(), head: elementStub(),
    getElementById: () => elementStub(),
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
  const probe = "\n;globalThis.__page = { view, MAX_ZOOM, CHUNK_TILES,"
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

test("ore totals read the way the game writes them", () => {
  const page = boot();
  assert.equal(page.formatAmount(950), "950");
  assert.equal(page.formatAmount(12_500), "12.5k");
  assert.equal(page.formatAmount(3_400_000), "3.40M");
  assert.equal(page.formatAmount(2_000_000_000), "2.00G");
});
