import { describe, expect, it, vi } from "vitest";

import { createTeslaPartnerTokenRequester } from "../src/tesla";
import { createWorker } from "../src/worker";

const TEST_PUBLIC_KEY_PEM = `-----BEGIN PUBLIC KEY-----
TEST-PUBLIC-KEY-CONTENT
-----END PUBLIC KEY-----
`;

describe("protected Tesla credential check", () => {
  it("conceals the endpoint and never contacts Tesla without the admin token", async () => {
    const requestPartnerToken = vi.fn();
    const worker = createWorker({
      publicKeyPem: TEST_PUBLIC_KEY_PEM,
      probeAuthToken: "server-managed-admin-token",
      teslaCredentials: {
        clientId: "server-managed-client-id",
        clientSecret: "server-managed-client-secret",
        audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
      },
      requestPartnerToken,
    });
    const response = await worker.fetch(
      new Request("https://worker.example/admin/credential-check", {
        method: "POST",
        headers: { Authorization: "Bearer incorrect-token" },
      }),
    );

    expect(response.status).toBe(404);
    expect(await response.text()).toBe("Not Found");
    expect(requestPartnerToken).not.toHaveBeenCalled();
  });

  it("returns only a sanitized Tesla rejection to an authorized caller", async () => {
    const requestPartnerToken = vi.fn().mockResolvedValue({
      ok: false,
      httpStatus: 400,
      error: "unauthorized_client",
      transactionId: "tesla-transaction-id",
    });
    const clientId = "server-managed-client-id";
    const clientSecret = "server-managed-client-secret";
    const worker = createWorker({
      publicKeyPem: TEST_PUBLIC_KEY_PEM,
      probeAuthToken: "server-managed-admin-token",
      teslaCredentials: {
        clientId,
        clientSecret,
        audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
      },
      requestPartnerToken,
    });
    const response = await worker.fetch(
      new Request("https://worker.example/admin/credential-check", {
        method: "POST",
        headers: { Authorization: "Bearer server-managed-admin-token" },
      }),
    );
    const body = await response.text();

    expect(response.status).toBe(502);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(JSON.parse(body)).toEqual({
      ok: false,
      provider: "tesla",
      tesla_status: 400,
      error: "unauthorized_client",
      x_txid: "tesla-transaction-id",
    });
    expect(body).not.toContain(clientId);
    expect(body).not.toContain(clientSecret);
  });

  it("reports token acquisition without exposing the Partner Token", async () => {
    const partnerToken = "partner-token-must-never-leave-the-worker";
    const requestPartnerToken = vi.fn().mockResolvedValue({
      ok: true,
      httpStatus: 200,
      tokenReceived: true,
      tokenType: "Bearer",
      expiresInSeconds: 28_800,
      transactionId: "tesla-success-transaction-id",
      accessToken: partnerToken,
    });
    const worker = createWorker({
      publicKeyPem: TEST_PUBLIC_KEY_PEM,
      probeAuthToken: "server-managed-admin-token",
      teslaCredentials: {
        clientId: "server-managed-client-id",
        clientSecret: "server-managed-client-secret",
        audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
      },
      requestPartnerToken,
    });
    const response = await worker.fetch(
      new Request("https://worker.example/admin/credential-check", {
        method: "POST",
        headers: { Authorization: "Bearer server-managed-admin-token" },
      }),
    );
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(JSON.parse(body)).toEqual({
      ok: true,
      provider: "tesla",
      tesla_status: 200,
      token_received: true,
      token_type: "Bearer",
      expires_in_seconds: 28_800,
      x_txid: "tesla-success-transaction-id",
    });
    expect(body).not.toContain(partnerToken);
  });

  it("uses Tesla's form-encoded contract while keeping its token inside the Worker", async () => {
    const partnerToken = "partner-token-must-never-leave-the-worker";
    const teslaFetch = vi.fn().mockResolvedValue(
      Response.json(
        {
          access_token: partnerToken,
          token_type: "Bearer",
          expires_in: 28_800,
        },
        {
          headers: { "x-txid": "tesla-live-contract-id" },
        },
      ),
    );
    const clientSecret = "secret-with-form-characters+@%";
    const requestPartnerToken = createTeslaPartnerTokenRequester(teslaFetch);
    const worker = createWorker({
      publicKeyPem: TEST_PUBLIC_KEY_PEM,
      probeAuthToken: "server-managed-admin-token",
      teslaCredentials: {
        clientId: "server-managed-client-id",
        clientSecret,
        audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
      },
      requestPartnerToken,
    });
    const response = await worker.fetch(
      new Request("https://worker.example/admin/credential-check", {
        method: "POST",
        headers: { Authorization: "Bearer server-managed-admin-token" },
      }),
    );
    const body = await response.text();

    expect(response.status).toBe(200);
    expect(body).not.toContain(partnerToken);
    const requestInit = teslaFetch.mock.calls[0]?.[1] as RequestInit;
    const submittedForm = new URLSearchParams(requestInit.body as string);
    expect(submittedForm.get("client_secret")).toBe(clientSecret);
  });
});
