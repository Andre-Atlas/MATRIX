// End-to-end check of the G0 web (login, project switch, settings, latency) against the mock.
//
//   cd apps/web && npm run build
//   MATRIX_MOCK=1 MATRIX_MOCK_LATENCY_MS=300 npm run start -- -p 3100 &      # server-side latency too
//   NODE_PATH=$(npm root -g) node e2e/g0.cjs [baseUrl] [screenshotDir]
//
// Part 2 is the "latency" criterion: every browser request is delayed 300 ms (page.route) while the
// server also waits 300 ms per API call; clicking spans, tabs, time windows and pagination must
// always update the page. Exit code 1 on any failed check.
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
const shot = (page, name) => page.screenshot({ path: path.join(OUT, `g0-${name}.png`), fullPage: false });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(15000);

  // 1. No session -> login with a validated next
  await page.goto(`${BASE}/traces?status=error`);
  check(new URL(page.url()).pathname === "/login" && new URL(page.url()).searchParams.get("next") === "/traces?status=error", "no session redirects to /login?next=/traces?status=error");
  await page.goto(`${BASE}/login?next=//evil.example`);
  await shot(page, "01-login");

  // 2. Wrong password, then the demo user
  await page.fill('input[name="email"]', "demo@example.com");
  await page.fill('input[name="password"]', "wrong-password-123");
  await page.click('button[type="submit"]');
  await page.waitForSelector('main [role="alert"]');
  check((await page.textContent('main [role="alert"], form [role="alert"]')).includes("Invalid email or password"), "wrong password shows the generic error");
  await shot(page, "02-login-error");
  await page.fill('input[name="password"]', "demo-password-123");
  await page.click('button[type="submit"]');
  await page.waitForURL((u) => new URL(u).pathname === "/");
  check(new URL(page.url()).origin === new URL(BASE).origin, "hostile next ignored: landed on the app home");
  const cookies = await ctx.cookies();
  const sess = cookies.find((c) => c.name === "matrix_session");
  check(sess && sess.httpOnly && sess.sameSite === "Lax" && sess.path === "/", "matrix_session is HttpOnly, SameSite=Lax, Path=/");
  check(!(await page.evaluate(() => document.cookie)).includes("matrix_session"), "JavaScript cannot read the session cookie");
  const html = await page.content();
  check(!html.includes(sess.value), "token is not in the page HTML");
  await page.waitForSelector("text=Error rate");
  await shot(page, "03-overview");

  // 3. Switch project
  const first = (await page.textContent('button[aria-haspopup="true"]')).trim();
  await page.click('button[aria-label^="Project:"]');
  await shot(page, "04-project-menu");
  const other = first.includes("Support") ? "Research Sandbox" : "Support Copilot";
  await page.click(`[role="menuitemradio"]:has-text("${other}")`);
  await page.waitForFunction((n) => document.querySelector('button[aria-label^="Project:"]')?.textContent?.includes(n), other);
  check(true, `switched project to ${other}`);
  await page.goto(`${BASE}/settings/keys`);
  const viewerView = other === "Research Sandbox";
  if (viewerView) {
    check((await page.textContent("main")).includes("403"), "viewer sees the 403 message on /settings/keys");
    check((await page.locator('nav[aria-label="Settings"] a').count()) === 1, "viewer only gets the Profile tab");
    await shot(page, "05-keys-forbidden");
  }
  // back to the owner project
  await page.click('button[aria-label^="Project:"]');
  await page.click('[role="menuitemradio"]:has-text("Support Copilot")');
  await page.waitForFunction(() => document.querySelector('button[aria-label^="Project:"]')?.textContent?.includes("Support"));

  // 4. Settings: profile, members, keys, egress, audit
  await page.goto(`${BASE}/settings`);
  await page.waitForSelector("text=this session");
  await shot(page, "06-settings");
  await page.fill('input[autocomplete="current-password"]', "demo-password-123");
  const news = page.locator('input[autocomplete="new-password"]');
  await news.nth(0).fill("short");
  await news.nth(1).fill("short");
  await page.click('button:has-text("Change password")');
  check((await page.textContent('main [role="alert"], form [role="alert"]')).includes("at least 12"), "password under 12 characters is rejected client-side");
  await page.fill('input[autocomplete="current-password"]', "not-the-password-1");
  await news.nth(0).fill("a-long-new-password");
  await news.nth(1).fill("a-long-new-password");
  await page.click('button:has-text("Change password")');
  await page.waitForSelector("text=current password is incorrect");
  check(true, "wrong current password shows the 403 message");

  await page.goto(`${BASE}/settings/members`);
  await page.waitForSelector("text=ana@example.com");
  await shot(page, "07-members");
  await page.fill('input[type="email"]', "dario@example.com");
  await page.click('button:has-text("Add member")');
  await page.waitForSelector('select[aria-label="Role of dario@example.com"]');
  check(true, "member added by email");
  await page.fill('input[type="email"]', "dario@example.com");
  await page.click('button:has-text("Add member")');
  await page.waitForSelector("text=already a member");
  check(true, "adding twice shows the 409 message");

  await page.goto(`${BASE}/settings/keys`);
  await page.fill('input[placeholder="prod-ingest"]', "e2e-key");
  await page.check('input[type="checkbox"] >> nth=1');
  await page.click('button:has-text("Create key")');
  await page.waitForSelector('input[aria-label="New API key"]');
  const secret = await page.inputValue('input[aria-label="New API key"]');
  check(/^mx_[0-9a-f]{8}_/.test(secret), "key secret shown once in a copyable box");
  await shot(page, "08-keys-secret");
  await page.click('button:has-text("Done")');
  await page.reload();
  check(!(await page.content()).includes(secret.split("_")[2]), "secret is gone after dismissing/reloading");
  await page.click('button[aria-label="Revoke key e2e-key"]');
  await page.click('button:has-text("Yes")');
  await page.waitForSelector("tr:has-text('e2e-key') >> text=revoked");
  check(true, "key revoked after confirmation");
  await shot(page, "09-keys");

  await page.goto(`${BASE}/settings/egress`);
  await page.fill('input[placeholder^="api.example.com"]', "*");
  check((await page.textContent("#egress-hint")).includes("wildcard"), "bare wildcard is flagged before submit");
  await page.fill('input[placeholder^="api.example.com"]', "*.example.net");
  await page.click('button:has-text("Allow host")');
  await page.waitForSelector("td:has-text('*.example.net')");
  await shot(page, "10-egress");

  await page.goto(`${BASE}/settings/audit`);
  await page.selectOption("select[name=action]", "key.create");
  await page.click('button:has-text("Apply")');
  await page.waitForURL(/action=key.create/);
  await page.waitForSelector('td span:has-text("key.create")');
  await shot(page, "11-audit");
  await page.goto(`${BASE}/settings/audit`);
  await page.click("text=Older →");
  await page.waitForURL(/cursor=/);
  check(true, "audit log paginates");

  // 5. Latency: 300 ms on every browser request (server-side latency comes from MATRIX_MOCK_LATENCY_MS)
  await page.route("**/*", async (route) => {
    await sleep(300);
    await route.continue();
  });
  await page.goto(`${BASE}/traces`);
  await page.waitForSelector("table");
  await page.click("tbody tr a >> nth=0");
  await page.waitForURL(/\/traces\/[^/?]+$/);
  await page.waitForSelector('[aria-label="Span detail"]');
  const rows = page.locator('ol[aria-label="Spans"] a');
  const n = await rows.count();
  check(n > 1, `trace has ${n} span links`);
  const panelText = () => page.textContent('[aria-label="Span detail"]');
  const before = await panelText();
  await rows.nth(Math.min(2, n - 1)).click();
  await page.waitForURL(/span=/);
  await page.waitForFunction((b) => document.querySelector('[aria-label="Span detail"]')?.textContent !== b, before);
  check(true, "clicking a span updates the detail panel under 300 ms latency");
  const afterSpan = await panelText();
  await rows.nth(1).click();
  await page.waitForFunction((b) => document.querySelector('[aria-label="Span detail"]')?.textContent !== b, afterSpan);
  check(true, "clicking a second span updates it again");
  await page.click('nav[aria-label="Trace views"] a:has-text("graph")');
  await page.waitForSelector(".react-flow");
  check(true, "graph tab renders under latency");
  await page.click('nav[aria-label="Trace views"] a:has-text("waterfall")');
  await page.waitForSelector('ol[aria-label="Spans"] a');
  check(true, "waterfall tab renders again");
  await shot(page, "12-trace-latency");

  await page.goto(`${BASE}/`);
  await page.waitForSelector("text=Error rate");
  await page.click('nav[aria-label="Time window"] a:has-text("7d")');
  await page.waitForSelector('nav[aria-label="Time window"] a[aria-current="true"]:has-text("7d")');
  check(true, "time window tab updates the overview");
  await page.click('nav[aria-label="Time window"] a:has-text("1h")');
  await page.waitForSelector('nav[aria-label="Time window"] a[aria-current="true"]:has-text("1h")');
  check(true, "and again");

  await page.goto(`${BASE}/experiments`);
  const link = page.locator("tbody tr a >> nth=0");
  if (await link.count()) {
    await link.click();
    await page.waitForURL(/\/experiments\/[^/?]+/);
    const poll = await page.locator("text=auto-refresh").count();
    if (poll) {
      let rsc = 0;
      page.on("request", (r) => r.url().includes("_rsc=") && rsc++);
      await sleep(8000);
      check(rsc > 0, `status poller keeps refreshing under latency (${rsc} refresh requests)`);
    } else console.log("INFO  no running experiment in the mock; poller not exercised");
  }

  // 6. Sign out
  await page.goto(`${BASE}/`);
  await page.click('button[aria-label^="User menu"]');
  await shot(page, "13-user-menu");
  await page.click('button:has-text("Sign out")');
  await page.waitForURL(/\/login/);
  check(!(await ctx.cookies()).some((c) => c.name === "matrix_session"), "sign out clears the cookie");
  await page.unroute("**/*");
  await page.goto(`${BASE}/traces`);
  check(new URL(page.url()).pathname === "/login", "after sign out the app asks for login again");

  await browser.close();
  console.log(failed ? `\n${failed} check(s) FAILED` : "\nall checks passed");
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
