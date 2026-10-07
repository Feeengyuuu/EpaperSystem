import portalCss from "../../../inkypi-weather/package/InkyPi/src/web_portal/static/portal.css";
import portalScript from "../../../inkypi-weather/package/InkyPi/src/web_portal/static/portal.js";
import { APPLE_TOUCH_ICON_PNG, LOGO_SVG } from "./brand_icons";

export { PortalLedger } from "./ledger";

// Tab icon and the iPhone "Add to Home Screen" icon.
const BRAND_HEAD = `<link rel="icon" href="/logo.svg" type="image/svg+xml">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">`;

const ICON_HEADERS = {
  "Cache-Control": "public, max-age=86400",
  "X-Content-Type-Options": "nosniff",
};

const HTML_HEADERS = {
  "Cache-Control": "private, no-store",
  "Content-Security-Policy": [
    "default-src 'none'",
    "base-uri 'none'",
    "connect-src 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "img-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
  ].join("; "),
  "Content-Type": "text/html; charset=utf-8",
  "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
} as const;

const textEncoder = new TextEncoder();
const SESSION_COOKIE = "__Host-epaper_session";
const SESSION_MAX_AGE_SECONDS = 400 * 24 * 60 * 60;
const DEFAULT_MINIMUM_REFRESH_INTERVAL_SECONDS = 120;
const MINIMUM_PUBLICATION_POLL_SECONDS = 15;
const MAXIMUM_PUBLICATION_POLL_SECONDS = 60;
const PUBLICATION_POLL_DIVISOR = 4;

interface ReaderItem {
  instanceId: string;
  pluginId: string;
  title: string;
  displayTitle: string;
  assetId: string;
  generatedAt: string;
  sourceUpdatedAt: string;
  width: 800;
  height: 480;
}

interface ReaderRelease {
  schemaVersion: 1 | 2;
  generation: number;
  publishedAt: string;
  timezone: string;
  playlist: { slug: string; title: string };
  minimumRefreshIntervalSeconds?: number;
  items: ReaderItem[];
}

function escapeHtml(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function weaklyMatchesEtag(candidate: string | null, current: string): boolean {
  if (candidate === null) return false;
  const normalizedCandidate = candidate.trim();
  if (normalizedCandidate === "*") return true;
  const normalizedCurrent = current.trim();
  const stripWeakPrefix = (value: string): string => value.startsWith("W/") ? value.slice(2) : value;
  return stripWeakPrefix(normalizedCandidate) === stripWeakPrefix(normalizedCurrent);
}

function localTime(value: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat("zh-CN", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: timezone,
    }).format(new Date(value));
  } catch (_error) {
    return value;
  }
}

function publicationPollMilliseconds(release: ReaderRelease): number {
  const candidate = release.minimumRefreshIntervalSeconds;
  const minimumRefreshIntervalSeconds =
    typeof candidate === "number" && Number.isSafeInteger(candidate) && candidate > 0
      ? candidate
      : DEFAULT_MINIMUM_REFRESH_INTERVAL_SECONDS;
  const pollSeconds = Math.max(
    MINIMUM_PUBLICATION_POLL_SECONDS,
    Math.min(
      MAXIMUM_PUBLICATION_POLL_SECONDS,
      Math.ceil(minimumRefreshIntervalSeconds / PUBLICATION_POLL_DIVISOR),
    ),
  );
  return pollSeconds * 1000;
}

function encodeBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

function decodeBase64Url(value: string): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) return null;
  try {
    const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch (_error) {
    return null;
  }
}

async function sessionKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

async function createSession(secret: string, now = Date.now()): Promise<string> {
  const expiresAt = Math.floor(now / 1000) + SESSION_MAX_AGE_SECONDS;
  const payload = `v1.${expiresAt}`;
  const signature = await crypto.subtle.sign("HMAC", await sessionKey(secret), textEncoder.encode(payload));
  return `${payload}.${encodeBase64Url(new Uint8Array(signature))}`;
}

function sessionCookieHeader(token: string): string {
  return `${SESSION_COOKIE}=${token}; Max-Age=${SESSION_MAX_AGE_SECONDS}; Path=/; HttpOnly; Secure; SameSite=Strict`;
}

