import { describe, expect, test, vi } from "vitest";

import type {
  BeginAuthorizationInput,
  VehicleSummaryResult,
} from "../src/contracts";
import {
  VehicleSessionCore,
  type CachedVehicleSnapshot,
  type StoredAuthorization,
  type VehicleSessionRepository,
} from "../src/vehicle-session-core";
import type {
  StoredVehicleSnapshot,
  TeslaTokens,
  TeslaUserClient,
  VehicleInventory,
} from "../src/tesla-user";

const NOW = 1_800_000_000_000;
const VEHICLE: VehicleInventory = {
  vin: "5YJ3E1EA7KF000001",
  display_name: "Gray Bullet",
  state: "online",
  access_type: "OWNER",
  in_service: false,
};
const TOKENS: TeslaTokens = {
  access_token: "access-token",
  refresh_token: "refresh-token",
  access_expires_at: NOW + 3_600_000,
  scopes: ["offline_access", "openid", "vehicle_device_data"],
};
const LOCATION_TOKENS: TeslaTokens = {
  ...TOKENS,
  scopes: [...TOKENS.scopes, "vehicle_location"],
};
const SNAPSHOT: StoredVehicleSnapshot = {
  captured_at_ms: NOW - 60_000,
  checked_at_ms: NOW - 60_000,
  vehicle_connectivity: "online",
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

const ENRICHED_SNAPSHOT: StoredVehicleSnapshot = {
  ...SNAPSHOT,
  vehicle: {
    ...SNAPSHOT.vehicle,
    exterior_color: "SolidBlack",
    wheel_type: "Induction20",
    roof_color: "Glass",
    charge_port_type: "US",
    efficiency_package: "MY2025",
    rear_seat_heaters: "All",
    right_hand_drive: false,
    europe_vehicle: false,
    sunroof_installed: "not_installed",
    sentry_mode: "armed",
    service_mode: false,
    valet_mode: false,
    center_display_state: "off",
    speed_limit_mode: {
      active: false,
      limit: { value: 85, unit: "mi/h" },
    },
  },
  battery: {
    ...SNAPSHOT.battery,
    usable_level_percent: 76,
    driving_range: { value: 203.5, unit: "mi" },
  },
  charging: {
    state: "Charging",
    charge_limit_percent: 80,
    time_to_full_minutes: 45,
    power_kw: 11,
    energy_added_kwh: 4.25,
    rate: { value: 33, unit: "mi/h" },
    actual_current_a: 32,
    voltage_v: 240,
    phases: 1,
    requested_current_a: 32,
    max_current_a: 48,
    enabled: true,
    cable_type: "sae",
    fast_charger_present: false,
    fast_charger_type: null,
    port_latch: "engaged",
    port_cold_weather_mode: false,
    preconditioning: true,
    not_enough_power_to_heat: false,
    supercharger_trip_planner: false,
    scheduled: { pending: true, mode: "depart_by" },
  },
  climate: {
    ...SNAPSHOT.climate,
    driver_target_temp_c: 21,
    passenger_target_temp_c: 22,
    keeper_mode: "dog",
    defrost_mode: "off",
    rear_defroster_on: false,
    battery_heater_on: true,
    wiper_heater_on: false,
    hvac_auto_mode: "on",
    fan_status: 3,
    steering_wheel_heat_level: 2,
    steering_wheel_heat_auto: false,
    seat_heaters: {
      front_left: 1,
      front_right: 0,
      rear_left: 0,
      rear_right: 0,
      rear_center: 0,
    },
    seat_cooling: { front_left: 0, front_right: 0 },
    auto_seat_climate: { front_left: true, front_right: false },
    cabin_overheat: { mode: "fan_only", temp_limit: "high" },
  },
  closures: {
    all_closed: false,
    open: ["driver_front_door"],
    charge_port_open: true,
    doors: {
      driver_front: true,
      driver_rear: false,
      passenger_front: false,
      passenger_rear: false,
      front_trunk: false,
      rear_trunk: false,
    },
    windows: {
      driver_front: false,
      driver_rear: false,
      passenger_front: false,
      passenger_rear: false,
    },
  },
  tires: {
    pressures: {
      front_left: { value: 2.91, unit: "bar" },
      front_right: { value: 2.92, unit: "bar" },
      rear_left: { value: 2.93, unit: "bar" },
      rear_right: { value: 2.94, unit: "bar" },
    },
    soft_warnings: {
      front_left: false,
      front_right: false,
      rear_left: false,
      rear_right: true,
    },
    hard_warnings: {
      front_left: false,
      front_right: false,
      rear_left: false,
      rear_right: false,
    },
  },
  software_update: {
    version: "2026.24.2",
    download_percent: 75,
    install_percent: 0,
    expected_duration_minutes: 25,
  },
  preferences: {
    distance_unit: "mi",
    temperature_unit: "F",
    pressure_unit: "psi",
    charge_display_unit: "percent",
    use_24_hour_time: false,
  },
};

class MemoryRepository implements VehicleSessionRepository {
  launches = new Map<string, number>();
  sessions = new Map<
    string,
    { browser_hash: string; expires_at: number }
  >();
  authorization: StoredAuthorization | null = null;
  cachedSnapshot: CachedVehicleSnapshot | null = null;
  atomicAuthorizationReplacements = 0;

  async createOAuthLaunch(hash: string, expiresAt: number): Promise<void> {
    this.launches.set(hash, expiresAt);
  }

  async beginAuthorization(input: BeginAuthorizationInput): Promise<boolean> {
    const launchExpiry = this.launches.get(input.launch_hash);
    if (!launchExpiry || launchExpiry < input.now_ms) {
      return false;
    }
    this.launches.delete(input.launch_hash);
    this.sessions.set(input.state_hash, {
      browser_hash: input.browser_hash,
      expires_at: input.expires_at,
    });
    return true;
  }

  async consumeAuthorization(
    stateHash: string,
    browserHash: string,
    nowMs: number,
  ): Promise<boolean> {
    const session = this.sessions.get(stateHash);
    if (
      !session ||
      session.browser_hash !== browserHash ||
      session.expires_at < nowMs
    ) {
      return false;
    }
    this.sessions.delete(stateHash);
    return true;
  }

  async getAuthorization(): Promise<StoredAuthorization | null> {
    return this.authorization;
  }

  async putAuthorization(value: StoredAuthorization): Promise<void> {
    this.authorization = structuredClone(value);
  }

  async markReauthorizationRequired(): Promise<void> {
    if (this.authorization) {
      this.authorization.reauthorization_required = true;
    }
  }

  async getCachedSnapshot(): Promise<CachedVehicleSnapshot | null> {
    return this.cachedSnapshot && structuredClone(this.cachedSnapshot);
  }

  async putCachedSnapshot(value: CachedVehicleSnapshot): Promise<void> {
    this.cachedSnapshot = structuredClone(value);
  }

  async clearCachedSnapshot(): Promise<void> {
    this.cachedSnapshot = null;
  }

  async replaceAuthorizationAndClearSnapshot(
    value: StoredAuthorization,
  ): Promise<void> {
    this.atomicAuthorizationReplacements += 1;
    this.authorization = structuredClone(value);
    this.cachedSnapshot = null;
  }
}

function createTesla(overrides: Partial<TeslaUserClient> = {}): TeslaUserClient & {
  exchangeAuthorizationCode: ReturnType<typeof vi.fn>;
  refreshTokens: ReturnType<typeof vi.fn>;
  listVehicles: ReturnType<typeof vi.fn>;
  fetchVehicleSnapshot: ReturnType<typeof vi.fn>;
} {
  return {
    exchangeAuthorizationCode: vi.fn(async () => ({ ok: true, tokens: TOKENS })),
    refreshTokens: vi.fn(async () => ({ ok: true, tokens: TOKENS })),
    listVehicles: vi.fn(async () => ({ ok: true, vehicles: [VEHICLE] })),
    fetchVehicleSnapshot: vi.fn(async () => ({ ok: true, snapshot: SNAPSHOT })),
    ...overrides,
  } as TeslaUserClient & {
    exchangeAuthorizationCode: ReturnType<typeof vi.fn>;
    refreshTokens: ReturnType<typeof vi.fn>;
    listVehicles: ReturnType<typeof vi.fn>;
    fetchVehicleSnapshot: ReturnType<typeof vi.fn>;
  };
}

function createCore(
  repository: MemoryRepository,
  tesla = createTesla(),
): VehicleSessionCore {
  return new VehicleSessionCore({
    repository,
    tesla,
    now: () => NOW,
    maxStaleSeconds: 86_400,
  });
}

describe("vehicle session coordination", () => {
  test("requires renewed vehicle-location consent before serving schema three", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const tesla = createTesla();

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      900,
      3,
    );

    expect(result).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(repository.authorization.reauthorization_required).toBe(true);
    expect(tesla.listVehicles).not.toHaveBeenCalled();
    expect(tesla.fetchVehicleSnapshot).not.toHaveBeenCalled();
  });

  test("projects a private cached location only into schema three", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: {
        ...SNAPSHOT,
        location: {
          captured_at_ms: NOW - 120_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };
    const core = createCore(repository);

    const versionThree = await core.getVehicleSummary(NOW, 900, 3);
    const versionTwo = await core.getVehicleSummary(NOW, 900, 2);

    expect(versionThree).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        location: {
          captured_at: new Date(NOW - 120_000).toISOString(),
          age_seconds: 120,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
    });
    expect(JSON.stringify(versionTwo)).not.toContain("location");
  });

  test("does not request location for schema two even after location consent", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    const tesla = createTesla();

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      0,
      2,
    );

    expect(result).toMatchObject({
      ok: true,
      summary: { schema_version: 2 },
    });
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledWith(
      "access-token",
      VEHICLE,
      NOW,
    );
    expect(tesla.fetchVehicleSnapshot.mock.calls[0]).toHaveLength(3);
    expect(JSON.stringify(repository.cachedSnapshot)).not.toContain("location");
  });

  test("schema three bypasses a fresh cache that never requested location", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: {
          ...SNAPSHOT,
          location: {
            captured_at_ms: NOW - 120_000,
            latitude: 37.501235,
            longitude: -122.001235,
          },
        },
      })),
    });

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      900,
      3,
    );

    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledWith(
      "access-token",
      VEHICLE,
      NOW,
      true,
    );
    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        location: { latitude: 37.501235, longitude: -122.001235 },
      },
    });
  });

  test("schema three refresh preserves a valid last-known location when Tesla temporarily omits it", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      selected_vehicle_id: VEHICLE.vin,
      snapshot: {
        ...SNAPSHOT,
        checked_at_ms: NOW - 2_000,
        location: {
          captured_at_ms: NOW - 120_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: { ...SNAPSHOT, location: null },
      })),
    });

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      1,
      3,
    );

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        location: {
          captured_at: new Date(NOW - 120_000).toISOString(),
          age_seconds: 120,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
    });
  });

  test("schema three refresh keeps the newer of cached and provider locations", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      selected_vehicle_id: VEHICLE.vin,
      snapshot: {
        ...SNAPSHOT,
        checked_at_ms: NOW - 2_000,
        location: {
          captured_at_ms: NOW - 120_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: {
          ...SNAPSHOT,
          location: {
            captured_at_ms: NOW - 300_000,
            latitude: 38.501235,
            longitude: -121.001235,
          },
        },
      })),
    });

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      1,
      3,
    );

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        location: {
          captured_at: new Date(NOW - 120_000).toISOString(),
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
    });
  });

  test("does not persist an out-of-window provider location", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: {
          ...SNAPSHOT,
          location: {
            captured_at_ms: NOW - 86_400_001,
            latitude: 37.501235,
            longitude: -122.001235,
          },
        },
      })),
    });

    const result = await createCore(repository, tesla).getVehicleSummary(
      NOW,
      1,
      3,
    );

    expect(result).toMatchObject({
      ok: true,
      summary: { schema_version: 3, location: null },
    });
    expect(repository.cachedSnapshot?.snapshot.location).toBeNull();
  });

  test("schema two refresh preserves a valid last-known location for later sleep", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      selected_vehicle_id: VEHICLE.vin,
      snapshot: {
        ...SNAPSHOT,
        checked_at_ms: NOW - 2_000,
        location: {
          captured_at_ms: NOW - 120_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };
    const asleep = { ...VEHICLE, state: "asleep" };
    const tesla = createTesla({
      listVehicles: vi
        .fn()
        .mockResolvedValueOnce({ ok: true, vehicles: [VEHICLE] })
        .mockResolvedValueOnce({ ok: true, vehicles: [asleep] }),
    });
    const core = createCore(repository, tesla);

    const versionTwo = await core.getVehicleSummary(NOW, 1, 2);
    const sleepingVersionThree = await core.getVehicleSummary(
      NOW + 2_000,
      1,
      3,
    );

    expect(versionTwo).toMatchObject({
      ok: true,
      summary: { schema_version: 2 },
    });
    expect(sleepingVersionThree).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        snapshot: { vehicle_connectivity: "asleep" },
        location: {
          age_seconds: 122,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
    });
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledTimes(1);
    expect(tesla.fetchVehicleSnapshot.mock.calls[0]).toHaveLength(3);
  });

  test("keeps a last-known location while asleep without waking or fetching data", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      selected_vehicle_id: VEHICLE.vin,
      snapshot: {
        ...SNAPSHOT,
        location: {
          captured_at_ms: NOW - 7_200_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };
    const asleep = { ...VEHICLE, state: "asleep" };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({ ok: true, vehicles: [asleep] })),
    });

    const result = await createCore(repository, tesla).getVehicleSummary(NOW, 1, 3);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 3,
        snapshot: { freshness: "stale_cache", vehicle_connectivity: "asleep" },
        location: { age_seconds: 7_200 },
      },
    });
    expect(tesla.fetchVehicleSnapshot).not.toHaveBeenCalled();
  });

  test("hides a location older than the maximum stale window", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: {
        ...SNAPSHOT,
        location: {
          captured_at_ms: NOW - 86_400_001,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };

    const result = await createCore(repository).getVehicleSummary(NOW, 900, 3);

    expect(result).toMatchObject({
      ok: true,
      summary: { schema_version: 3, location: null },
    });
  });

  test("hides a cached location more than five minutes in the future", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: LOCATION_TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: {
        ...SNAPSHOT,
        location: {
          captured_at_ms: NOW + 300_001,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
      stale: false,
    };

    const result = await createCore(repository).getVehicleSummary(NOW, 900, 3);

    expect(result).toMatchObject({
      ok: true,
      summary: { schema_version: 3, location: null },
    });
  });

  test("consumes both OAuth launch and callback state exactly once", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 6,
      account_generation: 2,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    const tesla = createTesla();
    const core = createCore(repository, tesla);
    repository.cachedSnapshot = {
      account_generation: 2,
      snapshot: SNAPSHOT,
      stale: false,
    };
    vi.spyOn(repository, "putAuthorization").mockRejectedValue(
      new Error("non-atomic authorization write was used"),
    );
    await core.createOAuthLaunch("launch-hash", NOW + 120_000);
    const input: BeginAuthorizationInput = {
      launch_hash: "launch-hash",
      state_hash: "state-hash",
      browser_hash: "browser-hash",
      expires_at: NOW + 600_000,
      now_ms: NOW,
    };

    expect(await core.beginAuthorization(input)).toBe(true);
    expect(await core.beginAuthorization(input)).toBe(false);
    expect(
      await core.completeAuthorization({
        authorization_code: "authorization-code",
        state_hash: "state-hash",
        browser_hash: "wrong-browser-hash",
        redirect_uri: "https://example.com/oauth/callback",
      }),
    ).toEqual({ ok: false, error: "invalid_oauth_session" });
    expect(tesla.exchangeAuthorizationCode).not.toHaveBeenCalled();

    expect(
      await core.completeAuthorization({
        authorization_code: "authorization-code",
        state_hash: "state-hash",
        browser_hash: "browser-hash",
        redirect_uri: "https://example.com/oauth/callback",
      }),
    ).toEqual({ ok: true });
    expect(repository.authorization).toEqual({
      generation: 7,
      account_generation: 3,
      reauthorization_required: false,
      tokens: TOKENS,
    });
    expect(repository.cachedSnapshot).toBeNull();
    expect(repository.atomicAuthorizationReplacements).toBe(1);
    expect(
      await core.completeAuthorization({
        authorization_code: "replayed-code",
        state_hash: "state-hash",
        browser_hash: "browser-hash",
        redirect_uri: "https://example.com/oauth/callback",
      }),
    ).toEqual({ ok: false, error: "invalid_oauth_session" });
    expect(tesla.exchangeAuthorizationCode).toHaveBeenCalledTimes(1);
  });

  test("returns a fresh cached summary without any Tesla request", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const tesla = createTesla();
    const core = createCore(repository, tesla);

    const result = await core.getVehicleSummary(NOW, 900);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 1,
        served_at: new Date(NOW).toISOString(),
        snapshot: {
          captured_at: new Date(NOW - 60_000).toISOString(),
          freshness: "fresh_cache",
          age_seconds: 60,
          vehicle_connectivity: "online",
        },
      },
    });
    expect(tesla.listVehicles).not.toHaveBeenCalled();
    expect(tesla.fetchVehicleSnapshot).not.toHaveBeenCalled();
  });

  test("projects a legacy cached snapshot into a complete version-two summary", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const core = createCore(repository);

    const result = await core.getVehicleSummary(NOW, 900, 2);

    expect(result).toEqual({
      ok: true,
      summary: {
        schema_version: 2,
        served_at: "2027-01-15T08:00:00.000Z",
        snapshot: {
          captured_at: "2027-01-15T07:59:00.000Z",
          freshness: "fresh_cache",
          age_seconds: 60,
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
          speed_limit_mode: { active: null, limit: null },
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
          scheduled: { pending: null, mode: null },
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
          seat_cooling: { front_left: null, front_right: null },
          auto_seat_climate: { front_left: null, front_right: null },
          cabin_overheat: { mode: null, temp_limit: null },
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
      },
    });
  });

  test("fills every charging key when an older cache contains only part of the new group", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: {
        ...SNAPSHOT,
        charging: {
          state: "Charging",
        } as NonNullable<StoredVehicleSnapshot["charging"]>,
      },
      stale: false,
    };
    const core = createCore(repository);

    const result = await core.getVehicleSummary(NOW, 900, 2);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        charging: {
          state: "charging",
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
          scheduled: { pending: null, mode: null },
        },
      },
    });
  });

  test("keeps the version-one projection exact when the stored snapshot has version-two fields", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: ENRICHED_SNAPSHOT,
      stale: false,
    };
    const core = createCore(repository);

    const result = await core.getVehicleSummary(NOW, 900, 1);

    expect(result).toEqual({
      ok: true,
      summary: {
        schema_version: 1,
        served_at: "2027-01-15T08:00:00.000Z",
        snapshot: {
          captured_at: "2027-01-15T07:59:00.000Z",
          freshness: "fresh_cache",
          age_seconds: 60,
          vehicle_connectivity: "online",
        },
        vehicle: SNAPSHOT.vehicle,
        battery: SNAPSHOT.battery,
        climate: SNAPSHOT.climate,
        closures: {
          all_closed: false,
          open: ["driver_front_door"],
          charge_port_open: true,
        },
      },
    });
  });

  test("persists live snapshots in a shape that remains exact after a version-one Worker rollback", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true as const,
        snapshot: ENRICHED_SNAPSHOT,
      })),
    });
    const core = createCore(repository, tesla);

    const result = await core.getVehicleSummary(NOW, 900, 2);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 2,
        vehicle: { exterior_color: "SolidBlack" },
        battery: { usable_level_percent: 76 },
        climate: { keeper_mode: "dog" },
        closures: { doors: { driver_front: true } },
      },
    });
    expect(repository.cachedSnapshot).not.toBeNull();
    const persisted = repository.cachedSnapshot!.snapshot;
    expect({
      vehicle: persisted.vehicle,
      battery: persisted.battery,
      climate: persisted.climate,
      closures: persisted.closures,
    }).toEqual({
      vehicle: SNAPSHOT.vehicle,
      battery: SNAPSHOT.battery,
      climate: SNAPSHOT.climate,
      closures: {
        all_closed: false,
        open: ["driver_front_door"],
        charge_port_open: true,
      },
    });
  });

  test("projects every enriched stored group into the version-two public contract", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: ENRICHED_SNAPSHOT,
      stale: false,
    };
    const core = createCore(repository);

    const result = await core.getVehicleSummary(NOW, 900, 2);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        schema_version: 2,
        vehicle: {
          exterior_color: "SolidBlack",
          wheel_type: "Induction20",
          rear_seat_heaters: "All",
          sentry_mode: "armed",
          speed_limit_mode: {
            active: false,
            limit: { value: 85, unit: "mi/h" },
          },
        },
        battery: {
          level_percent: 78,
          usable_level_percent: 76,
          rated_range: { value: 218, unit: "mi" },
          estimated_range: { value: 203.5, unit: "mi" },
        },
        charging: {
          state: "charging",
          energy_added_kwh: 4.25,
          actual_current_a: 32,
          voltage_v: 240,
          scheduled: { pending: true, mode: "depart_by" },
        },
        climate: {
          driver_target_temp_c: 21,
          passenger_target_temp_c: 22,
          keeper_mode: "dog",
          seat_heaters: { front_left: 1, rear_center: 0 },
          cabin_overheat: { mode: "fan_only", temp_limit: "high" },
        },
        closures: {
          all_closed: false,
          doors: { driver_front: true, rear_trunk: false },
          windows: { passenger_rear: false },
        },
        tires: {
          pressures: {
            front_left: { value: 2.91, unit: "bar" },
            front_right: { value: 2.92, unit: "bar" },
            rear_left: { value: 2.93, unit: "bar" },
            rear_right: { value: 2.94, unit: "bar" },
          },
          soft_warnings: { rear_right: true },
        },
        software_update: {
          version: "2026.24.2",
          download_percent: 75,
          install_percent: 0,
          expected_duration_minutes: 25,
        },
        preferences: {
          distance_unit: "mi",
          temperature_unit: "F",
          pressure_unit: "psi",
          charge_display_unit: "percent",
          use_24_hour_time: false,
        },
      },
    });
  });

  test("never reuses a cache from a previous OAuth account generation", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 5,
      account_generation: 2,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const tesla = createTesla({
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: {
          ...SNAPSHOT,
          captured_at_ms: NOW,
          checked_at_ms: NOW,
          vehicle: {
            ...SNAPSHOT.vehicle,
            display_name: "New account vehicle",
          },
        },
      })),
    });
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 900)).toMatchObject({
      ok: true,
      summary: {
        snapshot: { freshness: "live" },
        vehicle: { display_name: "New account vehicle" },
      },
    });
    expect(tesla.listVehicles).toHaveBeenCalledTimes(1);
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledTimes(1);
    expect(repository.cachedSnapshot?.account_generation).toBe(2);
  });

  test("keeps the cached vehicle selection when inventory order changes", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    const selectedVehicle: VehicleInventory = {
      ...VEHICLE,
      vin: "5YJ3E1EA7KF000002",
      display_name: "Selected Vehicle",
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      selected_vehicle_id: selectedVehicle.vin,
      snapshot: {
        ...SNAPSHOT,
        checked_at_ms: NOW - 3_600_000,
        vehicle: {
          ...SNAPSHOT.vehicle,
          display_name: selectedVehicle.display_name,
        },
      },
      stale: false,
    };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({
        ok: true,
        vehicles: [VEHICLE, selectedVehicle],
      })),
    });
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 900)).toMatchObject({ ok: true });
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledWith(
      TOKENS.access_token,
      selectedVehicle,
      NOW,
    );
    expect(repository.cachedSnapshot?.selected_vehicle_id).toBe(
      selectedVehicle.vin,
    );
  });

  test("selects a vehicle deterministically when no selection is cached", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    const laterVehicle: VehicleInventory = {
      ...VEHICLE,
      vin: "5YJ3E1EA7KF000002",
      display_name: "Later Vehicle",
    };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({
        ok: true,
        vehicles: [laterVehicle, VEHICLE],
      })),
    });
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 0)).toMatchObject({ ok: true });
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledWith(
      TOKENS.access_token,
      VEHICLE,
      NOW,
    );
    expect(repository.cachedSnapshot?.selected_vehicle_id).toBe(VEHICLE.vin);
  });

  test("never serves a fresh cache before checking current authorization", async () => {
    const repository = new MemoryRepository();
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const tesla = createTesla();
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 900)).toEqual({
      ok: false,
      error: "tesla_authorization_required",
    });

    repository.authorization = {
      generation: 2,
      account_generation: 1,
      reauthorization_required: true,
      tokens: TOKENS,
    };
    expect(await core.getVehicleSummary(NOW, 900)).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(tesla.listVehicles).not.toHaveBeenCalled();
  });

  test("checks connectivity but never requests live data for an asleep vehicle", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: { ...SNAPSHOT, checked_at_ms: NOW - 3_600_000 },
      stale: false,
    };
    const asleep = { ...VEHICLE, state: "asleep" };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({ ok: true, vehicles: [asleep] })),
    });
    const core = createCore(repository, tesla);

    const result = await core.getVehicleSummary(NOW, 900);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        snapshot: {
          freshness: "stale_cache",
          vehicle_connectivity: "asleep",
        },
      },
    });
    expect(tesla.listVehicles).toHaveBeenCalledTimes(1);
    expect(tesla.fetchVehicleSnapshot).not.toHaveBeenCalled();
    expect(repository.cachedSnapshot?.snapshot.checked_at_ms).toBe(NOW);
  });

  test("does not extend vehicle values beyond max stale while asleep", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: {
        ...SNAPSHOT,
        captured_at_ms: NOW - 86_400_001,
        checked_at_ms: NOW - 1_000,
      },
      stale: true,
    };
    const asleep = { ...VEHICLE, state: "asleep" };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({ ok: true, vehicles: [asleep] })),
    });
    const core = createCore(repository, tesla);

    const result = await core.getVehicleSummary(NOW, 900);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        snapshot: {
          captured_at: new Date(NOW).toISOString(),
          freshness: "stale_cache",
          vehicle_connectivity: "asleep",
        },
        vehicle: { display_name: "Gray Bullet", locked: null },
        battery: { level_percent: null, estimated_range: null },
        closures: { all_closed: null },
      },
    });
    expect(tesla.listVehicles).toHaveBeenCalledTimes(1);
    expect(tesla.fetchVehicleSnapshot).not.toHaveBeenCalled();
  });

  test("serializes concurrent refresh and live snapshot work", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 7,
      account_generation: 3,
      reauthorization_required: false,
      tokens: { ...TOKENS, access_expires_at: NOW + 10_000 },
    };
    const rotated: TeslaTokens = {
      ...TOKENS,
      access_token: "rotated-access-token",
      refresh_token: "rotated-refresh-token",
      access_expires_at: NOW + 3_600_000,
    };
    const tesla = createTesla({
      refreshTokens: vi.fn(async () => ({ ok: true, tokens: rotated })),
      fetchVehicleSnapshot: vi.fn(async () => ({
        ok: true,
        snapshot: { ...SNAPSHOT, captured_at_ms: NOW, checked_at_ms: NOW },
      })),
    });
    const core = createCore(repository, tesla);

    const results = await Promise.all(
      Array.from({ length: 10 }, () => core.getVehicleSummary(NOW, 900)),
    );

    expect(results.every((result) => result.ok)).toBe(true);
    expect(tesla.refreshTokens).toHaveBeenCalledTimes(1);
    expect(tesla.listVehicles).toHaveBeenCalledTimes(1);
    expect(tesla.fetchVehicleSnapshot).toHaveBeenCalledTimes(1);
    expect(repository.authorization).toEqual({
      generation: 8,
      account_generation: 3,
      reauthorization_required: false,
      tokens: rotated,
    });
    const freshness = results.map((result) =>
      result.ok ? result.summary.snapshot.freshness : "error",
    );
    expect(freshness.filter((value) => value === "live")).toHaveLength(1);
    expect(freshness.filter((value) => value === "fresh_cache")).toHaveLength(9);
  });

  test("returns stale cache on a provider failure and preserves authorization", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: TOKENS,
    };
    repository.cachedSnapshot = {
      account_generation: 1,
      snapshot: { ...SNAPSHOT, checked_at_ms: NOW - 3_600_000 },
      stale: false,
    };
    const tesla = createTesla({
      listVehicles: vi.fn(async () => ({
        ok: false,
        error: "provider_network_error",
      })),
    });
    const core = createCore(repository, tesla);

    const result: VehicleSummaryResult = await core.getVehicleSummary(NOW, 900);

    expect(result).toMatchObject({
      ok: true,
      summary: {
        snapshot: {
          freshness: "stale_cache",
          vehicle_connectivity: "unavailable",
        },
      },
    });
    expect(repository.cachedSnapshot?.snapshot.vehicle_connectivity).toBe(
      "unavailable",
    );
    expect(repository.authorization?.reauthorization_required).toBe(false);
  });

  test("persists reauthorization-required after a rejected refresh", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 1,
      account_generation: 1,
      reauthorization_required: false,
      tokens: { ...TOKENS, access_expires_at: NOW + 10_000 },
    };
    const tesla = createTesla({
      refreshTokens: vi.fn(async () => ({
        ok: false,
        error: "provider_http_error",
        http_status: 401,
      })),
    });
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 900)).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(repository.authorization?.reauthorization_required).toBe(true);
    expect(await core.getVehicleSummary(NOW, 900)).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(tesla.refreshTokens).toHaveBeenCalledTimes(1);
  });

  test("persists the rotated refresh token when refreshed scopes are reduced", async () => {
    const repository = new MemoryRepository();
    repository.authorization = {
      generation: 4,
      account_generation: 2,
      reauthorization_required: false,
      tokens: { ...TOKENS, access_expires_at: NOW + 10_000 },
    };
    repository.cachedSnapshot = {
      account_generation: 2,
      snapshot: SNAPSHOT,
      stale: false,
    };
    const reducedTokens: TeslaTokens = {
      access_token: "reduced-access-token",
      refresh_token: "rotated-refresh-token",
      access_expires_at: NOW + 3_600_000,
      scopes: ["offline_access", "openid"],
    };
    const tesla = createTesla({
      refreshTokens: vi.fn(async () => ({
        ok: false,
        error: "missing_required_scope",
        rotated_tokens: reducedTokens,
      })),
    });
    const core = createCore(repository, tesla);

    expect(await core.getVehicleSummary(NOW, 0)).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(repository.authorization).toEqual({
      generation: 5,
      account_generation: 2,
      reauthorization_required: true,
      tokens: reducedTokens,
    });
    expect(await core.getVehicleSummary(NOW, 0)).toEqual({
      ok: false,
      error: "tesla_reauthorization_required",
    });
    expect(tesla.refreshTokens).toHaveBeenCalledTimes(1);
    expect(tesla.listVehicles).not.toHaveBeenCalled();
  });
});
