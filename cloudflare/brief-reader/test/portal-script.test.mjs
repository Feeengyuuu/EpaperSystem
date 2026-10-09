import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

// Execute the shipped script. The small browser adapter supplies observable
// DOM, navigation, storage, fetch and clock behavior without a second player.
const script = readFileSync(new URL("../static/portal.js", import.meta.url), "utf8");

class Element {
  dataset = {};
  attributes = new Map();
  listeners = new Map();
  hidden = false;
  textContent = "";
  classNames = new Set();
  classList = {
    toggle: (name, force) => {
      if (force ?? !this.classNames.has(name)) this.classNames.add(name);
      else this.classNames.delete(name);
    },
    remove: (...names) => names.forEach((name) => this.classNames.delete(name)),
    add: (...names) => names.forEach((name) => this.classNames.add(name)),
    contains: (name) => this.classNames.has(name),
  };

  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) ?? [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }

  dispatch(name, event = {}) {
    for (const callback of this.listeners.get(name) ?? []) callback(event);
  }

  setAttribute(name, value) { this.attributes.set(name, value); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
}

class Browser {
  time = 0;
  nextTimer = 0;
  tasks = new Map();
  storage = new Map();
  requests = [];
  reloads = 0;
  visited = new Set();
  generation = 1;
  playlist = "home";
  instances = ["a", "b", "c"];
  generationAt = () => this.generation;
  fetchOverride = null;
  storageDisabled = false;
  initialBrokenImages = [];
  viewportScale = 1;

  constructor(options = {}) {
    Object.assign(this, options);
    this.boot();
  }

  later(callback, delay, repeat = 0) {
    const id = ++this.nextTimer;
    this.tasks.set(id, { id, callback, at: this.time + delay, repeat });
    return id;
  }

  boot() {
    this.tasks.clear();
    const browser = this;
    const renderedGeneration = this.generationAt();
    this.slides = renderedGeneration === 0 ? [] : this.instances.map((instanceId) => {
      const slide = new Element();
      slide.dataset.instanceId = instanceId;
      return slide;
    });
    this.images = this.slides.map((slide) => {
      const image = new Element();
      image.complete = this.initialBrokenImages.includes(slide.dataset.instanceId);
      image.naturalWidth = 0;
      return image;
    });
    this.segments = this.slides.map(() => {
      const segment = new Element();
      segment.offsetWidth = 0;
      segment.style = { properties: new Map(), setProperty(name, value) { this.properties.set(name, value); } };
      return segment;
    });
    this.buttons = Object.fromEntries([
      "previous", "next", "pause", "fullscreen", "play-chrome-toggle", "play-controls", "slide-deck",
    ].map((name) => [name, new Element()]));
    const label = {
      set textContent(value) {
        const instance = browser.slides[Number(value) - 1]?.dataset.instanceId;
        if (instance) browser.visited.add(instance);
      },
    };
    const player = new Element();
    player.dataset = {
      pollMs: "30000", generation: String(renderedGeneration), intervalMs: "20000",
      playlistId: this.playlist,
    };
    player.classList.add("is-chrome-hidden"); // as rendered by the Worker
    player.querySelectorAll = (selector) => ({
      "[data-slide]": this.slides,
      "[data-slide] img": this.images,
      "[data-progress-segment]": this.segments,
    })[selector] ?? [];
    player.querySelector = (selector) => selector === "[data-slide-current]"
      ? label : this.buttons[selector.slice(6, -1)] ?? null;
    const document = new Element();
    document.hidden = false;
    document.documentElement = new Element();
    document.querySelector = (selector) => {
      if (selector === "[data-poll-ms]") return player;
      if (selector === "[data-autoplay]" && renderedGeneration !== 0) return player;
      return null;
    };
    this.document = document;
    const checkStorage = () => {
      if (this.storageDisabled) throw new Error("browser storage disabled");
    };
    const window = {
      localStorage: { getItem: () => null },
      sessionStorage: {
        getItem: (key) => { checkStorage(); return this.storage.get(key) ?? null; },
        setItem: (key, value) => { checkStorage(); this.storage.set(key, value); },
        removeItem: (key) => { checkStorage(); this.storage.delete(key); },
      },
      AbortController,
      visualViewport: { scale: this.viewportScale },
      setTimeout: (callback, delay) => this.later(callback, delay),
      clearTimeout: (id) => this.tasks.delete(id),
      setInterval: (callback, delay) => this.later(callback, delay, delay),
      fetch: async (url, options) => {
        this.requests.push({ url, at: this.time, options });
        if (this.fetchOverride) return this.fetchOverride(url, options);
        const generation = this.generationAt();
        const etag = `"generation-${generation}"`;
        return {
          status: options.headers["If-None-Match"] === etag ? 304 : 200,
          ok: true,
          headers: { get: () => etag },
          json: async () => ({ release: generation === 0 ? null : { generation } }),
        };
      },
      location: {
        reload: () => { this.reloads += 1; this.boot(); },
        assign: (url) => { this.navigation = url; },
      },
    };
    runInNewContext(script, {
      document, window, Date: class extends Date { static now() { return browser.time; } },
    });
  }

