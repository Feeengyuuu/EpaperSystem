import { base64UrlToBytes } from "./security";

const TOKEN_URL =
  "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token";
const ALLOWED_AUDIENCES = new Set([
  "https://fleet-api.prd.na.vn.cloud.tesla.com",
  "https://fleet-api.prd.eu.vn.cloud.tesla.com",
  "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
]);
const VEHICLE_DATA_GROUPS = [
  "charge_state",
  "climate_state",
  "closures_state",
  "gui_settings",
  "vehicle_config",
  "vehicle_state",
].join(";");
const VIN_PATTERN = /^[A-HJ-NPR-Z0-9]{17}$/;

export type TeslaUserClientConfig = {
  clientId: string;
  clientSecret: string;
  audience: string;
  requiredScopes: string[];
};

export type TeslaTokens = {
  access_token: string;
  refresh_token: string;
  access_expires_at: number;
  scopes: string[];
};

export type VehicleInventory = {
  vin: string;
  display_name: string;
  state: string;
  access_type: string;
  in_service: boolean;
};

export type StoredVehicleSnapshotDetails = {
  vehicle: {
    exterior_color: string | null;
    wheel_type: string | null;
    roof_color: string | null;
    charge_port_type: string | null;
    efficiency_package: string | null;
    rear_seat_heaters: string | null;
    right_hand_drive: boolean | null;
    europe_vehicle: boolean | null;
    sunroof_installed: string | null;
    sentry_mode: string | null;
    service_mode: boolean | null;
    valet_mode: boolean | null;
    center_display_state: string | null;
    speed_limit_mode: {
      active: boolean | null;
      limit: { value: number; unit: "mi/h" } | null;
    };
  };
  battery: {
    usable_level_percent: number | null;
    driving_range: { value: number; unit: "mi" | "km" } | null;
  };
  climate: {
    driver_target_temp_c: number | null;
    passenger_target_temp_c: number | null;
    keeper_mode: string | null;
    defrost_mode: string | null;
    rear_defroster_on: boolean | null;
    battery_heater_on: boolean | null;
    wiper_heater_on: boolean | null;
    hvac_auto_mode: string | null;
    fan_status: number | null;
    steering_wheel_heat_level: number | null;
    steering_wheel_heat_auto: boolean | null;
    seat_heaters: {
      front_left: number | null;
      front_right: number | null;
      rear_left: number | null;
      rear_right: number | null;
      rear_center: number | null;
    };
    seat_cooling: {
      front_left: number | null;
      front_right: number | null;
    };
    auto_seat_climate: {
      front_left: boolean | null;
      front_right: boolean | null;
    };
    cabin_overheat: {
      mode: string | null;
      temp_limit: string | null;
    };
  };
  closures: {
    doors: {
      driver_front: boolean | null;
      driver_rear: boolean | null;
      passenger_front: boolean | null;
      passenger_rear: boolean | null;
      front_trunk: boolean | null;
      rear_trunk: boolean | null;
    };
    windows: {
      driver_front: boolean | null;
      driver_rear: boolean | null;
      passenger_front: boolean | null;
      passenger_rear: boolean | null;
    };
  };
};

