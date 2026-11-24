import { expect, test } from "@playwright/test";

const email = process.env.E2E_EMAIL || "";
const password = process.env.E2E_PASSWORD || "";
const runName = process.env.E2E_RUN_NAME || "";
const sweepName = process.env.E2E_SWEEP_NAME || "";
const modelName = process.env.E2E_MODEL_NAME || "";
const mvId = process.env.E2E_MV_ID || "";
const benchName = process.env.E2E_BENCH_NAME || "";

test("canonical run -> sweep -> registry promote -> lineage -> simulated badge", async ({ page }) => {
  expect(email && password && runName && sweepName && modelName && mvId && benchName).toBeTruthy();

  // 1. Sign in via UI form
  await page.goto("/login");
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('button[type="submit"]').click();
  await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });

  // 2. Canonical training run created by SDK
  await page.goto("/training");
  await expect(page.getByText(runName).first()).toBeVisible({ timeout: 15_000 });

  // 3. Sweep list -> sweep detail with trials & SVG plots
  await page.goto("/sweeps");
  const sweepLink = page.locator('a[href^="/sweeps/"]', { hasText: sweepName }).first();
  await expect(sweepLink).toBeVisible({ timeout: 15_000 });
  await sweepLink.click();
  await expect(page).toHaveURL(/\/sweeps\/.+/, { timeout: 15_000 });
  await expect(page.getByText("Parallel Coordinates")).toBeVisible({ timeout: 15_000 });

  // 4. Model Registry -> promote v1 to production via UI modal
  await page.goto("/models");
  const modelButton = page.getByRole("button", { name: modelName }).first();
  await expect(modelButton).toBeVisible({ timeout: 15_000 });
  await modelButton.click();

  await page.getByRole("button", { name: "Promote Stage" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible({ timeout: 10_000 });
  await dialog.locator('input[name="targetStage"][value="production"]').check();
  await dialog.getByRole("button", { name: "Set Stage to Production" }).click();
  await expect(dialog).toBeHidden({ timeout: 15_000 });
  const versionRow = page.locator("table tbody tr", { hasText: "v1" }).first();
  await expect(versionRow.getByText("Production", { exact: true })).toBeVisible({ timeout: 15_000 });

  // 5. Lineage Explorer renders bounded graph rooted at model version
  await page.goto(`/lineage?node=${encodeURIComponent(`model_version:${mvId}`)}`);
  const graphRegion = page.getByRole("region", { name: "Model lineage directed acyclic graph" });
  await expect(graphRegion).toBeVisible({ timeout: 15_000 });
  await expect(graphRegion.getByText(runName).first()).toBeVisible({ timeout: 15_000 });
  await expect(graphRegion.getByText("adapter.safetensors:v1").first()).toBeVisible({ timeout: 15_000 });

  // 6. Simulated area explicitly displays the "Simulated" badge and canonical summary_metrics.tasks score
  await page.goto("/benchmarks");
  const benchRow = page.locator("table tbody tr", { hasText: benchName }).first();
  await expect(benchRow).toBeVisible({ timeout: 15_000 });
  await expect(benchRow.getByText("Simulated", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(benchRow.getByText(/^\d+\.\d+%$/).first()).toBeVisible({ timeout: 15_000 });
});
