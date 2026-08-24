import { env, exports as workerExports } from "cloudflare:workers";
import { reset, runInDurableObject } from "cloudflare:test";
import { afterEach, describe, expect, test, vi } from "vitest";

import { VehicleAuthState } from "../src/vehicle-auth-state";

function base64UrlJson(value: unknown): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function accessToken(): string {
  return `${base64UrlJson({ alg: "RS256" })}.${base64UrlJson({
    exp: 2_000_000_000,
    scp: ["openid", "offline_access", "vehicle_device_data", "vehicle_location"],
  })}.test-signature`;
}

afterEach(async () => {
  vi.restoreAllMocks();
  await reset();
});

describe("deployed Worker composition", () => {
  test("completes OAuth and returns one sanitized read-only snapshot", async () => {
    const upstreamRequests: Request[] = [];
    const locationCapturedAtMs = Math.floor((Date.now() - 5_000) / 1_000) * 1_000;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const request = new Request(input, init);
      upstreamRequests.push(request);

      if (request.url.includes("/oauth2/v3/token")) {
        return Response.json({
          access_token: accessToken(),
          refresh_token: "integration-refresh-token",
        });
      }
      if (request.url.includes("/api/1/vehicles?")) {
        return Response.json({
          response: [
            {
              vin: "5YJ3E1EA7KF000001",
              display_name: "Gray Bullet",
              state: "online",
              access_type: "OWNER",
              in_service: false,
              tokens: ["provider-only-token"],
            },
          ],
        });
      }
      if (request.url.includes("/vehicle_data?")) {
        return Response.json({
          response: {
            charge_state: {
              battery_level: 78,
              battery_range: 218,
              charging_state: "Disconnected",
              charge_limit_soc: 80,
              charge_port_door_open: false,
              charger_power: 0,
            },
            climate_state: {
              inside_temp: 21.5,
              outside_temp: 18,
              is_climate_on: false,
            },
            closures_state: { df: 0, dr: 0, pf: 0, pr: 0, ft: 0, rt: 0 },
            vehicle_config: { car_type: "modely", trim_badging: "P74D" },
            vehicle_state: {
              locked: true,
              odometer: 12345.6,
              car_version: "2026.20.100 abc123",
            },
            drive_state: {
              gps_as_of: locationCapturedAtMs / 1_000,
              latitude: 37.501235,
              longitude: -122.001235,
              active_route_destination: "must not leave Tesla boundary",
            },
          },
        });
      }
      throw new Error("unexpected upstream request");
    });

    const origin = "https://epaper-vehicle-bridge.superxfy.workers.dev";
    const launchResponse = await workerExports.default.fetch(
      new Request(`${origin}/v1/oauth/launch`, {
        method: "POST",
        headers: { Authorization: "Bearer test-admin-token" },
      }),
    );
    expect(launchResponse.status).toBe(201);
    const launch = await launchResponse.json<{ authorization_url: string }>();
    const launchUrl = new URL(launch.authorization_url);
    expect(launchUrl.origin).toBe(origin);
    expect(launchUrl.pathname).toBe("/oauth/start");
    expect(launchUrl.searchParams.get("launch")).toMatch(
      /^[A-Za-z0-9_-]{43}$/,
    );

    const startResponse = await workerExports.default.fetch(
      new Request(launchUrl, { redirect: "manual" }),
    );
    expect(startResponse.status).toBe(303);
    const teslaAuthorize = new URL(startResponse.headers.get("Location")!);
    expect(teslaAuthorize.searchParams.get("scope")).toBe(
      "openid offline_access vehicle_device_data vehicle_location",
    );
    expect(teslaAuthorize.searchParams.has("code_challenge")).toBe(false);
    const browserCookie = startResponse.headers
      .get("Set-Cookie")!
      .split(";", 1)[0];

    const callback = new URL(`${origin}/oauth/callback`);
    callback.searchParams.set("code", "integration-code");
    callback.searchParams.set("state", teslaAuthorize.searchParams.get("state")!);
    const callbackResponse = await workerExports.default.fetch(
      new Request(callback, {
        headers: { Cookie: browserCookie },
        redirect: "manual",
      }),
    );
    expect(callbackResponse.status).toBe(303);
    expect(callbackResponse.headers.get("Location")).toBe(
      `${origin}/oauth/result?status=connected`,
    );

    const summaryResponse = await workerExports.default.fetch(
      new Request(`${origin}/api/vehicle-summary`, {
        headers: { Authorization: "Bearer test-read-token" },
      }),
    );
    expect(summaryResponse.status).toBe(200);
    const summary = await summaryResponse.json<Record<string, unknown>>();
    expect(summary).toMatchObject({
      schema_version: 1,
      snapshot: { freshness: "live", vehicle_connectivity: "online" },
      vehicle: {
        key: "primary",
        display_name: "Gray Bullet",
        model: "Model Y",
        trim: "Performance",
      },
      battery: { level_percent: 78 },
    });
    const serialized = JSON.stringify(summary);
    expect(serialized).not.toContain("5YJ3E1EA7KF000001");
    expect(serialized).not.toContain("latitude");
    expect(serialized).not.toContain("provider-only-token");
    expect(serialized).not.toContain("integration-refresh-token");

    const summaryV2Response = await workerExports.default.fetch(
      new Request(`${origin}/api/vehicle-summary?schema_version=2`, {
        headers: { Authorization: "Bearer test-read-token" },
      }),
    );
    expect(summaryV2Response.status).toBe(200);
    const summaryV2 = await summaryV2Response.json<Record<string, unknown>>();
    expect(Object.keys(summaryV2).sort()).toEqual([
      "battery",
      "charging",
      "climate",
      "closures",
      "preferences",
      "schema_version",
      "served_at",
      "snapshot",
      "software_update",
      "tires",
      "vehicle",
    ]);
    expect(summaryV2).toMatchObject({
      schema_version: 2,
      battery: {
        level_percent: 78,
        rated_range: { value: 218, unit: "mi" },
      },
      charging: { state: "disconnected", power_kw: 0 },
      vehicle: { display_name: "Gray Bullet" },
    });

    const summaryV3Response = await workerExports.default.fetch(
      new Request(`${origin}/api/vehicle-summary?schema_version=3`, {
        headers: { Authorization: "Bearer test-read-token" },
      }),
    );
    expect(summaryV3Response.status).toBe(200);
    const summaryV3 = await summaryV3Response.json<Record<string, unknown>>();
    expect(summaryV3).toMatchObject({
      schema_version: 3,
      location: {
        captured_at: new Date(locationCapturedAtMs).toISOString(),
        latitude: 37.501235,
        longitude: -122.001235,
      },
    });
    expect(JSON.stringify(summaryV3)).not.toContain("must not leave Tesla boundary");

    expect(upstreamRequests).toHaveLength(5);
    expect(upstreamRequests.some((request) => request.url.includes("wake_up"))).toBe(
      false,
    );
    const vehicleDataRequests = upstreamRequests.filter((request) =>
      request.url.includes("/vehicle_data?"),
    );
    expect(vehicleDataRequests).toHaveLength(2);
    expect(vehicleDataRequests[0].url).not.toContain("location_data");
    expect(vehicleDataRequests[1].url).toContain("location_data");
    expect(
      upstreamRequests.every((request) => request.redirect === "manual"),
    ).toBe(true);

    const stub = env.VEHICLE_AUTH.getByName("owner-v1");
    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const rows = state.storage.sql
          .exec<{ value: string }>("SELECT value FROM protected_state")
          .toArray();
        const stored = rows.map((row) => row.value).join("\n");
        expect(stored).not.toContain("integration-refresh-token");
        expect(stored).not.toContain(accessToken());
        expect(stored).not.toContain("37.501235");
      },
    );
  });
});