export type StoredVehicleSnapshot = {
  captured_at_ms: number;
  checked_at_ms: number;
  vehicle_connectivity: string;
  vehicle: {
    key: "primary";
    display_name: string;
    model: string | null;
    trim: string | null;
    locked: boolean | null;
    software_version: string | null;
    odometer: { value: number; unit: "mi" | "km" } | null;
  };
  battery: {
    level_percent: number | null;
    estimated_range: { value: number; unit: "mi" | "km" } | null;
    charging_state: string | null;
    charge_limit_percent: number | null;
    time_to_full_minutes: number | null;
    power_kw: number | null;
  };
  charging?: {
    state: string | null;
    charge_limit_percent: number | null;
    time_to_full_minutes: number | null;
    power_kw: number | null;
    energy_added_kwh: number | null;
    rate: { value: number; unit: "mi/h" } | null;
    actual_current_a: number | null;
    voltage_v: number | null;
    phases: number | null;
    requested_current_a: number | null;
    max_current_a: number | null;
    enabled: boolean | null;
    cable_type: string | null;
    fast_charger_present: boolean | null;
    fast_charger_type: string | null;
    port_latch: string | null;
    port_cold_weather_mode: boolean | null;
    preconditioning: boolean | null;
    not_enough_power_to_heat: boolean | null;
    supercharger_trip_planner: boolean | null;
    scheduled: {
      pending: boolean | null;
      mode: string | null;
    };
  };
  climate: {
    inside_temp_c: number | null;
    outside_temp_c: number | null;
    is_climate_on: boolean | null;
  };
  closures: {
    all_closed: boolean | null;
    open: string[];
    charge_port_open: boolean | null;
  };
  details?: StoredVehicleSnapshotDetails;
  tires?: {
    pressures: {
      front_left: { value: number; unit: "bar" } | null;
      front_right: { value: number; unit: "bar" } | null;
      rear_left: { value: number; unit: "bar" } | null;
      rear_right: { value: number; unit: "bar" } | null;
    };
    soft_warnings: {
      front_left: boolean | null;
      front_right: boolean | null;
      rear_left: boolean | null;
      rear_right: boolean | null;
    };
    hard_warnings: {
      front_left: boolean | null;
      front_right: boolean | null;
      rear_left: boolean | null;
      rear_right: boolean | null;
    };
  };
  preferences?: {
    distance_unit: "mi" | "km" | null;
    temperature_unit: "C" | "F" | null;
    pressure_unit: "psi" | "bar" | null;
    charge_display_unit: "distance" | "percent" | "unknown" | null;
    use_24_hour_time: boolean | null;
  };
  software_update?: {
    version: string | null;
    download_percent: number | null;
    install_percent: number | null;
    expected_duration_minutes: number | null;
  };
};

export type TeslaClientError = {
  ok: false;
  error: string;
  http_status?: number;
};

export type TeslaTokenResult =
  | { ok: true; tokens: TeslaTokens }
  | (TeslaClientError & { rotated_tokens?: TeslaTokens });
export type TeslaVehicleListResult =
  | { ok: true; vehicles: VehicleInventory[] }
  | TeslaClientError;
export type TeslaSnapshotResult =
  | { ok: true; snapshot: StoredVehicleSnapshot }
  | TeslaClientError;

export type TeslaUserClient = {
  exchangeAuthorizationCode(input: {
    code: string;
    redirectUri: string;
  }): Promise<TeslaTokenResult>;
  refreshTokens(refreshToken: string): Promise<TeslaTokenResult>;
  listVehicles(accessToken: string): Promise<TeslaVehicleListResult>;
  fetchVehicleSnapshot(
    accessToken: string,
    vehicle: VehicleInventory,
    nowMs: number,
  ): Promise<TeslaSnapshotResult>;
};

