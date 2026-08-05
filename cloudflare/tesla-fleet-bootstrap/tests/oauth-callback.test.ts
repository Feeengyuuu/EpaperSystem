import { describe, expect, it } from "vitest";

import { createWorker } from "../src/worker";

const TEST_PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
TEST-PUBLIC-KEY-CONTENT
-----END PUBLIC KEY-----
`;

describe("OAuth callback guard", () => {
  it("rejects a missing authorization code and state without caching", async () => {
    const worker = createWorker({ publicKeyPem: TEST_PUBLIC_KEY_PEM });
    const response = await worker.fetch(
      new Request("https://worker.example/oauth/callback"),
    );

    expect(response.status).toBe(400);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(response.headers.get("referrer-policy")).toBe("no-referrer");
    expect(await response.json()).toEqual({ error: "invalid_oauth_callback" });
  });

  it("rejects a mismatched state and never reflects OAuth query values", async () => {
    const worker = createWorker({ publicKeyPem: TEST_PUBLIC_KEY_PEM });
    const authorizationCode = "authorization-code-must-not-leak";
    const receivedState = "attacker-state-must-not-leak";
    const expectedState = "expected-state-must-not-leak";
    const response = await worker.fetch(
      new Request(
        `https://worker.example/oauth/callback?code=${authorizationCode}&state=${receivedState}`,
        {
          headers: {
            Cookie: `__Host-tesla_oauth_state=${expectedState}`,
          },
        },
      ),
    );
    const body = await response.text();

    expect(response.status).toBe(400);
    expect(body).toContain("invalid_oauth_callback");
    expect(body).not.toContain(authorizationCode);
    expect(body).not.toContain(receivedState);
    expect(body).not.toContain(expectedState);
  });

  it("stops safely before token exchange even when state matches", async () => {
    const worker = createWorker({ publicKeyPem: TEST_PUBLIC_KEY_PEM });
    const authorizationCode = "authorization-code-must-not-leak";
    const state = "one-time-state";
    const response = await worker.fetch(
      new Request(
        `https://worker.example/oauth/callback?code=${authorizationCode}&state=${state}`,
        {
          headers: {
            Cookie: `__Host-tesla_oauth_state=${state}`,
          },
        },
      ),
    );
    const body = await response.text();

    expect(response.status).toBe(503);
    expect(body).toContain("oauth_exchange_not_configured");
    expect(body).not.toContain(authorizationCode);
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});
