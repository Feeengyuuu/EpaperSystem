import { describe, expect, test, vi } from "vitest";

import {
  createTeslaUserClient,
  type TeslaUserClientConfig,
} from "../src/tesla-user";

const CONFIG: TeslaUserClientConfig = {
  clientId: "client-id+reserved",
  clientSecret: "client-secret&reserved=value",
  audience: "https://fleet-api.prd.na.vn.cloud.tesla.com",
  requiredScopes: ["openid", "offline_access", "vehicle_device_data"],
};

function base64UrlJson(value: unknown): string {
  return btoa(JSON.stringify(value))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function accessToken(
  expiresAtSeconds = 2_000_000_000,
  scopes = CONFIG.requiredScopes,
): string {
  return `${base64UrlJson({ alg: "RS256" })}.${base64UrlJson({
    exp: expiresAtSeconds,
    scp: scopes,
  })}.test-signature`;
}

describe("Tesla third-party user boundary", () => {
  test("exchanges an authorization code only at Tesla's server token host", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      return Response.json({
        access_token: accessToken(),
        refresh_token: "rotating-refresh-token",
        token_type: "Bearer",
      });
    };
    const client = createTeslaUserClient(CONFIG, fetcher);

    const result = await client.exchangeAuthorizationCode({
      code: "authorization-code",
      redirectUri: "https://example.com/oauth/callback",
    });

    expect(result).toEqual({
      ok: true,
      tokens: {
        access_token: accessToken(),
        refresh_token: "rotating-refresh-token",
        access_expires_at: 2_000_000_000_000,
        scopes: ["offline_access", "openid", "vehicle_device_data"],
      },
    });
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe(
      "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token",
    );
    expect(requests[0].method).toBe("POST");
    expect(requests[0].redirect).toBe("manual");
    expect(requests[0].headers.get("Content-Type")).toContain(
      "application/x-www-form-urlencoded",
    );
    expect(Object.fromEntries(new URLSearchParams(await requests[0].text()))).toEqual({
      grant_type: "authorization_code",
      client_id: CONFIG.clientId,
      client_secret: CONFIG.clientSecret,
      code: "authorization-code",
      audience: CONFIG.audience,
      redirect_uri: "https://example.com/oauth/callback",
    });
  });

  test("refreshes with only Tesla's currently documented fields", async () => {
    let request: Request | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      request = new Request(input, init);
      return Response.json({
        access_token: accessToken(2_000_000_100),
        refresh_token: "new-refresh-token",
      });
    };
    const client = createTeslaUserClient(CONFIG, fetcher);

    const result = await client.refreshTokens("old-refresh-token");

    expect(result.ok).toBe(true);
    expect(Object.fromEntries(new URLSearchParams(await request!.text()))).toEqual({
      grant_type: "refresh_token",
      client_id: CONFIG.clientId,
      refresh_token: "old-refresh-token",
    });
  });

  test("rejects token responses missing a required granted scope", async () => {
    const fetcher: typeof fetch = async () =>
      Response.json({
        access_token: accessToken(2_000_000_000, [
          "openid",
          "vehicle_device_data",
        ]),
        refresh_token: "refresh-token",
      });
    const client = createTeslaUserClient(CONFIG, fetcher);

    const result = await client.exchangeAuthorizationCode({
      code: "authorization-code",
      redirectUri: "https://example.com/oauth/callback",
    });

    expect(result).toMatchObject({
      ok: false,
      error: "missing_required_scope",
      rotated_tokens: {
        access_token: accessToken(2_000_000_000, [
          "openid",
          "vehicle_device_data",
        ]),
        refresh_token: "refresh-token",
        access_expires_at: 2_000_000_000_000,
        scopes: ["openid", "vehicle_device_data"],
      },
    });
  });

  test("lists vehicles without retaining provider token fields", async () => {
    const requests: Request[] = [];
    const fetcher: typeof fetch = async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      return Response.json({
        response: [
          {
            vin: "5YJ3E1EA7KF000001",
            display_name: "Gray Bullet",
            state: "online",
            access_type: "OWNER",
            in_service: false,
            tokens: ["provider-secret-token"],
            backseat_token: "provider-backseat-token",
          },
        ],
        count: 1,
      });
    };
    const client = createTeslaUserClient(CONFIG, fetcher);

    const result = await client.listVehicles("user-access-token");

    expect(result).toEqual({
      ok: true,
      vehicles: [
        {
          vin: "5YJ3E1EA7KF000001",
          display_name: "Gray Bullet",
          state: "online",
          access_type: "OWNER",
          in_service: false,
        },
      ],
    });
    expect(requests[0].url).toBe(
      `${CONFIG.audience}/api/1/vehicles?page=1&per_page=100`,
    );
    expect(requests[0].headers.get("Authorization")).toBe(
      "Bearer user-access-token",
    );
    expect(JSON.stringify(result)).not.toContain("provider-secret-token");
  });

  test("requests one privacy-minimal vehicle snapshot and returns only allowlisted fields", async () => {
    let request: Request | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      request = new Request(input, init);
      return Response.json({
        response: {
          vin: "5YJ3E1EA7KF000001",
          display_name: "provider-name-must-not-win",
          state: "online",
          latitude: 37.5,
          longitude: -122.0,
          charge_state: {
            battery_level: 78,
            battery_range: 218.25,
            charging_state: "Disconnected",
            charge_limit_soc: 80,
            charge_port_door_open: false,
            charger_power: 0,
            minutes_to_full_charge: 0,
          },
          climate_state: {
            inside_temp: 21.5,
            outside_temp: 18,
            is_climate_on: false,
          },
          closures_state: {
            df: 0,
            dr: 0,
            pf: 0,
            pr: 0,
            ft: 0,
            rt: 0,
            fd_window: 0,
            rd_window: 0,
            fp_window: 0,
            rp_window: 0,
          },
          gui_settings: { gui_distance_units: "mi/hr" },
          vehicle_config: { car_type: "modely", trim_badging: "P74D" },
          vehicle_state: {
            locked: true,
            odometer: 12345.6,
            car_version: "2026.20.100 abc123",
            sentry_mode: false,
            remote_start: true,
          },
        },
      });
    };
    const client = createTeslaUserClient(CONFIG, fetcher);

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    const url = new URL(request!.url);
    expect(url.origin).toBe(CONFIG.audience);
    expect(url.pathname).toBe(
      "/api/1/vehicles/5YJ3E1EA7KF000001/vehicle_data",
    );
    expect(url.searchParams.get("endpoints")).toBe(
      "charge_state;climate_state;closures_state;gui_settings;vehicle_config;vehicle_state",
    );
    expect(url.searchParams.has("location_data")).toBe(false);
    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        captured_at_ms: 1_786_000_000_000,
        checked_at_ms: 1_786_000_000_000,
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
          estimated_range: { value: 218.25, unit: "mi" },
          charging_state: "Disconnected",
          charge_limit_percent: 80,
          time_to_full_minutes: 0,
          power_kw: 0,
        },
        charging: {
          state: "disconnected",
          charge_limit_percent: 80,
          time_to_full_minutes: 0,
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
        },
        closures: {
          all_closed: true,
          open: [],
          charge_port_open: false,
        },
        preferences: {
          distance_unit: "mi",
          temperature_unit: null,
          pressure_unit: null,
          charge_display_unit: null,
          use_24_hour_time: null,
        },
        details: {
          battery: {
            usable_level_percent: null,
            driving_range: null,
          },
        },
      },
    });
    if (!result.ok) {
      throw new Error("expected_vehicle_snapshot");
    }
    expect(Object.keys(result.snapshot).sort()).toEqual([
      "battery",
      "captured_at_ms",
      "charging",
      "checked_at_ms",
      "climate",
      "closures",
      "details",
      "preferences",
      "software_update",
      "tires",
      "vehicle",
      "vehicle_connectivity",
    ]);
    expect(Object.keys(result.snapshot.vehicle).sort()).toEqual([
      "display_name",
      "key",
      "locked",
      "model",
      "odometer",
      "software_version",
      "trim",
    ]);
    expect(Object.keys(result.snapshot.battery).sort()).toEqual([
      "charge_limit_percent",
      "charging_state",
      "estimated_range",
      "level_percent",
      "power_kw",
      "time_to_full_minutes",
    ]);
    expect(Object.keys(result.snapshot.climate).sort()).toEqual([
      "inside_temp_c",
      "is_climate_on",
      "outside_temp_c",
    ]);
    expect(Object.keys(result.snapshot.closures).sort()).toEqual([
      "all_closed",
      "charge_port_open",
      "open",
    ]);
    expect(JSON.stringify(result)).not.toContain("5YJ3E1EA7KF000001");
    expect(JSON.stringify(result)).not.toContain("latitude");
    expect(JSON.stringify(result)).not.toContain("remote_start");
  });

  test("requests location only when authorized and keeps only bounded coordinates", async () => {
    let request: Request | undefined;
    const client = createTeslaUserClient(CONFIG, async (input, init) => {
      request = new Request(input, init);
      return Response.json({
        response: {
          drive_state: {
            gps_as_of: 1_785_999_995,
            latitude: 37.5012349,
            longitude: -122.0012349,
            heading: 270,
            speed: 55,
            active_route_destination: "private destination",
          },
          latitude: 1,
          longitude: 2,
        },
      });
    });

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
      true,
    );

    expect(new URL(request!.url).searchParams.get("endpoints")).toBe(
      "charge_state;climate_state;closures_state;gui_settings;location_data;vehicle_config;vehicle_state",
    );
    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        location: {
          captured_at_ms: 1_785_999_995_000,
          latitude: 37.501235,
          longitude: -122.001235,
        },
      },
    });
    expect(JSON.stringify(result)).not.toContain("private destination");
    expect(JSON.stringify(result)).not.toContain("heading");
    expect(JSON.stringify(result)).not.toContain('"speed":55');
  });

  test.each([
    [{ latitude: 91, longitude: -122 }],
    [{ latitude: 37.5, longitude: -181 }],
    [{ latitude: 37.5 }],
    [{ longitude: -122 }],
    [{ latitude: "37.5", longitude: -122 }],
  ])("rejects incomplete or invalid vehicle location pairs: %j", async (driveState) => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({ response: { drive_state: driveState } }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
      true,
    );

    expect(result).toMatchObject({ ok: true, snapshot: { location: null } });
  });

  test.each([
    [{ latitude: 37.5, longitude: -122 }],
    [{ latitude: 37.5, longitude: -122, gps_as_of: "invalid" }],
    [{ latitude: 37.5, longitude: -122, timestamp: -1 }],
  ])("rejects location without a trustworthy provider timestamp: %j", async (driveState) => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({ response: { drive_state: driveState } }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
      true,
    );

    expect(result).toMatchObject({ ok: true, snapshot: { location: null } });
  });

  test("canonicalizes energy, charging, and unit preferences from existing groups", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          charge_state: {
            battery_level: 78,
            usable_battery_level: 76,
            battery_range: 218.25,
            est_battery_range: 201.5,
            charging_state: "Charging",
            charge_limit_soc: 80,
            minutes_to_full_charge: 95,
            charger_power: 11,
            charge_energy_added: 5.4,
            charge_rate: 44,
            charger_actual_current: 32,
            charger_voltage: 240,
            charger_phases: 1,
            charge_current_request: 32,
            charge_current_request_max: 48,
            charge_enable_request: true,
            conn_charge_cable: "IEC",
            fast_charger_present: false,
            fast_charger_type: "SNA",
            charge_port_latch: "Engaged",
            charge_port_cold_weather_mode: false,
            preconditioning_enabled: true,
            not_enough_power_to_heat: false,
            supercharger_session_trip_planner: false,
            scheduled_charging_pending: true,
            scheduled_charging_mode: "StartAt",
          },
          gui_settings: {
            gui_distance_units: "mi/hr",
            gui_temperature_units: "F",
            gui_tirepressure_units: "psi",
            gui_charge_rate_units: "mi/hr",
            gui_24_hour_time: true,
          },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        battery: {
          level_percent: 78,
          estimated_range: { value: 218.25, unit: "mi" },
        },
        details: {
          battery: {
            usable_level_percent: 76,
            driving_range: { value: 201.5, unit: "mi" },
          },
        },
        charging: {
          energy_added_kwh: 5.4,
          rate: { value: 44, unit: "mi/h" },
          actual_current_a: 32,
          voltage_v: 240,
          phases: 1,
          requested_current_a: 32,
          max_current_a: 48,
          enabled: true,
          cable_type: "iec",
          fast_charger_present: false,
          fast_charger_type: "sna",
          port_latch: "engaged",
          port_cold_weather_mode: false,
          preconditioning: true,
          not_enough_power_to_heat: false,
          supercharger_trip_planner: false,
          scheduled: { pending: true, mode: "start_at" },
        },
        preferences: {
          distance_unit: "mi",
          temperature_unit: "F",
          pressure_unit: "psi",
          charge_display_unit: "distance",
          use_24_hour_time: true,
        },
      },
    });
  });

  test("canonicalizes configuration, security, climate, and software telemetry", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          vehicle_config: {
            car_type: "modely",
            trim_badging: "P74D",
            exterior_color: "MidnightSilver",
            wheel_type: "UberTurbine20",
            roof_color: "Colored",
            charge_port_type: "US",
            efficiency_package: "MY2021",
            rear_seat_heaters: "PremiumHeaters",
            rhd: false,
            eu_vehicle: false,
            sun_roof_installed: 0,
          },
          vehicle_state: {
            locked: true,
            car_version: "2026.20.100 abc123",
            odometer: 12345.6,
            sentry_mode: true,
            service_mode: false,
            valet_mode: false,
            center_display_state: 2,
            speed_limit_mode: { active: true, current_limit_mph: 75 },
            software_update: {
              version: "2026.26.3",
              download_perc: 54.5,
              install_perc: 0,
              expected_duration_sec: 1500,
            },
          },
          climate_state: {
            inside_temp: 25,
            outside_temp: 20,
            is_climate_on: true,
            driver_temp_setting: 21.5,
            passenger_temp_setting: 22,
            climate_keeper_mode: "dog",
            defrost_mode: 0,
            is_rear_defroster_on: false,
            battery_heater_on: true,
            wiper_blade_heater: false,
            hvac_auto_request: "on",
            fan_status: 3,
            steering_wheel_heat_level: 2,
            auto_steering_wheel_heat: true,
            seat_heater_left: 1,
            seat_heater_right: 2,
            seat_heater_rear_left: 0,
            seat_heater_rear_right: 0,
            seat_heater_rear_center: 0,
            seat_fan_front_left: 1,
            seat_fan_front_right: 0,
            auto_seat_climate_left: true,
            auto_seat_climate_right: false,
            cabin_overheat_protection: "On",
            cop_activation_temperature: "High",
          },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        details: {
          vehicle: {
            exterior_color: "MidnightSilver",
            wheel_type: "UberTurbine20",
            roof_color: "Colored",
            charge_port_type: "US",
            efficiency_package: "MY2021",
            rear_seat_heaters: "PremiumHeaters",
            right_hand_drive: false,
            europe_vehicle: false,
            sunroof_installed: "not_installed",
            sentry_mode: "on",
            service_mode: false,
            valet_mode: false,
            center_display_state: "on",
            speed_limit_mode: {
              active: true,
              limit: { value: 75, unit: "mi/h" },
            },
          },
          climate: {
            driver_target_temp_c: 21.5,
            passenger_target_temp_c: 22,
            keeper_mode: "dog",
            defrost_mode: "off",
            rear_defroster_on: false,
            battery_heater_on: true,
            wiper_heater_on: false,
            hvac_auto_mode: "on",
            fan_status: 3,
            steering_wheel_heat_level: 2,
            steering_wheel_heat_auto: true,
            seat_heaters: {
              front_left: 1,
              front_right: 2,
              rear_left: 0,
              rear_right: 0,
              rear_center: 0,
            },
            seat_cooling: { front_left: 1, front_right: 0 },
            auto_seat_climate: { front_left: true, front_right: false },
            cabin_overheat: { mode: "on", temp_limit: "high" },
          },
        },
        software_update: {
          version: "2026.26.3",
          download_percent: 54.5,
          install_percent: 0,
          expected_duration_minutes: 25,
        },
      },
    });
  });

  test("uses official vehicle-state closures and all four independent TPMS corners", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          charge_state: { charge_port_door_open: false },
          vehicle_state: {
            df: 0,
            dr: 0,
            pf: 0,
            pr: 0,
            ft: 0,
            rt: 0,
            fd_window: 0,
            rd_window: 0,
            fp_window: 0,
            rp_window: 0,
            tpms_pressure_fl: 2.8,
            tpms_pressure_fr: 2.9,
            tpms_pressure_rl: 3.0,
            tpms_pressure_rr: 3.1,
            tpms_soft_warning_fl: false,
            tpms_soft_warning_fr: false,
            tpms_soft_warning_rl: true,
            tpms_soft_warning_rr: false,
            tpms_hard_warning_fl: false,
            tpms_hard_warning_fr: false,
            tpms_hard_warning_rl: false,
            tpms_hard_warning_rr: true,
          },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        closures: {
          all_closed: true,
          open: [],
        },
        details: {
          closures: {
            doors: {
              driver_front: false,
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
        },
        tires: {
          pressures: {
            front_left: { value: 2.8, unit: "bar" },
            front_right: { value: 2.9, unit: "bar" },
            rear_left: { value: 3.0, unit: "bar" },
            rear_right: { value: 3.1, unit: "bar" },
          },
          soft_warnings: {
            front_left: false,
            front_right: false,
            rear_left: true,
            rear_right: false,
          },
          hard_warnings: {
            front_left: false,
            front_right: false,
            rear_left: false,
            rear_right: true,
          },
        },
      },
    });
  });

  test("fails closed when closure sources disagree", async () => {
    const complete = {
      df: 0,
      dr: 0,
      pf: 0,
      pr: 0,
      ft: 0,
      rt: 0,
      fd_window: 0,
      rd_window: 0,
      fp_window: 0,
      rp_window: 0,
    };
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          charge_state: { charge_port_door_open: false },
          closures_state: complete,
          vehicle_state: { ...complete, df: 1 },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        closures: {
          all_closed: null,
          open: [],
          charge_port_open: false,
        },
        details: {
          closures: {
            doors: { driver_front: null, rear_trunk: false },
          },
        },
      },
    });
  });

  test("reports a known opening even when the remaining closure data is partial", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({ response: { vehicle_state: { rt: 1 } } }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        closures: {
          all_closed: false,
          open: ["rear_trunk"],
        },
        details: {
          closures: { doors: { rear_trunk: true } },
        },
      },
    });
  });

  test("nulls malformed telemetry and preserves unknown enum states", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          charge_state: {
            battery_level: 101,
            usable_battery_level: -1,
            battery_range: 1501,
            est_battery_range: -1,
            charging_state: "brand-new-state",
            charger_phases: 1.5,
            conn_charge_cable: "brand-new-cable",
          },
          climate_state: {
            inside_temp: 101,
            fan_status: 21,
            is_climate_on: "false",
          },
          vehicle_config: {
            exterior_color: "Bad\u0000Color",
            rear_seat_heaters: 1.5,
          },
          vehicle_state: {
            tpms_pressure_rr: 10.1,
            sentry_mode: "brand-new-mode",
            software_update: { expected_duration_sec: 600_001 },
          },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "  Gray   Bullet  ",
        state: "\u0000online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        vehicle: {
          display_name: "Gray Bullet",
        },
        vehicle_connectivity: "unknown",
        battery: {
          level_percent: null,
          estimated_range: null,
        },
        charging: {
          state: "unknown",
          phases: null,
          cable_type: "unknown",
        },
        climate: { inside_temp_c: null, is_climate_on: null },
        details: {
          vehicle: {
            exterior_color: null,
            rear_seat_heaters: null,
            sentry_mode: "unknown",
          },
          battery: {
            usable_level_percent: null,
            driving_range: null,
          },
          climate: { fan_status: null },
        },
        tires: { pressures: { rear_right: null } },
        software_update: { expected_duration_minutes: null },
      },
    });
  });

  test("normalizes endpoint-specific legacy REST representations", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          vehicle_config: {
            rear_seat_heaters: 1,
            sun_roof_installed: 0,
          },
          vehicle_state: {
            center_display_state: 2,
            software_update: { expected_duration_sec: 2700 },
          },
          climate_state: { defrost_mode: 0 },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        details: {
          vehicle: {
            rear_seat_heaters: "package_1",
            sunroof_installed: "not_installed",
            center_display_state: "on",
          },
          climate: { defrost_mode: "off" },
        },
        software_update: { expected_duration_minutes: 45 },
      },
    });
  });

  test("does not interpret unknown REST numbers as telemetry proto enums", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          vehicle_config: { sun_roof_installed: 99 },
          vehicle_state: { center_display_state: 99 },
          climate_state: { defrost_mode: 99 },
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "user-access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: {
        details: {
          vehicle: {
            sunroof_installed: "unknown",
            center_display_state: "unknown",
          },
          climate: { defrost_mode: "unknown" },
        },
      },
    });
  });

  test("blocks redirects and oversized provider responses", async () => {
    const redirects = createTeslaUserClient(CONFIG, async () =>
      new Response(null, {
        status: 302,
        headers: { Location: "https://untrusted.example/steal" },
      }),
    );
    expect(await redirects.listVehicles("access-token")).toEqual({
      ok: false,
      error: "provider_redirect_blocked",
      http_status: 302,
    });

    const oversized = createTeslaUserClient(CONFIG, async () =>
      new Response("{}", {
        status: 200,
        headers: { "Content-Length": "600000" },
      }),
    );
    expect(await oversized.listVehicles("access-token")).toEqual({
      ok: false,
      error: "provider_response_too_large",
      http_status: 200,
    });
  });

  test("normalizes response stream failures instead of rejecting", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('{"response":'));
        controller.error(new Error("body transport failed"));
      },
    });
    const client = createTeslaUserClient(
      CONFIG,
      async () => new Response(stream, { status: 200 }),
    );

    await expect(client.listVehicles("access-token")).resolves.toEqual({
      ok: false,
      error: "provider_network_error",
      http_status: 200,
    });
  });

  test("keeps oversized-response errors normalized when body cancellation fails", async () => {
    const cancel = vi.fn(() => {
      throw new Error("cancel failed");
    });
    const stream = new ReadableStream<Uint8Array>({ cancel });
    const client = createTeslaUserClient(
      CONFIG,
      async () =>
        new Response(stream, {
          status: 200,
          headers: { "Content-Length": "600000" },
        }),
    );

    await expect(client.listVehicles("access-token")).resolves.toEqual({
      ok: false,
      error: "provider_response_too_large",
      http_status: 200,
    });
    expect(cancel).toHaveBeenCalledTimes(1);
  });

  test("does not claim every closure is closed from a partial payload", async () => {
    const client = createTeslaUserClient(CONFIG, async () =>
      Response.json({
        response: {
          charge_state: { charge_port_door_open: false },
          closures_state: { df: 0 },
          vehicle_config: {},
          vehicle_state: {},
        },
      }),
    );

    const result = await client.fetchVehicleSnapshot(
      "access-token",
      {
        vin: "5YJ3E1EA7KF000001",
        display_name: "Gray Bullet",
        state: "online",
        access_type: "OWNER",
        in_service: false,
      },
      1_786_000_000_000,
    );

    expect(result).toMatchObject({
      ok: true,
      snapshot: { closures: { all_closed: null, open: [] } },
    });
  });
});
