import { DurableObject } from "cloudflare:workers";

import type {
  BeginAuthorizationInput,
  CompleteAuthorizationInput,
  OAuthCompletion,
  VehicleSummaryResult,
  VehicleSummarySchemaVersion,
} from "./contracts";
import { createTeslaUserClient } from "./tesla-user";
import { VehicleSessionCore } from "./vehicle-session-core";
import {
  migrateVehicleAuthStorage,
  SqliteVehicleSessionRepository,
} from "./vehicle-session-repository";

export class VehicleAuthState extends DurableObject<Env> {
  readonly #core: VehicleSessionCore;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.blockConcurrencyWhile(async () => {
      migrateVehicleAuthStorage(ctx.storage);
    });
    const repository = new SqliteVehicleSessionRepository(
      ctx.storage,
      env.TOKEN_ENCRYPTION_KEY_V1,
    );
    this.#core = new VehicleSessionCore({
      repository,
      tesla: createTeslaUserClient({
        clientId: env.TESLA_CLIENT_ID,
        clientSecret: env.TESLA_CLIENT_SECRET,
        audience: env.TESLA_AUDIENCE,
        requiredScopes: env.TESLA_OAUTH_SCOPES.split(/\s+/).filter(Boolean),
      }),
      maxStaleSeconds: positiveInteger(
        env.VEHICLE_SUMMARY_MAX_STALE_SECONDS,
        86_400,
      ),
      diagnostic: (event) => console.log(JSON.stringify(event)),
    });
  }

  async createOAuthLaunch(
    launchHash: string,
    expiresAt: number,
  ): Promise<void> {
    await this.#core.createOAuthLaunch(launchHash, expiresAt);
  }

  async beginAuthorization(input: BeginAuthorizationInput): Promise<boolean> {
    return this.#core.beginAuthorization(input);
  }

  async completeAuthorization(
    input: CompleteAuthorizationInput,
  ): Promise<OAuthCompletion> {
    return this.#core.completeAuthorization(input);
  }

  async getVehicleSummary(
    nowMs: number,
    cacheTtlSeconds: number,
    schemaVersion: VehicleSummarySchemaVersion = 1,
  ): Promise<VehicleSummaryResult> {
    return this.#core.getVehicleSummary(nowMs, cacheTtlSeconds, schemaVersion);
  }
}

function positiveInteger(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}
