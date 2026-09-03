import type {
  BeginAuthorizationInput,
  CompleteAuthorizationInput,
  OAuthCompletion,
  VehicleSummary,
  VehicleSummaryResult,
  VehicleSummarySchemaVersion,
  VehicleSummaryV1,
  VehicleSummaryV2,
  VehicleSummaryV3,
} from "./contracts";
import type {
  StoredVehicleSnapshot,
  StoredVehicleSnapshotDetails,
  TeslaTokens,
  TeslaUserClient,
  TeslaVehicleListResult,
  VehicleInventory,
} from "./tesla-user";

const ACCESS_REFRESH_MARGIN_MS = 60_000;
const DIAGNOSTIC_ERRORS = [
  "provider_network_error", "provider_redirect_blocked", "provider_response_too_large",
  "provider_http_error", "provider_invalid_json", "invalid_vehicle_list_response",
  "invalid_vehicle_identifier", "invalid_vehicle_data_response", "invalid_token_response",
  "missing_required_scope", "vehicle_data_temporarily_unavailable", "tesla_reauthorization_required",
] as const;
const DIAGNOSTIC_STATES = ["online", "offline", "asleep", "unknown", "unavailable"] as const;

export type VehicleSourceDiagnostic = {
  event: "vehicle_source_check";
  stage: "cache_hit" | "token_refresh" | "inventory" | "vehicle_data" | "fallback";
  outcome: "success" | "error" | "fresh_cache" | "stale_cache";
  actual_checked_at: string | null;
  cache_checked_at: string | null;
  cache_age_seconds: number | null;
  http_status: number | null;
  error: (typeof DIAGNOSTIC_ERRORS)[number] | "unknown_provider_error" | null;
  vehicle_count: number | null;
  selected_state: (typeof DIAGNOSTIC_STATES)[number] | null;
};

export type StoredAuthorization = {
  generation: number;
  account_generation: number;
  reauthorization_required: boolean;
  tokens: TeslaTokens;
};

export type CachedVehicleSnapshot = {
  account_generation: number;
  selected_vehicle_id?: string;
  snapshot: StoredVehicleSnapshot;
  stale: boolean;
};

export interface VehicleSessionRepository {
  createOAuthLaunch(hash: string, expiresAt: number): Promise<void>;
  beginAuthorization(input: BeginAuthorizationInput): Promise<boolean>;
  consumeAuthorization(
    stateHash: string,
    browserHash: string,
    nowMs: number,
  ): Promise<boolean>;
  getAuthorization(): Promise<StoredAuthorization | null>;
  putAuthorization(value: StoredAuthorization): Promise<void>;
  replaceAuthorizationAndClearSnapshot(
    value: StoredAuthorization,
  ): Promise<void>;
  markReauthorizationRequired(): Promise<void>;
  getCachedSnapshot(): Promise<CachedVehicleSnapshot | null>;
  putCachedSnapshot(value: CachedVehicleSnapshot): Promise<void>;
  clearCachedSnapshot(): Promise<void>;
}

type VehicleSessionCoreOptions = {
  repository: VehicleSessionRepository;
  tesla: TeslaUserClient;
  now?: () => number;
  maxStaleSeconds: number;
  diagnostic?: (event: VehicleSourceDiagnostic) => void;
};

export class VehicleSessionCore {
  readonly #repository: VehicleSessionRepository;
  readonly #tesla: TeslaUserClient;
  readonly #now: () => number;
  readonly #maxStaleMs: number;
  readonly #diagnostic?: (event: VehicleSourceDiagnostic) => void;
  #exclusiveTail: Promise<void> = Promise.resolve();

  constructor(options: VehicleSessionCoreOptions) {
    this.#repository = options.repository;
    this.#tesla = options.tesla;
    this.#now = options.now ?? Date.now;
    this.#maxStaleMs = options.maxStaleSeconds * 1_000;
    this.#diagnostic = options.diagnostic;
  }

  async createOAuthLaunch(
    launchHash: string,
    expiresAt: number,
  ): Promise<void> {
    await this.#repository.createOAuthLaunch(launchHash, expiresAt);
  }

  async beginAuthorization(input: BeginAuthorizationInput): Promise<boolean> {
    return this.#repository.beginAuthorization(input);
  }

  async completeAuthorization(
    input: CompleteAuthorizationInput,
  ): Promise<OAuthCompletion> {
    return this.#runExclusive(async () => {
      const consumed = await this.#repository.consumeAuthorization(
        input.state_hash,
        input.browser_hash,
        this.#now(),
      );
      if (!consumed) {
        return { ok: false, error: "invalid_oauth_session" };
      }

