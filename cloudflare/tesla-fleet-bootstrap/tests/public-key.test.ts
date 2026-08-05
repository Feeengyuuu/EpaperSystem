import { describe, expect, it } from "vitest";

import { createWorker } from "../src/worker";

const TEST_PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
TEST-PUBLIC-KEY-CONTENT
-----END PUBLIC KEY-----
`;

describe("Tesla public-key endpoint", () => {
  it("returns the exact PEM at Tesla's required path", async () => {
    const worker = createWorker({ publicKeyPem: TEST_PUBLIC_KEY_PEM });
    const response = await worker.fetch(
      new Request(
        "https://worker.example/.well-known/appspecific/com.tesla.3p.public-key.pem",
      ),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toBe("application/x-pem-file");
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(await response.text()).toBe(TEST_PUBLIC_KEY_PEM);
  });
});
