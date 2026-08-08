import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

process.env.TESLA_CLIENT_ID ??= "test-client-id";
process.env.TESLA_CLIENT_SECRET ??= "test-client-secret";
process.env.BRIDGE_ADMIN_TOKEN ??= "test-admin-token";
process.env.BRIDGE_READ_TOKEN ??= "test-read-token";
process.env.TOKEN_ENCRYPTION_KEY_V1 ??=
  "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          TESLA_CLIENT_ID: "test-client-id",
          TESLA_CLIENT_SECRET: "test-client-secret",
          BRIDGE_ADMIN_TOKEN: "test-admin-token",
          BRIDGE_READ_TOKEN: "test-read-token",
          TOKEN_ENCRYPTION_KEY_V1:
            "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        },
      },
    }),
  ],
});