      const exchanged = await this.#tesla.exchangeAuthorizationCode({
        code: input.authorization_code,
        redirectUri: input.redirect_uri,
      });
      if (!exchanged.ok) {
        const error =
          exchanged.error === "invalid_token_response" ||
          exchanged.error === "missing_required_scope"
            ? "oauth_invalid_response"
            : "oauth_exchange_failed";
        return { ok: false, error };
      }

      const previous = await this.#repository.getAuthorization();
      await this.#repository.replaceAuthorizationAndClearSnapshot({
        generation: (previous?.generation ?? 0) + 1,
        account_generation: previous
          ? accountGenerationOf(previous) + 1
          : 1,
        reauthorization_required: false,
        tokens: exchanged.tokens,
      });
      return { ok: true };
    });
  }

  async getVehicleSummary(
    nowMs: number,
    cacheTtlSeconds: number,
    schemaVersion: VehicleSummarySchemaVersion = 1,
  ): Promise<VehicleSummaryResult> {
    return this.#runExclusive(async () => {
      const cacheTtlMs = Math.max(1, cacheTtlSeconds) * 1_000;
      let authorization = await this.#repository.getAuthorization();
      if (!authorization) {
        return { ok: false, error: "tesla_authorization_required" };
      }
      if (authorization.reauthorization_required) {
        return { ok: false, error: "tesla_reauthorization_required" };
      }
      const hasLocationScope = authorization.tokens.scopes.includes(
        "vehicle_location",
      );
      if (schemaVersion === 3 && !hasLocationScope) {
        await this.#repository.markReauthorizationRequired();
        return { ok: false, error: "tesla_reauthorization_required" };
      }
      const includeLocation = schemaVersion === 3 && hasLocationScope;
      const accountGeneration = accountGenerationOf(authorization);
      let cached = await this.#repository.getCachedSnapshot();
      if (cached && cached.account_generation !== accountGeneration) {
        await this.#repository.clearCachedSnapshot();
        cached = null;
      }
      const preferredVehicleId = cached?.selected_vehicle_id;
      if (cached && !isWithinMaxStale(cached, nowMs, this.#maxStaleMs)) {
        await this.#repository.clearCachedSnapshot();
        cached = null;
      }
      if (cached && !hasRollbackSafeV1Groups(cached.snapshot)) {
        cached = {
          ...cached,
          snapshot: rollbackSafeSnapshot(cached.snapshot),
        };
        await this.#repository.putCachedSnapshot(cached);
      }
      if (
        cached &&
        nowMs - cached.snapshot.checked_at_ms < cacheTtlMs &&
        (!includeLocation || cached.snapshot.location !== undefined)
      ) {
        this.#recordDiagnostic("cache_hit", cached.stale ? "stale_cache" : "fresh_cache", nowMs, cached, {
          selectedState: cached.snapshot.vehicle_connectivity,
        });
        return {
          ok: true,
          summary: toSummary(
            cached,
            nowMs,
            false,
            schemaVersion,
            this.#maxStaleMs,
          ),
        };
      }

      const access = await this.#ensureAccessToken(
        authorization,
        nowMs,
        false,
        cached,
      );
      if (!access.ok) {
        return this.#fallbackOrError(cached, nowMs, access.error, schemaVersion);
      }
      authorization = access.authorization;

      let vehicles = await this.#tesla.listVehicles(
        authorization.tokens.access_token,
      );
      this.#recordInventory(vehicles, preferredVehicleId, nowMs, cached);
      if (!vehicles.ok && vehicles.http_status === 401) {
        const refreshed = await this.#ensureAccessToken(
          authorization,
          nowMs,
          true,
          cached,
        );
        if (!refreshed.ok) {
          return this.#fallbackOrError(
            cached,
            nowMs,
            refreshed.error,
            schemaVersion,
          );
        }
        authorization = refreshed.authorization;
        vehicles = await this.#tesla.listVehicles(
          authorization.tokens.access_token,
        );
        this.#recordInventory(vehicles, preferredVehicleId, nowMs, cached);
      }

      if (!vehicles.ok) {
        if (vehicles.http_status === 401) {
          await this.#repository.markReauthorizationRequired();
          return {
            ok: false,
            error: "tesla_reauthorization_required",
          };
        }
        return this.#fallbackOrError(
          cached,
          nowMs,
          "vehicle_data_temporarily_unavailable",
          schemaVersion,
        );
      }

      const vehicle = selectVehicle(vehicles.vehicles, preferredVehicleId);
      if (!vehicle) {
        return this.#fallbackOrError(
          cached,
          nowMs,
          "vehicle_data_temporarily_unavailable",
          schemaVersion,
        );
      }

      if (cached && !cacheMatchesVehicle(cached, vehicle, vehicles.vehicles.length)) {
        await this.#repository.clearCachedSnapshot();
        cached = null;
      }

      if (vehicle.state !== "online") {
        const offline = cached
          ? {
              ...markStale(cached, vehicle.state, nowMs),
              selected_vehicle_id: vehicleIdOf(vehicle),
            }
          : {
              account_generation: accountGenerationOf(authorization),
              selected_vehicle_id: vehicleIdOf(vehicle),
              snapshot: emptyVehicleSnapshot(vehicle, nowMs),
              stale: true,
            };
        await this.#repository.putCachedSnapshot(offline);
        return {
          ok: true,
          summary: toSummary(
            offline,
            nowMs,
            false,
            schemaVersion,
            this.#maxStaleMs,
          ),
        };
      }

      let snapshot = includeLocation
        ? await this.#tesla.fetchVehicleSnapshot(
            authorization.tokens.access_token,
            vehicle,
            nowMs,
            true,
          )
        : await this.#tesla.fetchVehicleSnapshot(
            authorization.tokens.access_token,
            vehicle,
            nowMs,
          );
      this.#recordDiagnostic("vehicle_data", snapshot.ok ? "success" : "error", nowMs, cached, {
        httpStatus: snapshot.http_status,
        error: snapshot.ok ? undefined : snapshot.error,
      });
      if (!snapshot.ok && snapshot.http_status === 401) {
        const refreshed = await this.#ensureAccessToken(
          authorization,
          nowMs,
          true,
          cached,
        );
        if (!refreshed.ok) {
          return this.#fallbackOrError(
            cached,
            nowMs,
            refreshed.error,
            schemaVersion,
          );
        }
        authorization = refreshed.authorization;
        snapshot = includeLocation
          ? await this.#tesla.fetchVehicleSnapshot(
              authorization.tokens.access_token,
              vehicle,
              nowMs,
              true,
            )
          : await this.#tesla.fetchVehicleSnapshot(
              authorization.tokens.access_token,
              vehicle,
              nowMs,
            );
        this.#recordDiagnostic("vehicle_data", snapshot.ok ? "success" : "error", nowMs, cached, {
          httpStatus: snapshot.http_status,
          error: snapshot.ok ? undefined : snapshot.error,
        });
      }

      if (!snapshot.ok) {
        if (snapshot.http_status === 401) {
          await this.#repository.markReauthorizationRequired();
          return {
            ok: false,
            error: "tesla_reauthorization_required",
          };
        }
        return this.#fallbackOrError(
          cached,
          nowMs,
          "vehicle_data_temporarily_unavailable",
          schemaVersion,
        );
      }

      let storedSnapshot = rollbackSafeSnapshot(snapshot.snapshot);
      if (hasLocationScope) {
        const previousSnapshot =
          cached?.selected_vehicle_id === vehicleIdOf(vehicle)
            ? cached.snapshot
            : undefined;
        storedSnapshot = preserveLastKnownLocation(
          storedSnapshot,
          previousSnapshot,
          nowMs,
          this.#maxStaleMs,
        );
      }
      cached = {
        account_generation: accountGenerationOf(authorization),
        selected_vehicle_id: vehicleIdOf(vehicle),
        snapshot: storedSnapshot,
        stale: false,
      };
      await this.#repository.putCachedSnapshot(cached);
      return {
        ok: true,
        summary: toSummary(
          cached,
          nowMs,
          true,
          schemaVersion,
          this.#maxStaleMs,
        ),
      };
    });
  }

  async #ensureAccessToken(
    authorization: StoredAuthorization,
    nowMs: number,
    force: boolean,
    cached: CachedVehicleSnapshot | null,
  ): Promise<
    | { ok: true; authorization: StoredAuthorization }
    | {
        ok: false;
        error:
          | "tesla_reauthorization_required"
          | "vehicle_data_temporarily_unavailable";
      }
  > {
    if (
      !force &&
      authorization.tokens.access_expires_at - nowMs >
        ACCESS_REFRESH_MARGIN_MS
    ) {
      return { ok: true, authorization };
    }

    const refreshed = await this.#tesla.refreshTokens(
      authorization.tokens.refresh_token,
    );
    this.#recordDiagnostic("token_refresh", refreshed.ok ? "success" : "error", nowMs, cached, {
      httpStatus: refreshed.http_status,
      error: refreshed.ok ? undefined : refreshed.error,
    });
    if (!refreshed.ok) {
      if (refreshed.error === "missing_required_scope") {
        if (refreshed.rotated_tokens) {
          await this.#repository.putAuthorization({
            generation: authorization.generation + 1,
            account_generation: accountGenerationOf(authorization),
            reauthorization_required: true,
            tokens: refreshed.rotated_tokens,
          });
        } else {
          await this.#repository.markReauthorizationRequired();
        }
        return { ok: false, error: "tesla_reauthorization_required" };
      }
      if (refreshed.http_status === 401) {
        await this.#repository.markReauthorizationRequired();
        return { ok: false, error: "tesla_reauthorization_required" };
      }
      return {
        ok: false,
        error: "vehicle_data_temporarily_unavailable",
      };
    }

    const rotated: StoredAuthorization = {
      generation: authorization.generation + 1,
      account_generation: accountGenerationOf(authorization),
      reauthorization_required: false,
      tokens: refreshed.tokens,
    };
    await this.#repository.putAuthorization(rotated);
    return { ok: true, authorization: rotated };
  }

  async #fallbackOrError(
    cached: CachedVehicleSnapshot | null,
    nowMs: number,
    error:
      | "tesla_reauthorization_required"
      | "vehicle_data_temporarily_unavailable",
    schemaVersion: VehicleSummarySchemaVersion,
  ): Promise<VehicleSummaryResult> {
    if (error === "tesla_reauthorization_required") {
      return { ok: false, error };
    }
    if (
      cached &&
      nowMs - cached.snapshot.captured_at_ms <= this.#maxStaleMs
    ) {
      const stale = markStale(
        cached,
        "unavailable",
        nowMs,
      );
      await this.#repository.putCachedSnapshot(stale);
      this.#recordDiagnostic("fallback", "stale_cache", nowMs, stale, {
        error,
        selectedState: stale.snapshot.vehicle_connectivity,
      });
      return {
        ok: true,
        summary: toSummary(
          stale,
          nowMs,
          false,
          schemaVersion,
          this.#maxStaleMs,
        ),
      };
    }
    this.#recordDiagnostic("fallback", "error", nowMs, cached, { error });
    return { ok: false, error };
  }

  async #runExclusive<T>(operation: () => Promise<T>): Promise<T> {
    const previous = this.#exclusiveTail;
    let release: () => void = () => undefined;
    this.#exclusiveTail = new Promise<void>((resolve) => {
      release = resolve;
    });
    await previous;
    try {
      return await operation();
    } finally {
      release();
    }
  }

  #recordInventory(
    result: TeslaVehicleListResult,
    preferredVehicleId: string | undefined,
    nowMs: number,
    cached: CachedVehicleSnapshot | null,
  ): void {
    this.#recordDiagnostic("inventory", result.ok ? "success" : "error", nowMs, cached, {
      httpStatus: result.http_status,
      error: result.ok ? undefined : result.error,
      vehicleCount: result.ok ? result.vehicles.length : undefined,
      selectedState: result.ok ? selectVehicle(result.vehicles, preferredVehicleId)?.state : undefined,
    });
  }

  #recordDiagnostic(
    stage: VehicleSourceDiagnostic["stage"],
    outcome: VehicleSourceDiagnostic["outcome"],
    nowMs: number,
    cached: CachedVehicleSnapshot | null,
    details: { httpStatus?: number; error?: string; vehicleCount?: number; selectedState?: string } = {},
  ): void {
    if (!this.#diagnostic) return;
    const capturedAt = cached?.snapshot.captured_at_ms;
    const checkedAt = cached?.snapshot.checked_at_ms;
    const knownError = DIAGNOSTIC_ERRORS.find((value) => value === details.error);
    const knownState = DIAGNOSTIC_STATES.find((value) => value === details.selectedState);
    // Only these primitives cross the logging boundary; never spread provider objects.
    const event: VehicleSourceDiagnostic = {
      event: "vehicle_source_check",
      stage,
      outcome,
      actual_checked_at: stage === "cache_hit" || stage === "fallback" ? null : diagnosticTimestamp(this.#now()),
      cache_checked_at: diagnosticTimestamp(checkedAt),
      cache_age_seconds: typeof capturedAt === "number" && Number.isFinite(capturedAt)
        ? Math.max(0, Math.floor((nowMs - capturedAt) / 1_000)) : null,
      http_status: Number.isInteger(details.httpStatus) && details.httpStatus! >= 100 && details.httpStatus! <= 599
        ? details.httpStatus! : null,
      error: details.error ? knownError ?? "unknown_provider_error" : null,
      vehicle_count: Number.isSafeInteger(details.vehicleCount) && details.vehicleCount! >= 0
        ? details.vehicleCount! : null,
      selected_state: details.selectedState ? knownState ?? "unknown" : null,
    };
    try {
      this.#diagnostic(event);
    } catch {
      // Observability must not affect token rotation, cache persistence, or reads.
    }
  }
}

function diagnosticTimestamp(value: number | undefined): string | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 8.64e15
    ? new Date(value).toISOString() : null;
}

