import { defineConfig } from "@playwright/test";

/**
 * These are production-shaped public regression checks. They intentionally
 * avoid private records and authenticated flows, while catching the layout
 * breaks that are most costly on the shared project surfaces.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: process.env.PUBLIC_PLATFORM_BASE_URL || "https://finance.gordongouger.com",
    headless: true,
    trace: "retain-on-failure",
  },
  reporter: "list",
});
