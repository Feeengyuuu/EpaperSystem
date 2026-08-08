import { PUBLIC_KEY_PEM } from "./public-key";
import { createWorker } from "./worker";

export { VehicleAuthState } from "./vehicle-auth-state";

export default {
  fetch(request: Request, env: Env): Promise<Response> {
    const session = env.VEHICLE_AUTH.getByName("owner-v1");
    const worker = createWorker({
      publicKeyPem: PUBLIC_KEY_PEM,
      bridgeAdminToken: env.BRIDGE_ADMIN_TOKEN,
      bridgeReadToken: env.BRIDGE_READ_TOKEN,
      teslaClientId: env.TESLA_CLIENT_ID,
      teslaAudience: env.TESLA_AUDIENCE,
      teslaScopes: env.TESLA_OAUTH_SCOPES,
      oauthLaunchTtlSeconds: positiveInteger(
        env.OAUTH_LAUNCH_TTL_SECONDS,
        120,
      ),
      oauthSessionTtlSeconds: positiveInteger(
        env.OAUTH_SESSION_TTL_SECONDS,
        600,
      ),
      vehicleSummaryTtlSeconds: positiveInteger(
        env.VEHICLE_SUMMARY_TTL_SECONDS,
        900,
      ),
      session,
      appOrigin: `https://${env.TESLA_APP_DOMAIN}`,
      teslaRedirectUri: env.TESLA_REDIRECT_URI,
    });
    return worker.fetch(request);
  },
} satisfies ExportedHandler<Env>;

function positiveInteger(value: string, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}
