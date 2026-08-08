export type TeslaCredentials = {
  clientId: string;
  clientSecret: string;
  audience: string;
};

export type PartnerRegisterResult =
  | {
      ok: true;
      httpStatus: number;
      transactionId: string | null;
    }
  | {
      ok: false;
      httpStatus: number;
      error: string;
      transactionId: string | null;
    };

const PARTNER_TOKEN_URL =
  "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token";
const PARTNER_PUBLIC_KEY_SCOPES =
  "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds";
const MAX_RESPONSE_BYTES = 16 * 1024;

type Fetcher = typeof fetch;

type PartnerTokenResult =
  | { ok: true; token: string }
  | { ok: false; result: PartnerRegisterResult };

export function createTeslaPartnerRegistrar(fetcher: Fetcher = fetch) {
  return async function registerPartner(
    credentials: TeslaCredentials,
    appDomain: string,
  ): Promise<PartnerRegisterResult> {
    const tokenResult = await obtainPartnerToken(credentials, fetcher);
    if (!tokenResult.ok) {
      return tokenResult.result;
    }

    let registerResponse: Response;
    try {
      const registerUrl = new URL(
        "/api/1/partner_accounts",
        `${credentials.audience}/`,
      );
      registerResponse = await fetcher(registerUrl, {
        method: "POST",
        redirect: "manual",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${tokenResult.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ domain: appDomain }),
      });
      await readBoundedJson(registerResponse);
    } catch {
      return {
        ok: false,
        httpStatus: 0,
        error: "partner_register_network_error",
        transactionId: null,
      };
    }

    if (isRedirectStatus(registerResponse.status)) {
      return {
        ok: false,
        httpStatus: registerResponse.status,
        error: "partner_register_redirect_blocked",
        transactionId: null,
      };
    }

    if (!registerResponse.ok) {
      return {
        ok: false,
        httpStatus: registerResponse.status,
        error: "partner_register_failed",
        transactionId: null,
      };
    }

    return {
      ok: true,
      httpStatus: registerResponse.status,
      transactionId: null,
    };
  };
}

export function createTeslaPartnerPublicKeyVerifier(fetcher: Fetcher = fetch) {
  return async function verifyPartnerPublicKey(
    credentials: TeslaCredentials,
    appDomain: string,
  ): Promise<PartnerRegisterResult> {
    const tokenResult = await obtainPartnerToken(
      credentials,
      fetcher,
      PARTNER_PUBLIC_KEY_SCOPES,
    );
    if (!tokenResult.ok) {
      return tokenResult.result;
    }

    let publicKeyResponse: Response;
    let publicKeyPayload: Record<string, unknown> | null;
    try {
      const publicKeyUrl = new URL(
        "/api/1/partner_accounts/public_key",
        `${credentials.audience}/`,
      );
      publicKeyUrl.searchParams.set("domain", appDomain);
      publicKeyResponse = await fetcher(publicKeyUrl, {
        method: "GET",
        redirect: "manual",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${tokenResult.token}`,
        },
      });
      publicKeyPayload = await readBoundedJson(publicKeyResponse);
    } catch {
      return {
        ok: false,
        httpStatus: 0,
        error: "partner_public_key_network_error",
        transactionId: null,
      };
    }

    if (isRedirectStatus(publicKeyResponse.status)) {
      return {
        ok: false,
        httpStatus: publicKeyResponse.status,
        error: "partner_public_key_redirect_blocked",
        transactionId: null,
      };
    }

    if (!publicKeyResponse.ok) {
      return {
        ok: false,
        httpStatus: publicKeyResponse.status,
        error: "partner_public_key_lookup_failed",
        transactionId: null,
      };
    }

    if (!hasNonNullResponse(publicKeyPayload)) {
      return {
        ok: false,
        httpStatus: publicKeyResponse.status,
        error: "partner_public_key_invalid_response",
        transactionId: null,
      };
    }

    return {
      ok: true,
      httpStatus: publicKeyResponse.status,
      transactionId: null,
    };
  };
}

async function obtainPartnerToken(
  credentials: TeslaCredentials,
  fetcher: Fetcher,
  scope?: string,
): Promise<PartnerTokenResult> {
  let tokenResponse: Response;
  let tokenPayload: Record<string, unknown> | null;
  try {
    const tokenForm = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: credentials.clientId,
      client_secret: credentials.clientSecret,
      audience: credentials.audience,
    });
    if (scope) {
      tokenForm.set("scope", scope);
    }

    tokenResponse = await fetcher(PARTNER_TOKEN_URL, {
      method: "POST",
      redirect: "manual",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
      },
      body: tokenForm,
    });
    tokenPayload = await readBoundedJson(tokenResponse);
  } catch {
    return {
      ok: false,
      result: {
        ok: false,
        httpStatus: 0,
        error: "partner_token_network_error",
        transactionId: null,
      },
    };
  }

  if (isRedirectStatus(tokenResponse.status)) {
    return {
      ok: false,
      result: {
        ok: false,
        httpStatus: tokenResponse.status,
        error: "partner_token_redirect_blocked",
        transactionId: null,
      },
    };
  }

  if (!tokenResponse.ok) {
    return {
      ok: false,
      result: {
        ok: false,
        httpStatus: tokenResponse.status,
        error: "partner_token_failed",
        transactionId: null,
      },
    };
  }

  const partnerToken = readAccessToken(tokenPayload);
  if (!partnerToken) {
    return {
      ok: false,
      result: {
        ok: false,
        httpStatus: tokenResponse.status,
        error: "partner_token_invalid_response",
        transactionId: null,
      },
    };
  }

  return { ok: true, token: partnerToken };
}

function isRedirectStatus(status: number): boolean {
  return status >= 300 && status < 400;
}

function hasNonNullResponse(payload: Record<string, unknown> | null): boolean {
  return payload !== null && payload.response !== null && payload.response !== undefined;
}

async function readBoundedJson(
  response: Response,
): Promise<Record<string, unknown> | null> {
  const declaredLength = Number(response.headers.get("Content-Length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_RESPONSE_BYTES) {
    await response.body?.cancel();
    return null;
  }

  if (!response.body) {
    return null;
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let receivedBytes = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      break;
    }

    receivedBytes += value.byteLength;
    if (receivedBytes > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      return null;
    }
    chunks.push(value);
  }

  const bytes = new Uint8Array(receivedBytes);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    const parsed: unknown = JSON.parse(new TextDecoder().decode(bytes));
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function readAccessToken(payload: Record<string, unknown> | null): string | null {
  const value = payload?.access_token;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
