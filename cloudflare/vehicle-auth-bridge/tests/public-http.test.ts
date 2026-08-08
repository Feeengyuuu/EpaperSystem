import { describe, expect, test, vi } from "vitest";

import type {
  OAuthCompletion,
  VehicleSummary,
  VehicleSummaryResult,
  VehicleSummaryV2,
} from "../src/contracts";
import { createWorker, type VehicleAuthSession } from "../src/worker";

const PUBLIC_KEY = [
  "-----BEGIN PUBLIC KEY-----",
  "TEST-PUBLIC-KEY",
  "-----END PUBLIC KEY-----",
  "",
].join("\n");

const ADMIN_TOKEN = "test-admin-token";
const READ_TOKEN = "test-read-token";
const TESLA_AUDIENCE = "https://fleet-api.prd.na.vn.cloud.tesla.com";

const SUMMARY: VehicleSummary = {
  schema_version: 1,
  served_at: "2026-08-05T20:30:00.000Z",
  snapshot: {
    captured_at: "2026-08-05T20:28:30.000Z",
    freshness: "fresh_cache",
    age_seconds: 90,
    vehicle_connectivity: "online",
  },
  vehicle: {
    key: "primary",
    display_name: "Gray Bullet",
    model: "Model Y",
    trim: "Performance",
    locked: true,
    software_version: "2026.20.100",
    odometer: { value: 12345.6, unit: "mi" },
  },
  battery: {
    level_percent: 78,
    estimated_range: { value: 218, unit: "mi" },
    charging_state: "Disconnected",
    charge_limit_percent: 80,
    time_to_full_minutes: null,
    power_kw: 0,
  },
  climate: {
    inside_temp_c: 21.5,
    outside_temp_c: 18,
    is_climate_on: false,
  },
  closures: {
    all_closed: true,
    open: [],
    charge_port_open: false,
  },
};

const SUMMARY_V2: VehicleSummaryV2 = {
  schema_version: 2,
  served_at: "2026-08-05T20:30:00.000Z",
  snapshot: {
    captured_at: "2026-08-05T20:28:30.000Z",
    freshness: "fresh_cache",
    age_seconds: 90,
    vehicle_connectivity: "online",
  },
  vehicle: {
    key: "primary",
    display_name: "Gray Bullet",
    model: "Model Y",
    trim: "Performance",
    locked: true,
    software_version: "2026.20.100",
    odometer: { value: 12345.6, unit: "mi" },
    exterior_color: null,
    wheel_type: null,
    roof_color: null,
    charge_port_type: null,
    efficiency_package: null,
    rear_seat_heaters: null,
    right_hand_drive: null,
    europe_vehicle: null,
    sunroof_installed: null,
    sentry_mode: null,
    service_mode: null,
    valet_mode: null,
    center_display_state: null,
    speed_limit_mode: {
      active: null,
      limit: null,
    },
  },
  battery: {
    level_percent: 78,
    usable_level_percent: null,
    rated_range: { value: 218, unit: "mi" },
    estimated_range: null,
  },
  charging: {
    state: "disconnected",
    charge_limit_percent: 80,
    time_to_full_minutes: null,
    power_kw: 0,
    energy_added_kwh: null,
    rate: null,
    actual_current_a: null,
    voltage_v: null,
    phases: null,
    requested_current_a: null,
    max_current_a: null,
    enabled: null,
    cable_type: null,
    fast_charger_present: null,
    fast_charger_type: null,
    port_latch: null,
    port_cold_weather_mode: null,
    preconditioning: null,
    not_enough_power_to_heat: null,
    supercharger_trip_planner: null,
    scheduled: {
      pending: null,
      mode: null,
    },
  },
  climate: {
    inside_temp_c: 21.5,
    outside_temp_c: 18,
    is_climate_on: false,
    driver_target_temp_c: null,
    passenger_target_temp_c: null,
    keeper_mode: null,
    defrost_mode: null,
    rear_defroster_on: null,
    battery_heater_on: null,
    wiper_heater_on: null,
    hvac_auto_mode: null,
    fan_status: null,
    steering_wheel_heat_level: null,
    steering_wheel_heat_auto: null,
    seat_heaters: {
      front_left: null,
      front_right: null,
      rear_left: null,
      rear_right: null,
      rear_center: null,
    },
    seat_cooling: {
      front_left: null,
      front_right: null,
    },
    auto_seat_climate: {
      front_left: null,
      front_right: null,
    },
    cabin_overheat: {
      mode: null,
      temp_limit: null,
    },
  },
  closures: {
    all_closed: true,
    open: [],
    charge_port_open: false,
    doors: {
      driver_front: null,
      driver_rear: null,
      passenger_front: null,
      passenger_rear: null,
      front_trunk: null,
      rear_trunk: null,
    },
    windows: {
      driver_front: null,
      driver_rear: null,
      passenger_front: null,
      passenger_rear: null,
    },
  },
  tires: {
    pressures: {
      front_left: null,
      front_right: null,
      rear_left: null,
      rear_right: null,
    },
    soft_warnings: {
      front_left: null,
      front_right: null,
      rear_left: null,
      rear_right: null,
    },
    hard_warnings: {
      front_left: null,
      front_right: null,
      rear_left: null,
      rear_right: null,
    },
  },
  software_update: {
    version: null,
    download_percent: null,
    install_percent: null,
    expected_duration_minutes: null,
  },
  preferences: {
    distance_unit: null,
    temperature_unit: null,
    pressure_unit: null,
    charge_display_unit: null,
    use_24_hour_time: null,
  },
};