export function createTeslaUserClient(
  config: TeslaUserClientConfig,
  fetcher: typeof fetch = fetch,
): TeslaUserClient {
  if (!ALLOWED_AUDIENCES.has(config.audience)) {
    throw new Error("unsupported_tesla_audience");
  }

  return {
    async exchangeAuthorizationCode(input) {
      const form = new URLSearchParams({
        grant_type: "authorization_code",
        client_id: config.clientId,
        client_secret: config.clientSecret,
        code: input.code,
        audience: config.audience,
        redirect_uri: input.redirectUri,
      });
      const response = await requestJson(
        fetcher,
        TOKEN_URL,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: form.toString(),
        },
        64 * 1_024,
        10_000,
      );
      if (!response.ok) {
        return response;
      }
      return parseTokenResponse(response.data, config.requiredScopes);
    },

    async refreshTokens(refreshToken) {
      const form = new URLSearchParams({
        grant_type: "refresh_token",
        client_id: config.clientId,
        refresh_token: refreshToken,
      });
      const response = await requestJson(
        fetcher,
        TOKEN_URL,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: form.toString(),
        },
        64 * 1_024,
        10_000,
      );
      if (!response.ok) {
        return response;
      }
      return parseTokenResponse(response.data, config.requiredScopes);
    },

    async listVehicles(accessToken) {
      const url = new URL("/api/1/vehicles", config.audience);
      url.searchParams.set("page", "1");
      url.searchParams.set("per_page", "100");
      const response = await requestJson(
        fetcher,
        url.toString(),
        fleetGet(accessToken),
        512 * 1_024,
        15_000,
      );
      if (!response.ok) {
        return response;
      }
      const root = asRecord(response.data);
      const rows = Array.isArray(root?.response) ? root.response : null;
      if (!rows) {
        return { ok: false, error: "invalid_vehicle_list_response" };
      }
      const vehicles: VehicleInventory[] = [];
      for (const row of rows) {
        const item = asRecord(row);
        const vin = cleanString(item?.vin, 17).toUpperCase();
        if (!VIN_PATTERN.test(vin)) {
          continue;
        }
        vehicles.push({
          vin,
          display_name: cleanString(item?.display_name, 64) || "Vehicle",
          state: cleanString(item?.state, 32).toLowerCase() || "unknown",
          access_type: cleanString(item?.access_type, 32).toUpperCase(),
          in_service: item?.in_service === true,
        });
      }
      return { ok: true, vehicles };
    },

    async fetchVehicleSnapshot(accessToken, vehicle, nowMs) {
      const vin = vehicle.vin.toUpperCase();
      if (!VIN_PATTERN.test(vin)) {
        return { ok: false, error: "invalid_vehicle_identifier" };
      }
      const url = new URL(
        `/api/1/vehicles/${encodeURIComponent(vin)}/vehicle_data`,
        config.audience,
      );
      url.searchParams.set("endpoints", VEHICLE_DATA_GROUPS);
      const response = await requestJson(
        fetcher,
        url.toString(),
        fleetGet(accessToken),
        1024 * 1_024,
        15_000,
      );
      if (!response.ok) {
        return response;
      }
      const root = asRecord(response.data);
      const data = asRecord(root?.response);
      if (!data) {
        return { ok: false, error: "invalid_vehicle_data_response" };
      }
      return {
        ok: true,
        snapshot: sanitizeVehicleSnapshot(data, vehicle, nowMs),
      };
    },
  };
}

function fleetGet(accessToken: string): RequestInit {
  return {
    method: "GET",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
  };
}

function parseTokenResponse(
  payload: unknown,
  requiredScopes: string[],
): TeslaTokenResult {
  const data = asRecord(payload);
  const accessToken = cleanString(data?.access_token, 16 * 1_024);
  const refreshToken = cleanString(data?.refresh_token, 16 * 1_024);
  if (!accessToken || !refreshToken) {
    return { ok: false, error: "invalid_token_response" };
  }

  const claims = decodeJwtClaims(accessToken);
  const expiresAtSeconds = finiteNumber(claims?.exp, 1, 10_000_000_000);
  const scopes = normalizeScopes(claims?.scp);
  if (!expiresAtSeconds || scopes.length === 0) {
    return { ok: false, error: "invalid_token_response" };
  }
  const tokens: TeslaTokens = {
    access_token: accessToken,
    refresh_token: refreshToken,
    access_expires_at: Math.trunc(expiresAtSeconds * 1_000),
    scopes,
  };
  if (requiredScopes.some((required) => !scopes.includes(required))) {
    return { ok: false, error: "missing_required_scope", rotated_tokens: tokens };
  }

  return { ok: true, tokens };
}

function decodeJwtClaims(token: string): Record<string, unknown> | null {
  const parts = token.split(".");
  if (parts.length !== 3) {
    return null;
  }
  try {
    return asRecord(
      JSON.parse(new TextDecoder().decode(base64UrlToBytes(parts[1]))),
    );
  } catch {
    return null;
  }
}

function normalizeScopes(value: unknown): string[] {
  const values = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(/\s+/)
      : [];
  return [...new Set(values.filter((item): item is string => typeof item === "string"))]
    .map((item) => item.trim())
    .filter(Boolean)
    .sort();
}