function withRenewedSession(response: Response, token: string): Response {
  const headers = new Headers(response.headers);
  headers.append("Set-Cookie", sessionCookieHeader(token));
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function verifySession(token: string | null, secret: string, now = Date.now()): Promise<boolean> {
  if (token === null) return false;
  const match = /^(v1)\.(\d{10})\.([A-Za-z0-9_-]+)$/u.exec(token);
  if (match === null) return false;
  const version = match[1];
  const expiresText = match[2];
  const encodedSignature = match[3];
  if (version === undefined || expiresText === undefined || encodedSignature === undefined) return false;
  const expiresAt = Number(expiresText);
  const nowSeconds = Math.floor(now / 1000);
  if (!Number.isSafeInteger(expiresAt) || expiresAt <= nowSeconds || expiresAt > nowSeconds + SESSION_MAX_AGE_SECONDS + 60) {
    return false;
  }
  const signature = decodeBase64Url(encodedSignature);
  if (signature === null || signature.byteLength !== 32) return false;
  return crypto.subtle.verify(
    "HMAC",
    await sessionKey(secret),
    signature,
    textEncoder.encode(`${version}.${expiresText}`),
  );
}

function cookieValue(request: Request, name: string): string | null {
  const cookieHeader = request.headers.get("Cookie");
  if (cookieHeader === null) return null;
  for (const pair of cookieHeader.split(";")) {
    const separator = pair.indexOf("=");
    if (separator === -1) continue;
    if (pair.slice(0, separator).trim() === name) return pair.slice(separator + 1).trim();
  }
  return null;
}

async function constantTimeEqual(provided: string, expected: string): Promise<boolean> {
  const [providedHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", textEncoder.encode(provided)),
    crypto.subtle.digest("SHA-256", textEncoder.encode(expected)),
  ]);
  return crypto.subtle.timingSafeEqual(providedHash, expectedHash);
}

async function readBoundedText(request: Request, maximumBytes: number): Promise<string | null> {
  if (request.body === null) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    length += result.value.byteLength;
    if (length > maximumBytes) {
      await reader.cancel();
      return null;
    }
    chunks.push(result.value);
  }
  const body = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(body);
}