function accountGenerationOf(authorization: StoredAuthorization): number {
  return Number.isSafeInteger(authorization.account_generation) &&
    authorization.account_generation > 0
    ? authorization.account_generation
    : Math.max(1, authorization.generation);
}

function vehicleIdOf(vehicle: VehicleInventory): string {
  return vehicle.vin.trim().toUpperCase();
}

function selectVehicle(
  vehicles: VehicleInventory[],
  preferredVehicleId?: string,
): VehicleInventory | undefined {
  const preferred = preferredVehicleId?.trim().toUpperCase();
  if (preferred) {
    const match = vehicles.find((vehicle) => vehicleIdOf(vehicle) === preferred);
    if (match) {
      return match;
    }
  }

  let selected: VehicleInventory | undefined;
  for (const vehicle of vehicles) {
    if (!selected || vehicleIdOf(vehicle) < vehicleIdOf(selected)) {
      selected = vehicle;
    }
  }
  return selected;
}

function cacheMatchesVehicle(
  cached: CachedVehicleSnapshot,
  vehicle: VehicleInventory,
  inventoryCount: number,
): boolean {
  const cachedVehicleId = cached.selected_vehicle_id?.trim().toUpperCase();
  return cachedVehicleId
    ? cachedVehicleId === vehicleIdOf(vehicle)
    : inventoryCount === 1;
}