function sanitizeVehicleSnapshot(
  data: Record<string, unknown>,
  vehicle: VehicleInventory,
  nowMs: number,
): StoredVehicleSnapshot {
  const charge = asRecord(data.charge_state);
  const climate = asRecord(data.climate_state);
  const closures = asRecord(data.closures_state);
  const preferences = asRecord(data.gui_settings);
  const config = asRecord(data.vehicle_config);
  const state = asRecord(data.vehicle_state);
  const speedLimitMode = asRecord(state?.speed_limit_mode);
  const softwareUpdate = asRecord(state?.software_update);
  const chargePortOpen = nullableBoolean(charge?.charge_port_door_open);
  const closureDetails = detailedClosures(closures, state);
  const openClosures = closureLabels(closureDetails);
  const closureStates = [
    ...Object.values(closureDetails.doors),
    ...Object.values(closureDetails.windows),
    chargePortOpen,
  ];
  const softwareVersion = cleanString(state?.car_version, 64).split(/\s+/)[0] || null;

  return {
    captured_at_ms: nowMs,
    checked_at_ms: nowMs,
    vehicle_connectivity:
      cleanString(vehicle.state, 32).toLowerCase() || "unknown",
    vehicle: {
      key: "primary",
      display_name: cleanString(vehicle.display_name, 64) || "Vehicle",
      model: modelName(cleanString(config?.car_type, 32)),
      trim: trimName(cleanString(config?.trim_badging, 32)),
      locked: nullableBoolean(state?.locked),
      software_version: softwareVersion,
      odometer: measurement(state?.odometer, 0, 2_000_000, "mi"),
    },
    battery: {
      level_percent: finiteNumber(charge?.battery_level, 0, 100),
      estimated_range: measurement(charge?.battery_range, 0, 1_500, "mi"),
      charging_state: cleanString(charge?.charging_state, 40) || null,
      charge_limit_percent: finiteNumber(charge?.charge_limit_soc, 0, 100),
      time_to_full_minutes: timeToFullMinutes(charge),
      power_kw: finiteNumber(charge?.charger_power, 0, 1_000),
    },
    charging: {
      state: normalizedEnum(charge?.charging_state, {
        disconnected: "disconnected",
        nopower: "no_power",
        starting: "starting",
        charging: "charging",
        complete: "complete",
        stopped: "stopped",
      }),
      charge_limit_percent: finiteNumber(charge?.charge_limit_soc, 0, 100),
      time_to_full_minutes: timeToFullMinutes(charge),
      power_kw: finiteNumber(charge?.charger_power, 0, 1_000),
      energy_added_kwh: finiteNumber(charge?.charge_energy_added, 0, 500),
      rate: rateMeasurement(charge?.charge_rate),
      actual_current_a: finiteNumber(charge?.charger_actual_current, 0, 1_000),
      voltage_v: finiteNumber(charge?.charger_voltage, 0, 1_000),
      phases: finiteInteger(charge?.charger_phases, 0, 3),
      requested_current_a: finiteNumber(charge?.charge_current_request, 0, 1_000),
      max_current_a: finiteNumber(charge?.charge_current_request_max, 0, 1_000),
      enabled: nullableBoolean(charge?.charge_enable_request),
      cable_type: normalizedEnum(charge?.conn_charge_cable, {
        iec: "iec",
        sae: "sae",
        gbac: "gb_ac",
        gbdc: "gb_dc",
        sna: "sna",
      }),
      fast_charger_present: nullableBoolean(charge?.fast_charger_present),
      fast_charger_type: normalizedEnum(charge?.fast_charger_type, {
        supercharger: "supercharger",
        chademo: "chademo",
        gb: "gb",
        acsinglewirecan: "ac_single_wire_can",
        combo: "combo",
        mcsinglewirecan: "mc_single_wire_can",
        other: "other",
        sna: "sna",
      }),
      port_latch: normalizedEnum(charge?.charge_port_latch, {
        sna: "sna",
        disengaged: "disengaged",
        engaged: "engaged",
        blocking: "blocking",
      }),
      port_cold_weather_mode: nullableBoolean(charge?.charge_port_cold_weather_mode),
      preconditioning: nullableBoolean(charge?.preconditioning_enabled),
      not_enough_power_to_heat: nullableBoolean(charge?.not_enough_power_to_heat),
      supercharger_trip_planner: nullableBoolean(
        charge?.supercharger_session_trip_planner,
      ),
      scheduled: {
        pending: nullableBoolean(charge?.scheduled_charging_pending),
        mode: normalizedEnum(charge?.scheduled_charging_mode, {
          off: "off",
          startat: "start_at",
          departby: "depart_by",
        }),
      },
    },
    climate: {
      inside_temp_c: finiteNumber(climate?.inside_temp, -100, 100),
      outside_temp_c: finiteNumber(climate?.outside_temp, -100, 100),
      is_climate_on: nullableBoolean(climate?.is_climate_on),
    },
    closures: {
      all_closed: closureStates.some((value) => value === true)
        ? false
        : closureStates.every((value) => value === false)
          ? true
          : null,
      open: openClosures,
      charge_port_open: chargePortOpen,
    },
    details: {
      vehicle: {
        exterior_color: optionalString(config?.exterior_color, 40),
        wheel_type: optionalString(config?.wheel_type, 40),
        roof_color: optionalString(config?.roof_color, 40),
        charge_port_type: optionalString(config?.charge_port_type, 40),
        efficiency_package: optionalString(config?.efficiency_package, 40),
        rear_seat_heaters: rearSeatHeaterPackage(config?.rear_seat_heaters),
        right_hand_drive: nullableFlag(config?.rhd),
        europe_vehicle: nullableFlag(config?.eu_vehicle),
        sunroof_installed: sunroofState(config?.sun_roof_installed),
        sentry_mode: sentryMode(state?.sentry_mode),
        service_mode: nullableFlag(state?.service_mode),
        valet_mode: nullableFlag(state?.valet_mode),
        center_display_state: centerDisplayState(state?.center_display_state),
        speed_limit_mode: {
          active: nullableFlag(speedLimitMode?.active),
          limit: rateMeasurement(speedLimitMode?.current_limit_mph, 250),
        },
      },
      battery: {
        usable_level_percent: finiteNumber(charge?.usable_battery_level, 0, 100),
        driving_range: measurement(charge?.est_battery_range, 0, 1_500, "mi"),
      },
      climate: {
        driver_target_temp_c: finiteNumber(climate?.driver_temp_setting, -100, 100),
        passenger_target_temp_c: finiteNumber(
          climate?.passenger_temp_setting,
          -100,
          100,
        ),
        keeper_mode: climateKeeperMode(climate?.climate_keeper_mode),
        defrost_mode: defrostMode(climate?.defrost_mode),
        rear_defroster_on: nullableFlag(climate?.is_rear_defroster_on),
        battery_heater_on: nullableFlag(climate?.battery_heater_on),
        wiper_heater_on: nullableFlag(climate?.wiper_blade_heater),
        hvac_auto_mode: normalizedEnum(climate?.hvac_auto_request, {
          on: "on",
          override: "override",
        }),
        fan_status: finiteInteger(climate?.fan_status, 0, 20),
        steering_wheel_heat_level: finiteInteger(
          climate?.steering_wheel_heat_level,
          0,
          3,
        ),
        steering_wheel_heat_auto: nullableFlag(climate?.auto_steering_wheel_heat),
        seat_heaters: {
          front_left: finiteInteger(climate?.seat_heater_left, 0, 3),
          front_right: finiteInteger(climate?.seat_heater_right, 0, 3),
          rear_left: finiteInteger(climate?.seat_heater_rear_left, 0, 3),
          rear_right: finiteInteger(climate?.seat_heater_rear_right, 0, 3),
          rear_center: finiteInteger(climate?.seat_heater_rear_center, 0, 3),
        },
        seat_cooling: {
          front_left: finiteInteger(climate?.seat_fan_front_left, 0, 3),
          front_right: finiteInteger(climate?.seat_fan_front_right, 0, 3),
        },
        auto_seat_climate: {
          front_left: nullableFlag(climate?.auto_seat_climate_left),
          front_right: nullableFlag(climate?.auto_seat_climate_right),
        },
        cabin_overheat: {
          mode: normalizedEnum(climate?.cabin_overheat_protection, {
            off: "off",
            on: "on",
            fanonly: "fan_only",
          }),
          temp_limit: normalizedEnum(climate?.cop_activation_temperature, {
            low: "low",
            medium: "medium",
            high: "high",
          }),
        },
      },
      closures: {
        doors: closureDetails.doors,
        windows: closureDetails.windows,
      },
    },
    tires: {
      pressures: {
        front_left: pressureMeasurement(state?.tpms_pressure_fl),
        front_right: pressureMeasurement(state?.tpms_pressure_fr),
        rear_left: pressureMeasurement(state?.tpms_pressure_rl),
        rear_right: pressureMeasurement(state?.tpms_pressure_rr),
      },
      soft_warnings: {
        front_left: nullableFlag(state?.tpms_soft_warning_fl),
        front_right: nullableFlag(state?.tpms_soft_warning_fr),
        rear_left: nullableFlag(state?.tpms_soft_warning_rl),
        rear_right: nullableFlag(state?.tpms_soft_warning_rr),
      },
      hard_warnings: {
        front_left: nullableFlag(state?.tpms_hard_warning_fl),
        front_right: nullableFlag(state?.tpms_hard_warning_fr),
        rear_left: nullableFlag(state?.tpms_hard_warning_rl),
        rear_right: nullableFlag(state?.tpms_hard_warning_rr),
      },
    },
    preferences: {
      distance_unit: distanceUnit(preferences?.gui_distance_units),
      temperature_unit: temperatureUnit(preferences?.gui_temperature_units),
      pressure_unit: pressureUnit(preferences?.gui_tirepressure_units),
      charge_display_unit: chargeDisplayUnit(preferences?.gui_charge_rate_units),
      use_24_hour_time: nullableBoolean(preferences?.gui_24_hour_time),
    },
    software_update: {
      version: optionalString(softwareUpdate?.version, 64),
      download_percent: finiteNumber(softwareUpdate?.download_perc, 0, 100),
      install_percent: finiteNumber(softwareUpdate?.install_perc, 0, 100),
      expected_duration_minutes: durationMinutes(
        softwareUpdate?.expected_duration_sec,
      ),
    },
  };
}

