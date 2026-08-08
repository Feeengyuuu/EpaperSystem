import { describe, expect, test } from "vitest";

import {
  createTeslaPartnerPublicKeyVerifier,
  createTeslaPartnerRegistrar,
} from "../src/tesla-partner";

const CREDENTIALS = {
  clientId: "client-id+reserved",
  clientSecret: "secret&reserved=value",
  audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
};

describe("Tesla Partner API boundary", () => {
  test("obtains a Partner Token internally and registers the exact app domain", async () => {
    const requests: Request[] = [];
    const fakePartnerToken = "partner-token-must-not-escape";
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);

      if (requests.length === 1) {
        return Response.json(
          {
            access_token: fakePartnerToken,
            token_type: "Bearer",
            expires_in: 300,
          },
          { status: 200, headers: { "x-txid": "token-tx" } },
        );
      }

      return Response.json(
        { response: null },
        { status: 200, headers: { "x-txid": "register-tx" } },
      );
    };
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: true,
      httpStatus: 200,
      transactionId: null,
    });
    expect(JSON.stringify(result)).not.toContain(fakePartnerToken);

    expect(requests).toHaveLength(2);
    expect(requests[0].url).toBe(
      "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
    );
    expect(requests[0].headers.get("Content-Type")).toContain(
      "application/x-www-form-urlencoded",
    );
    expect(requests[0].redirect).toBe("manual");
    const tokenForm = new URLSearchParams(await requests[0].text());
    expect(Object.fromEntries(tokenForm)).toEqual({
      grant_type: "client_credentials",
      client_id: CREDENTIALS.clientId,
      client_secret: CREDENTIALS.clientSecret,
      audience: CREDENTIALS.audience,
    });

    expect(requests[1].url).toBe(
      "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts",
    );
    expect(requests[1].headers.get("Authorization")).toBe(
      `Bearer ${fakePartnerToken}`,
    );
    expect(requests[1].redirect).toBe("manual");
    expect(await requests[1].json()).toEqual({
      domain: "epaper-vehicle-bridge.superxfy.workers.dev",
    });
  });

  test("sanitizes Partner Token failures without leaking credentials", async () => {
    const fetcher: typeof fetch = async () =>
      Response.json(
        {
          error: CREDENTIALS.clientSecret,
          error_description: `rejected ${CREDENTIALS.clientSecret}`,
        },
        {
          status: 401,
          headers: { "x-txid": CREDENTIALS.clientSecret },
        },
      );
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 401,
      error: "partner_token_failed",
      transactionId: null,
    });
    expect(JSON.stringify(result)).not.toContain(CREDENTIALS.clientSecret);
  });

  test("sanitizes network failures", async () => {
    const fetcher: typeof fetch = async () => {
      throw new Error(`network failure ${CREDENTIALS.clientSecret}`);
    };
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 0,
      error: "partner_token_network_error",
      transactionId: null,
    });
    expect(JSON.stringify(result)).not.toContain(CREDENTIALS.clientSecret);
  });

  test("distinguishes a register-stage network failure", async () => {
    let requestCount = 0;
    const fetcher: typeof fetch = async () => {
      requestCount += 1;
      if (requestCount === 1) {
        return Response.json({ access_token: "internal-partner-token" });
      }
      throw new Error("register network failure");
    };
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 0,
      error: "partner_register_network_error",
      transactionId: null,
    });
    expect(requestCount).toBe(2);
  });

  test("verifies registration through Tesla's read-only public-key endpoint", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      if (requests.length === 1) {
        return Response.json({ access_token: "internal-partner-token" });
      }
      return Response.json({ response: { public_key: "PUBLIC KEY" } });
    };
    const verifyPartnerPublicKey =
      createTeslaPartnerPublicKeyVerifier(fetcher);

    const result = await verifyPartnerPublicKey(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: true,
      httpStatus: 200,
      transactionId: null,
    });
    expect(requests).toHaveLength(2);
    const verifierTokenForm = new URLSearchParams(await requests[0].text());
    expect(verifierTokenForm.get("scope")).toBe(
      "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds",
    );
    expect(requests[1].method).toBe("GET");
    expect(requests[1].redirect).toBe("manual");
    expect(requests[1].url).toBe(
      "https://fleet-api.prd.na.vn.cloud.tesla.com/api/1/partner_accounts/public_key?domain=epaper-vehicle-bridge.superxfy.workers.dev",
    );
    expect(requests[1].headers.get("Authorization")).toBe(
      "Bearer internal-partner-token",
    );
  });

  test("never follows a Partner Token redirect", async () => {
    let requestCount = 0;
    const fetcher: typeof fetch = async () => {
      requestCount += 1;
      return new Response(null, {
        status: 302,
        headers: { Location: "https://untrusted.example/token" },
      });
    };
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 302,
      error: "partner_token_redirect_blocked",
      transactionId: null,
    });
    expect(requestCount).toBe(1);
  });

  test("never follows a Partner Register redirect", async () => {
    let requestCount = 0;
    const fetcher: typeof fetch = async () => {
      requestCount += 1;
      if (requestCount === 1) {
        return Response.json({ access_token: "internal-partner-token" });
      }
      return new Response(null, {
        status: 307,
        headers: { Location: "https://untrusted.example/register" },
      });
    };
    const registerPartner = createTeslaPartnerRegistrar(fetcher);

    const result = await registerPartner(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 307,
      error: "partner_register_redirect_blocked",
      transactionId: null,
    });
    expect(requestCount).toBe(2);
  });

  test("does not claim registration for an invalid public-key response", async () => {
    let requestCount = 0;
    const fetcher: typeof fetch = async () => {
      requestCount += 1;
      if (requestCount === 1) {
        return Response.json({ access_token: "internal-partner-token" });
      }
      return new Response("not-json", { status: 200 });
    };
    const verifyPartnerPublicKey =
      createTeslaPartnerPublicKeyVerifier(fetcher);

    const result = await verifyPartnerPublicKey(
      CREDENTIALS,
      "epaper-vehicle-bridge.superxfy.workers.dev",
    );

    expect(result).toEqual({
      ok: false,
      httpStatus: 200,
      error: "partner_public_key_invalid_response",
      transactionId: null,
    });
  });
});
