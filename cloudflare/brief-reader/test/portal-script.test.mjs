import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

// Execute the shipped script. The small browser adapter supplies an
// observable DOM tree, navigation, storage, fetch, clock and the device APIs
// the player uses (fullscreen, orientation, wake lock) without a second player.
const script = readFileSync(new URL("../static/portal.js", import.meta.url), "utf8");

const camelCase = (name) => name.replace(/-([a-z])/gu, (_match, letter) => letter.toUpperCase());
const kebabCase = (name) => name.replace(/[A-Z]/gu, (letter) => `-${letter.toLowerCase()}`);

// Compound selectors joined by descendant spaces: tag, .class, [attr], [attr="value"].
const parseCompound = (compound) => {
  const parts = { tag: null, classes: [], attributes: [] };
  const pattern = /([a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/gu;
  let consumed = 0;
  for (const match of compound.matchAll(pattern)) {
    if (match.index !== consumed) throw new Error(`unsupported selector: ${compound}`);
    consumed += match[0].length;
    if (match[1]) parts.tag = match[1].toUpperCase();
    else if (match[2]) parts.classes.push(match[2]);
    else parts.attributes.push({ name: match[3], value: match[4] });
  }
  if (consumed !== compound.length) throw new Error(`unsupported selector: ${compound}`);
  return parts;
};

class Element {
  constructor(tagName = "div", attributes = {}, children = []) {
    this.tagName = tagName.toUpperCase();
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.children = [];
    this.parentNode = null;
    this.textContent = "";
    this.classNames = new Set();
    this.offsetWidth = 0;
    this.visitLog = null;
    this.hiddenValue = false;
    this.style = {
      properties: new Map(),
      setProperty(name, value) { this.properties.set(name, value); },
    };
    this.classList = {
      toggle: (name, force) => {
        if (force ?? !this.classNames.has(name)) this.classNames.add(name);
        else this.classNames.delete(name);
      },
      remove: (...names) => names.forEach((name) => this.classNames.delete(name)),
      add: (...names) => names.forEach((name) => this.classNames.add(name)),
      contains: (name) => this.classNames.has(name),
    };
    for (const [name, value] of Object.entries(attributes)) {
      if (value === false || value === undefined || value === null) continue;
      if (name === "class") String(value).split(/\s+/u).filter(Boolean).forEach((item) => this.classNames.add(item));
      else if (name === "hidden") this.hiddenValue = true;
      else if (name === "text") this.textContent = String(value);
      else this.setAttribute(name, value === true ? "" : String(value));
    }
    for (const child of children) this.appendChild(child);
  }

  get hidden() { return this.hiddenValue; }

  set hidden(value) {
    this.hiddenValue = Boolean(value);
    if (!value && this.visitLog && this.dataset.instanceId) this.visitLog.add(this.dataset.instanceId);
  }

  get firstChild() { return this.children[0] ?? null; }

  addEventListener(name, callback) {
    const callbacks = this.listeners.get(name) ?? [];
    callbacks.push(callback);
    this.listeners.set(name, callbacks);
  }

  removeEventListener(name, callback) {
    const callbacks = this.listeners.get(name) ?? [];
    const index = callbacks.indexOf(callback);
    if (index >= 0) callbacks.splice(index, 1);
  }

  listenerCount(name) { return (this.listeners.get(name) ?? []).length; }

  dispatch(name, event = {}) {
    for (const callback of [...(this.listeners.get(name) ?? [])]) callback(event);
  }

  setAttribute(name, value) {
    if (name.startsWith("data-")) this.dataset[camelCase(name.slice(5))] = String(value);
    else this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    if (name.startsWith("data-")) return this.dataset[camelCase(name.slice(5))] ?? null;
    if (name === "class") return [...this.classNames].join(" ");
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    if (name.startsWith("data-")) delete this.dataset[camelCase(name.slice(5))];
    else this.attributes.delete(name);
  }

  hasAttribute(name) { return this.getAttribute(name) !== null; }

  appendChild(child) {
    child.parentNode?.removeChild(child);
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index < 0) throw new Error("not a child");
    this.children.splice(index, 1);
    child.parentNode = null;
    return child;
  }

  replaceChild(next, previous) {
    const index = this.children.indexOf(previous);
    if (index < 0) throw new Error("not a child");
    next.parentNode?.removeChild(next);
    this.children[index] = next;
    next.parentNode = this;
    previous.parentNode = null;
    return previous;
  }

  replaceChildren(...nodes) {
    for (const child of [...this.children]) this.removeChild(child);
    for (const node of nodes) this.appendChild(node);
  }

  matchesCompound(parts) {
    if (parts.tag && parts.tag !== this.tagName) return false;
    if (parts.classes.some((name) => !this.classNames.has(name))) return false;
    return parts.attributes.every(({ name, value }) => {
      const actual = this.getAttribute(name);
      return actual !== null && (value === undefined || actual === value);
    });
  }

  matches(selector) {
    const compounds = selector.trim().split(/\s+/u).map(parseCompound);
    if (!this.matchesCompound(compounds.at(-1))) return false;
    let ancestor = this.parentNode;
    for (let index = compounds.length - 2; index >= 0; index -= 1) {
      while (ancestor && !ancestor.matchesCompound(compounds[index])) ancestor = ancestor.parentNode;
      if (!ancestor) return false;
      ancestor = ancestor.parentNode;
    }
    return true;
  }

  *descendants() {
    for (const child of this.children) {
      yield child;
      yield* child.descendants();
    }
  }

  querySelectorAll(selector) {
    return [...this.descendants()].filter((node) => node.matches(selector));
  }

  querySelector(selector) {
    for (const node of this.descendants()) if (node.matches(selector)) return node;
    return null;
  }
}

class Document extends Element {
  constructor(documentElement) {
    super("#document");
    this.hidden = false;
    this.documentElement = documentElement;
    this.appendChild(documentElement);
    this.fullscreenEnabled = false;
    this.fullscreenElement = null;
    this.adopted = 0;
  }

  get body() { return this.documentElement.querySelector("body"); }

  adoptNode(node) {
    node.parentNode?.removeChild(node);
    this.adopted += 1;
    return node;
  }
}

const h = (tagName, attributes = {}, children = []) => new Element(tagName, attributes, children);

// Mirrors the Worker's markup closely enough for every selector the player uses.
function buildDocument(spec, browser) {
  let content;
  if (spec.login) {
    content = h("body", { class: "login-page" }, [h("form", { class: "login-form", action: "/login" })]);
  } else if (!spec.generation) {
    content = h("body", { class: "empty-page" }, [
      h("main", { class: "empty-reader", "data-poll-ms": "30000", "data-generation": "0" }),
    ]);
  } else {
    const slides = spec.instances.map((instanceId, index) => {
      const image = h("img", { src: `/assets/${instanceId}-${spec.generation}`, loading: index ? "lazy" : "eager" });
      image.complete = browser.initialBrokenImages.includes(instanceId);
      image.naturalWidth = 0;
      if (browser.imageDecode) image.decode = () => browser.imageDecode(image);
      const slide = h("article", {
        class: index ? "play-slide" : "play-slide is-active",
        "data-slide": true,
        "data-instance-id": instanceId,
        "aria-hidden": index ? "true" : "false",
        hidden: index > 0,
      }, [h("div", { class: "publication-stage" }, [h("figure", {}, [image])])]);
      slide.visitLog = browser.visited;
      return slide;
    });
    const segments = spec.instances.length > 1
      ? [h("ol", { class: "play-progress" }, spec.instances.map(() => h("li", { class: "progress-segment", "data-progress-segment": true })))]
      : [];
    const button = (attributes) => h("button", { class: "dock-button", type: "button", ...attributes });
    content = h("body", { class: "play-page" }, [
      h("main", {
        class: "play-shell is-chrome-hidden",
        "data-autoplay": true,
        "data-playlist-id": spec.playlist,
        "data-interval-ms": "20000",
        "data-poll-ms": String(spec.pollMs),
        "data-generation": String(spec.generation),
        "data-asset-version": spec.assetVersion,
      }, [
        h("section", { class: "slide-deck", "data-slide-deck": true }, slides),
        h("nav", { class: "play-dock", "data-play-controls": true }, [
          h("div", { class: "dock-status", "data-dock-status": true }, [
            h("p", { class: "slide-position" }, [
              h("span", { "data-slide-current": true, text: "1" }),
              h("span", { "data-slide-total": true, text: String(spec.instances.length) }),
            ]),
            ...segments,
          ]),
          h("div", { class: "dock-buttons" }, [
            h("div", { class: "dock-tools" }, [
              button({ "data-fullscreen": true, "aria-pressed": "false" }),
              button({ "data-rotate-fullscreen": true, "aria-pressed": "false" }),
              button({ "data-keep-awake": true, "aria-pressed": "false" }),
            ]),
            button({ "data-previous": true }),
            button({ "data-pause": true, "aria-pressed": "false" }),
            button({ "data-next": true }),
            button({ "data-play-chrome-toggle": true, "aria-expanded": "false" }),
          ]),
        ]),
      ]),
    ]);
  }
  return new Document(h("html", { lang: "zh-CN" }, [h("head"), content]));
}

class FakeWakeLockSentinel {
  released = false;
  listeners = [];
  type = "screen";

  addEventListener(name, callback) { if (name === "release") this.listeners.push(callback); }

  async release() {
    if (this.released) return;
    this.released = true;
    for (const callback of this.listeners) callback({ type: "release" });
  }
}

class Browser {
  time = 0;
  nextTimer = 0;
  tasks = new Map();
  storage = new Map();
  local = new Map();
  requests = [];
  reloads = 0;
  visited = new Set();
  generation = 1;
  playlist = "home";
  instances = ["a", "b", "c"];
  pollMs = 30000;
  assetVersion = "v1";
  generationAt = () => this.generation;
  fetchOverride = null;
  pageOverride = null;
  storageDisabled = false;
  initialBrokenImages = [];
  viewportScale = 1;
  domParser = true;
  fullscreen = false;
  fullscreenRequests = [];
  orientationLock = "missing"; // "missing" | "reject" | "fullscreen-only"
  lockedOrientation = null;
  unlocks = 0;
  portrait = false;
  wakeLock = false;
  wakeLockRejects = false;
  wakeLockGate = null;
  wakeRequests = 0;
  sentinels = [];

  constructor(options = {}) {
    Object.assign(this, options);
    this.mediaListeners = new Map();
    this.boot();
  }

  later(callback, delay, repeat = 0) {
    const id = ++this.nextTimer;
    this.tasks.set(id, { id, callback, at: this.time + delay, repeat });
    return id;
  }

  spec() {
    const generation = this.generationAt();
    return {
      generation,
      playlist: this.playlist,
      instances: this.instances,
      pollMs: this.pollMs,
      assetVersion: this.assetVersion,
    };
  }

  storageArea(map) {
    const check = () => {
      if (this.storageDisabled) throw new Error("browser storage disabled");
    };
    return {
      getItem: (key) => { check(); return map.get(key) ?? null; },
      setItem: (key, value) => { check(); map.set(key, String(value)); },
      removeItem: (key) => { check(); map.delete(key); },
    };
  }

  mediaQuery(query) {
    const browser = this;
    return {
      media: query,
      get matches() { return query === "(orientation: portrait)" ? browser.portrait : false; },
      addEventListener: (name, callback) => {
        if (name !== "change") return;
        const callbacks = browser.mediaListeners.get(query) ?? [];
        callbacks.push(callback);
        browser.mediaListeners.set(query, callbacks);
      },
    };
  }

  setPortrait(portrait) {
    this.portrait = portrait;
    for (const callback of this.mediaListeners.get("(orientation: portrait)") ?? []) {
      callback({ matches: portrait, media: "(orientation: portrait)" });
    }
  }

  boot() {
    this.tasks.clear();
    this.mediaListeners = new Map();
    const browser = this;
    const document = buildDocument(this.spec(), this);
    document.fullscreenEnabled = this.fullscreen;
    if (this.fullscreen) {
      document.documentElement.requestFullscreen = async (options) => {
        this.fullscreenRequests.push(options);
        document.fullscreenElement = document.documentElement;
        document.dispatch("fullscreenchange");
      };
      document.exitFullscreen = async () => {
        document.fullscreenElement = null;
        if (this.lockedOrientation) this.lockedOrientation = null; // leaving fullscreen unlocks
        document.dispatch("fullscreenchange");
      };
    }
    this.document = document;
    const orientation = this.orientationLock === "missing" ? undefined : {
      lock: async (value) => {
        if (this.orientationLock === "reject" || !document.fullscreenElement) {
          throw Object.assign(new Error("orientation lock unavailable"), { name: "NotSupportedError" });
        }
        this.lockedOrientation = value;
      },
      unlock: () => {
        this.unlocks += 1;
        this.lockedOrientation = null;
      },
    };
    const wakeLock = this.wakeLock ? {
      request: async (type) => {
        this.wakeRequests += 1;
        assert.equal(type, "screen");
        if (this.wakeLockGate) await this.wakeLockGate;
        if (this.wakeLockRejects || document.hidden) {
          throw Object.assign(new Error("wake lock refused"), { name: "NotAllowedError" });
        }
        const sentinel = new FakeWakeLockSentinel();
        this.sentinels.push(sentinel);
        return sentinel;
      },
    } : undefined;
    const window = {
      localStorage: this.storageArea(this.local),
      sessionStorage: this.storageArea(this.storage),
      AbortController,
      visualViewport: { scale: this.viewportScale },
      setTimeout: (callback, delay) => this.later(callback, delay),
      clearTimeout: (id) => this.tasks.delete(id),
      setInterval: (callback, delay) => this.later(callback, delay, delay),
      clearInterval: (id) => this.tasks.delete(id),
      matchMedia: (query) => this.mediaQuery(query),
      navigator: { wakeLock },
      screen: { orientation },
      fetch: async (url, options = {}) => {
        this.requests.push({ url, at: this.time, options });
        if (this.fetchOverride) return this.fetchOverride(url, options);
        if (url === "/api/publications") {
          const generation = this.generationAt();
          const etag = `"generation-${generation}"`;
          return {
            status: options.headers?.["If-None-Match"] === etag ? 304 : 200,
            ok: true,
            headers: { get: () => etag },
            json: async () => ({ release: generation === 0 ? null : { generation } }),
          };
        }
        if (this.pageOverride) return this.pageOverride(url, options);
        return {
          status: 200,
          ok: true,
          redirected: false,
          url: "https://e.example/",
          headers: { get: () => null },
          text: async () => JSON.stringify(this.spec()),
        };
      },
      location: {
        pathname: "/",
        search: "",
        reload: () => { this.reloads += 1; this.boot(); },
        assign: (url) => { this.navigation = url; },
      },
    };
    if (this.domParser) {
      window.DOMParser = class {
        parseFromString(text, type) {
          assert.equal(type, "text/html");
          browser.parses = (browser.parses ?? 0) + 1;
          return buildDocument(JSON.parse(text), browser);
        }
      };
    }
    this.window = window;
    runInNewContext(script, {
      document, window, Date: class extends Date { static now() { return browser.time; } },
    });
  }

  get player() { return this.document.querySelector("[data-autoplay]"); }

  get slides() { return this.player?.querySelectorAll("[data-slide]") ?? []; }

  get images() { return this.player?.querySelectorAll("[data-slide] img") ?? []; }

  get segments() { return this.player?.querySelectorAll("[data-progress-segment]") ?? []; }

  get activeInstance() { return this.slides.find((slide) => !slide.hidden)?.dataset.instanceId; }

  get buttons() {
    return new Proxy({}, { get: (_target, name) => this.document.querySelector(`[data-${kebabCase(String(name))}]`) });
  }

  get pageRequests() { return this.requests.filter((request) => request.url !== "/api/publications"); }

  swipe(dx, dy = 0) {
    const deck = this.buttons["slide-deck"];
    deck.dispatch("touchstart", { touches: [{ clientX: 200, clientY: 300 }] });
    deck.dispatch("touchend", { changedTouches: [{ clientX: 200 + dx, clientY: 300 + dy }] });
  }

  async settle() {
    for (let round = 0; round < 120; round += 1) await Promise.resolve();
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

// ---------- Publication changes: full-page fallback (no DOMParser) ----------

test("fallback: frequent editions still play the full playlist instead of restarting at item one", async () => {
  const browser = new Browser({ domParser: false, instances: ["a", "b", "c", "d", "e", "f", "g", "h"] });
  browser.generationAt = () => Math.floor(browser.time / 120000) + 1;
  await browser.advance(360000);
  assert.equal(browser.reloads, 3);
  assert.deepEqual([...browser.visited].sort(), browser.instances);
});

test("fallback: an edition keeps the selected instance and pause state across reordering", async () => {
  const browser = new Browser({ domParser: false });
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

test("fallback: image recovery immediately reloads the newer edition and preserves the slide deadline", async () => {
  const browser = new Browser({ domParser: false });
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

test("fallback: repeated early publication recovery does not indefinitely postpone the next slide", async () => {
  const browser = new Browser({ domParser: false });
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

test("fallback: image errors during an in-flight check recheck once it settles", async () => {
  const browser = new Browser({ domParser: false });
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

test("fallback: a removed instance falls back safely and a different playlist starts fresh", async () => {
  const browser = new Browser({ domParser: false });
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

test("fallback: unavailable session storage cannot prevent receiving a new edition", async () => {
  const browser = new Browser({ domParser: false, storageDisabled: true });
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
});

test("fallback: a restored edition resumes the progress fill where the slide left off", async () => {
  const browser = new Browser({ domParser: false });
  await browser.advance(5000);
  browser.generation = 2;
  browser.images[0].dispatch("error");
  await browser.settle();
  assert.equal(browser.reloads, 1);
  assert.equal(browser.segments[0].style.properties.get("--slide-offset"), "-5000ms");
});

// ---------- Publication changes: in-place replacement ----------

test("frequent editions are swapped in place and still play the full playlist", async () => {
  const browser = new Browser({ instances: ["a", "b", "c", "d", "e", "f", "g", "h"] });
  browser.generationAt = () => Math.floor(browser.time / 120000) + 1;
  await browser.advance(360000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.pageRequests.length, 3);
  assert.equal(browser.player.dataset.generation, "4");
  assert.deepEqual([...browser.visited].sort(), browser.instances);
});

test("an in-place edition replaces the slides, removes the old nodes and keeps the deck", async () => {
  const browser = new Browser();
  await browser.settle();
  const deck = browser.buttons["slide-deck"];
  const oldSlides = browser.slides;
  const oldImages = browser.images;
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.buttons["slide-deck"], deck);
  assert.equal(oldSlides.every((slide) => slide.parentNode === null), true);
  assert.equal(oldImages.every((image) => image.listenerCount("error") === 0), true);
  assert.deepEqual(browser.images.map((image) => image.getAttribute("src")), ["/assets/a-2", "/assets/b-2", "/assets/c-2"]);
  assert.equal(browser.slides.length, 3);
  assert.equal(browser.slides.filter((slide) => !slide.hidden).length, 1);
  assert.equal(browser.player.dataset.generation, "2");
  const [page] = browser.pageRequests;
  assert.equal(page.url, "/");
  assert.equal(page.options.cache, "no-store");
  assert.equal(page.options.credentials, "same-origin");
});

test("an in-place edition keeps the selected instance and pause state across reordering", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.buttons.pause.dispatch("click");
  browser.instances = ["c", "a", "b"];
  browser.generation = 2;
  await browser.advance(60000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.document.querySelector("[data-slide-current]").textContent, "3");
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
  assert.equal(browser.player.classList.contains("is-paused"), true);
  assert.equal(browser.segments[2].classList.contains("is-current"), true);
  assert.equal(browser.segments[2].classList.contains("is-frozen"), true);
  assert.equal(browser.segments[0].classList.contains("is-done"), true);
});

test("an in-place edition preserves the slide deadline and the progress fill", async () => {
  const browser = new Browser();
  await browser.advance(5000);
  browser.generation = 2;
  browser.images[1].dispatch("error");
  await browser.settle();
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.generation, "2");
  assert.equal(browser.activeInstance, "a");
  assert.equal(browser.segments[0].style.properties.get("--slide-offset"), "-5000ms");
  await browser.advance(14999);
  assert.equal(browser.activeInstance, "a");
  await browser.advance(1);
  assert.equal(browser.activeInstance, "b");
});

test("repeated early in-place recovery does not indefinitely postpone the next slide", async () => {
  const browser = new Browser();
  for (let iteration = 0; iteration < 3; iteration += 1) {
    await browser.advance(5000);
    browser.generation += 1;
    browser.images[0].dispatch("error");
    await browser.settle();
  }
  await browser.advance(5000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.pageRequests.length, 3);
  assert.equal(browser.activeInstance, "b");
});

test("the swapped picture does not replay the page-turn animation", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.player.dataset.direction, "steady");
  browser.buttons.next.dispatch("click");
  assert.equal(browser.player.dataset.direction, "forward");
});

test("an in-place edition syncs the total and progress segments when the item count changes", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.instances = ["a", "b", "c", "d"];
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.document.querySelector("[data-slide-total]").textContent, "4");
  assert.equal(browser.segments.length, 4);
  assert.equal(browser.segments[1].classList.contains("is-running"), true);
  assert.equal(browser.activeInstance, "b");
  browser.instances = ["b"];
  browser.generation = 3;
  await browser.advance(30000);
  assert.equal(browser.document.querySelector("[data-slide-total]").textContent, "1");
  assert.equal(browser.segments.length, 0);
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.document.querySelector("[data-slide-current]").textContent, "1");
  assert.equal(browser.reloads, 0);
});

test("a removed instance in place is clamped into the new playlist range", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.previous.dispatch("click");
  browser.buttons.pause.dispatch("click");
  assert.equal(browser.activeInstance, "c");
  browser.instances = ["a", "b"];
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.buttons.pause.getAttribute("aria-pressed"), "true");
});

test("the current picture of an in-place edition is fetched eagerly", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.buttons.pause.dispatch("click");
  browser.generation = 2;
  await browser.advance(30000);
  const current = browser.slides.find((slide) => !slide.hidden);
  assert.equal(current.dataset.instanceId, "b");
  assert.equal(current.querySelector("img").getAttribute("loading"), "eager");
});

test("an in-place swap waits briefly for the next picture to decode", async () => {
  const browser = new Browser();
  await browser.settle();
  let decoded;
  browser.imageDecode = () => new Promise((resolve) => { decoded = resolve; });
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.images[0].getAttribute("src"), "/assets/a-1");
  decoded();
  await browser.settle();
  assert.equal(browser.images[0].getAttribute("src"), "/assets/a-2");
  browser.imageDecode = () => new Promise(() => {});
  browser.generation = 3;
  await browser.advance(30000);
  assert.equal(browser.images[0].getAttribute("src"), "/assets/a-2");
  await browser.advance(4000);
  assert.equal(browser.images[0].getAttribute("src"), "/assets/a-3");
  assert.equal(browser.reloads, 0);
});

test("the replaced edition's poll cadence takes effect", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.pollMs = 15000;
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.pollMs, "15000");
  const before = browser.requests.filter((request) => request.url === "/api/publications").length;
  await browser.advance(60000);
  const after = browser.requests.filter((request) => request.url === "/api/publications").length;
  assert.equal(after - before, 4);
});