function isWithinMaxStale(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  maxStaleMs: number,
): boolean {
  const capturedAt = cached.snapshot.captured_at_ms;
  if (!Number.isFinite(capturedAt)) {
    return false;
  }
  return Math.max(0, nowMs - capturedAt) <= maxStaleMs;
}

function markStale(
  cached: CachedVehicleSnapshot,
  connectivity: string,
  nowMs: number,
): CachedVehicleSnapshot {
  return {
    ...cached,
    stale: true,
    snapshot: rollbackSafeSnapshot({
      ...cached.snapshot,
      checked_at_ms: nowMs,
      vehicle_connectivity: connectivity || "unavailable",
    }),
  };
}

function emptyVehicleSnapshot(
  vehicle: VehicleInventory,
  nowMs: number,
): StoredVehicleSnapshot {
  return {
    captured_at_ms: nowMs,
    checked_at_ms: nowMs,
    vehicle_connectivity: vehicle.state || "unknown",
    vehicle: {
      key: "primary",
      display_name: vehicle.display_name || "Vehicle",
      model: null,
      trim: null,
      locked: null,
      software_version: null,
      odometer: null,
    },
    battery: {
      level_percent: null,
      estimated_range: null,
      charging_state: null,
      charge_limit_percent: null,
      time_to_full_minutes: null,
      power_kw: null,
    },
    climate: {
      inside_temp_c: null,
      outside_temp_c: null,
      is_climate_on: null,
    },
    closures: {
      all_closed: null,
      open: [],
      charge_port_open: null,
    },
  };
}

