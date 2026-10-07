import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

process.env.BROWSER_PASSWORD ??= "test-browser-password-not-production";
process.env.PUBLISH_KEY ??= "test-publish-key-not-production-32-bytes";
process.env.SESSION_SECRET ??= "test-session-secret-not-production-32-bytes";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      miniflare: {
        bindings: {
          BROWSER_PASSWORD: "test-browser-password-not-production",
          PUBLISH_KEY: "test-publish-key-not-production-32-bytes",
          SESSION_SECRET: "test-session-secret-not-production-32-bytes",
        },
      },
    }),
  ],
  test: {
    include: ["test/**/*.test.ts"],
  },
});
