// T9 Accessibility (WCAG 2.2 AA via axe-core) & Screenshot verification against MATRIX_MOCK=1.
//
// Usage:
//   cd apps/web && npm run build
//   MATRIX_MOCK=1 MATRIX_WEB_ORIGIN=http://localhost:3100 MATRIX_INSECURE_COOKIES=1 \
//     npm run start -- -p 3100 &
//   node e2e/t9-a11y-shots.cjs [baseUrl] [screenshotDir]
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

const BASE = process.argv[2] || "http://localhost:3100";
const OUT =
  process.argv[3] ||
  path.join(__dirname, "../../../docs/atlas/2026-11-17-reconciliacao/screenshots");

fs.mkdirSync(OUT, { recursive: true });

let failed = 0;
function check(ok, msg) {
  console.log(`${ok ? "PASS" : "FAIL"}  ${msg}`);
  if (!ok) failed++;
}

async function auditA11y(page, label) {
  await page.waitForFunction(() => document.title.trim().length > 0);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    .analyze();
  const severe = results.violations.filter(
    (v) => v.impact === "critical" || v.impact === "serious",
  );
  if (results.violations.length > 0) {
    console.log(`  [axe ${label}] violations (${results.violations.length} total, ${severe.length} critical/serious):`);
    for (const v of results.violations) {
      const sample = v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ");
      console.log(`    - [${v.impact}] ${v.id}: ${v.help} (${sample})`);
    }
  }
  check(severe.length === 0, `axe WCAG 2.2 AA (0 critical/serious) on ${label}`);
  return results.violations;
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: false });
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  page.setDefaultTimeout(15000);

  // Login in mock mode
  await page.goto(`${BASE}/login`);
  await page.fill('input[name="email"]', "demo@example.com");
  await page.fill('input[name="password"]', "demo-password-123");
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => new URL(u).pathname === "/projects");
  await page.click('li:has-text("Support Copilot") button:has-text("Open")');
  await page.waitForURL((u) => !new URL(u).pathname.startsWith("/projects"));
  await page.waitForSelector("main");

  // 1. /sweeps
  await page.goto(`${BASE}/sweeps`);
  await page.waitForSelector("text=llama3-support-lora-tpe");
  await auditA11y(page, "/sweeps");
  await shot(page, "t9-01-sweeps-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "t9-01-sweeps-light");
  await page.emulateMedia({ colorScheme: "dark" });

  // 2. /sweeps/swp-tpe-01
  await page.goto(`${BASE}/sweeps/swp-tpe-01`);
  await page.waitForSelector("text=run-lora-01");
  await auditA11y(page, "/sweeps/swp-tpe-01");
  await shot(page, "t9-02-sweep-detail-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "t9-02-sweep-detail-light");
  await page.emulateMedia({ colorScheme: "dark" });

  // 3. /models (Versions, History, Webhooks)
  await page.goto(`${BASE}/models`);
  await page.waitForSelector("text=support-copilot-lora");
  await auditA11y(page, "/models (versions)");
  await shot(page, "t9-03-models-versions-dark");

  await page.goto(`${BASE}/models?tab=history`);
  await page.waitForSelector("text=Stage Transition Timeline");
  await auditA11y(page, "/models?tab=history");
  await shot(page, "t9-04-models-history-dark");

  await page.goto(`${BASE}/models?tab=webhooks`);
  await page.waitForSelector("text=CI/CD Webhooks");
  await auditA11y(page, "/models?tab=webhooks");
  await shot(page, "t9-05-models-webhooks-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "t9-05-models-webhooks-light");
  await page.emulateMedia({ colorScheme: "dark" });

  // 4. /lineage (normal + truncated)
  await page.goto(`${BASE}/lineage?node=model_version:mv-lora-v3&direction=both&depth=3`);
  await page.waitForSelector("text=Lineage Explorer");
  await page.waitForTimeout(400);
  await auditA11y(page, "/lineage");
  await shot(page, "t9-06-lineage-dark");
  await page.emulateMedia({ colorScheme: "light" });
  await shot(page, "t9-06-lineage-light");
  await page.emulateMedia({ colorScheme: "dark" });

  await page.goto(`${BASE}/lineage?node=model_version:mv-lora-v3&direction=both&depth=1`);
  await page.waitForSelector('[data-testid="lineage-truncated-banner"]');
  await auditA11y(page, "/lineage (truncated)");
  await shot(page, "t9-07-lineage-truncated-dark");

  // 5. /training (canonical runs + sweeps adapter)
  await page.goto(`${BASE}/training`);
  await page.waitForSelector("text=Training Studio");
  await auditA11y(page, "/training");
  await shot(page, "t9-08-training-dark");

  // 6. Simulated & Safety screens
  for (const [route, shotName, marker] of [
    ["/safety", "t9-09-safety-dark", "OWASP"],
    ["/benchmarks", "t9-10-benchmarks-dark", "Llama-3.1-8B-Instruct"],
    ["/sandboxes", "t9-11-sandboxes-dark", "Sandboxes"],
    ["/gateway", "t9-12-gateway-dark", "AI Gateway"],
    ["/vectors", "t9-13-vectors-dark", "Simulated"],
  ]) {
    await page.goto(`${BASE}${route}`);
    await page.waitForSelector(`text=${marker}`);
    await auditA11y(page, route);
    await shot(page, shotName);
  }

  await browser.close();
  console.log(`\nSummary: ${failed} failed check(s).`);
  process.exit(failed > 0 ? 1 : 0);
})();
