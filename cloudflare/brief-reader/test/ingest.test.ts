import { env, runInDurableObject, SELF } from "cloudflare:test";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  browserSessionCookie,
  editionBody,
  encoder,
  makePng,
  putAsset,
  putEdition,
  putSigned,
  sha256Hex,
  signedHeaders,
} from "./support";

afterEach(() => {
  vi.useRealTimers();
});

describe("signed asset ingestion", () => {
  it("accepts an HMAC-authenticated 800x480 PNG", async () => {
    const response = await putAsset(makePng());

    expect(response.status).toBe(201);
  });

  it("rejects a body changed after it was signed", async () => {
    const original = makePng();
    const tampered = original.slice();
    const tamperedIndex = tampered.byteLength - 6;
    tampered[tamperedIndex] = (tampered[tamperedIndex] ?? 0) ^ 1;
    const path = `/api/v1/assets/${await sha256Hex(original)}`;

    const response = await putSigned(path, "image/png", tampered, { signedBody: original });

    expect(response.status).toBe(401);
  });

  it("rejects reuse of an authenticated nonce", async () => {
    const body = makePng();
    const path = `/api/v1/assets/${await sha256Hex(body)}`;
    const headers = await signedHeaders("PUT", path, "image/png", body, "a".repeat(32));

    const first = await putSigned(path, "image/png", body, { headers });
    const replay = await putSigned(path, "image/png", body, { headers });

    expect(first.status).toBe(201);
    expect(replay.status).toBe(409);
  });

  it("forgets a nonce after its signature can no longer be replayed", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-03T12:00:00.000Z"));
    const body = makePng();
    const path = `/api/v1/assets/${await sha256Hex(body)}`;
    const reusableNonce = "b".repeat(32);
    const originalHeaders = await signedHeaders("PUT", path, "image/png", body, reusableNonce);

    const first = await putSigned(path, "image/png", body, {
      headers: originalHeaders,
    });
    vi.setSystemTime(new Date("2026-08-03T12:05:00.000Z"));
    const boundaryReplay = await putSigned(path, "image/png", body, { headers: originalHeaders });
    vi.setSystemTime(new Date("2026-08-03T12:05:01.000Z"));
    const expiredReplay = await putSigned(path, "image/png", body, { headers: originalHeaders });
    vi.setSystemTime(new Date("2026-08-03T12:10:01.000Z"));
    const cleanup = await putSigned(path, "image/png", body);
    const reused = await putSigned(path, "image/png", body, {
      headers: await signedHeaders("PUT", path, "image/png", body, reusableNonce),
    });

    expect(first.status).toBe(201);
    expect(boundaryReplay.status).toBe(409);
    expect(expiredReplay.status).toBe(401);
    expect(cleanup.status).toBe(200);
    expect(reused.status).toBe(200);
  });

  it("retains a nonce when a maximum-future-skew request performs cleanup", async () => {
    vi.useFakeTimers();
    const originalTime = new Date("2026-08-03T12:00:00.000Z");
    vi.setSystemTime(originalTime);
    const body = makePng();
    const path = `/api/v1/assets/${await sha256Hex(body)}`;
    const originalHeaders = await signedHeaders("PUT", path, "image/png", body, "c".repeat(32));
    expect((await putSigned(path, "image/png", body, { headers: originalHeaders })).status).toBe(201);

    vi.setSystemTime(new Date(originalTime.getTime() + 600_000));
    const futureBoundaryHeaders = await signedHeaders("PUT", path, "image/png", body);
    vi.setSystemTime(new Date(originalTime.getTime() + 300_000));
    const cleanup = await putSigned(path, "image/png", body, { headers: futureBoundaryHeaders });
    const replay = await putSigned(path, "image/png", body, { headers: originalHeaders });

    expect(cleanup.status).toBe(200);
    expect(replay.status).toBe(409);
  });

  it("uses the timestamp index when pruning expired nonces", async () => {
    expect((await putAsset(makePng())).status).toBe(201);
    const stub = env.PORTAL.getByName("private-portal-v1");

    const details = await runInDurableObject(stub, (_instance, state) =>
      state.storage.sql
        .exec<{ detail: string }>(
          "EXPLAIN QUERY PLAN DELETE FROM used_nonces WHERE timestamp < ?",
          Math.floor(Date.now() / 1000),
        )
        .toArray()
        .map((row) => row.detail),
    );

    expect(details.some((detail) => detail.includes("used_nonces_by_timestamp"))).toBe(true);
    expect(details.every((detail) => !detail.includes("SCAN used_nonces"))).toBe(true);
  });


  it("rejects a validly signed PNG with the wrong dimensions", async () => {
    const response = await putAsset(makePng(799, 480));

    expect(response.status).toBe(422);
  });

  it("rejects a validly signed body whose PNG magic signature is invalid", async () => {
    const body = makePng();
    body[0] = 0;

    const response = await putAsset(body);

    expect(response.status).toBe(422);
  });

  it("rejects an invalid HMAC signature", async () => {
    const body = makePng();
    const path = `/api/v1/assets/${await sha256Hex(body)}`;
    const headers = await signedHeaders("PUT", path, "image/png", body);
    headers["X-Epaper-Signature"] = "0".repeat(64);

    const response = await putSigned(path, "image/png", body, { headers });

    expect(response.status).toBe(401);
  });
});