  get activeInstance() { return this.slides.find((slide) => !slide.hidden)?.dataset.instanceId; }

  get player() { return this.document.querySelector("[data-autoplay]"); }

  swipe(dx, dy = 0) {
    const deck = this.buttons["slide-deck"];
    deck.dispatch("touchstart", { touches: [{ clientX: 200, clientY: 300 }] });
    deck.dispatch("touchend", { changedTouches: [{ clientX: 200 + dx, clientY: 300 + dy }] });
  }

  async settle() {
    for (let round = 0; round < 16; round += 1) await Promise.resolve();
  }

  async advance(milliseconds) {
    await this.settle();
    const target = this.time + milliseconds;
    while (true) {
      const task = [...this.tasks.values()].sort((a, b) => a.at - b.at || a.id - b.id)[0];
      if (!task || task.at > target) break;
      this.time = task.at;
      this.tasks.delete(task.id);
      if (task.repeat) this.tasks.set(task.id, { ...task, at: this.time + task.repeat });
      task.callback();
      await this.settle();
    }
    this.time = target;
  }
}

test("frequent editions still play the full playlist instead of restarting at item one", async () => {
  const browser = new Browser({ instances: ["a", "b", "c", "d", "e", "f", "g", "h"] });
  browser.generationAt = () => Math.floor(browser.time / 120000) + 1;
  await browser.advance(360000);
  assert.equal(browser.reloads, 3);
  assert.deepEqual([...browser.visited].sort(), browser.instances);
});

test("an edition keeps the selected instance and pause state across reordering", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.buttons.pause.dispatch("click");
  assert.equal(browser.activeInstance, "b");
  browser.instances = ["c", "a", "b"];
  browser.generation = 2;
  await browser.advance(60000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
  assert.equal(browser.buttons.pause.getAttribute("aria-label"), "继续播放");
  assert.equal(browser.player.classList.contains("is-paused"), true);
});

test("image recovery immediately reloads the newer edition and preserves the slide deadline", async () => {
  const browser = new Browser();
  await browser.advance(5000);
  browser.generation = 2;
  browser.images[1].dispatch("error");
  await browser.settle();
  assert.equal(browser.reloads, 1);
  assert.equal(browser.activeInstance, "a");
  assert.equal(browser.requests[1].at, 5000);
  await browser.advance(14999);
  assert.equal(browser.activeInstance, "a");
  await browser.advance(1);
  assert.equal(browser.activeInstance, "b");
});

test("repeated early publication recovery does not indefinitely postpone the next slide", async () => {
  const browser = new Browser();
  for (let iteration = 0; iteration < 3; iteration += 1) {
    await browser.advance(5000);
    browser.generation += 1;
    browser.images[0].dispatch("error");
    await browser.settle();
  }
  await browser.advance(5000);
  assert.equal(browser.reloads, 3);
  assert.equal(browser.activeInstance, "b");
});

test("image errors during an in-flight check recheck once it settles", async () => {
  const browser = new Browser();
  await browser.settle();
  let finish;
  browser.fetchOverride = () => new Promise((resolve) => { finish = resolve; });
  browser.images[0].dispatch("error");
  browser.images[1].dispatch("error");
  assert.equal(browser.requests.length, 2);
  browser.generation = 2;
  browser.fetchOverride = null;
  finish({ status: 304, ok: false });
  await browser.settle();
  assert.equal(browser.reloads, 1);
  assert.equal(browser.requests.length, 4); // initial, pending, recovery, new document
});

test("images that failed before deferred script execution get a catalog recheck", async () => {
  const browser = new Browser({ initialBrokenImages: ["b"] });
  await browser.settle();
  assert.equal(browser.requests.length, 2);
  assert.equal(browser.reloads, 0);
});

test("a genuine failed image with an unchanged edition does not cause a reload loop", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.images[0].dispatch("error");
  await browser.settle();
  assert.equal(browser.requests.length, 2);
  assert.equal(browser.reloads, 0);
});