type LegacyEnrichedSnapshot = StoredVehicleSnapshot & {
  vehicle: StoredVehicleSnapshot["vehicle"] &
    Partial<StoredVehicleSnapshotDetails["vehicle"]>;
  battery: StoredVehicleSnapshot["battery"] &
    Partial<StoredVehicleSnapshotDetails["battery"]>;
  climate: StoredVehicleSnapshot["climate"] &
    Partial<StoredVehicleSnapshotDetails["climate"]>;
  closures: StoredVehicleSnapshot["closures"] &
    Partial<StoredVehicleSnapshotDetails["closures"]>;
};

const V1_VEHICLE_KEYS = [
  "key",
  "display_name",
  "model",
  "trim",
  "locked",
  "software_version",
  "odometer",
] as const;
const V1_BATTERY_KEYS = [
  "level_percent",
  "estimated_range",
  "charging_state",
  "charge_limit_percent",
  "time_to_full_minutes",
  "power_kw",
] as const;
const V1_CLIMATE_KEYS = [
  "inside_temp_c",
  "outside_temp_c",
  "is_climate_on",
] as const;
const V1_CLOSURE_KEYS = [
  "all_closed",
  "open",
  "charge_port_open",
] as const;

function hasRollbackSafeV1Groups(snapshot: StoredVehicleSnapshot): boolean {
  return hasExactKeys(snapshot.vehicle, V1_VEHICLE_KEYS)
    && hasExactKeys(snapshot.battery, V1_BATTERY_KEYS)
    && hasExactKeys(snapshot.climate, V1_CLIMATE_KEYS)
    && hasExactKeys(snapshot.closures, V1_CLOSURE_KEYS);
}

function hasExactKeys(
  value: object,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value);
  return actual.length === expected.length
    && expected.every((key) => Object.prototype.hasOwnProperty.call(value, key));
}

function snapshotDetails(
  snapshot: StoredVehicleSnapshot,
): StoredVehicleSnapshotDetails {
  const legacy = snapshot as LegacyEnrichedSnapshot;
  const vehicle = snapshot.details?.vehicle ?? legacy.vehicle;
  const battery = snapshot.details?.battery ?? legacy.battery;
  const climate = snapshot.details?.climate ?? legacy.climate;
  const closures = snapshot.details?.closures ?? legacy.closures;
  return {
    vehicle: {
      exterior_color: vehicle.exterior_color ?? null,
      wheel_type: vehicle.wheel_type ?? null,
      roof_color: vehicle.roof_color ?? null,
      charge_port_type: vehicle.charge_port_type ?? null,
      efficiency_package: vehicle.efficiency_package ?? null,
      rear_seat_heaters: vehicle.rear_seat_heaters ?? null,
      right_hand_drive: vehicle.right_hand_drive ?? null,
      europe_vehicle: vehicle.europe_vehicle ?? null,
      sunroof_installed: vehicle.sunroof_installed ?? null,
      sentry_mode: vehicle.sentry_mode ?? null,
      service_mode: vehicle.service_mode ?? null,
      valet_mode: vehicle.valet_mode ?? null,
      center_display_state: vehicle.center_display_state ?? null,
      speed_limit_mode: {
        active: vehicle.speed_limit_mode?.active ?? null,
        limit: vehicle.speed_limit_mode?.limit ?? null,
      },
    },
    battery: {
      usable_level_percent: battery.usable_level_percent ?? null,
      driving_range: battery.driving_range ?? null,
    },
    climate: {
      driver_target_temp_c: climate.driver_target_temp_c ?? null,
      passenger_target_temp_c: climate.passenger_target_temp_c ?? null,
      keeper_mode: climate.keeper_mode ?? null,
      defrost_mode: climate.defrost_mode ?? null,
      rear_defroster_on: climate.rear_defroster_on ?? null,
      battery_heater_on: climate.battery_heater_on ?? null,
      wiper_heater_on: climate.wiper_heater_on ?? null,
      hvac_auto_mode: climate.hvac_auto_mode ?? null,
      fan_status: climate.fan_status ?? null,
      steering_wheel_heat_level: climate.steering_wheel_heat_level ?? null,
      steering_wheel_heat_auto: climate.steering_wheel_heat_auto ?? null,
      seat_heaters: {
        front_left: climate.seat_heaters?.front_left ?? null,
        front_right: climate.seat_heaters?.front_right ?? null,
        rear_left: climate.seat_heaters?.rear_left ?? null,
        rear_right: climate.seat_heaters?.rear_right ?? null,
        rear_center: climate.seat_heaters?.rear_center ?? null,
      },
      seat_cooling: {
        front_left: climate.seat_cooling?.front_left ?? null,
        front_right: climate.seat_cooling?.front_right ?? null,
      },
      auto_seat_climate: {
        front_left: climate.auto_seat_climate?.front_left ?? null,
        front_right: climate.auto_seat_climate?.front_right ?? null,
      },
      cabin_overheat: {
        mode: climate.cabin_overheat?.mode ?? null,
        temp_limit: climate.cabin_overheat?.temp_limit ?? null,
      },
    },
    closures: {
      doors: {
        driver_front: closures.doors?.driver_front ?? null,
        driver_rear: closures.doors?.driver_rear ?? null,
        passenger_front: closures.doors?.passenger_front ?? null,
        passenger_rear: closures.doors?.passenger_rear ?? null,
        front_trunk: closures.doors?.front_trunk ?? null,
        rear_trunk: closures.doors?.rear_trunk ?? null,
      },
      windows: {
        driver_front: closures.windows?.driver_front ?? null,
        driver_rear: closures.windows?.driver_rear ?? null,
        passenger_front: closures.windows?.passenger_front ?? null,
        passenger_rear: closures.windows?.passenger_rear ?? null,
      },
    },
  };
}