describe("atomic edition ingestion", () => {
  it("enforces the exact v1 and v2 refresh metadata shapes", async () => {
    const asset = makePng();
    const assetId = await sha256Hex(asset);
    expect((await putAsset(asset)).status).toBe(201);
    const v2WithoutMetadata = JSON.parse(
      new TextDecoder().decode(editionBody(assetId, 2)),
    ) as Record<string, unknown>;
    delete v2WithoutMetadata.minimumRefreshIntervalSeconds;
    const v1WithMetadata = JSON.parse(
      new TextDecoder().decode(editionBody(assetId, 3)),
    ) as Record<string, unknown>;
    v1WithMetadata.schemaVersion = 1;

    expect((await putEdition(encoder.encode(JSON.stringify(v2WithoutMetadata)))).status).toBe(422);
    expect((await putEdition(encoder.encode(JSON.stringify(v1WithMetadata)))).status).toBe(422);
  });

  it("does not advance the current generation when an edition references a missing asset", async () => {
    const missingAsset = "f".repeat(64);
    const rejected = await putEdition(editionBody(missingAsset, 5));
    expect(rejected.status).toBe(422);

    const asset = makePng();
    expect((await putAsset(asset)).status).toBe(201);
    const lowerGeneration = await putEdition(editionBody(await sha256Hex(asset), 4));

    expect(lowerGeneration.status).toBe(201);
  });

  it("commits a complete edition and treats the same immutable edition as idempotent", async () => {
    const asset = makePng();
    expect((await putAsset(asset)).status).toBe(201);
    const body = editionBody(await sha256Hex(asset), 20);

    const committed = await putEdition(body);
    const duplicate = await putEdition(body);

    expect(committed.status).toBe(201);
    expect(duplicate.status).toBe(200);
  });

  it("renders the committed original frame through the authenticated Model Y reader", async () => {
    const asset = makePng();
    const assetId = await sha256Hex(asset);
    expect((await putAsset(asset)).status).toBe(201);
    expect((await putEdition(editionBody(assetId, 25))).status).toBe(201);
    const cookie = await browserSessionCookie();

    const catalog = await SELF.fetch("https://example.com/api/publications", {
      headers: { Cookie: cookie },
    });
    expect(catalog.status).toBe(200);
    const catalogBody = (await catalog.json()) as { release: { generation: number } };
    expect(catalogBody.release.generation).toBe(25);

    const page = await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } });
    const html = await page.text();
    expect(html).toContain("当地天气");
    expect(html).toContain("当地时间");
    expect(html).toContain(`/assets/${assetId}`);
    expect(html).toContain("data-slide");
    expect(html).toContain('data-instance-id="weather-home"');
    expect(html).toContain('data-playlist-id="home"');
    expect(html).toContain('data-generation="25"');
    expect(html).toContain('data-poll-ms="30000"');
    // Every control, including the reveal button, sits in the bottom dock.
    const dock = html.slice(html.indexOf("data-play-controls"));
    for (const control of ["data-previous", "data-pause", "data-next", "data-fullscreen", "data-play-chrome-toggle"]) {
      expect(dock).toContain(control);
    }
    expect(html).not.toContain("play-progress"); // one slide has nothing to count down
    expect(html).not.toMatch(/\sstyle=/u); // CSP allows only the external stylesheet

    expect((await SELF.fetch(`https://example.com/assets/${assetId}`)).status).toBe(401);
    const frame = await SELF.fetch(`https://example.com/assets/${assetId}`, {
      headers: { Cookie: cookie },
    });
    expect(frame.status).toBe(200);
    expect(frame.headers.get("content-type")).toBe("image/png");
    expect(new Uint8Array(await frame.arrayBuffer())).toEqual(asset);
  });

  it("keeps legacy v1 editions readable with the derived polling fallback", async () => {
    const asset = makePng();
    const assetId = await sha256Hex(asset);
    expect((await putAsset(asset)).status).toBe(201);
    expect((await putEdition(editionBody(assetId, 26, {
      minimumRefreshIntervalSeconds: null,
    }))).status).toBe(201);
    const cookie = await browserSessionCookie();

    const page = await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } });
    const html = await page.text();

    expect(html).toContain('data-poll-ms="30000"');
  });

  it("bounds the derived polling delay between fifteen and sixty seconds", async () => {
    const asset = makePng();
    const assetId = await sha256Hex(asset);
    expect((await putAsset(asset)).status).toBe(201);
    expect((await putEdition(editionBody(assetId, 27, {
      minimumRefreshIntervalSeconds: 30,
    }))).status).toBe(201);
    const cookie = await browserSessionCookie();

    const fastPage = await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } });
    expect(await fastPage.text()).toContain('data-poll-ms="15000"');

    expect((await putEdition(editionBody(assetId, 28, {
      minimumRefreshIntervalSeconds: 604800,
    }))).status).toBe(201);
    const slowPage = await SELF.fetch("https://example.com/", { headers: { Cookie: cookie } });
    expect(await slowPage.text()).toContain('data-poll-ms="60000"');
  });

  it("rejects an older generation without rolling the current edition back", async () => {
    const newerAsset = makePng(800, 480, [24, 79, 126, 255]);
    const olderAsset = makePng(800, 480, [196, 89, 17, 255]);
    expect((await putAsset(newerAsset)).status).toBe(201);
    expect((await putAsset(olderAsset)).status).toBe(201);

    const newer = editionBody(await sha256Hex(newerAsset), 30);
    expect((await putEdition(newer)).status).toBe(201);

    const cookie = await browserSessionCookie();
    const orphaned = await SELF.fetch(
      `https://example.com/assets/${await sha256Hex(olderAsset)}`,
      { headers: { Cookie: cookie } },
    );
    expect(orphaned.status).toBe(404);

    const stale = editionBody(await sha256Hex(olderAsset), 29);
    expect((await putEdition(stale)).status).toBe(409);

    const conflictingCurrentGeneration = editionBody(await sha256Hex(olderAsset), 30, {
      instanceId: "weather-conflict",
      publishedAt: "2026-08-02T12:02:00.000Z",
    });
    expect((await putEdition(conflictingCurrentGeneration)).status).toBe(409);
  });
});
