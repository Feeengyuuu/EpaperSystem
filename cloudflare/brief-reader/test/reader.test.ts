import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { browserSessionCookie, publishPlaylist } from "./support";

const EXPECTED_CSP = [
  "default-src 'none'",
  "base-uri 'none'",
  "connect-src 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "img-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
].join("; ");

function permissionsPolicy(header: string | null): Map<string, string> {
  return new Map(
    (header ?? "")
      .split(",")
      .map((directive) => directive.trim())
      .filter(Boolean)
      .map((directive) => {
        const separator = directive.indexOf("=");
        return [directive.slice(0, separator), directive.slice(separator + 1)] as [string, string];
      }),
  );
}

function openingTag(html: string, attribute: string): string {
  const tag = new RegExp(`<button\\b[^>]*\\s${attribute}(?=[\\s=>])[^>]*>`, "u").exec(html)?.[0];
  if (tag === undefined) throw new Error(`missing button ${attribute}`);
  return tag;
}

describe("Model Y browser reader", () => {
  it("fails closed with a private login page", async () => {
    const response = await SELF.fetch("https://example.com/");

    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("content-security-policy")).toContain("default-src 'none'");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");

    const body = await response.text();
    expect(body).toContain("EpaperSystem 私人简报");
    expect(body).toContain('name="password"');
    expect(body).not.toContain("AwesomeWeather");
  });

  it("rejects an invalid browser password without creating a session", async () => {
    const response = await SELF.fetch("https://example.com/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: "https://example.com",
      },
      body: new URLSearchParams({ username: "admin", password: "wrong-password" }),
    });

    expect(response.status).toBe(401);
    expect(response.headers.get("set-cookie")).toBeNull();
    expect(await response.text()).toContain("用户名或密码不正确");
  });

  it("creates a secure long-lived session after a valid login", async () => {
    const login = await SELF.fetch("https://example.com/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: "https://example.com",
      },
      body: new URLSearchParams({
        username: "admin",
        password: "test-browser-password-not-production",
      }),
      redirect: "manual",
    });

    expect(login.status).toBe(303);
    expect(login.headers.get("location")).toBe("/");
    const setCookie = login.headers.get("set-cookie") ?? "";
    expect(setCookie).toContain("__Host-epaper_session=");
    expect(setCookie).toContain("HttpOnly");
    expect(setCookie).toContain("Secure");
    expect(setCookie).toContain("SameSite=Strict");
    expect(setCookie).toContain("Path=/");
    expect(setCookie).toContain("Max-Age=34560000");

    const cookie = setCookie.split(";", 1)[0];
    if (cookie === undefined) throw new Error("missing session cookie");
    const reader = await SELF.fetch("https://example.com/", {
      headers: { Cookie: cookie },
    });
    expect(reader.status).toBe(200);
    const renewedCookie = reader.headers.get("set-cookie") ?? "";
    expect(renewedCookie).toContain("__Host-epaper_session=");
    expect(renewedCookie).toContain("Max-Age=34560000");
    expect(renewedCookie).toContain("HttpOnly");
    expect(renewedCookie).toContain("Secure");
    expect(renewedCookie).toContain("SameSite=Strict");
    expect(renewedCookie).toContain("Path=/");
    const body = await reader.text();
    expect(body).toContain("还没有发布内容");
    expect(body).toMatch(/<script src="\/portal\.js\?v=[0-9a-f]{8}" defer><\/script>/u);
    expect(body).toContain('data-poll-ms="30000" data-generation="0"');
    expect(body).not.toContain('name="password"');
  });

  it("accepts Chromium's opaque same-page form origin but rejects a cross-site origin", async () => {
    const body = new URLSearchParams({
      username: "admin",
      password: "test-browser-password-not-production",
    });
    const opaque = await SELF.fetch("https://example.com/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: "null",
      },
      body,
      redirect: "manual",
    });
    expect(opaque.status).toBe(303);

    const crossSite = await SELF.fetch("https://example.com/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Origin: "https://attacker.example",
      },
      body,
      redirect: "manual",
    });
    expect(crossSite.status).toBe(403);
  });
});

describe("long-running display controls", () => {
  it("lets only the reader itself use fullscreen and the screen wake lock", async () => {
    const cookie = await browserSessionCookie();
    const responses = [
      await SELF.fetch("https://example.com/"),
      await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } }),
    ];
    for (const response of responses) {
      const policy = permissionsPolicy(response.headers.get("permissions-policy"));
      expect(policy.get("fullscreen")).toBe("(self)");
      expect(policy.get("screen-wake-lock")).toBe("(self)");
      expect(policy.get("camera")).toBe("()");
      expect(policy.get("geolocation")).toBe("()");
      expect(policy.get("microphone")).toBe("()");
      expect(response.headers.get("content-security-policy")).toBe(EXPECTED_CSP);
    }
  });

  it("versions the stylesheet and script so a deployment reaches already open readers", async () => {
    const versioned = /href="\/portal\.css\?v=([0-9a-f]{8})"/u;
    const login = await (await SELF.fetch("https://example.com/")).text();
    const version = versioned.exec(login)?.[1];
    expect(version).toMatch(/^[0-9a-f]{8}$/u);

    const cookie = await browserSessionCookie();
    const empty = await (await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } })).text();
    expect(empty).toContain(`href="/portal.css?v=${version}"`);
    expect(empty).toContain(`<script src="/portal.js?v=${version}" defer></script>`);

    await publishPlaylist(41, ["clock-a"]);
    const player = await (await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } })).text();
    expect(player).toContain(`href="/portal.css?v=${version}"`);
    expect(player).toContain(`<script src="/portal.js?v=${version}" defer></script>`);
    expect(player).toContain(`data-asset-version="${version}"`);
  });
  it("puts the rotate-fullscreen and keep-awake toggles in the bottom dock", async () => {
    await publishPlaylist(42, ["clock-a", "clock-b"]);
    const cookie = await browserSessionCookie();
    const html = await (await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } })).text();
    const dock = html.slice(html.indexOf("data-play-controls"));

    const rotate = openingTag(dock, "data-rotate-fullscreen");
    expect(rotate).toContain('type="button"');
    expect(rotate).toContain('aria-pressed="false"');
    expect(rotate).toContain('aria-label="旋转全屏"');
    expect(rotate).toContain('title="旋转全屏"');
    const awake = openingTag(dock, "data-keep-awake");
    expect(awake).toContain('type="button"');
    expect(awake).toContain('aria-pressed="false"');
    expect(awake).toContain('aria-label="开启不息屏"');
    expect(awake).toContain('title="开启不息屏"');
    expect(dock).toMatch(/<svg class="icon icon-rotate"[^>]*fill="none" stroke="currentColor"/u);
    expect(dock).toMatch(/<svg class="icon icon-awake"[^>]*fill="none" stroke="currentColor"/u);
    // Display options share one group; playback controls keep their places.
    expect(dock).toMatch(
      /<div class="dock-tools" role="group" aria-label="显示选项">\s*<button[^>]*data-fullscreen[\s\S]*?data-rotate-fullscreen[\s\S]*?data-keep-awake[\s\S]*?<\/div>/u,
    );
    for (const control of ["data-previous", "data-pause", "data-next", "data-play-chrome-toggle"]) {
      expect(dock).toContain(control);
    }
    expect(html).toContain("data-dock-status");
    expect(html.match(/data-progress-segment/gu)?.length).toBe(2);
    expect(html).not.toMatch(/\sstyle=/u);
    expect(html).not.toContain("<style");
    expect(html).not.toMatch(/<script(?![^>]*\ssrc=)/u);
  });

});
