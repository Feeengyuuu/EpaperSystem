import { env } from "cloudflare:workers";
import {
  evictDurableObject,
  reset,
  runInDurableObject,
} from "cloudflare:test";
import { afterEach, describe, expect, test } from "vitest";

import { VehicleAuthState } from "../src/vehicle-auth-state";
import {
  SqliteVehicleSessionRepository,
  type CachedVehicleSnapshot,
  type StoredAuthorization,
} from "../src/vehicle-session-core";

const NOW = 1_800_000_000_000;
const AUTHORIZATION: StoredAuthorization = {
  generation: 4,
  account_generation: 3,
  reauthorization_required: false,
  tokens: {
    access_token: "sensitive-access-token",
    refresh_token: "sensitive-refresh-token",
    access_expires_at: NOW + 3_600_000,
    scopes: ["offline_access", "openid", "vehicle_device_data"],
  },
};
const CACHED_SNAPSHOT: CachedVehicleSnapshot = {
  account_generation: 3,
  stale: false,
  snapshot: {
    captured_at_ms: NOW - 60_000,
    checked_at_ms: NOW - 60_000,
    vehicle_connectivity: "online",
    vehicle: {
      key: "primary",
      display_name: "Old account vehicle",
      model: "Model Y",
      trim: null,
      locked: true,
      software_version: null,
      odometer: null,
    },
    battery: {
      level_percent: 75,
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
  },
};

afterEach(async () => {
  await reset();
});

describe("SQLite Durable Object authority", () => {
  test("consumes an OAuth launch and browser-bound state only once", async () => {
    const stub = env.VEHICLE_AUTH.getByName("oauth-state-test");
    await stub.createOAuthLaunch("launch-hash", NOW + 120_000);

    expect(
      await stub.beginAuthorization({
        launch_hash: "launch-hash",
        state_hash: "state-hash",
        browser_hash: "browser-hash",
        expires_at: NOW + 600_000,
        now_ms: NOW,
      }),
    ).toBe(true);
    expect(
      await stub.beginAuthorization({
        launch_hash: "launch-hash",
        state_hash: "other-state-hash",
        browser_hash: "browser-hash",
        expires_at: NOW + 600_000,
        now_ms: NOW,
      }),
    ).toBe(false);

    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const repository = new SqliteVehicleSessionRepository(
          state.storage,
          env.TOKEN_ENCRYPTION_KEY_V1,
        );
        expect(
          await repository.consumeAuthorization(
            "state-hash",
            "wrong-browser-hash",
            NOW,
          ),
        ).toBe(false);
        expect(
          await repository.consumeAuthorization(
            "state-hash",
            "browser-hash",
            NOW,
          ),
        ).toBe(true);
        expect(
          await repository.consumeAuthorization(
            "state-hash",
            "browser-hash",
            NOW,
          ),
        ).toBe(false);
      },
    );
  });

  test("stores tokens as AES-GCM ciphertext and restores them after eviction", async () => {
    const stub = env.VEHICLE_AUTH.getByName("encrypted-token-test");
    await stub.createOAuthLaunch("initializer", NOW + 120_000);

    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const repository = new SqliteVehicleSessionRepository(
          state.storage,
          env.TOKEN_ENCRYPTION_KEY_V1,
        );
        await repository.putAuthorization(AUTHORIZATION);
        const row = state.storage.sql
          .exec<{ value: string }>(
            "SELECT value FROM protected_state WHERE key = ?",
            "authorization",
          )
          .one();
        expect(row.value).not.toContain("sensitive-access-token");
        expect(row.value).not.toContain("sensitive-refresh-token");
        expect(JSON.parse(row.value)).toMatchObject({
          key_version: 1,
          generation: 4,
        });
        expect(await repository.getAuthorization()).toEqual(AUTHORIZATION);
      },
    );

    await evictDurableObject(stub);

    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const repository = new SqliteVehicleSessionRepository(
          state.storage,
          env.TOKEN_ENCRYPTION_KEY_V1,
        );
        expect(await repository.getAuthorization()).toEqual(AUTHORIZATION);
      },
    );
  });

  test("refuses to decrypt protected state with the wrong application key", async () => {
    const stub = env.VEHICLE_AUTH.getByName("wrong-key-test");
    await stub.createOAuthLaunch("initializer", NOW + 120_000);

    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const repository = new SqliteVehicleSessionRepository(
          state.storage,
          env.TOKEN_ENCRYPTION_KEY_V1,
        );
        await repository.putAuthorization(AUTHORIZATION);
        const wrongKey = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE";
        const wrongRepository = new SqliteVehicleSessionRepository(
          state.storage,
          wrongKey,
        );
        await expect(wrongRepository.getAuthorization()).rejects.toThrow(
          "protected_state_decrypt_failed",
        );
      },
    );
  });

  test("atomically replaces authorization and removes the previous account cache", async () => {
    const stub = env.VEHICLE_AUTH.getByName("atomic-account-switch-test");
    await stub.createOAuthLaunch("initializer", NOW + 120_000);

    await runInDurableObject(
      stub,
      async (_instance: VehicleAuthState, state) => {
        const repository = new SqliteVehicleSessionRepository(
          state.storage,
          env.TOKEN_ENCRYPTION_KEY_V1,
        );
        const replacement: StoredAuthorization = {
          ...AUTHORIZATION,
          generation: 5,
          account_generation: 4,
          tokens: {
            ...AUTHORIZATION.tokens,
            access_token: "replacement-access-token",
            refresh_token: "replacement-refresh-token",
          },
        };
        await repository.putAuthorization(AUTHORIZATION);
        await repository.putCachedSnapshot(CACHED_SNAPSHOT);
        state.storage.sql.exec(`
          CREATE TRIGGER fail_vehicle_summary_delete
          BEFORE DELETE ON protected_state
          WHEN OLD.key = 'vehicle_summary'
          BEGIN
            SELECT RAISE(ABORT, 'forced atomic rollback');
          END
        `);

        await expect(
          repository.replaceAuthorizationAndClearSnapshot(replacement),
        ).rejects.toThrow();
        state.storage.sql.exec("DROP TRIGGER fail_vehicle_summary_delete");
        expect(await repository.getAuthorization()).toEqual(AUTHORIZATION);
        expect(await repository.getCachedSnapshot()).toEqual(CACHED_SNAPSHOT);

        await repository.replaceAuthorizationAndClearSnapshot(replacement);
        expect(await repository.getAuthorization()).toEqual(replacement);
        expect(await repository.getCachedSnapshot()).toBeNull();
      },
    );
  });
});
