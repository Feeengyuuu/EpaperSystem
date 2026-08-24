import type {
  BeginAuthorizationInput,
  CompleteAuthorizationInput,
  OAuthCompletion,
  VehicleSummaryResult,
  VehicleSummarySchemaVersion,
} from "./contracts";
import {
  constantTimeStringMatch,
  isUrlToken,
  randomUrlToken,
  sha256Base64Url,
} from "./security";

const PUBLIC_KEY_PATH =
  "/.well-known/appspecific/com.tesla.3p.public-key.pem";
const OAUTH_LAUNCH_PATH = "/v1/oauth/launch";
const OAUTH_START_PATH = "/oauth/start";
const OAUTH_CALLBACK_PATH = "/oauth/callback";
const OAUTH_RESULT_PATH = "/oauth/result";
const VEHICLE_SUMMARY_PATH = "/api/vehicle-summary";
const OAUTH_BROWSER_COOKIE = "__Host-vehicle_oauth_browser";
const TESLA_AUTHORIZE_URL = "https://auth.tesla.com/oauth2/v3/authorize";

export interface VehicleAuthSession {
  createOAuthLaunch(launchHash: string, expiresAt: number): Promise<void>;
  beginAuthorization(input: BeginAuthorizationInput): Promise<boolean>;
  completeAuthorization(
    input: CompleteAuthorizationInput,
  ): Promise<OAuthCompletion>;
  getVehicleSummary(
    nowMs: number,
    cacheTtlSeconds: number,
    schemaVersion: VehicleSummarySchemaVersion,
  ): Promise<VehicleSummaryResult>;
}

type WorkerOptions = {
  publicKeyPem: string;
  bridgeAdminToken: string;
  bridgeReadToken: string;
  teslaClientId: string;
  teslaAudience: string;
  teslaScopes: string;
  oauthSessionTtlSeconds: number;
  oauthLaunchTtlSeconds: number;
  vehicleSummaryTtlSeconds: number;
  session: VehicleAuthSession;
  appOrigin?: string;
  teslaRedirectUri?: string;
};

export function createWorker(options: WorkerOptions) {
  return {
    async fetch(request: Request): Promise<Response> {
      try {
        return await routeRequest(request, options);
      } catch {
        return jsonResponse({ error: "internal_error" }, 500);
      }
    },
  };
}

async function routeRequest(
  request: Request,
  options: WorkerOptions,
): Promise<Response> {
      const url = new URL(request.url);

      if (request.method === "GET" && url.pathname === PUBLIC_KEY_PATH) {
        return new Response(options.publicKeyPem, {
          headers: {
            "Cache-Control": "public, max-age=300",
            "Content-Type": "application/x-pem-file",
            "X-Content-Type-Options": "nosniff",
          },
        });
      }

      if (request.method === "POST" && url.pathname === OAUTH_LAUNCH_PATH) {
        return handleOAuthLaunch(request, url, options);
      }

      if (request.method === "GET" && url.pathname === OAUTH_START_PATH) {
        return handleOAuthStart(url, options);
      }

      if (request.method === "GET" && url.pathname === OAUTH_CALLBACK_PATH) {
        return handleOAuthCallback(request, url, options);
      }

      if (request.method === "GET" && url.pathname === OAUTH_RESULT_PATH) {
        return oauthResultPage(url.searchParams.get("status"));
      }

      if (
        request.method === "GET" &&
        url.pathname === VEHICLE_SUMMARY_PATH
      ) {
        return handleVehicleSummary(request, url, options);
      }

      return new Response("Not Found", { status: 404 });
}