test("image recovery listeners follow the replaced pictures", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  const before = browser.requests.length;
  browser.images[2].dispatch("error");
  await browser.settle();
  assert.equal(browser.requests.length, before + 1);
  assert.equal(browser.requests.at(-1).url, "/api/publications");
});

test("repeated in-place editions do not stack document, deck or media listeners", async () => {
  const browser = new Browser();
  await browser.settle();
  const events = ["keydown", "pointerdown", "pointermove", "focusin", "visibilitychange", "fullscreenchange"];
  const documentCounts = events.map((name) => browser.document.listenerCount(name));
  const deck = browser.buttons["slide-deck"];
  const deckCounts = ["click", "touchstart", "touchmove", "touchend"].map((name) => deck.listenerCount(name));
  const mediaCount = [...browser.mediaListeners.values()].reduce((total, list) => total + list.length, 0);
  for (let generation = 2; generation <= 6; generation += 1) {
    browser.generation = generation;
    await browser.advance(30000);
  }
  assert.equal(browser.player.dataset.generation, "6");
  assert.equal(browser.reloads, 0);
  assert.deepEqual(events.map((name) => browser.document.listenerCount(name)), documentCounts);
  assert.deepEqual(["click", "touchstart", "touchmove", "touchend"].map((name) => deck.listenerCount(name)), deckCounts);
  assert.equal([...browser.mediaListeners.values()].reduce((total, list) => total + list.length, 0), mediaCount);
  assert.equal(deck.children.length, 3);
});