function modelName(code: string): string | null {
  const normalized = code.toLowerCase().replace(/[^a-z0-9]/g, "");
  const names: Record<string, string> = {
    model3: "Model 3",
    modely: "Model Y",
    models: "Model S",
    modelx: "Model X",
    cybertruck: "Cybertruck",
  };
  return names[normalized] ?? (code || null);
}

function trimName(code: string): string | null {
  if (!code) {
    return null;
  }
  if (/performance/i.test(code) || /^P[0-9A-Z]+D$/i.test(code)) {
    return "Performance";
  }
  return code;
}

type DetailedClosures = {
  doors: {
    driver_front: boolean | null;
    driver_rear: boolean | null;
    passenger_front: boolean | null;
    passenger_rear: boolean | null;
    front_trunk: boolean | null;
    rear_trunk: boolean | null;
  };
  windows: {
    driver_front: boolean | null;
    driver_rear: boolean | null;
    passenger_front: boolean | null;
    passenger_rear: boolean | null;
  };
};

function detailedClosures(
  closures: Record<string, unknown> | null,
  state: Record<string, unknown> | null,
): DetailedClosures {
  return {
    doors: {
      driver_front: reconciledClosure("df", closures, state),
      driver_rear: reconciledClosure("dr", closures, state),
      passenger_front: reconciledClosure("pf", closures, state),
      passenger_rear: reconciledClosure("pr", closures, state),
      front_trunk: reconciledClosure("ft", closures, state),
      rear_trunk: reconciledClosure("rt", closures, state),
    },
    windows: {
      driver_front: reconciledClosure("fd_window", closures, state),
      driver_rear: reconciledClosure("rd_window", closures, state),
      passenger_front: reconciledClosure("fp_window", closures, state),
      passenger_rear: reconciledClosure("rp_window", closures, state),
    },
  };
}

