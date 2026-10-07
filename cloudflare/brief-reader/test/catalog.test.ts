import { SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { browserSessionCookie } from "./support";

describe("published catalog reader", () => {
  it("does not reveal publication state without a valid session", async () => {
    const response = await SELF.fetch("https://example.com/api/publications");

    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(await response.text()).toBe("Unauthorized");
  });

  it("returns a stable empty catalog before the first atomic release", async () => {
    const response = await SELF.fetch("https://example.com/api/publications", {
      headers: { Cookie: await browserSessionCookie() },
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("application/json");
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    expect(response.headers.get("etag")).toBe('"empty"');
    expect(await response.json()).toEqual({ release: null });
  });

  it("accepts a weak conditional validator produced by the edge", async () => {
    const response = await SELF.fetch("https://example.com/api/publications", {
      headers: {
        Cookie: await browserSessionCookie(),
        "If-None-Match": 'W/"empty"',
      },
    });

    expect(response.status).toBe(304);
    expect(await response.text()).toBe("");
  });
});