test("in-place editions work without session storage", async () => {
  const browser = new Browser({ storageDisabled: true });
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.generation, "2");
});

test("image errors during an in-flight check swap the edition in once it settles", async () => {
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
  assert.equal(browser.reloads, 0);
  assert.equal(browser.requests.length, 4); // initial, pending, recovery, page
  assert.equal(browser.pageRequests.length, 1);
  assert.equal(browser.player.dataset.generation, "2");
});

// ---------- In-place replacement fallbacks ----------

const pageResponse = (overrides) => async () => ({
  status: 200,
  ok: true,
  redirected: false,
  url: "https://e.example/",
  headers: { get: () => null },
  text: async () => "{}",
  ...overrides,
});

test("a page answer of 401 during replacement goes to the login page", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.pageOverride = pageResponse({ status: 401, ok: false });
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.navigation, "/login");
  assert.equal(browser.reloads, 0);
});

test("a page request redirected to the login page goes to the login page", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.pageOverride = pageResponse({ redirected: true, url: "https://e.example/login" });
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.navigation, "/login");
  assert.equal(browser.reloads, 0);
});

test("an expired session that renders the login form goes to the login page", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.pageOverride = pageResponse({ text: async () => JSON.stringify({ login: true }) });
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.navigation, "/login");
  assert.equal(browser.reloads, 0);
});