function reconciledClosure(
  key: string,
  closures: Record<string, unknown> | null,
  state: Record<string, unknown> | null,
): boolean | null {
  const legacy = closureValue(closures?.[key]);
  const official = closureValue(state?.[key]);
  if (legacy !== null && official !== null && legacy !== official) {
    return null;
  }
  return official ?? legacy;
}

function closureValue(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  const number = finiteNumber(value, 0, 100);
  return number === null ? null : number > 0;
}

function closureLabels(details: DetailedClosures): string[] {
  const values: Array<[boolean | null, string]> = [
    [details.doors.driver_front, "driver_front_door"],
    [details.doors.driver_rear, "driver_rear_door"],
    [details.doors.passenger_front, "passenger_front_door"],
    [details.doors.passenger_rear, "passenger_rear_door"],
    [details.doors.front_trunk, "front_trunk"],
    [details.doors.rear_trunk, "rear_trunk"],
    [details.windows.driver_front, "driver_front_window"],
    [details.windows.driver_rear, "driver_rear_window"],
    [details.windows.passenger_front, "passenger_front_window"],
    [details.windows.passenger_rear, "passenger_rear_window"],
  ];
  return values.filter(([value]) => value === true).map(([, label]) => label);
}

function timeToFullMinutes(
  charge: Record<string, unknown> | null,
): number | null {
  const minutes = finiteNumber(charge?.minutes_to_full_charge, 0, 10_000);
  if (minutes !== null) {
    return Math.round(minutes);
  }
  const hours = finiteNumber(charge?.time_to_full_charge, 0, 168);
  return hours === null ? null : Math.round(hours * 60);
}

