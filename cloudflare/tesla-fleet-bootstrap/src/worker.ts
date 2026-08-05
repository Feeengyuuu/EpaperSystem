const TESLA_PUBLIC_KEY_PATH =
  "/.well-known/appspecific/com.tesla.3p.public-key.pem";
const OAUTH_CALLBACK_PATH = "/oauth/callback";
const OAUTH_STATE_COOKIE = "__Host-tesla_oauth_state";
const CREDENTIAL_CHECK_PATH = "/admin/credential-check";

export type TeslaCredentials = {
  clientId: string;
  clientSecret: string;
  audience: string;
};

export type PartnerTokenProbeResult =
  | {
      ok: true;
      httpStatus: number;
      tokenReceived: boolean;
      tokenType: string | null;
      expiresInSeconds: number | null;
      transactionId: string | null;
    }
  | {
      ok: false;
      httpStatus: number;
      error: string;
      transactionId: string | null;
    };

type WorkerOptions = {
  publicKeyPem: string;
  probeAuthToken?: string;
  teslaCredentials?: TeslaCredentials;
  requestPartnerToken?: (
    credentials: TeslaCredentials,
  ) => Promise<PartnerTokenProbeResult>;
};

export function createWorker(options: WorkerOptions) {
  return {
    async fetch(request: Request): Promise<Response> {
      const url = new URL(request.url);

      if (request.method === "GET" && url.pathname === TESLA_PUBLIC_KEY_PATH) {
        return new Response(options.publicKeyPem, {
          headers: {
            "Cache-Control": "public, max-age=300",
            "Content-Type": "application/x-pem-file",
            "X-Content-Type-Options": "nosniff",
          },
        });
      }

      if (request.method === "GET" && url.pathname === OAUTH_CALLBACK_PATH) {
        return handleOAuthCallback(request, url);
      }

      if (
        request.method === "POST" &&
        url.pathname === CREDENTIAL_CHECK_PATH &&
        options.probeAuthToken &&
        options.teslaCredentials &&
        options.requestPartnerToken &&
        (await hasValidBearerToken(request, options.probeAuthToken))
      ) {
        const result = await options.requestPartnerToken(
          options.teslaCredentials,
        );

        if (!result.ok) {
          return credentialJsonResponse(502, {
            ok: false,
            provider: "tesla",
            tesla_status: result.httpStatus,
            error: result.error,
            x_txid: result.transactionId,
          });
        }

        return credentialJsonResponse(200, {
          ok: true,
          provider: "tesla",
          tesla_status: result.httpStatus,
          token_received: result.tokenReceived,
          token_type: result.tokenType,
          expires_in_seconds: result.expiresInSeconds,
          x_txid: result.transactionId,
        });
      }

      return new Response("Not Found", { status: 404 });
    },
  };
}

async function hasValidBearerToken(
  request: Request,
  expectedToken: string,
): Promise<boolean> {
  const authorization = request.headers.get("Authorization");
  if (!authorization?.startsWith("Bearer ")) {
    return false;
  }

  const receivedToken = authorization.slice("Bearer ".length);
  if (!receivedToken) {
    return false;
  }

  const encoder = new TextEncoder();
  const [receivedDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(receivedToken)),
    crypto.subtle.digest("SHA-256", encoder.encode(expectedToken)),
  ]);

  if (typeof crypto.subtle.timingSafeEqual === "function") {
    return crypto.subtle.timingSafeEqual(receivedDigest, expectedDigest);
  }

  const receivedBytes = new Uint8Array(receivedDigest);
  const expectedBytes = new Uint8Array(expectedDigest);
  let difference = 0;
  for (let index = 0; index < receivedBytes.length; index += 1) {
    difference |= receivedBytes[index] ^ expectedBytes[index];
  }
  return difference === 0;
}

function credentialJsonResponse(
  status: number,
  body: Record<string, unknown>,
): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
      "Referrer-Policy": "no-referrer",
      "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "DENY",
    },
  });
}

async function handleOAuthCallback(
  request: Request,
  url: URL,
): Promise<Response> {
  const authorizationCode = url.searchParams.get("code");
  const receivedState = url.searchParams.get("state");
  const expectedState = readCookie(request.headers.get("Cookie"), OAUTH_STATE_COOKIE);

  if (
    !authorizationCode ||
    !receivedState ||
    !expectedState ||
    !(await statesMatch(receivedState, expectedState))
  ) {
    return oauthJsonResponse(400, "invalid_oauth_callback");
  }

  return oauthJsonResponse(503, "oauth_exchange_not_configured", {
    "Set-Cookie": `${OAUTH_STATE_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
  });
}

function readCookie(cookieHeader: string | null, name: string): string | null {
  if (!cookieHeader) {
    return null;
  }

  for (const cookie of cookieHeader.split(";")) {
    const separator = cookie.indexOf("=");
    if (separator === -1 || cookie.slice(0, separator).trim() !== name) {
      continue;
    }

    const value = cookie.slice(separator + 1).trim();
    try {
      return decodeURIComponent(value);
    } catch {
      return null;
    }
  }

  return null;
}

async function statesMatch(received: string, expected: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const [receivedDigest, expectedDigest] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(received)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  const receivedBytes = new Uint8Array(receivedDigest);
  const expectedBytes = new Uint8Array(expectedDigest);
  let difference = 0;

  for (let index = 0; index < receivedBytes.length; index += 1) {
    difference |= receivedBytes[index] ^ expectedBytes[index];
  }

  return difference === 0;
}

function oauthJsonResponse(
  status: number,
  error: string,
  extraHeaders: Record<string, string> = {},
): Response {
  return Response.json(
    { error },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        ...extraHeaders,
      },
    },
  );
}