test("a failed page response falls back to a full reload", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.buttons.pause.dispatch("click");
  browser.pageOverride = pageResponse({ status: 500, ok: false });
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.activeInstance, "b"); // restored through session storage
});

test("an emptied publication falls back to a full reload", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.generation = 0;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.player, null);
});

test("a different playlist falls back to a full reload and starts fresh", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.buttons.next.dispatch("click");
  browser.playlist = "different";
  browser.instances = ["new-a", "new-b"];
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.activeInstance, "new-a");
});

test("a new Worker deployment falls back to a full reload for the new script", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.assetVersion = "v2";
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
});

test("an exception while replacing falls back to a full reload", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.document.adoptNode = () => { throw new Error("adoption failed"); };
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
});

test("a network failure while fetching the page keeps the picture and retries on the next check", async () => {
  const browser = new Browser();
  await browser.settle();
  browser.pageOverride = async () => { throw new TypeError("network down"); };
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.generation, "1");
  browser.pageOverride = null;
  await browser.advance(30000);
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.generation, "2");
  assert.equal(browser.pageRequests.length, 2);
});

test("the empty reader automatically discovers its first publication", async () => {
  const browser = new Browser({ generation: 0 });
  await browser.settle();
  browser.generation = 1;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.pageRequests.length, 0);
  assert.equal(browser.activeInstance, "a");
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

test("browsers without system fullscreen omit the fullscreen control", async () => {
  const browser = new Browser();
  await browser.settle();
  assert.equal(browser.buttons.fullscreen.hidden, true);
});

// ---------- Rotate to fullscreen ----------

const rotation = (browser) => browser.buttons["rotate-fullscreen"];

test("rotate fullscreen enters fullscreen and locks landscape where the browser allows it", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "fullscreen-only", portrait: true });
  await browser.settle();
  assert.equal(rotation(browser).getAttribute("aria-label"), "旋转全屏");
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.fullscreenRequests.length, 1);
  assert.equal(browser.fullscreenRequests[0]?.navigationUI, "hide");
  assert.equal(browser.document.fullscreenElement, browser.document.documentElement);
  assert.equal(browser.lockedOrientation, "landscape");
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  assert.equal(rotation(browser).getAttribute("aria-label"), "退出旋转全屏");
  assert.equal(rotation(browser).title, "退出旋转全屏");
  assert.equal(browser.local.get("epaper-portal-rotation"), "on");
});