async function handleOAuthLaunch(
  request: Request,
  url: URL,
  options: WorkerOptions,
): Promise<Response> {
  if (!(await isAuthorized(request, options.bridgeAdminToken))) {
    return unauthorized();
  }

  const launch = randomUrlToken();
  const launchHash = await sha256Base64Url(launch);
  const expiresAt = Date.now() + options.oauthLaunchTtlSeconds * 1_000;
  await options.session.createOAuthLaunch(launchHash, expiresAt);

  const authorizationUrl = new URL(
    OAUTH_START_PATH,
    options.appOrigin ?? url.origin,
  );
  authorizationUrl.searchParams.set("launch", launch);
  return jsonResponse(
    {
      authorization_url: authorizationUrl.toString(),
      expires_in: options.oauthLaunchTtlSeconds,
    },
    201,
  );
}

async function handleOAuthStart(
  url: URL,
  options: WorkerOptions,
): Promise<Response> {
  const launches = url.searchParams.getAll("launch");
  const launch = launches.length === 1 ? launches[0] : null;
  if (!isUrlToken(launch)) {
    return oauthError(400, "invalid_oauth_launch");
  }

  const now = Date.now();
  const state = randomUrlToken();
  const browser = randomUrlToken();
  const [launchHash, stateHash, browserHash] = await Promise.all([
    sha256Base64Url(launch),
    sha256Base64Url(state),
    sha256Base64Url(browser),
  ]);
  const began = await options.session.beginAuthorization({
    launch_hash: launchHash,
    state_hash: stateHash,
    browser_hash: browserHash,
    expires_at: now + options.oauthSessionTtlSeconds * 1_000,
    now_ms: now,
  });
  if (!began) {
    return oauthError(400, "invalid_oauth_launch");
  }

  const redirectUri =
    options.teslaRedirectUri ??
    new URL(OAUTH_CALLBACK_PATH, url.origin).toString();
  const teslaUrl = new URL(TESLA_AUTHORIZE_URL);
  teslaUrl.searchParams.set("response_type", "code");
  teslaUrl.searchParams.set("client_id", options.teslaClientId);
  teslaUrl.searchParams.set("redirect_uri", redirectUri);
  teslaUrl.searchParams.set("scope", options.teslaScopes);
  teslaUrl.searchParams.set("state", state);
  teslaUrl.searchParams.set("prompt_missing_scopes", "true");
  teslaUrl.searchParams.set("require_requested_scopes", "true");

  return redirectResponse(teslaUrl.toString(), {
    "Set-Cookie": `${OAUTH_BROWSER_COOKIE}=${browser}; Path=/; Max-Age=${options.oauthSessionTtlSeconds}; HttpOnly; Secure; SameSite=Lax`,
  });
}

async function handleOAuthCallback(
  request: Request,
  url: URL,
  options: WorkerOptions,
): Promise<Response> {
  const codes = url.searchParams.getAll("code");
  const states = url.searchParams.getAll("state");
  const authorizationCode = codes.length === 1 ? codes[0] : null;
  const state = states.length === 1 ? states[0] : null;
  const browser = readCookie(
    request.headers.get("Cookie"),
    OAUTH_BROWSER_COOKIE,
  );

  if (
    !authorizationCode ||
    authorizationCode.length > 2_048 ||
    !isUrlToken(state) ||
    !isUrlToken(browser)
  ) {
    return oauthError(400, "invalid_oauth_callback", clearBrowserCookie());
  }

  const [stateHash, browserHash] = await Promise.all([
    sha256Base64Url(state),
    sha256Base64Url(browser),
  ]);
  const redirectUri =
    options.teslaRedirectUri ??
    new URL(OAUTH_CALLBACK_PATH, url.origin).toString();
  const completion = await options.session.completeAuthorization({
    authorization_code: authorizationCode,
    state_hash: stateHash,
    browser_hash: browserHash,
    redirect_uri: redirectUri,
  });

  if (!completion.ok) {
    const status =
      completion.error === "invalid_oauth_session" ? 400 : 502;
    return oauthError(status, completion.error, clearBrowserCookie());
  }

  const resultUrl = new URL(
    OAUTH_RESULT_PATH,
    options.appOrigin ?? url.origin,
  );
  resultUrl.searchParams.set("status", "connected");
  return redirectResponse(resultUrl.toString(), clearBrowserCookie());
}

