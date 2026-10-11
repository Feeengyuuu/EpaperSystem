import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

describe("vehicle reader assets", () => {
  it("answers the browser favicon probe without a console-visible 404", async () => {
    const response = await SELF.fetch("https://example.com/favicon.ico");

    expect(response.status).toBe(204);
    expect(response.headers.get("cache-control")).toContain("max-age=86400");
    expect(await response.text()).toBe("");
  });

  it("serves the logo as an opaque iPhone home-screen icon without authentication", async () => {
    for (const path of ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"]) {
      const response = await SELF.fetch(`https://example.com${path}`);

      expect(response.status).toBe(200);
      expect(response.headers.get("content-type")).toBe("image/png");
      const bytes = new Uint8Array(await response.arrayBuffer());
      expect(Array.from(bytes.slice(1, 4))).toEqual([0x50, 0x4e, 0x47]);
      const view = new DataView(bytes.buffer);
      expect([view.getUint32(16), view.getUint32(20)]).toEqual([180, 180]);
      // Color type 2 is RGB without alpha.
      expect(bytes[25]).toBe(2);
    }
  });

  it("serves the logo as the tab icon and links both icons from the login page", async () => {
    const icon = await SELF.fetch("https://example.com/logo.svg");
    expect(icon.status).toBe(200);
    expect(icon.headers.get("content-type")).toBe("image/svg+xml");
    expect(await icon.text()).toContain("<svg");

    const page = await (await SELF.fetch("https://example.com/")).text();
    expect(page).toContain('<link rel="icon" href="/logo.svg" type="image/svg+xml">');
    expect(page).toContain('<link rel="apple-touch-icon" href="/apple-touch-icon.png">');
  });

  it("serves the proven no-crop fullscreen stylesheet without authentication", async () => {
    const response = await SELF.fetch("https://example.com/portal.css");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/css");
    expect(response.headers.get("cache-control")).toContain("max-age=300");
    expect(response.headers.get("cache-control")).not.toContain("immutable");
    const css = await response.text();
    expect(css).toContain("height: 100dvh");
    expect(css).toContain("object-fit: contain");
  });

  it("serves autoplay and optional fullscreen behavior without inline script", async () => {
    const response = await SELF.fetch("https://example.com/portal.js");

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("javascript");
    expect(response.headers.get("cache-control")).toBe("public, max-age=0, must-revalidate");
    const script = await response.text();
    expect(script).toContain("requestFullscreen");
    expect(script).toContain('window.fetch("/api/publications"');
    expect(script).toContain("knownEtag !== nextEtag");
    expect(script).toContain("AbortController");
    expect(script).toContain("pollAgain");
    expect(script).toContain('lock("landscape")');
    expect(script).toContain("navigator?.wakeLock");
    expect(script).toContain('request("screen")');
    expect(script).toContain("DOMParser");
    expect(script).not.toContain("eval(");
  });

  it("serves the versioned stylesheet and script URLs with the same content and caching", async () => {
    for (const path of ["/portal.css", "/portal.js"]) {
      const plain = await SELF.fetch(`https://example.com${path}`);
      const versioned = await SELF.fetch(`https://example.com${path}?v=0123abcd`);
      expect(versioned.status).toBe(200);
      expect(versioned.headers.get("cache-control")).toBe(plain.headers.get("cache-control"));
      expect(versioned.headers.get("content-type")).toBe(plain.headers.get("content-type"));
      expect(await versioned.text()).toBe(await plain.text());
    }
  });

  it("derives the asset version from the shipped stylesheet and script", async () => {
    const css = await (await SELF.fetch("https://example.com/portal.css")).text();
    const script = await (await SELF.fetch("https://example.com/portal.js")).text();
    // 32-bit FNV-1a over both files: any deployment that changes them changes the URL.
    let hash = 0x811c9dc5;
    const content = `${css}\n${script}`;
    for (let index = 0; index < content.length; index += 1) {
      hash ^= content.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    const expected = (hash >>> 0).toString(16).padStart(8, "0");
    const page = await (await SELF.fetch("https://example.com/")).text();
    expect(page).toContain(`href="/portal.css?v=${expected}"`);
  });

  it("rotates the player with CSS only while the viewport is portrait", async () => {
    const css = await (await SELF.fetch("https://example.com/portal.css")).text();
    const portrait = /@media \(orientation: portrait\)\s*\{([\s\S]*?)\n\}/u.exec(css)?.[1] ?? "";
    expect(portrait).toMatch(/\.play-shell\.is-rotated\s*\{[^}]*rotate\(90deg\)/u);
    expect(portrait).toContain("100dvw");
    expect(portrait).toContain("100dvh");
    expect(css).toMatch(/\.play-shell\[data-direction="steady"\] \.publication-stage\s*\{[^}]*animation: none/u);
    expect(css).toContain('[data-keep-awake][data-wake-state="active"]');
  });
});