function rollbackSafeSnapshot(
  snapshot: StoredVehicleSnapshot,
): StoredVehicleSnapshot {
  return {
    ...snapshot,
    vehicle: {
      key: snapshot.vehicle.key,
      display_name: snapshot.vehicle.display_name,
      model: snapshot.vehicle.model,
      trim: snapshot.vehicle.trim,
      locked: snapshot.vehicle.locked,
      software_version: snapshot.vehicle.software_version,
      odometer: snapshot.vehicle.odometer,
    },
    battery: {
      level_percent: snapshot.battery.level_percent,
      estimated_range: snapshot.battery.estimated_range,
      charging_state: snapshot.battery.charging_state,
      charge_limit_percent: snapshot.battery.charge_limit_percent,
      time_to_full_minutes: snapshot.battery.time_to_full_minutes,
      power_kw: snapshot.battery.power_kw,
    },
    climate: {
      inside_temp_c: snapshot.climate.inside_temp_c,
      outside_temp_c: snapshot.climate.outside_temp_c,
      is_climate_on: snapshot.climate.is_climate_on,
    },
    closures: {
      all_closed: snapshot.closures.all_closed,
      open: snapshot.closures.open,
      charge_port_open: snapshot.closures.charge_port_open,
    },
    details: snapshotDetails(snapshot),
  };
}

type OptionalTireDetails = {
  tires?: {
    pressures?: {
      front_left: { value: number; unit: "bar" } | null;
      front_right: { value: number; unit: "bar" } | null;
      rear_left: { value: number; unit: "bar" } | null;
      rear_right: { value: number; unit: "bar" } | null;
    };
    soft_warnings?: {
      front_left: boolean | null;
      front_right: boolean | null;
      rear_left: boolean | null;
      rear_right: boolean | null;
    };
    hard_warnings?: {
      front_left: boolean | null;
      front_right: boolean | null;
      rear_left: boolean | null;
      rear_right: boolean | null;
    };
  };
};

function toSummary(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  live: boolean,
  schemaVersion: VehicleSummarySchemaVersion,
  maxStaleMs: number,
): VehicleSummary {
  if (schemaVersion === 3) {
    return toSummaryV3(cached, nowMs, live, maxStaleMs);
  }
  return schemaVersion === 2
    ? toSummaryV2(cached, nowMs, live)
    : toSummaryV1(cached, nowMs, live);
}

function summarySnapshot(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  live: boolean,
): VehicleSummaryV1["snapshot"] {
  const snapshot = cached.snapshot;
  return {
    captured_at: new Date(snapshot.captured_at_ms).toISOString(),
    freshness: live
      ? "live"
      : cached.stale
        ? "stale_cache"
        : "fresh_cache",
    age_seconds: Math.max(
      0,
      Math.floor((nowMs - snapshot.captured_at_ms) / 1_000),
    ),
    vehicle_connectivity: snapshot.vehicle_connectivity,
  };
}

function toSummaryV1(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  live: boolean,
): VehicleSummaryV1 {
  const { vehicle, battery, climate, closures } = cached.snapshot;
  return {
    schema_version: 1,
    served_at: new Date(nowMs).toISOString(),
    snapshot: summarySnapshot(cached, nowMs, live),
    vehicle: {
      key: vehicle.key,
      display_name: vehicle.display_name,
      model: vehicle.model,
      trim: vehicle.trim,
      locked: vehicle.locked,
      software_version: vehicle.software_version,
      odometer: vehicle.odometer,
    },
    battery: {
      level_percent: battery.level_percent,
      estimated_range: battery.estimated_range,
      charging_state: battery.charging_state,
      charge_limit_percent: battery.charge_limit_percent,
      time_to_full_minutes: battery.time_to_full_minutes,
      power_kw: battery.power_kw,
    },
    climate: {
      inside_temp_c: climate.inside_temp_c,
      outside_temp_c: climate.outside_temp_c,
      is_climate_on: climate.is_climate_on,
    },
    closures: {
      all_closed: closures.all_closed,
      open: closures.open,
      charge_port_open: closures.charge_port_open,
    },
  };
}