function loginPage(error: string | null = null, status = 200): Response {
  const errorMarkup = error === null ? "" : `<p class="form-error" role="alert">${error}</p>`;
  const body = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>登录 | EpaperSystem 简报</title>
  <link rel="stylesheet" href="/portal.css">
  ${BRAND_HEAD}
</head>
<body class="login-page">
  <main id="main-content" class="login-shell">
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-identity"><p>EpaperSystem 私人简报</p></div>
      <h1 id="login-title">查看你的播放列表</h1>
      <p class="login-intro">页面只展示已经发布的内容，不会控制实体屏幕。</p>
      ${errorMarkup}
      <form class="login-form" action="/login" method="post">
        <label for="username">用户名</label>
        <input id="username" name="username" type="text" value="admin" autocomplete="username" autocapitalize="none" required>
        <label for="password">密码</label>
        <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
        <button type="submit">登录简报</button>
      </form>
      <p class="privacy-note">私人站点，请勿在共用设备上保持登录。</p>
    </section>
  </main>
</body>
</html>`;
  return new Response(body, { status, headers: HTML_HEADERS });
}

async function handleLogin(request: Request, env: Env, url: URL): Promise<Response> {
  const origin = request.headers.get("Origin");
  const fetchSite = request.headers.get("Sec-Fetch-Site");
  if (
    (origin !== url.origin && origin !== "null") ||
    (fetchSite !== null && fetchSite !== "same-origin" && fetchSite !== "none")
  ) {
    return new Response("Forbidden", { status: 403, headers: { "Cache-Control": "no-store" } });
  }
  if (!request.headers.get("Content-Type")?.toLowerCase().startsWith("application/x-www-form-urlencoded")) {
    return new Response("Unsupported Media Type", {
      status: 415,
      headers: { "Cache-Control": "no-store" },
    });
  }
  let encoded: string | null;
  try {
    encoded = await readBoundedText(request, 4096);
  } catch (_error) {
    encoded = null;
  }
  if (encoded === null) {
    return new Response("Request body too large", {
      status: 413,
      headers: { "Cache-Control": "no-store" },
    });
  }
  const form = new URLSearchParams(encoded);
  const usernameValid = form.get("username") === "admin";
  const passwordValid = await constantTimeEqual(form.get("password") ?? "", env.BROWSER_PASSWORD);
  if (!usernameValid || !passwordValid) {
    return loginPage("用户名或密码不正确", 401);
  }
  const token = await createSession(env.SESSION_SECRET);
  const headers = new Headers({
    "Cache-Control": "no-store",
    Location: "/",
  });
  headers.append(
    "Set-Cookie",
    sessionCookieHeader(token),
  );
  return new Response(null, { status: 303, headers });
}

function populatedReaderPage(release: ReaderRelease): Response {
  const slides = release.items
    .map((item, index) => {
      const active = index === 0;
      const title = escapeHtml(item.displayTitle);
      const sourceTime = escapeHtml(item.sourceUpdatedAt);
      const timeLabel = item.pluginId === "weather" ? "当地时间" : "更新时间";
      return `<article class="play-slide${active ? " is-active" : ""}" data-slide aria-hidden="${active ? "false" : "true"}"${active ? "" : " hidden"}>
        <header class="play-publication-heading">
          <div>
            <p class="plugin-name">${escapeHtml(item.pluginId)}</p>
            <h2>${title}</h2>
          </div>
          <div class="freshness-block">
            <span class="time-prefix">${timeLabel}</span>
            <time datetime="${sourceTime}">${escapeHtml(localTime(item.sourceUpdatedAt, release.timezone))}</time>
            <span class="timezone-label">${escapeHtml(release.timezone)}</span>
          </div>
        </header>
        <div class="publication-stage">
          <figure class="legacy-png original-plugin-frame">
            <img src="/assets/${escapeHtml(item.assetId)}" alt="${title}" width="800" height="480" decoding="async" loading="${active ? "eager" : "lazy"}">
            <figcaption>${escapeHtml(item.title)} 原版 800×480 画面</figcaption>
          </figure>
        </div>
      </article>`;
    })
    .join("");
  const body = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>${escapeHtml(release.playlist.title)} | EpaperSystem 简报</title>
  <link rel="stylesheet" href="/portal.css">
  ${BRAND_HEAD}
  <script src="/portal.js" defer></script>
</head>
<body class="play-page">
  <main id="main-content" class="play-shell is-chrome-hidden" data-autoplay data-interval-ms="20000" data-poll-ms="${publicationPollMilliseconds(release)}" data-generation="${release.generation}">
    <header class="play-heading">
      <p class="slide-position" aria-live="polite"><span data-slide-current>1</span> / <span data-slide-total>${release.items.length}</span></p>
    </header>
    <section class="slide-deck" aria-label="自动播放内容">${slides}</section>
    <button class="button button-secondary play-chrome-toggle" type="button" data-play-chrome-toggle aria-pressed="true">显示导航</button>
    <nav class="play-controls" aria-label="播放控制" data-play-controls>
      <button class="button button-secondary" type="button" data-previous>上一页</button>
      <button class="button button-primary" type="button" data-pause aria-pressed="false">暂停播放</button>
      <button class="button button-secondary" type="button" data-next>下一页</button>
      <button class="button button-secondary" type="button" data-fullscreen>全屏显示</button>
    </nav>
  </main>
</body>
</html>`;
  return new Response(body, { headers: HTML_HEADERS });
}