function createSession(
  summaryResult: VehicleSummaryResult = { ok: true, summary: SUMMARY },
): VehicleAuthSession & {
  createOAuthLaunch: ReturnType<typeof vi.fn>;
  beginAuthorization: ReturnType<typeof vi.fn>;
  completeAuthorization: ReturnType<typeof vi.fn>;
  getVehicleSummary: ReturnType<typeof vi.fn>;
} {
  return {
    createOAuthLaunch: vi.fn(async () => undefined),
    beginAuthorization: vi.fn(async () => true),
    completeAuthorization: vi.fn(
      async (): Promise<OAuthCompletion> => ({ ok: true }),
    ),
    getVehicleSummary: vi.fn(async () => summaryResult),
  };
}

function workerFor(session: VehicleAuthSession = createSession()) {
  return createWorker({
    publicKeyPem: PUBLIC_KEY,
    bridgeAdminToken: ADMIN_TOKEN,
    bridgeReadToken: READ_TOKEN,
    teslaClientId: "test-client-id",
    teslaAudience: TESLA_AUDIENCE,
    teslaScopes: "openid offline_access vehicle_device_data",
    oauthSessionTtlSeconds: 600,
    oauthLaunchTtlSeconds: 120,
    vehicleSummaryTtlSeconds: 900,
    session,
  });
}

function adminBearer(token = ADMIN_TOKEN): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

function readBearer(token = READ_TOKEN): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

