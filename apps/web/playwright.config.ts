import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  timeout: 60_000,
  expect: {
    timeout: 15_000,
  },
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:3100",
    trace: "off",
    screenshot: "off",
    video: "off",
  },
});
