// End-to-end check of the G1 web (training runs) against the mock.
//
//   cd apps/web && npm run build
//   MATRIX_MOCK=1 MATRIX_MOCK_LATENCY_MS=300 npm run start -- -p 3100 &      # server-side latency too
//   NODE_PATH=$(npm root -g) node e2e/g1.cjs [baseUrl] [screenshotDir]
//
// Covers /runs (filters, sort, selection → compare), the run detail tabs (charts with smoothing,
// log scale and time axis; system telemetry with 2 GPUs; artifacts; ROC/PR/confusion, regression
// and importance reports) and, with 300 ms on every browser request, that tab and filter clicks
// always land while the running-run poller is active. Exit code 1 on any failed check.
const { chromium } = require("playwright");
const path = require("node:path");

const BASE = process.argv[2] || "http://localhost:3100";
const OUT = process.argv[3] || path.join(__dirname, "../../../docs/atlas/2026-11-10-plataforma-completa/screenshots");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (ok, what) => {
  console.log(`${ok ? "PASS" : "FAIL"}  ${what}`);
  if (!ok) failed++;
};
const shot = (page, name, opts = {}) => page.screenshot({ path: path.join(OUT, `g1-${name}.png`), fullPage: false, ...opts });
const canvases = (page) => page.locator("main figure canvas").count();
const waitCharts = (page, n) => page.waitForFunction((min) => document.querySelectorAll("main figure canvas").length >= min, n, { timeout: 20000 });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(20000);
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // Login (mock demo user)
  await page.goto(`${BASE}/login?next=/runs`);
  await page.fill('input[name="email"]', "demo@example.com");
  await page.fill('input[name="password"]', "demo-password-123");
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => new URL(u).pathname !== "/login");
  if (new URL(page.url()).pathname.startsWith("/projects")) {
    await page.click('li:has-text("Support Copilot") button:has-text("Open")');
    await page.waitForURL((u) => !new URL(u).pathname.startsWith("/projects"));
  }
  if (new URL(page.url()).pathname !== "/runs") await page.goto(`${BASE}/runs`);
  check(new URL(page.url()).pathname === "/runs", "logged in (owner project) and on /runs");

  // 1. Runs table
  await page.waitForSelector("main table tbody tr");
  const rows = await page.locator("main table tbody tr").count();
  check(rows === 12, `runs table lists 12 runs (${rows})`);
  check((await page.locator('nav[aria-label="Primary"] a[aria-current="page"]').textContent()).trim() === "Runs", "Runs is active in the navigation");
  check((await page.locator('[role="status"]:has-text("auto-refresh")').count()) === 1, "poller is on while a run is running");
  await shot(page, "01-runs");

  // Filters in the URL
  await page.click('nav[aria-label="Status"] a:has-text("failed")');
  await page.waitForURL(/status=failed/);
  await page.waitForFunction(() => document.querySelectorAll("main table tbody tr").length === 2);
  check(true, "status=failed filter shows 2 runs");
  await page.click('nav[aria-label="Status"] a:has-text("all")');
  await page.waitForURL((u) => !new URL(u).searchParams.has("status"));
  await page.fill('input[name="q"]', "resnet");
  await page.click('form[role="search"] button[type="submit"]');
  await page.waitForURL(/q=resnet/);
  await page.waitForFunction(() => document.querySelectorAll("main table tbody tr").length === 4);
  check(true, "q=resnet shows the 4 sweep runs");

  // Columns chooser (persisted)
  await page.click('summary:has-text("Columns")');
  await page.check('fieldset:has(legend:has-text("Parameters")) label:has-text("momentum") input');
  await page.click('summary:has-text("Columns")');
  check((await page.locator('thead th:has-text("momentum")').count()) === 1, "chosen param column appears");

  // Selection → compare
  const boxes = page.locator('tbody input[type="checkbox"]');
  for (let i = 0; i < 3; i++) await boxes.nth(i).check();
  await page.waitForSelector('a:has-text("Compare (3)")');
  await shot(page, "02-runs-selected");
  await page.click('a:has-text("Compare (3)")');
  await page.waitForURL(/\/runs\/compare\?ids=/);
  await waitCharts(page, 4);
  check((await page.locator("#cmp-params ~ * tbody tr, section[aria-labelledby=cmp-params] tbody tr").count()) >= 1, "param diff lists the differing params");
  check((await page.locator('section[aria-labelledby="cmp-params"] tbody th:has-text("lr")').count()) === 1, "lr is in the param diff");
  check((await page.locator('section[aria-labelledby="cmp-metrics"] [aria-label="best"]').count()) > 0, "best final values are marked");
  await sleep(500);
  await shot(page, "03-compare");
  const legend = page.locator('[role="group"][aria-label^="Runs in this comparison"] button').first();
  await legend.click();
  check((await legend.getAttribute("aria-pressed")) === "false", "clicking a legend entry hides the run");
  await legend.click();
  await page.locator('section[aria-labelledby="cmp-params"]').scrollIntoViewIfNeeded();
  await shot(page, "04-compare-tables");
  await page.goto(`${BASE}/runs`); // a fresh page reads the stored column choice
  await page.waitForSelector("main table tbody tr");
  check((await page.locator('thead th:has-text("momentum")').count()) === 1, "column choice persisted in localStorage");

  // 2. Detail: overview of the running DPO run
  await page.goto(`${BASE}/runs/run_llama3_dpo`);
  await page.waitForSelector("text=Parameters");
  check((await page.textContent("main")).includes("dirty"), "git dirty flag shown");
  await page.fill('input[aria-label="Search packages"]', "torch");
  check((await page.locator('ul li:has-text("torchvision")').count()) === 1, "package search filters the environment");
  await shot(page, "05-run-overview");

  // Charts: smoothing, log, time axis
  await page.click('nav[aria-label="Run sections"] a:has-text("Charts")');
  await page.waitForURL(/tab=charts/);
  await waitCharts(page, 6);
  check((await page.locator('main h2:has-text("train/")').count()) === 1 && (await page.locator('main h2:has-text("rewards/")').count()) === 1, "keys grouped by prefix (train/, rewards/)");
  await page.locator('input[type="range"]').fill("0.9");
  await page.click('button:has-text("Log Y")');
  check((await page.locator('button:has-text("Log Y")').getAttribute("aria-pressed")) === "true", "log scale toggled");
  await sleep(600);
  await shot(page, "06-charts-smooth-log");
  await page.click('[role="group"][aria-label="X axis"] button:has-text("time")');
  await page.click('button:has-text("One chart per group")');
  await sleep(1200);
  await shot(page, "07-charts-time-grouped");
  await page.click('button:has-text("One chart per group")');
  await page.click('button:has-text("Log Y")');
  await page.click('[role="group"][aria-label="X axis"] button:has-text("step")');

  // System
  await page.click('nav[aria-label="Run sections"] a:has-text("System")');
  await page.waitForURL(/tab=system/);
  await page.waitForSelector('h2:has-text("GPU 1")');
  await waitCharts(page, 10);
  check(true, "system tab shows host panels and 2 GPUs");
  await sleep(500);
  await shot(page, "08-system");

  // Artifacts
  await page.click('nav[aria-label="Run sections"] a:has-text("Artifacts")');
  await page.waitForSelector('a:has-text("Download")');
  const dl = await page.request.get(`${BASE}${await page.locator('a:has-text("Download")').first().getAttribute("href")}`);
  check(dl.ok(), "artifact download answers 200");
  await shot(page, "09-artifacts");

  // Reports: classification + importances
  await page.goto(`${BASE}/runs/run_churn_gbm?tab=reports`);
  await page.waitForSelector('figcaption:has-text("ROC")');
  await waitCharts(page, 3);
  for (const t of ["ROC", "Precision–recall", "Confusion matrix"]) check((await page.locator(`figcaption:has-text("${t}")`).count()) >= 1, `${t} chart is rendered`);
  check((await page.textContent("main")).includes("AUC"), "legend/description carries the AUC");
  await sleep(500);
  await shot(page, "10-reports-classification");
  await page.click('button:has-text("Normalize rows")');
  check((await page.locator('figcaption:has-text("(counts)")').count()) === 1, "confusion matrix switches to counts");
  await page.locator('section:has(h2:has-text("permutation importance"))').scrollIntoViewIfNeeded();
  await sleep(400);
  await shot(page, "11-reports-importance");
  await page.locator('section:has(h2:has-text("worst predictions"))').scrollIntoViewIfNeeded();
  check((await page.locator("text=truncated: the full table is an artifact").count()) === 1, "truncated table is flagged");

  // Regression
  await page.goto(`${BASE}/runs/run_house_rf?tab=reports`);
  await page.waitForSelector('figcaption:has-text("Predicted vs true")');
  await waitCharts(page, 2);
  await sleep(500);
  await shot(page, "12-reports-regression");

  // Failed run and a run without telemetry
  await page.goto(`${BASE}/runs/run_resnet_3e3`);
  await page.waitForSelector("text=Run failed");
  await shot(page, "13-run-failed");
  await page.goto(`${BASE}/runs/run_whisper_ft?tab=system`);
  await page.waitForSelector("text=No system telemetry");
  check(true, "run without sys/ keys shows the empty state");

  // Data table alternative
  await page.goto(`${BASE}/runs/run_bert_ner?tab=charts`);
  await waitCharts(page, 2);
  await page.locator('summary:has-text("Data table")').first().click();
  check((await page.locator("details[open] table tbody tr").count()) > 0, "chart data table alternative opens");

  // 3. Latency: 300 ms on every browser request + 300 ms server-side per API call
  await page.route("**/*", async (route) => {
    await sleep(300);
    await route.continue();
  });
  await page.goto(`${BASE}/runs`);
  await page.waitForSelector("main table tbody tr");
  await sleep(6000); // let the poller refresh at least once
  await page.click('nav[aria-label="Status"] a:has-text("running")');
  await page.waitForURL(/status=running/);
  await page.waitForFunction(() => document.querySelectorAll("main table tbody tr").length === 2);
  check(true, "status filter lands under latency while the poller runs");
  await page.click('tbody a:has-text("llama-3-8b-dpo")');
  await page.waitForURL(/\/runs\/run_llama3_dpo$/);
  for (const t of ["Charts", "System", "Reports", "Charts"]) {
    await page.click(`nav[aria-label="Run sections"] a:has-text("${t}")`);
    await page.waitForSelector(`nav[aria-label="Run sections"] a[aria-current="page"]:has-text("${t}")`);
  }
  await waitCharts(page, 6);
  check(new URL(page.url()).searchParams.get("tab") === "charts", "rapid tab clicks end on the last tab");
  // Quick double click: the second target must win.
  await page.click('nav[aria-label="Run sections"] a:has-text("System")');
  await page.click('nav[aria-label="Run sections"] a:has-text("Overview")');
  await page.waitForSelector('nav[aria-label="Run sections"] a[aria-current="page"]:has-text("Overview")');
  await sleep(1500);
  check(!new URL(page.url()).searchParams.has("tab"), "last click wins (overview)");
  await shot(page, "14-latency");

  check(errors.length === 0, `no page errors${errors.length ? `: ${errors.slice(0, 3).join(" | ")}` : ""}`);
  await browser.close();
  console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