describe("public onboarding HTTP interface", () => {
  test("serves the configured public key at Tesla's exact well-known path", async () => {
    const response = await workerFor().fetch(
      new Request(
        "https://example.com/.well-known/appspecific/com.tesla.3p.public-key.pem",
      ),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("application/x-pem-file");
    expect(await response.text()).toBe(PUBLIC_KEY);
  });

  test("requires the bridge bearer before minting an OAuth launch", async () => {
    const session = createSession();
    const response = await workerFor(session).fetch(
      new Request("https://example.com/v1/oauth/launch", { method: "POST" }),
    );

    expect(response.status).toBe(401);
    expect(response.headers.get("WWW-Authenticate")).toBe("Bearer");
    expect(await response.json()).toEqual({ error: "unauthorized" });
    expect(session.createOAuthLaunch).not.toHaveBeenCalled();
  });

  test("mints a short-lived one-time OAuth launch without exposing its hash", async () => {
    const session = createSession();
    const response = await workerFor(session).fetch(
      new Request("https://example.com/v1/oauth/launch", {
        method: "POST",
        headers: adminBearer(),
      }),
    );

    expect(response.status).toBe(201);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const body = await response.json<{
      authorization_url: string;
      expires_in: number;
    }>();
    expect(body.expires_in).toBe(120);
    const launchUrl = new URL(body.authorization_url);
    expect(launchUrl.origin).toBe("https://example.com");
    expect(launchUrl.pathname).toBe("/oauth/start");
    expect(launchUrl.searchParams.get("launch")).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(session.createOAuthLaunch).toHaveBeenCalledTimes(1);
    const [storedHash, expiresAt] = session.createOAuthLaunch.mock.calls[0];
    expect(storedHash).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(storedHash).not.toBe(launchUrl.searchParams.get("launch"));
    expect(expiresAt).toBeGreaterThan(Date.now());
  });

  test("consumes a launch and redirects to Tesla with only minimal read scopes", async () => {
    const session = createSession();
    const response = await workerFor(session).fetch(
      new Request(
        `https://example.com/oauth/start?launch=${"l".repeat(43)}`,
      ),
    );

    expect(response.status).toBe(303);
    const location = new URL(response.headers.get("Location")!);
    expect(location.origin).toBe("https://auth.tesla.com");
    expect(location.pathname).toBe("/oauth2/v3/authorize");
    expect(Object.fromEntries(location.searchParams)).toEqual({
      response_type: "code",
      client_id: "test-client-id",
      redirect_uri: "https://example.com/oauth/callback",
      scope: "openid offline_access vehicle_device_data",
      state: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      prompt_missing_scopes: "true",
      require_requested_scopes: "true",
    });
    expect(location.searchParams.has("code_challenge")).toBe(false);
    expect(location.searchParams.has("nonce")).toBe(false);
    expect(response.headers.get("Set-Cookie")).toMatch(
      /^__Host-vehicle_oauth_browser=[A-Za-z0-9_-]{43};/,
    );
    expect(response.headers.get("Set-Cookie")).toContain("HttpOnly");
    expect(response.headers.get("Set-Cookie")).toContain("SameSite=Lax");
    expect(session.beginAuthorization).toHaveBeenCalledTimes(1);
    const [authorization] = session.beginAuthorization.mock.calls[0];
    expect(authorization.launch_hash).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(authorization.state_hash).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(authorization.browser_hash).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(authorization.expires_at).toBeGreaterThan(Date.now());
  });

  test("rejects a consumed or expired OAuth launch", async () => {
    const session = createSession();
    session.beginAuthorization.mockResolvedValue(false);

    const response = await workerFor(session).fetch(
      new Request(
        `https://example.com/oauth/start?launch=${"l".repeat(43)}`,
      ),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_oauth_launch" });
    expect(response.headers.get("Location")).toBeNull();
  });

  test("rejects an OAuth callback that has no authorization state", async () => {
    const session = createSession();
    const response = await workerFor(session).fetch(
      new Request("https://example.com/oauth/callback?code=sample-code"),
    );

    expect(response.status).toBe(400);
    expect(await response.json()).toEqual({ error: "invalid_oauth_callback" });
    expect(session.completeAuthorization).not.toHaveBeenCalled();
  });

  test("completes a server-bound callback then removes code and state from the URL", async () => {
    const session = createSession();
    const response = await workerFor(session).fetch(
      new Request(
        `https://example.com/oauth/callback?code=sample-code&state=${"s".repeat(43)}`,
        {
          headers: {
            Cookie: `__Host-vehicle_oauth_browser=${"b".repeat(43)}`,
          },
        },
      ),
    );

    expect(response.status).toBe(303);
    expect(response.headers.get("Location")).toBe(
      "https://example.com/oauth/result?status=connected",
    );
    expect(response.headers.get("Set-Cookie")).toContain(
      "__Host-vehicle_oauth_browser=;",
    );
    expect(session.completeAuthorization).toHaveBeenCalledTimes(1);
    expect(session.completeAuthorization).toHaveBeenCalledWith({
      authorization_code: "sample-code",
      state_hash: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      browser_hash: expect.stringMatching(/^[A-Za-z0-9_-]{43}$/),
      redirect_uri: "https://example.com/oauth/callback",
    });
  });

  test("serves a generic OAuth result page without credentials or codes", async () => {
    const response = await workerFor().fetch(
      new Request("https://example.com/oauth/result?status=connected"),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("text/html");
    const body = await response.text();
    expect(body).toContain("Tesla account connected");
    expect(body).not.toContain("access_token");
    expect(body).not.toContain("refresh_token");
  });

  test("uses separate admin and read-only bearer capabilities", async () => {
    const session = createSession();
    const unauthorized = await workerFor(session).fetch(
      new Request("https://example.com/api/vehicle-summary"),
    );
    expect(unauthorized.status).toBe(401);
    expect(session.getVehicleSummary).not.toHaveBeenCalled();

    const authorized = await workerFor(session).fetch(
      new Request("https://example.com/api/vehicle-summary", {
        headers: readBearer(),
      }),
    );
    expect(authorized.status).toBe(200);
    expect(authorized.headers.get("Cache-Control")).toBe("no-store");
    expect(authorized.headers.get("Vary")).toBe("Authorization");
    expect(await authorized.json()).toEqual(SUMMARY);
    expect(session.getVehicleSummary).toHaveBeenCalledTimes(1);
    expect(session.getVehicleSummary).toHaveBeenCalledWith(
      expect.any(Number),
      900,
      1,
    );

    const adminCannotRead = await workerFor(session).fetch(
      new Request("https://example.com/api/vehicle-summary", {
        headers: adminBearer(),
      }),
    );
    expect(adminCannotRead.status).toBe(401);

    const readCannotLaunch = await workerFor(session).fetch(
      new Request("https://example.com/v1/oauth/launch", {
        method: "POST",
        headers: readBearer(),
      }),
    );
    expect(readCannotLaunch.status).toBe(401);
  });

  test.each([
    "?schema_version=",
    "?schema_version=3",
    "?schema_version=1&schema_version=1",
    "?schema_version=1&schema_version=2",
    "?schema_version=2&schema_version=2",
  ])(
    "rejects an invalid vehicle-summary schema version before reading vehicle data: %s",
    async (query) => {
      const session = createSession();

      const response = await workerFor(session).fetch(
        new Request(`https://example.com/api/vehicle-summary${query}`, {
          headers: readBearer(),
        }),
      );

      expect(response.status).toBe(400);
      expect(await response.json()).toEqual({ error: "invalid_schema_version" });
      expect(session.getVehicleSummary).not.toHaveBeenCalled();
    },
  );

  test("keeps the version-one contract when explicitly requested", async () => {
    const session = createSession();

    const response = await workerFor(session).fetch(
      new Request(
        "https://example.com/api/vehicle-summary?schema_version=1",
        { headers: readBearer() },
      ),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(SUMMARY);
    expect(session.getVehicleSummary).toHaveBeenCalledWith(
      expect.any(Number),
      900,
      1,
    );
  });

  test("selects the version-two vehicle summary only when explicitly requested", async () => {
    const session = createSession({
      ok: true,
      summary: SUMMARY_V2,
    });

    const response = await workerFor(session).fetch(
      new Request(
        "https://example.com/api/vehicle-summary?schema_version=2",
        { headers: readBearer() },
      ),
    );

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(SUMMARY_V2);
    expect(session.getVehicleSummary).toHaveBeenCalledWith(
      expect.any(Number),
      900,
      2,
    );
  });

  test("converts unexpected session failures into a generic safe response", async () => {
    const session = createSession();
    session.getVehicleSummary.mockRejectedValue(
      new Error("must-not-leak-secret-or-provider-url"),
    );

    const response = await workerFor(session).fetch(
      new Request("https://example.com/api/vehicle-summary", {
        headers: readBearer(),
      }),
    );

    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "internal_error" });
  });

  test.each(["/admin/partner-register", "/admin/partner-public-key"])(
    "does not expose the removed onboarding endpoint %s",
    async (path) => {
      const response = await workerFor().fetch(
        new Request(`https://example.com${path}`, {
          method: "POST",
          headers: adminBearer("obsolete-probe-token"),
        }),
      );

      expect(response.status).toBe(404);
    },
  );
});