test("without fullscreen or an orientation lock a portrait screen rotates the picture with CSS", async () => {
  const browser = new Browser({ portrait: true }); // iPhone Safari
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.fullscreenRequests.length, 0);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
});

test("a refused orientation lock in portrait keeps fullscreen and rotates with CSS", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "reject", portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.fullscreenRequests.length, 1);
  assert.equal(browser.lockedOrientation, null);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
});

test("a landscape screen is not rotated twice and turning to portrait rotates the picture", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "reject", portrait: false });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.document.fullscreenElement, browser.document.documentElement);
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  browser.setPortrait(true);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
});

test("turning rotate fullscreen off unlocks and leaves the fullscreen it started", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "fullscreen-only", portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.unlocks, 1);
  assert.equal(browser.lockedOrientation, null);
  assert.equal(browser.document.fullscreenElement, null);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "false");
  assert.equal(rotation(browser).getAttribute("aria-label"), "旋转全屏");
  assert.equal(browser.local.get("epaper-portal-rotation"), "off");
});

test("turning the CSS rotation off restores the upright page", async () => {
  const browser = new Browser({ portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "false");
});

test("rotation keeps a fullscreen that the visitor entered separately", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "fullscreen-only", portrait: true });
  await browser.settle();
  browser.buttons.fullscreen.dispatch("click");
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.fullscreenRequests.length, 1); // only the fullscreen button's request
  assert.equal(browser.lockedOrientation, "landscape");
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.document.fullscreenElement, browser.document.documentElement);
  assert.equal(browser.lockedOrientation, null);
});

