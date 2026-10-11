import { deflateSync } from "node:zlib";

import { SELF } from "cloudflare:test";

export const encoder = new TextEncoder();
const PUBLISH_KEY = "test-publish-key-not-production-32-bytes";
const PNG_SIGNATURE = Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10);

let nonceSequence = 0;
let pngSequence = 0;

export async function browserSessionCookie(): Promise<string> {
  const response = await SELF.fetch("https://example.com/login", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Origin: "https://example.com",
    },
    body: new URLSearchParams({
      username: "admin",
      password: "test-browser-password-not-production",
    }),
    redirect: "manual",
  });
  if (response.status !== 303) throw new Error(`login failed: ${response.status}`);
  const cookie = response.headers.get("set-cookie")?.split(";", 1)[0];
  if (cookie === undefined) throw new Error("login response did not set a cookie");
  return cookie;
}

function nextNonce(): string {
  nonceSequence += 1;
  return nonceSequence.toString(16).padStart(32, "0");
}

function concatenate(...parts: Uint8Array[]): Uint8Array {
  const output = new Uint8Array(parts.reduce((length, part) => length + part.byteLength, 0));
  let offset = 0;
  for (const part of parts) {
    output.set(part, offset);
    offset += part.byteLength;
  }
  return output;
}

function uint32(value: number): Uint8Array {
  const bytes = new Uint8Array(4);
  new DataView(bytes.buffer).setUint32(0, value, false);
  return bytes;
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(name: "IHDR" | "IDAT" | "IEND", data: Uint8Array): Uint8Array {
  const type = encoder.encode(name);
  return concatenate(uint32(data.byteLength), type, data, uint32(crc32(concatenate(type, data))));
}

export function makePng(
  width = 800,
  height = 480,
  rgba?: readonly [number, number, number, number],
): Uint8Array {
  const fill = rgba ?? ([24, 79, (126 + ++pngSequence) % 256, 255] as const);
  const ihdr = new Uint8Array(13);
  const ihdrView = new DataView(ihdr.buffer);
  ihdrView.setUint32(0, width, false);
  ihdrView.setUint32(4, height, false);
  ihdr.set([8, 6, 0, 0, 0], 8);

  const stride = 1 + width * 4;
  const scanlines = new Uint8Array(stride * height);
  for (let y = 0; y < height; y += 1) {
    const row = y * stride;
    scanlines[row] = 0;
    for (let x = 0; x < width; x += 1) {
      scanlines.set(fill, row + 1 + x * 4);
    }
  }

  return concatenate(
    PNG_SIGNATURE,
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", new Uint8Array(deflateSync(scanlines))),
    pngChunk("IEND", new Uint8Array()),
  );
}

export async function sha256Hex(body: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", body);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function signedHeaders(
  method: "PUT",
  path: string,
  contentType: "image/png" | "application/json",
  body: Uint8Array,
  nonce = nextNonce(),
): Promise<Record<string, string>> {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const bodySha256 = await sha256Hex(body);
  const canonical = [
    "v1",
    method,
    path,
    contentType,
    String(body.byteLength),
    bodySha256,
    timestamp,
    nonce,
  ].join("\n");
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(PUBLISH_KEY),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(canonical));
  const signatureHex = [...new Uint8Array(signature)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");

  return {
    "Content-Length": String(body.byteLength),
    "Content-Type": contentType,
    "X-Epaper-Content-SHA256": bodySha256,
    "X-Epaper-Nonce": nonce,
    "X-Epaper-Signature": signatureHex,
    "X-Epaper-Timestamp": timestamp,
  };
}

export async function putSigned(
  path: string,
  contentType: "image/png" | "application/json",
  body: Uint8Array,
  options: { headers?: Record<string, string>; signedBody?: Uint8Array } = {},
): Promise<Response> {
  const headers =
    options.headers ?? (await signedHeaders("PUT", path, contentType, options.signedBody ?? body));
  return SELF.fetch(`https://example.com${path}`, {
    method: "PUT",
    headers,
    body,
  });
}

export async function putAsset(body: Uint8Array): Promise<Response> {
  return putSigned(`/api/v1/assets/${await sha256Hex(body)}`, "image/png", body);
}

export function editionBody(
  assetId: string,
  generation: number,
  overrides: {
    instanceId?: string;
    minimumRefreshIntervalSeconds?: number | null;
    publishedAt?: string;
  } = {},
): Uint8Array {
  const generatedAt = "2026-08-02T12:00:00.000Z";
  const minimumRefreshIntervalSeconds = overrides.minimumRefreshIntervalSeconds ?? 120;
  const legacy = overrides.minimumRefreshIntervalSeconds === null;
  const manifest: Record<string, unknown> = {
    schemaVersion: legacy ? 1 : 2,
    generation,
    publishedAt: overrides.publishedAt ?? "2026-08-02T12:01:00.000Z",
    timezone: "America/Los_Angeles",
    playlist: { slug: "home", title: "Home" },
    items: [
      {
        instanceId: overrides.instanceId ?? "weather-home",
        pluginId: "weather",
        title: "Weather",
        displayTitle: "当地天气",
        assetId,
        generatedAt,
        sourceUpdatedAt: generatedAt,
        width: 800,
        height: 480,
      },
    ],
  };
  if (!legacy) manifest.minimumRefreshIntervalSeconds = minimumRefreshIntervalSeconds;
  return encoder.encode(
    JSON.stringify(manifest),
  );
}

export async function putEdition(body: Uint8Array): Promise<Response> {
  return putSigned(`/api/v1/editions/${await sha256Hex(body)}`, "application/json", body);
}

// Uploads one picture per instance and commits them as a single playlist edition.
export async function publishPlaylist(generation: number, instanceIds: readonly string[]): Promise<void> {
  const generatedAt = "2026-08-02T12:00:00.000Z";
  const items = [];
  for (const instanceId of instanceIds) {
    const asset = makePng();
    const uploaded = await putAsset(asset);
    if (uploaded.status !== 201) throw new Error(`asset upload failed: ${uploaded.status}`);
    items.push({
      instanceId,
      pluginId: "clock",
      title: `Clock ${instanceId}`,
      displayTitle: `时钟 ${instanceId}`,
      assetId: await sha256Hex(asset),
      generatedAt,
      sourceUpdatedAt: generatedAt,
      width: 800,
      height: 480,
    });
  }
  const committed = await putEdition(encoder.encode(JSON.stringify({
    schemaVersion: 2,
    generation,
    publishedAt: "2026-08-02T12:01:00.000Z",
    timezone: "America/Los_Angeles",
    playlist: { slug: "home", title: "Home" },
    minimumRefreshIntervalSeconds: 120,
    items,
  })));
  if (committed.status !== 201) throw new Error(`edition commit failed: ${committed.status}`);
}