function toSummaryV2(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  live: boolean,
): VehicleSummaryV2 {
  const snapshot = cached.snapshot;
  const { vehicle, battery, climate, closures } = snapshot;
  const details = snapshotDetails(snapshot);
  const vehicleDetails = details.vehicle;
  const batteryDetails = details.battery;
  const climateDetails = details.climate;
  const closureDetails = details.closures;
  const charging = snapshot.charging;
  const tires = (snapshot as StoredVehicleSnapshot & OptionalTireDetails).tires;
  const preferences = snapshot.preferences;
  const softwareUpdate = snapshot.software_update;
  const seatHeaters = climateDetails.seat_heaters;
  const seatCooling = climateDetails.seat_cooling;
  const autoSeatClimate = climateDetails.auto_seat_climate;
  const cabinOverheat = climateDetails.cabin_overheat;
  const chargingState =
    charging?.state === undefined
      ? battery.charging_state
      : charging.state;

  return {
    schema_version: 2,
    served_at: new Date(nowMs).toISOString(),
    snapshot: summarySnapshot(cached, nowMs, live),
    vehicle: {
      key: vehicle.key,
      display_name: vehicle.display_name,
      model: vehicle.model,
      trim: vehicle.trim,
      locked: vehicle.locked,
      software_version: vehicle.software_version,
      odometer: vehicle.odometer,
      exterior_color: vehicleDetails.exterior_color,
      wheel_type: vehicleDetails.wheel_type,
      roof_color: vehicleDetails.roof_color,
      charge_port_type: vehicleDetails.charge_port_type,
      efficiency_package: vehicleDetails.efficiency_package,
      rear_seat_heaters: vehicleDetails.rear_seat_heaters,
      right_hand_drive: vehicleDetails.right_hand_drive,
      europe_vehicle: vehicleDetails.europe_vehicle,
      sunroof_installed: vehicleDetails.sunroof_installed,
      sentry_mode: vehicleDetails.sentry_mode,
      service_mode: vehicleDetails.service_mode,
      valet_mode: vehicleDetails.valet_mode,
      center_display_state: vehicleDetails.center_display_state,
      speed_limit_mode: {
        active: vehicleDetails.speed_limit_mode.active,
        limit: vehicleDetails.speed_limit_mode.limit,
      },
    },
    battery: {
      level_percent: battery.level_percent,
      usable_level_percent: batteryDetails.usable_level_percent,
      rated_range: battery.estimated_range,
      estimated_range: batteryDetails.driving_range,
    },
    charging: {
      state: normalizeChargingState(chargingState),
      charge_limit_percent:
        charging?.charge_limit_percent === undefined
          ? battery.charge_limit_percent
          : charging.charge_limit_percent,
      time_to_full_minutes:
        charging?.time_to_full_minutes === undefined
          ? battery.time_to_full_minutes
          : charging.time_to_full_minutes,
      power_kw:
        charging?.power_kw === undefined
          ? battery.power_kw
          : charging.power_kw,
      energy_added_kwh: charging?.energy_added_kwh ?? null,
      rate: charging?.rate ?? null,
      actual_current_a: charging?.actual_current_a ?? null,
      voltage_v: charging?.voltage_v ?? null,
      phases: charging?.phases ?? null,
      requested_current_a: charging?.requested_current_a ?? null,
      max_current_a: charging?.max_current_a ?? null,
      enabled: charging?.enabled ?? null,
      cable_type: charging?.cable_type ?? null,
      fast_charger_present: charging?.fast_charger_present ?? null,
      fast_charger_type: charging?.fast_charger_type ?? null,
      port_latch: charging?.port_latch ?? null,
      port_cold_weather_mode: charging?.port_cold_weather_mode ?? null,
      preconditioning: charging?.preconditioning ?? null,
      not_enough_power_to_heat: charging?.not_enough_power_to_heat ?? null,
      supercharger_trip_planner: charging?.supercharger_trip_planner ?? null,
      scheduled: {
        pending: charging?.scheduled?.pending ?? null,
        mode: charging?.scheduled?.mode ?? null,
      },
    },
    climate: {
      inside_temp_c: climate.inside_temp_c,
      outside_temp_c: climate.outside_temp_c,
      is_climate_on: climate.is_climate_on,
      driver_target_temp_c: climateDetails.driver_target_temp_c,
      passenger_target_temp_c: climateDetails.passenger_target_temp_c,
      keeper_mode: climateDetails.keeper_mode,
      defrost_mode: climateDetails.defrost_mode,
      rear_defroster_on: climateDetails.rear_defroster_on,
      battery_heater_on: climateDetails.battery_heater_on,
      wiper_heater_on: climateDetails.wiper_heater_on,
      hvac_auto_mode: climateDetails.hvac_auto_mode,
      fan_status: climateDetails.fan_status,
      steering_wheel_heat_level: climateDetails.steering_wheel_heat_level,
      steering_wheel_heat_auto: climateDetails.steering_wheel_heat_auto,
      seat_heaters: {
        front_left: seatHeaters?.front_left ?? null,
        front_right: seatHeaters?.front_right ?? null,
        rear_left: seatHeaters?.rear_left ?? null,
        rear_right: seatHeaters?.rear_right ?? null,
        rear_center: seatHeaters?.rear_center ?? null,
      },
      seat_cooling: {
        front_left: seatCooling?.front_left ?? null,
        front_right: seatCooling?.front_right ?? null,
      },
      auto_seat_climate: {
        front_left: autoSeatClimate?.front_left ?? null,
        front_right: autoSeatClimate?.front_right ?? null,
      },
      cabin_overheat: {
        mode: cabinOverheat?.mode ?? null,
        temp_limit: cabinOverheat?.temp_limit ?? null,
      },
    },
    closures: {
      all_closed: closures.all_closed,
      open: closures.open,
      charge_port_open: closures.charge_port_open,
      doors: {
        driver_front: closureDetails.doors.driver_front,
        driver_rear: closureDetails.doors.driver_rear,
        passenger_front: closureDetails.doors.passenger_front,
        passenger_rear: closureDetails.doors.passenger_rear,
        front_trunk: closureDetails.doors.front_trunk,
        rear_trunk: closureDetails.doors.rear_trunk,
      },
      windows: {
        driver_front: closureDetails.windows.driver_front,
        driver_rear: closureDetails.windows.driver_rear,
        passenger_front: closureDetails.windows.passenger_front,
        passenger_rear: closureDetails.windows.passenger_rear,
      },
    },
    tires: {
      pressures: {
        front_left: tires?.pressures?.front_left ?? null,
        front_right: tires?.pressures?.front_right ?? null,
        rear_left: tires?.pressures?.rear_left ?? null,
        rear_right: tires?.pressures?.rear_right ?? null,
      },
      soft_warnings: {
        front_left: tires?.soft_warnings?.front_left ?? null,
        front_right: tires?.soft_warnings?.front_right ?? null,
        rear_left: tires?.soft_warnings?.rear_left ?? null,
        rear_right: tires?.soft_warnings?.rear_right ?? null,
      },
      hard_warnings: {
        front_left: tires?.hard_warnings?.front_left ?? null,
        front_right: tires?.hard_warnings?.front_right ?? null,
        rear_left: tires?.hard_warnings?.rear_left ?? null,
        rear_right: tires?.hard_warnings?.rear_right ?? null,
      },
    },
    software_update: {
      version: softwareUpdate?.version ?? null,
      download_percent: softwareUpdate?.download_percent ?? null,
      install_percent: softwareUpdate?.install_percent ?? null,
      expected_duration_minutes:
        softwareUpdate?.expected_duration_minutes ?? null,
    },
    preferences: {
      distance_unit: preferences?.distance_unit ?? null,
      temperature_unit: preferences?.temperature_unit ?? null,
      pressure_unit: preferences?.pressure_unit ?? null,
      charge_display_unit: preferences?.charge_display_unit ?? null,
      use_24_hour_time: preferences?.use_24_hour_time ?? null,
    },
  };
}