function readerPage(release: ReaderRelease | null): Response {
  if (release !== null) return populatedReaderPage(release);
  const body = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="robots" content="noindex,nofollow,noarchive">
  <title>EpaperSystem 简报</title>
  <link rel="stylesheet" href="/portal.css">
  ${BRAND_HEAD}
</head>
<body>
  <main><h1>还没有发布内容</h1><p>首个完整版本上传后会自动出现在这里。</p></main>
</body>
</html>`;
  return new Response(body, { headers: HTML_HEADERS });
}

function unauthorized(): Response {
  return new Response("Unauthorized", {
    status: 401,
    headers: { "Cache-Control": "private, no-store" },
  });
}

async function currentRelease(env: Env): Promise<Response> {
  const stub = env.PORTAL.getByName("private-portal-v1");
  return stub.fetch("https://portal.internal/internal/current");
}

async function readCurrentRelease(env: Env): Promise<ReaderRelease | null> {
  const response = await currentRelease(env);
  if (!response.ok) return null;
  const payload = (await response.json()) as { release?: ReaderRelease | null };
  return payload.release ?? null;
}

export default {
  async fetch(request: Request, env: Env, context: ExecutionContext): Promise<Response> {
    void context;
    const url = new URL(request.url);
    const ingestPath = /^\/api\/v1\/(?:assets|editions)\/[0-9a-f]{64}$/u.test(url.pathname);
    if (request.method === "PUT" && ingestPath) {
      const stub = env.PORTAL.getByName("private-portal-v1");
      return stub.fetch(request);
    }
    if (request.method === "GET" && url.pathname === "/favicon.ico") {
      return new Response(null, {
        status: 204,
        headers: {
          "Cache-Control": "public, max-age=86400",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }
    if (
      request.method === "GET" &&
      (url.pathname === "/apple-touch-icon.png" || url.pathname === "/apple-touch-icon-precomposed.png")
    ) {
      return new Response(APPLE_TOUCH_ICON_PNG, { headers: { ...ICON_HEADERS, "Content-Type": "image/png" } });
    }
    if (request.method === "GET" && url.pathname === "/logo.svg") {
      return new Response(LOGO_SVG, { headers: { ...ICON_HEADERS, "Content-Type": "image/svg+xml" } });
    }
    if (request.method === "GET" && url.pathname === "/portal.css") {
      return new Response(portalCss, {
        headers: {
          "Cache-Control": "public, max-age=300, must-revalidate",
          "Content-Type": "text/css; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }
    if (request.method === "GET" && url.pathname === "/portal.js") {
      return new Response(portalScript, {
        headers: {
          "Cache-Control": "public, max-age=0, must-revalidate",
          "Content-Type": "text/javascript; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }
    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/login")) {
      if (await verifySession(cookieValue(request, SESSION_COOKIE), env.SESSION_SECRET)) {
        return withRenewedSession(
          readerPage(await readCurrentRelease(env)),
          await createSession(env.SESSION_SECRET),
        );
      }
      return loginPage();
    }
    if (request.method === "POST" && url.pathname === "/login") {
      return handleLogin(request, env, url);
    }
    if (request.method === "GET" && url.pathname === "/api/publications") {
      if (!(await verifySession(cookieValue(request, SESSION_COOKIE), env.SESSION_SECRET))) {
        return unauthorized();
      }
      const current = await currentRelease(env);
      const headers = new Headers({
        "Cache-Control": "private, no-store",
        "Content-Type": "application/json; charset=utf-8",
        ETag: current.headers.get("ETag") ?? '"empty"',
        "X-Content-Type-Options": "nosniff",
      });
      if (weaklyMatchesEtag(request.headers.get("If-None-Match"), headers.get("ETag") ?? '"empty"')) {
        return new Response(null, { status: 304, headers });
      }
      return new Response(current.body, { status: current.status, headers });
    }
    const assetPath = /^\/assets\/([0-9a-f]{64})$/u.exec(url.pathname);
    if (request.method === "GET" && assetPath?.[1] !== undefined) {
      if (!(await verifySession(cookieValue(request, SESSION_COOKIE), env.SESSION_SECRET))) {
        return unauthorized();
      }
      const stub = env.PORTAL.getByName("private-portal-v1");
      const stored = await stub.fetch(`https://portal.internal/internal/assets/${assetPath[1]}`);
      if (!stored.ok) {
        return new Response("Not Found", {
          status: 404,
          headers: { "Cache-Control": "private, no-store" },
        });
      }
      const headers = new Headers({
        "Cache-Control": "private, max-age=31536000, immutable",
        "Content-Type": "image/png",
        ETag: stored.headers.get("ETag") ?? `"${assetPath[1]}"`,
        Vary: "Cookie",
        "X-Content-Type-Options": "nosniff",
      });
      return new Response(stored.body, { headers });
    }
    return new Response("Not Found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