test("leaving fullscreen with a system gesture turns a locked rotation off", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "fullscreen-only", portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  await browser.document.exitFullscreen(); // system back gesture
  await browser.settle();
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "false");
  assert.equal(rotation(browser).getAttribute("aria-label"), "旋转全屏");
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  assert.equal(browser.local.get("epaper-portal-rotation"), "off");
});

test("leaving fullscreen with a system gesture keeps an active CSS rotation", async () => {
  const browser = new Browser({ fullscreen: true, orientationLock: "reject", portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  await browser.document.exitFullscreen();
  await browser.settle();
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  assert.equal(browser.fullscreenRequests.length, 1);
});

test("a CSS-rotated picture maps swipes into the picture's own direction", async () => {
  const browser = new Browser({ portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  browser.swipe(0, -120); // toward the picture's left edge
  assert.equal(browser.activeInstance, "b");
  assert.equal(browser.player.dataset.direction, "forward");
  browser.swipe(10, 140); // toward the picture's right edge
  assert.equal(browser.activeInstance, "a");
  browser.swipe(-160, 0); // across the picture's height
  assert.equal(browser.activeInstance, "a");
  browser.buttons["slide-deck"].dispatch("click");
  assert.equal(browser.player.classList.contains("is-chrome-hidden"), false);
});

test("a rotated preference on a device turned to landscape swipes normally", async () => {
  const browser = new Browser({ portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  browser.setPortrait(false);
  // The class stays; the stylesheet only rotates a portrait viewport.
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  browser.swipe(-120, 0);
  assert.equal(browser.activeInstance, "b");
});

test("the rotation preference survives a full reload and restores the CSS rotation", async () => {
  const browser = new Browser({ portrait: true, domParser: false });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  assert.equal(browser.fullscreenRequests.length, 0);
});

test("a restored rotation in landscape waits for a portrait viewport", async () => {
  const browser = new Browser({ portrait: false });
  browser.local.set("epaper-portal-rotation", "on");
  browser.boot();
  await browser.settle();
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  assert.equal(browser.player.classList.contains("is-rotated"), false);
  browser.setPortrait(true);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
});

test("rotation works when browser storage is unavailable", async () => {
  const browser = new Browser({ portrait: true, storageDisabled: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.player.classList.contains("is-rotated"), true);
});

// ---------- Keep the screen awake ----------

const awake = (browser) => browser.buttons["keep-awake"];

test("keep awake requests a screen wake lock and shows it as active", async () => {
  const browser = new Browser({ wakeLock: true });
  await browser.settle();
  assert.equal(awake(browser).getAttribute("aria-pressed"), "false");
  assert.equal(awake(browser).getAttribute("aria-label"), "开启不息屏");
  assert.equal(browser.wakeRequests, 0);
  awake(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
  assert.equal(browser.sentinels[0].released, false);
  assert.equal(awake(browser).getAttribute("aria-pressed"), "true");
  assert.equal(awake(browser).getAttribute("aria-label"), "关闭不息屏");
  assert.equal(awake(browser).title, "关闭不息屏");
  assert.equal(awake(browser).dataset.wakeState, "active");
  assert.equal(browser.local.get("epaper-portal-keep-awake"), "on");
});

test("turning keep awake off releases the wake lock", async () => {
  const browser = new Browser({ wakeLock: true });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.sentinels[0].released, true);
  assert.equal(awake(browser).getAttribute("aria-pressed"), "false");
  assert.equal(awake(browser).getAttribute("aria-label"), "开启不息屏");
  assert.equal(awake(browser).dataset.wakeState, "off");
  assert.equal(browser.local.get("epaper-portal-keep-awake"), "off");
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
});

test("a wake lock released while hidden is requested again once the page is visible", async () => {
  const browser = new Browser({ wakeLock: true });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  browser.document.hidden = true;
  await browser.sentinels[0].release(); // the browser drops the lock for a hidden page
  browser.document.dispatch("visibilitychange");
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
  assert.equal(awake(browser).dataset.wakeState, "pending");
  assert.equal(awake(browser).getAttribute("aria-pressed"), "true");
  browser.document.hidden = false;
  browser.document.dispatch("visibilitychange");
  await browser.settle();
  assert.equal(browser.wakeRequests, 2);
  assert.equal(browser.sentinels[1].released, false);
  assert.equal(awake(browser).dataset.wakeState, "active");
});

test("a refused wake lock waits for the next tap or key instead of retrying in a loop", async () => {
  const browser = new Browser({ wakeLock: true, wakeLockRejects: true });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
  assert.equal(awake(browser).dataset.wakeState, "pending");
  assert.equal(awake(browser).getAttribute("aria-pressed"), "true");
  await browser.advance(120000);
  browser.document.dispatch("pointermove", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 2);
  assert.equal(awake(browser).dataset.wakeState, "pending");
  browser.wakeLockRejects = false;
  browser.document.dispatch("keydown", { key: "a", target: {} });
  await browser.settle();
  assert.equal(browser.wakeRequests, 3);
  assert.equal(awake(browser).dataset.wakeState, "active");
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 3);
});

test("a lock the system drops while visible is not requested again in a loop", async () => {
  const browser = new Browser({ wakeLock: true });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  await browser.sentinels[0].release(); // power saving
  await browser.advance(60000);
  assert.equal(browser.wakeRequests, 1);
  assert.equal(awake(browser).dataset.wakeState, "pending");
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 2);
  assert.equal(awake(browser).dataset.wakeState, "active");
});

test("a saved keep-awake preference asks for the wake lock on load", async () => {
  const browser = new Browser({ wakeLock: true, wakeLockRejects: true });
  browser.local.set("epaper-portal-keep-awake", "on");
  browser.boot();
  await browser.settle();
  assert.equal(browser.wakeRequests, 1);
  assert.equal(awake(browser).getAttribute("aria-pressed"), "true");
  assert.equal(awake(browser).dataset.wakeState, "pending");
  browser.wakeLockRejects = false;
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(browser.wakeRequests, 2);
  assert.equal(awake(browser).dataset.wakeState, "active");
});

test("a wake lock granted after keep awake was turned off is released at once", async () => {
  let grant;
  const browser = new Browser({ wakeLock: true });
  browser.wakeLockGate = new Promise((resolve) => { grant = resolve; });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  awake(browser).dispatch("click");
  grant();
  await browser.settle();
  assert.equal(browser.sentinels.length, 1);
  assert.equal(browser.sentinels[0].released, true);
  assert.equal(awake(browser).dataset.wakeState, "off");
});

test("browsers without the Wake Lock API disable the keep-awake control quietly", async () => {
  const browser = new Browser();
  await browser.settle();
  const button = awake(browser);
  assert.equal(button.getAttribute("aria-disabled"), "true");
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(button.getAttribute("aria-label"), "此浏览器不支持不息屏");
  assert.equal(button.title, "此浏览器不支持不息屏");
  button.dispatch("click");
  browser.document.dispatch("pointerdown", {});
  await browser.settle();
  assert.equal(button.getAttribute("aria-pressed"), "false");
  assert.equal(browser.local.has("epaper-portal-keep-awake"), false);
});

test("the keep-awake preference survives a full reload", async () => {
  const browser = new Browser({ wakeLock: true, domParser: false });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  browser.generation = 2;
  await browser.advance(30000);
  assert.equal(browser.reloads, 1);
  assert.equal(browser.wakeRequests, 2);
  assert.equal(awake(browser).dataset.wakeState, "active");
});

test("keep awake works when browser storage is unavailable", async () => {
  const browser = new Browser({ wakeLock: true, storageDisabled: true });
  await browser.settle();
  awake(browser).dispatch("click");
  await browser.settle();
  assert.equal(awake(browser).dataset.wakeState, "active");
});

test("an in-place edition leaves fullscreen, the rotation and the wake lock untouched", async () => {
  const browser = new Browser({ wakeLock: true, fullscreen: true, orientationLock: "reject", portrait: true });
  await browser.settle();
  rotation(browser).dispatch("click");
  awake(browser).dispatch("click");
  await browser.settle();
  for (let generation = 2; generation <= 4; generation += 1) {
    browser.generation = generation;
    await browser.advance(30000);
  }
  assert.equal(browser.reloads, 0);
  assert.equal(browser.player.dataset.generation, "4");
  assert.equal(browser.document.fullscreenElement, browser.document.documentElement);
  assert.equal(browser.fullscreenRequests.length, 1);
  assert.equal(browser.player.classList.contains("is-rotated"), true);
  assert.equal(rotation(browser).getAttribute("aria-pressed"), "true");
  assert.equal(browser.wakeRequests, 1);
  assert.equal(browser.sentinels[0].released, false);
  assert.equal(awake(browser).dataset.wakeState, "active");
});
