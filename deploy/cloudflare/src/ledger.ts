import { DurableObject } from "cloudflare:workers";

const ASSET_LIMIT_BYTES = 1_000_000;
const EDITION_LIMIT_BYTES = 128 * 1024;
const SIGNATURE_WINDOW_SECONDS = 300;
const NONCE_RETENTION_SECONDS = SIGNATURE_WINDOW_SECONDS * 2;
const MINIMUM_REFRESH_INTERVAL_SECONDS = 30;
const MAXIMUM_REFRESH_INTERVAL_SECONDS = 7 * 24 * 60 * 60;
const SHA256_PATTERN = /^[0-9a-f]{64}$/u;
const NONCE_PATTERN = /^[A-Za-z0-9_-]{16,64}$/u;
const PNG_SIGNATURE = Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10);
const encoder = new TextEncoder();

interface ReleaseItem {
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

interface ReleaseManifest {
  schemaVersion: 1 | 2;
  generation: number;
  publishedAt: string;
  timezone: string;
  playlist: { slug: string; title: string };
  minimumRefreshIntervalSeconds?: number;
  items: ReleaseItem[];
}

function apiResponse(status: number, code: string): Response {
  return Response.json(
    { error: code },
    {
      status,
      headers: {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
      },
    },
  );
}

function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(value: string): Uint8Array | null {
  if (!SHA256_PATTERN.test(value)) return null;
  const bytes = new Uint8Array(32);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  return bytesToHex(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)));
}

async function readBoundedBytes(request: Request, limit: number): Promise<Uint8Array | null> {
  if (request.body === null) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  while (true) {
    const result = await reader.read();
    if (result.done) break;
    length += result.value.byteLength;
    if (length > limit) {
      await reader.cancel();
      return null;
    }
    chunks.push(result.value);
  }
  const bytes = new Uint8Array(length);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return bytes;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function isBoundedText(value: unknown, maximum: number, allowEmpty = false): value is string {
  return (
    typeof value === "string" &&
    value.length <= maximum &&
    (allowEmpty || value.length > 0) &&
    !/[\u0000-\u001f\u007f]/u.test(value)
  );
}

function isIdentifier(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9._:-]{1,128}$/u.test(value);
}

function isIsoTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length >= 20 &&
    value.length <= 40 &&
    value.includes("T") &&
    Number.isFinite(Date.parse(value))
  );
}

function parseManifest(bytes: Uint8Array): ReleaseManifest | null {
  let parsed: unknown;
  try {
    const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
    parsed = JSON.parse(text);
  } catch (_error) {
    return null;
  }
  if (!isRecord(parsed)) return null;
  const schemaVersion = parsed.schemaVersion;
  if (schemaVersion !== 1 && schemaVersion !== 2) return null;
  const manifestKeys = [
    "schemaVersion",
    "generation",
    "publishedAt",
    "timezone",
    "playlist",
    ...(schemaVersion === 2 ? ["minimumRefreshIntervalSeconds"] : []),
    "items",
  ];
  if (
    !hasExactKeys(parsed, manifestKeys) ||
    !Number.isSafeInteger(parsed.generation) ||
    (parsed.generation as number) <= 0 ||
    !isIsoTimestamp(parsed.publishedAt) ||
    typeof parsed.timezone !== "string" ||
    !/^[A-Za-z0-9_+/-]{1,64}$/u.test(parsed.timezone) ||
    !isRecord(parsed.playlist) ||
    !hasExactKeys(parsed.playlist, ["slug", "title"]) ||
    !isIdentifier(parsed.playlist.slug) ||
    !isBoundedText(parsed.playlist.title, 160) ||
    (schemaVersion === 2 &&
      (!Number.isSafeInteger(parsed.minimumRefreshIntervalSeconds) ||
        (parsed.minimumRefreshIntervalSeconds as number) < MINIMUM_REFRESH_INTERVAL_SECONDS ||
        (parsed.minimumRefreshIntervalSeconds as number) > MAXIMUM_REFRESH_INTERVAL_SECONDS)) ||
    !Array.isArray(parsed.items) ||
    parsed.items.length < 1 ||
    parsed.items.length > 100
  ) {
    return null;
  }

  const instanceIds = new Set<string>();
  const releaseItems: ReleaseItem[] = [];
  for (const candidate of parsed.items) {
    if (
      !isRecord(candidate) ||
      !hasExactKeys(candidate, [
        "instanceId",
        "pluginId",
        "title",
        "displayTitle",
        "assetId",
        "generatedAt",
        "sourceUpdatedAt",
        "width",
        "height",
      ]) ||
      !isIdentifier(candidate.instanceId) ||
      !isIdentifier(candidate.pluginId) ||
      !isBoundedText(candidate.title, 160) ||
      !isBoundedText(candidate.displayTitle, 160) ||
      !SHA256_PATTERN.test(typeof candidate.assetId === "string" ? candidate.assetId : "") ||
      !isIsoTimestamp(candidate.generatedAt) ||
      !isIsoTimestamp(candidate.sourceUpdatedAt) ||
      candidate.width !== 800 ||
      candidate.height !== 480 ||
      (candidate.pluginId === "weather" && candidate.displayTitle !== "当地天气") ||
      instanceIds.has(candidate.instanceId)
    ) {
      return null;
    }
    instanceIds.add(candidate.instanceId);
    releaseItems.push({
      instanceId: candidate.instanceId,
      pluginId: candidate.pluginId,
      title: candidate.title,
      displayTitle: candidate.displayTitle,
      assetId: candidate.assetId as string,
      generatedAt: candidate.generatedAt,
      sourceUpdatedAt: candidate.sourceUpdatedAt,
      width: 800,
      height: 480,
    });
  }
  return {
    schemaVersion,
    generation: parsed.generation as number,
    publishedAt: parsed.publishedAt,
    timezone: parsed.timezone,
    playlist: {
      slug: parsed.playlist.slug,
      title: parsed.playlist.title,
    },
    ...(schemaVersion === 2
      ? { minimumRefreshIntervalSeconds: parsed.minimumRefreshIntervalSeconds as number }
      : {}),
    items: releaseItems,
  };
}

