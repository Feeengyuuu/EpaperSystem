import { TESLA_PUBLIC_KEY_PEM } from "./public-key";
import { createTeslaPartnerTokenRequester } from "./tesla";
import { createWorker } from "./worker";

const requestPartnerToken = createTeslaPartnerTokenRequester();

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const worker = createWorker({
      publicKeyPem: TESLA_PUBLIC_KEY_PEM,
      probeAuthToken: env.PROBE_AUTH_TOKEN,
      teslaCredentials: {
        clientId: env.TESLA_CLIENT_ID,
        clientSecret: env.TESLA_CLIENT_SECRET,
        audience: env.TESLA_AUDIENCE,
      },
      requestPartnerToken,
    });

    return worker.fetch(request);
  },
} satisfies ExportedHandler<Env>;
