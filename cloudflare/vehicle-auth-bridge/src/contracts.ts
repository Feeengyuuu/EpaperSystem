export type SnapshotFreshness = "live" | "fresh_cache" | "stale_cache";
export type VehicleSummarySchemaVersion = 1 | 2;

export type NullableMeasurement = {
  value: number;
  unit: "mi" | "km";
} | null;

export type NullableSpeedMeasurement = {
  value: number;
  unit: "mi/h";
} | null;

export type NullablePressureMeasurement = {
  value: number;
  unit: "bar";
} | null;

export type VehicleSummaryV1 = {
  schema_version: 1;
  served_at: string;
  snapshot: {
    captured_at: string | null;
    freshness: SnapshotFreshness;
    age_seconds: number | null;
    vehicle_connectivity: string;
  };
  vehicle: {
    key: "primary";
    display_name: string;
    model: string | null;
    trim: string | null;
    locked: boolean | null;
    software_version: string | null;
    odometer: NullableMeasurement;
  };
  battery: {
    level_percent: number | null;
    estimated_range: NullableMeasurement;
    charging_state: string | null;
    charge_limit_percent: number | null;
    time_to_full_minutes: number | null;
    power_kw: number | null;
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
};

type VehicleSnapshotSummary = {
  captured_at: string | null;
  freshness: SnapshotFreshness;
  age_seconds: number | null;
  vehicle_connectivity: string;
};

type TirePositions<T> = {
  front_left: T;
  front_right: T;
  rear_left: T;
  rear_right: T;
};

export type VehicleSummaryV2 = {
  schema_version: 2;
  served_at: string;
  snapshot: VehicleSnapshotSummary;
  vehicle: {
    key: "primary";
    display_name: string;
    model: string | null;
    trim: string | null;
    locked: boolean | null;
    software_version: string | null;
    odometer: NullableMeasurement;
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
      limit: NullableSpeedMeasurement;
    };
  };
  battery: {
    level_percent: number | null;
    usable_level_percent: number | null;
    rated_range: NullableMeasurement;
    estimated_range: NullableMeasurement;
  };
  charging: {
    state: string | null;
    charge_limit_percent: number | null;
    time_to_full_minutes: number | null;
    power_kw: number | null;
    energy_added_kwh: number | null;
    rate: NullableSpeedMeasurement;
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
    all_closed: boolean | null;
    open: string[];
    charge_port_open: boolean | null;
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
  tires: {
    pressures: TirePositions<NullablePressureMeasurement>;
    soft_warnings: TirePositions<boolean | null>;
    hard_warnings: TirePositions<boolean | null>;
  };
  software_update: {
    version: string | null;
    download_percent: number | null;
    install_percent: number | null;
    expected_duration_minutes: number | null;
  };
  preferences: {
    distance_unit: "mi" | "km" | null;
    temperature_unit: "C" | "F" | null;
    pressure_unit: "psi" | "bar" | null;
    charge_display_unit: "distance" | "percent" | "unknown" | null;
    use_24_hour_time: boolean | null;
  };
};

export type VehicleSummary = VehicleSummaryV1 | VehicleSummaryV2;

export type OAuthCompletion =
  | { ok: true }
  | {
      ok: false;
      error:
        | "invalid_oauth_session"
        | "oauth_exchange_failed"
        | "oauth_invalid_response";
    };

export type VehicleSummaryResult =
  | { ok: true; summary: VehicleSummary }
  | {
      ok: false;
      error:
        | "tesla_authorization_required"
        | "tesla_reauthorization_required"
        | "vehicle_data_temporarily_unavailable";
    };

export type BeginAuthorizationInput = {
  launch_hash: string;
  state_hash: string;
  browser_hash: string;
  expires_at: number;
  now_ms: number;
};

export type CompleteAuthorizationInput = {
  authorization_code: string;
  state_hash: string;
  browser_hash: string;
  redirect_uri: string;
};
