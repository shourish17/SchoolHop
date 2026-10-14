import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/layout",
  use: { browserName: "chromium", hasTouch: true, trace: "retain-on-failure" },
});