function measurement(
  value: unknown,
  minimum: number,
  maximum: number,
  unit: "mi" | "km",
): { value: number; unit: "mi" | "km" } | null {
  const number = finiteNumber(value, minimum, maximum);
  return number === null ? null : { value: number, unit };
}

function pressureMeasurement(
  value: unknown,
): { value: number; unit: "bar" } | null {
  const number = finiteNumber(value, 0, 10);
  return number === null ? null : { value: number, unit: "bar" };
}

function rateMeasurement(
  value: unknown,
  maximum = 5_000,
): { value: number; unit: "mi/h" } | null {
  const number = finiteNumber(value, 0, maximum);
  return number === null ? null : { value: number, unit: "mi/h" };
}

function optionalString(value: unknown, maxLength: number): string | null {
  return cleanString(value, maxLength) || null;
}

function nullableFlag(value: unknown): boolean | null {
  if (typeof value === "boolean") {
    return value;
  }
  return value === 0 ? false : value === 1 ? true : null;
}

function sentryMode(value: unknown): string | null {
  if (typeof value === "boolean") {
    return value ? "on" : "off";
  }
  return normalizedEnum(value, {
    off: "off",
    on: "on",
    idle: "idle",
    armed: "armed",
    aware: "aware",
    panic: "panic",
    quiet: "quiet",
  });
}

function centerDisplayState(value: unknown): string | null {
  // The REST vehicle_data endpoint uses legacy numeric values that differ from
  // Fleet Telemetry proto ordinals. Keep this mapping endpoint-specific.
  if (typeof value === "number") {
    return ({
      0: "off",
      2: "on",
    } as Record<number, string>)[value] ?? "unknown";
  }
  return normalizedEnum(value, {
    off: "off",
    dim: "dim",
    accessory: "accessory",
    on: "on",
    driving: "driving",
    charging: "charging",
    lock: "lock",
    sentry: "sentry",
    dog: "dog",
    entertainment: "entertainment",
  });
}

function sunroofState(value: unknown): string | null {
  if (typeof value === "number") {
    return ({
      0: "not_installed",
      1: "gen1_installed",
      2: "gen2_installed",
    } as Record<number, string>)[value] ?? "unknown";
  }
  return normalizedEnum(value, {
    notinstalled: "not_installed",
    gen1installed: "gen1_installed",
    gen2installed: "gen2_installed",
  });
}

function climateKeeperMode(value: unknown): string | null {
  const mode = normalizedEnum(value, {
    off: "off",
    on: "keep",
    keep: "keep",
    dog: "dog",
    party: "camp",
    camp: "camp",
  });
  return mode;
}

function defrostMode(value: unknown): string | null {
  if (typeof value === "number") {
    return ({
      0: "off",
      1: "normal",
      2: "max",
    } as Record<number, string>)[value] ?? "unknown";
  }
  return normalizedEnum(value, {
    off: "off",
    normal: "normal",
    max: "max",
    autodefog: "auto_defog",
  });
}

function rearSeatHeaterPackage(value: unknown): string | null {
  const text = optionalString(value, 40);
  if (text !== null) {
    return text;
  }
  if (
    typeof value === "number"
    && Number.isInteger(value)
    && value >= 0
    && value <= 10
  ) {
    return `package_${value}`;
  }
  return null;
}

function durationMinutes(value: unknown): number | null {
  const seconds = finiteNumber(value, 0, 600_000);
  return seconds === null ? null : Math.round(seconds / 60);
}

function finiteInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): number | null {
  const number = finiteNumber(value, minimum, maximum);
  return number !== null && Number.isInteger(number) ? number : null;
}

function enumKey(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const cleaned = value.trim();
  return cleaned ? cleaned.toLowerCase().replace(/[^a-z0-9]/g, "") : null;
}

