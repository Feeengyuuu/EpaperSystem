import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";

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
    expect(body).toContain('<script src="/portal.js" defer></script>');
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