function pngDimensions(bytes: Uint8Array): { width: number; height: number } | null {
  if (bytes.byteLength < 33) return null;
  for (let index = 0; index < PNG_SIGNATURE.length; index += 1) {
    if (bytes[index] !== PNG_SIGNATURE[index]) return null;
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  if (
    view.getUint32(8, false) !== 13 ||
    bytes[12] !== 73 ||
    bytes[13] !== 72 ||
    bytes[14] !== 68 ||
    bytes[15] !== 82
  ) {
    return null;
  }
  return { width: view.getUint32(16, false), height: view.getUint32(20, false) };
}

export class PortalLedger extends DurableObject<Env> {
  constructor(context: DurableObjectState, env: Env) {
    super(context, env);
    context.blockConcurrencyWhile(async () => {
      context.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS assets (
          asset_id TEXT PRIMARY KEY,
          media_type TEXT NOT NULL,
          body BLOB NOT NULL,
          size INTEGER NOT NULL,
          width INTEGER NOT NULL,
          height INTEGER NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS editions (
          edition_id TEXT PRIMARY KEY,
          generation INTEGER NOT NULL UNIQUE,
          manifest TEXT NOT NULL,
          created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS current_release (
          singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
          edition_id TEXT NOT NULL,
          generation INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS used_nonces (
          nonce TEXT PRIMARY KEY,
          timestamp INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS used_nonces_by_timestamp ON used_nonces(timestamp);
      `);
    });
  }

  private consumeNonce(nonce: string, timestamp: number): boolean {
    return this.ctx.storage.transactionSync(() => {
      this.ctx.storage.sql.exec(
        "DELETE FROM used_nonces WHERE timestamp < ?",
        timestamp - NONCE_RETENTION_SECONDS,
      );
      const existing = this.ctx.storage.sql
        .exec<{ nonce: string }>("SELECT nonce FROM used_nonces WHERE nonce = ?", nonce)
        .toArray()[0];
      if (existing !== undefined) return false;
      this.ctx.storage.sql.exec(
        "INSERT INTO used_nonces (nonce, timestamp) VALUES (?, ?)",
        nonce,
        timestamp,
      );
      return true;
    });
  }

  private async authenticate(
    request: Request,
    maximumBytes: number,
  ): Promise<{ bytes: Uint8Array } | Response> {
    const url = new URL(request.url);
    if (url.search !== "") return apiResponse(400, "query_not_allowed");

    const contentType = request.headers.get("Content-Type")?.trim().toLowerCase() ?? "";
    const declaredLengthText = request.headers.get("Content-Length") ?? "";
    const bodyDigestHeader = request.headers.get("X-Epaper-Content-SHA256") ?? "";
    const timestampText = request.headers.get("X-Epaper-Timestamp") ?? "";
    const nonce = request.headers.get("X-Epaper-Nonce") ?? "";
    const signatureHex = request.headers.get("X-Epaper-Signature") ?? "";
    if (
      !/^\d{1,9}$/u.test(declaredLengthText) ||
      !SHA256_PATTERN.test(bodyDigestHeader) ||
      !/^\d{10}$/u.test(timestampText) ||
      !NONCE_PATTERN.test(nonce) ||
      !SHA256_PATTERN.test(signatureHex)
    ) {
      return apiResponse(401, "invalid_signature");
    }

    const declaredLength = Number(declaredLengthText);
    if (declaredLength > maximumBytes) return apiResponse(413, "body_too_large");
    const timestamp = Number(timestampText);
    const now = Math.floor(Date.now() / 1000);
    if (Math.abs(now - timestamp) > SIGNATURE_WINDOW_SECONDS) {
      return apiResponse(401, "expired_signature");
    }

    const canonical = [
      "v1",
      request.method,
      url.pathname,
      contentType,
      declaredLengthText,
      bodyDigestHeader,
      timestampText,
      nonce,
    ].join("\n");
    const signature = hexToBytes(signatureHex);
    if (signature === null) return apiResponse(401, "invalid_signature");
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(this.env.PUBLISH_KEY),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["verify"],
    );
    const valid = await crypto.subtle.verify("HMAC", key, signature, encoder.encode(canonical));
    if (!valid) return apiResponse(401, "invalid_signature");

    const bytes = await readBoundedBytes(request, maximumBytes);
    if (bytes === null) return apiResponse(413, "body_too_large");
    if (bytes.byteLength !== declaredLength || (await sha256Hex(bytes)) !== bodyDigestHeader) {
      return apiResponse(401, "invalid_signature");
    }
    if (!this.consumeNonce(nonce, timestamp)) return apiResponse(409, "replay_detected");
    return { bytes };
  }

  private async putAsset(request: Request, assetId: string): Promise<Response> {
    if (request.headers.get("Content-Type")?.trim().toLowerCase() !== "image/png") {
      return apiResponse(415, "unsupported_media_type");
    }
    const authenticated = await this.authenticate(request, ASSET_LIMIT_BYTES);
    if (authenticated instanceof Response) return authenticated;
    if ((await sha256Hex(authenticated.bytes)) !== assetId) return apiResponse(422, "asset_id_mismatch");
    const dimensions = pngDimensions(authenticated.bytes);
    if (dimensions === null || dimensions.width !== 800 || dimensions.height !== 480) {
      return apiResponse(422, "invalid_frame");
    }

    const inserted = this.ctx.storage.transactionSync(() => {
      const existing = this.ctx.storage.sql
        .exec<{ asset_id: string }>("SELECT asset_id FROM assets WHERE asset_id = ?", assetId)
        .toArray()[0];
      if (existing !== undefined) return false;
      const body = authenticated.bytes.slice().buffer;
      this.ctx.storage.sql.exec(
        `INSERT INTO assets
          (asset_id, media_type, body, size, width, height, created_at)
          VALUES (?, 'image/png', ?, ?, 800, 480, ?)`,
        assetId,
        body,
        authenticated.bytes.byteLength,
        Math.floor(Date.now() / 1000),
      );
      return true;
    });
    return Response.json(
      { assetId },
      { status: inserted ? 201 : 200, headers: { "Cache-Control": "no-store" } },
    );
  }

  private async putEdition(request: Request, editionId: string): Promise<Response> {
    if (request.headers.get("Content-Type")?.trim().toLowerCase() !== "application/json") {
      return apiResponse(415, "unsupported_media_type");
    }
    const authenticated = await this.authenticate(request, EDITION_LIMIT_BYTES);
    if (authenticated instanceof Response) return authenticated;
    if ((await sha256Hex(authenticated.bytes)) !== editionId) {
      return apiResponse(422, "edition_id_mismatch");
    }
    const manifest = parseManifest(authenticated.bytes);
    if (manifest === null) return apiResponse(422, "invalid_manifest");
    const manifestText = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(
      authenticated.bytes,
    );

    const result = this.ctx.storage.transactionSync(() => {
      const existingEdition = this.ctx.storage.sql
        .exec<{ generation: number; manifest: string }>(
          "SELECT generation, manifest FROM editions WHERE edition_id = ?",
          editionId,
        )
        .toArray()[0];
      if (existingEdition !== undefined) {
        return existingEdition.generation === manifest.generation && existingEdition.manifest === manifestText
          ? { status: 200, code: "idempotent" }
          : { status: 409, code: "edition_conflict" };
      }

      const current = this.ctx.storage.sql
        .exec<{ edition_id: string; generation: number }>(
          "SELECT edition_id, generation FROM current_release WHERE singleton = 1",
        )
        .toArray()[0];
      if (current !== undefined && manifest.generation <= current.generation) {
        return { status: 409, code: "generation_conflict" };
      }
      const generationOwner = this.ctx.storage.sql
        .exec<{ edition_id: string }>(
          "SELECT edition_id FROM editions WHERE generation = ?",
          manifest.generation,
        )
        .toArray()[0];
      if (generationOwner !== undefined) return { status: 409, code: "generation_conflict" };

      for (const item of manifest.items) {
        const asset = this.ctx.storage.sql
          .exec<{ size: number; width: number; height: number }>(
            "SELECT size, width, height FROM assets WHERE asset_id = ?",
            item.assetId,
          )
          .toArray()[0];
        if (asset === undefined || asset.width !== 800 || asset.height !== 480 || asset.size <= 0) {
          return { status: 422, code: "missing_asset" };
        }
      }

      this.ctx.storage.sql.exec(
        "INSERT INTO editions (edition_id, generation, manifest, created_at) VALUES (?, ?, ?, ?)",
        editionId,
        manifest.generation,
        manifestText,
        Math.floor(Date.now() / 1000),
      );
      this.ctx.storage.sql.exec(
        `INSERT INTO current_release (singleton, edition_id, generation)
         VALUES (1, ?, ?)
         ON CONFLICT(singleton) DO UPDATE SET edition_id = excluded.edition_id, generation = excluded.generation`,
        editionId,
        manifest.generation,
      );
      this.ctx.storage.sql.exec("DELETE FROM editions WHERE edition_id <> ?", editionId);
      const retainedAssetIds = [...new Set(manifest.items.map((item) => item.assetId))];
      const placeholders = retainedAssetIds.map(() => "?").join(", ");
      this.ctx.storage.sql.exec(
        `DELETE FROM assets WHERE asset_id NOT IN (${placeholders})`,
        ...retainedAssetIds,
      );
      return { status: 201, code: "committed" };
    });

    if (result.status >= 400) return apiResponse(result.status, result.code);
    return Response.json(
      { editionId, generation: manifest.generation },
      { status: result.status, headers: { "Cache-Control": "no-store" } },
    );
  }

  private current(): Response {
    const current = this.ctx.storage.sql.exec<{ edition_id: string; manifest: string }>(`
      SELECT editions.edition_id, editions.manifest
      FROM current_release
      JOIN editions ON editions.edition_id = current_release.edition_id
      WHERE current_release.singleton = 1
    `).toArray()[0];
    if (current === undefined) {
      return Response.json({ release: null }, { headers: { ETag: '"empty"' } });
    }
    return new Response(`{"release":${current.manifest}}`, {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        ETag: `"${current.edition_id}"`,
      },
    });
  }

  private asset(assetId: string): Response {
    const asset = this.ctx.storage.sql
      .exec<{ body: ArrayBuffer; size: number }>(
        "SELECT body, size FROM assets WHERE asset_id = ?",
        assetId,
      )
      .toArray()[0];
    if (asset === undefined) return new Response("Not Found", { status: 404 });
    return new Response(asset.body, {
      headers: {
        "Content-Length": String(asset.size),
        "Content-Type": "image/png",
        ETag: `"${assetId}"`,
      },
    });
  }

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/internal/current") return this.current();
    const internalAsset = /^\/internal\/assets\/([0-9a-f]{64})$/u.exec(url.pathname);
    if (request.method === "GET" && internalAsset?.[1] !== undefined) {
      return this.asset(internalAsset[1]);
    }
    const assetUpload = /^\/api\/v1\/assets\/([0-9a-f]{64})$/u.exec(url.pathname);
    if (request.method === "PUT" && assetUpload?.[1] !== undefined) {
      return this.putAsset(request, assetUpload[1]);
    }
    const editionUpload = /^\/api\/v1\/editions\/([0-9a-f]{64})$/u.exec(url.pathname);
    if (request.method === "PUT" && editionUpload?.[1] !== undefined) {
      return this.putEdition(request, editionUpload[1]);
    }
    return new Response("Not Found", { status: 404 });
  }
}