function normalizedEnum<T extends string>(
  value: unknown,
  values: Record<string, T>,
): T | "unknown" | null {
  const key = enumKey(value);
  return key === null ? null : (values[key] ?? "unknown");
}

function distanceUnit(value: unknown): "mi" | "km" | null {
  const key = enumKey(value);
  if (key === "mihr" || key === "mi") {
    return "mi";
  }
  if (key === "kmhr" || key === "km") {
    return "km";
  }
  return null;
}

function temperatureUnit(value: unknown): "C" | "F" | null {
  const key = enumKey(value);
  return key === "c" ? "C" : key === "f" ? "F" : null;
}

function pressureUnit(value: unknown): "psi" | "bar" | null {
  const key = enumKey(value);
  return key === "psi" ? "psi" : key === "bar" ? "bar" : null;
}

function chargeDisplayUnit(
  value: unknown,
): "distance" | "percent" | "unknown" | null {
  const key = enumKey(value);
  if (key === null) {
    return null;
  }
  if (key === "mihr" || key === "kmhr" || key === "distance") {
    return "distance";
  }
  return key === "percent" ? "percent" : "unknown";
}

function nullableBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function finiteNumber(
  value: unknown,
  minimum: number,
  maximum: number,
): number | null {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return value >= minimum && value <= maximum ? value : null;
}

function cleanString(value: unknown, maxLength: number): string {
  if (typeof value !== "string" || /[\u0000-\u001f\u007f-\u009f]/.test(value)) {
    return "";
  }
  const normalized = value.trim().split(/\s+/).filter(Boolean).join(" ");
  return normalized.length <= maxLength ? normalized : "";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

async function requestJson(
  fetcher: typeof fetch,
  url: string,
  init: RequestInit,
  maxBytes: number,
  timeoutMs: number,
): Promise<
  | { ok: true; data: unknown; http_status: number }
  | TeslaClientError
> {
  let response: Response;
  try {
    response = await fetcher(url, {
      ...init,
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    return { ok: false, error: "provider_network_error" };
  }

  if (response.status >= 300 && response.status < 400) {
    return {
      ok: false,
      error: "provider_redirect_blocked",
      http_status: response.status,
    };
  }

  let body: Awaited<ReturnType<typeof readBoundedText>>;
  try {
    const declaredLength = Number(response.headers.get("Content-Length"));
    if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
      await cancelBody(response.body);
      return {
        ok: false,
        error: "provider_response_too_large",
        http_status: response.status,
      };
    }
    body = await readBoundedText(response, maxBytes);
  } catch {
    await cancelBody(response.body);
    return {
      ok: false,
      error: "provider_network_error",
      http_status: response.status,
    };
  }
  if (!body.ok) {
    return {
      ok: false,
      error: body.error,
      http_status: response.status,
    };
  }
  if (!response.ok) {
    return {
      ok: false,
      error: "provider_http_error",
      http_status: response.status,
    };
  }

  try {
    return {
      ok: true,
      data: JSON.parse(body.text),
      http_status: response.status,
    };
  } catch {
    return {
      ok: false,
      error: "provider_invalid_json",
      http_status: response.status,
    };
  }
}

async function readBoundedText(
  response: Response,
  maxBytes: number,
): Promise<
  | { ok: true; text: string }
  | {
      ok: false;
      error: "provider_response_too_large" | "provider_network_error";
    }
> {
  if (!response.body) {
    return { ok: true, text: "" };
  }
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (next.done) {
        break;
      }
      total += next.value.byteLength;
      if (total > maxBytes) {
        await cancelReader(reader);
        return { ok: false, error: "provider_response_too_large" };
      }
      chunks.push(next.value);
    }
  } catch {
    await cancelReader(reader);
    return { ok: false, error: "provider_network_error" };
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { ok: true, text: new TextDecoder().decode(bytes) };
}

async function cancelBody(body: ReadableStream<Uint8Array> | null): Promise<void> {
  try {
    await body?.cancel();
  } catch {
    // Cancellation is best-effort and must never mask the provider result.
  }
}

async function cancelReader(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<void> {
  try {
    await reader.cancel();
  } catch {
    // Cancellation is best-effort and must never reject the client call.
  }
}