function toSummaryV3(
  cached: CachedVehicleSnapshot,
  nowMs: number,
  live: boolean,
  maxStaleMs: number,
): VehicleSummaryV3 {
  const versionTwo = toSummaryV2(cached, nowMs, live);
  const location = validStoredLocation(cached.snapshot.location);
  const rawAgeMs = location
    ? nowMs - location.captured_at_ms
    : Number.POSITIVE_INFINITY;
  return {
    ...versionTwo,
    schema_version: 3,
    location:
      location && rawAgeMs >= -5 * 60_000 && rawAgeMs <= maxStaleMs
        ? {
            captured_at: new Date(location.captured_at_ms).toISOString(),
            age_seconds: Math.max(0, Math.floor(rawAgeMs / 1_000)),
            latitude: location.latitude,
            longitude: location.longitude,
          }
        : null,
  };
}

function validStoredLocation(
  value: StoredVehicleSnapshot["location"],
): NonNullable<StoredVehicleSnapshot["location"]> | null {
  if (
    !value ||
    !Number.isFinite(value.captured_at_ms) ||
    value.captured_at_ms < 0 ||
    !Number.isFinite(value.latitude) ||
    value.latitude < -90 ||
    value.latitude > 90 ||
    !Number.isFinite(value.longitude) ||
    value.longitude < -180 ||
    value.longitude > 180
  ) {
    return null;
  }
  return value;
}

function preserveLastKnownLocation(
  next: StoredVehicleSnapshot,
  previous: StoredVehicleSnapshot | undefined,
  nowMs: number,
  maxStaleMs: number,
): StoredVehicleSnapshot {
  const usableLocation = (
    value: StoredVehicleSnapshot["location"],
  ): NonNullable<StoredVehicleSnapshot["location"]> | null => {
    const location = validStoredLocation(value);
    if (!location) {
      return null;
    }
    const ageMs = nowMs - location.captured_at_ms;
    return ageMs >= -5 * 60_000 && ageMs <= maxStaleMs ? location : null;
  };
  const nextLocation = usableLocation(next.location);
  const previousLocation = usableLocation(previous?.location);
  const newestLocation =
    nextLocation &&
    (!previousLocation ||
      nextLocation.captured_at_ms >= previousLocation.captured_at_ms)
      ? nextLocation
      : previousLocation;
  if (newestLocation) {
    return { ...next, location: { ...newestLocation } };
  }
  return next.location === undefined ? next : { ...next, location: null };
}

function normalizeChargingState(value: string | null): string | null {
  if (value === null) {
    return null;
  }
  const normalized = value.trim().toLowerCase().replace(/[^a-z]/g, "");
  const states: Record<string, string> = {
    disconnected: "disconnected",
    nopower: "no_power",
    starting: "starting",
    charging: "charging",
    complete: "complete",
    stopped: "stopped",
    unknown: "unknown",
  };
  return states[normalized] ?? (normalized ? "unknown" : null);
}

export { SqliteVehicleSessionRepository } from "./vehicle-session-repository";