test("the empty reader automatically discovers its first publication", async () => {
  const browser = new Browser({ generation: 0 });
  await browser.settle();
  browser.generation = 1;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.activeInstance, "a");
});

test("a removed instance falls back safely and a different playlist starts fresh", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.buttons.pause.dispatch("click");
  browser.instances = ["c", "a"];
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.activeInstance, "c");
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
  browser.playlist = "different";
  browser.instances = ["new-a", "new-b"];
  browser.generation = 3;
  await browser.advance(30000);
  assert.equal(browser.activeInstance, "new-a");
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "false");
});

test("unavailable session storage cannot prevent receiving a new edition", async () => {
  const browser = new Browser({ storageDisabled: true });
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
});

test("recovery keeps authentication failures on the login path", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.fetchOverride = async () => ({ status: 401, ok: false });
  browser.images[0].dispatch("error");
  await browser.settle();
  assert.equal(browser.navigation, "/login");
  assert.equal(browser.reloads, 0);
});

test("the bottom reveal button and a tap on the picture both toggle the controls", async () => {
  const browser = new Browser();
  await browser.settle();
  const toggle = browser.buttons["play-chrome-toggle"];
  assert.equal(browser.player.classList.contains("is-chrome-hidden"), true);
  toggle.dispatch("click");
  assert.equal(browser.player.classList.contains("is-chrome-hidden"), false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  assert.equal(toggle.getAttribute("aria-label"), "隐藏控制");
  browser.buttons["slide-deck"].dispatch("click");
  assert.equal(browser.player.classList.contains("is-chrome-hidden"), true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(toggle.getAttribute("aria-label"), "显示控制");
});

test("a horizontal swipe turns the page and restarts the slide timer", async () => {
  const browser = new Browser();
  await browser.advance(15000);
  browser.swipe(-120);
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.player.dataset.direction, "forward");
  await browser.advance(19999);
  assert.equal(browser.activeInstance, "b");
  browser.swipe(140, 30);
  assert.equal(browser.activeInstance, "a");
  assert.equal(browser.player.dataset.direction, "backward");
  browser.swipe(-120);
  browser.swipe(-120);
  browser.swipe(-120); // wraps from the last slide to the first
  assert.equal(browser.activeInstance, "a");
  assert.equal(browser.player.dataset.direction, "forward");
});

test("short, vertical and pinch-zoomed gestures do not turn the page", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.swipe(-30);
  browser.swipe(-80, 120);
  assert.equal(browser.activeInstance, "a");
  const zoomed = new Browser({ viewportScale: 2 });
  await zoomed.settle();
  zoomed.swipe(-200);
  assert.equal(zoomed.activeInstance, "a");
});

test("arrow keys turn the page and space toggles pause outside buttons", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.document.dispatch("keydown", { key: "ArrowRight", target: {} });
  assert.equal(browser.activeInstance, "b");
  browser.document.dispatch("keydown", { key: "ArrowLeft", target: {} });
  assert.equal(browser.activeInstance, "a");
  let prevented = false;
  browser.document.dispatch("keydown", { key: " ", target: {}, preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
  browser.document.dispatch("keydown", { key: " ", target: { tagName: "BUTTON" } });
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
});

test("story progress fills the current segment over the remaining time and freezes on pause", async () => {
  const browser = new Browser();
  await browser.settle();
  const [first, second] = browser.segments;
  assert.equal(first.classList.contains("is-running"), true);
  assert.equal(first.style.properties.get("--slide-duration"), "20000ms");
  assert.equal(first.style.properties.get("--slide-offset"), "0ms");
  await browser.advance(20000);
  assert.equal(first.classList.contains("is-done"), true);
  assert.equal(second.classList.contains("is-current"), true);
  assert.equal(second.classList.contains("is-running"), true);
  browser.buttons.pause.dispatch("click");
  assert.equal(second.classList.contains("is-frozen"), true);
  browser.buttons.pause.dispatch("click");
  assert.equal(second.classList.contains("is-frozen"), false);
  assert.equal(second.classList.contains("is-running"), true);
});

test("a restored edition resumes the progress fill where the slide left off", async () => {
  const browser = new Browser();
  await browser.advance(5000);
  browser.generation = 2;
  browser.images[0].dispatch("error");
  await browser.settle();
  assert.equal(browser.reloads, 1);
  assert.equal(browser.segments[0].style.properties.get("--slide-offset"), "-5000ms");
});

test("browsers without system fullscreen omit the fullscreen control", async () => {
  const browser = new Browser();
  await browser.settle();
  assert.equal(browser.buttons.fullscreen.hidden, true);
});
