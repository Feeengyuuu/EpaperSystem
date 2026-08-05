import type {
  PartnerTokenProbeResult,
  TeslaCredentials,
} from "./worker";

const TESLA_PARTNER_TOKEN_URL =
  "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token";
const MAX_OAUTH_RESPONSE_BYTES = 16 * 1024;

export function createTeslaPartnerTokenRequester(
  fetchFn: typeof fetch = fetch,
): (credentials: TeslaCredentials) => Promise<PartnerTokenProbeResult> {
  return async (credentials: TeslaCredentials): Promise<PartnerTokenProbeResult> => {
    const form = new URLSearchParams({
      grant_type: "client_credentials",
      client_id: credentials.clientId,
      client_secret: credentials.clientSecret,
      audience: credentials.audience,
    });

    let response: Response;
    try {
      response = await fetchFn(TESLA_PARTNER_TOKEN_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
        },
        body: form.toString(),
        redirect: "error",
      });
    } catch {
      return {
        ok: false,
        httpStatus: 0,
        error: "oauth_transport_error",
        transactionId: null,
      };
    }

    const transactionId = safeHeaderValue(response.headers.get("x-txid"));
    const payload = await readBoundedJson(response);

    if (!response.ok) {
      return {
        ok: false,
        httpStatus: response.status,
        error: safeOAuthError(payload?.error),
        transactionId,
      };
    }

    const tokenReceived =
      typeof payload?.access_token === "string" &&
      payload.access_token.length > 0;
    if (!tokenReceived) {
      return {
        ok: false,
        httpStatus: response.status,
        error: "missing_access_token",
        transactionId,
      };
    }

    return {
      ok: true,
      httpStatus: response.status,
      tokenReceived: true,
      tokenType: safeTokenType(payload?.token_type),
      expiresInSeconds: safeExpiry(payload?.expires_in),
      transactionId,
    };
  };
}

async function readBoundedJson(
  response: Response,
): Promise<Record<string, unknown> | null> {
  const declaredLength = Number(response.headers.get("Content-Length"));
  if (
    Number.isFinite(declaredLength) &&
    declaredLength > MAX_OAUTH_RESPONSE_BYTES
  ) {
    if (response.body) {
      await response.body.cancel();
    }
    return null;
  }

  if (!response.body) {
    return null;
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      totalBytes += value.byteLength;
      if (totalBytes > MAX_OAUTH_RESPONSE_BYTES) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const bytes = new Uint8Array(totalBytes);
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeOAuthError(value: unknown): string {
  return typeof value === "string" && /^[a-z0-9_]{1,64}$/.test(value)
    ? value
    : "oauth_request_failed";
}

function safeHeaderValue(value: string | null): string | null {
  return value && /^[A-Za-z0-9._-]{1,128}$/.test(value) ? value : null;
}

function safeTokenType(value: unknown): string | null {
  return typeof value === "string" && /^[A-Za-z0-9._-]{1,32}$/.test(value)
    ? value
    : null;
}

function safeExpiry(value: unknown): number | null {
  return typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= 0 &&
    value <= 604_800
    ? value
    : null;
}