async function handleVehicleSummary(
  request: Request,
  url: URL,
  options: WorkerOptions,
): Promise<Response> {
  if (!(await isAuthorized(request, options.bridgeReadToken))) {
    return unauthorized();
  }

  const requestedVersions = url.searchParams.getAll("schema_version");
  if (
    requestedVersions.length > 1 ||
    (requestedVersions.length === 1 &&
      requestedVersions[0] !== "1" &&
      requestedVersions[0] !== "2" &&
      requestedVersions[0] !== "3")
  ) {
    return jsonResponse({ error: "invalid_schema_version" }, 400);
  }
  const schemaVersion: VehicleSummarySchemaVersion =
    requestedVersions[0] === "3"
      ? 3
      : requestedVersions[0] === "2"
        ? 2
        : 1;

  const result = await options.session.getVehicleSummary(
    Date.now(),
    options.vehicleSummaryTtlSeconds,
    schemaVersion,
  );
  if (!result.ok) {
    const status =
      result.error === "vehicle_data_temporarily_unavailable" ? 503 : 409;
    return jsonResponse({ error: result.error }, status, {
      Vary: "Authorization",
    });
  }

  return jsonResponse(result.summary, 200, { Vary: "Authorization" });
}

async function isAuthorized(
  request: Request,
  expectedToken: string,
): Promise<boolean> {
  const authorization = request.headers.get("Authorization");
  if (!authorization) {
    return false;
  }
  const match = /^Bearer ([A-Za-z0-9_-]+)$/.exec(authorization);
  if (!match || !expectedToken) {
    return false;
  }
  return constantTimeStringMatch(match[1], expectedToken);
}

function readCookie(cookieHeader: string | null, name: string): string | null {
  if (!cookieHeader) {
    return null;
  }

  let result: string | null = null;
  for (const cookie of cookieHeader.split(";")) {
    const separator = cookie.indexOf("=");
    if (separator === -1 || cookie.slice(0, separator).trim() !== name) {
      continue;
    }
    if (result !== null) {
      return null;
    }
    try {
      result = decodeURIComponent(cookie.slice(separator + 1).trim());
    } catch {
      return null;
    }
  }
  return result;
}

function clearBrowserCookie(): Record<string, string> {
  return {
    "Set-Cookie": `${OAUTH_BROWSER_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
  };
}

function redirectResponse(
  location: string,
  extraHeaders: Record<string, string> = {},
): Response {
  return new Response(null, {
    status: 303,
    headers: {
      ...securityHeaders(),
      "Cache-Control": "no-store",
      Location: location,
      ...extraHeaders,
    },
  });
}

function oauthResultPage(status: string | null): Response {
  const connected = status === "connected";
  const title = connected
    ? "Tesla account connected"
    : "Tesla connection was not completed";
  const detail = connected
    ? "The read-only bridge is ready. You can close this tab."
    : "Return to the setup tool and create a new authorization link.";
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${title}</title></head><body><main><h1>${title}</h1><p>${detail}</p></main></body></html>`;
  return new Response(html, {
    headers: {
      ...securityHeaders(),
      "Cache-Control": "no-store",
      "Content-Type": "text/html; charset=utf-8",
    },
  });
}

function unauthorized(): Response {
  return jsonResponse({ error: "unauthorized" }, 401, {
    "WWW-Authenticate": "Bearer",
  });
}

function oauthError(
  status: number,
  error: string,
  extraHeaders: Record<string, string> = {},
): Response {
  return jsonResponse({ error }, status, extraHeaders);
}

function jsonResponse(
  body: unknown,
  status: number,
  extraHeaders: Record<string, string> = {},
): Response {
  return Response.json(body, {
    status,
    headers: {
      ...securityHeaders(),
      "Cache-Control": "no-store",
      ...extraHeaders,
    },
  });
}

function securityHeaders(): Record<string, string> {
  return {
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
  };
}
