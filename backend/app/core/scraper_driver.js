// Aegis — minimal playwright-core driver script.
//
// Invoked via a plain (non-asyncio) Python subprocess.run call. Reason: on
// this PyInstaller-frozen macOS build, Python's own asyncio.create_subprocess_exec
// hangs unconditionally — confirmed with a trivial `/bin/echo` test via a
// dedicated selftest CLI entry point in main.py, completely independent of
// Playwright. Plain subprocess.run/Popen works fine (used elsewhere in
// scraper.py for the browser install step). Since Playwright's Python
// bindings (sync AND async API) are hardwired to asyncio subprocess
// transport for driver communication with no way to swap that out, all
// browser automation happens here instead, inside a single plain Node
// process, and Python only ever does one blocking subprocess.run of it.
//
// Every visit is anonymous — no cookie injection, no login flow. See
// app/core/scraper.py's module docstring for why.

const playwrightCorePath = process.argv[2];
const url = process.argv[3];
const { chromium } = require(playwrightCorePath);

const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

(async () => {
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ userAgent: UA });
    const page = await context.newPage();

    let status = null;
    let timedOut = false;
    try {
      const response = await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
      status = response ? response.status() : null;
    } catch (e) {
      timedOut = true;
      try {
        const response = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 });
        status = response ? response.status() : null;
      } catch (e2) {
        // Nothing loaded at all — fall through with whatever's on the page.
      }
    }

    await page.waitForTimeout(1500).catch(() => {});
    const title = await page.title().catch(() => url);
    const html = await page.content().catch(() => '');
    const hasPasswordField =
      (await page.locator('input[type="password"]').count().catch(() => 0)) > 0;

    process.stdout.write(JSON.stringify({ ok: true, title, html, status, timedOut, hasPasswordField }));
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }));
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
})();
