// Aegis — persistent Playwright driver for the browser_* agent tools.
//
// Unlike scraper_driver.js (one action, one process, exits immediately
// after), this process stays alive for an entire browsing session: it
// reads one JSON command per stdin line, executes it against a shared
// browser context, and writes one JSON response per stdout line, looping
// until a "close" command or stdin EOF. See app/core/browser_session.py
// for why: a persistent session is what lets the agent navigate once and
// then click/fill/scroll/extract against that SAME live page across
// several separate tool calls, instead of starting from a blank page
// every time the way app/core/scraper.py's one-shot web_scrape does.
//
// Invoked via a plain (non-asyncio) Python subprocess — same constraint as
// scraper_driver.js: Playwright's own Python bindings are hardwired to
// asyncio subprocess transport, which was confirmed to hang unconditionally
// in the frozen PyInstaller build, so all real browser automation happens
// here instead, in a single long-lived Node process.
//
// Protocol: each stdin line is `{"id": <n>, "action": "<name>", "params": {...}}`;
// each stdout line is `{"id": <n>, "ok": true, ...fields}` or
// `{"id": <n>, "ok": false, "error": "..."}`. Exactly one command is ever
// in flight — browser_session.py's lock guarantees the Python side never
// writes a second command before reading the first's response — so there
// is no need to queue or reorder here.

const playwrightCorePath = process.argv[2];
const { chromium } = require(playwrightCorePath);
const readline = require('readline');

const UA =
  'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

let browser = null;
let context = null;
let pages = [];   // every open tab, in open order — never spliced, so indices stay stable
let current = 0;  // index into pages[] that every non-navigate action acts on

function currentPage() {
  const page = pages[current];
  if (!page) throw new Error('No page open — call browser_navigate first.');
  return page;
}

async function ensureBrowser() {
  if (!browser) {
    browser = await chromium.launch({ headless: true });
    context = await browser.newContext({ userAgent: UA });
  }
}

const actions = {
  // Opens a NEW tab and navigates it there, becoming the active tab —
  // mirrors scraper_driver.js's own load logic (networkidle, falling back
  // to domcontentloaded on timeout) so behavior matches the one-shot
  // web_scrape tool the agent already knows.
  async navigate({ url }) {
    if (!url) throw new Error('navigate requires a url');
    await ensureBrowser();
    const page = await context.newPage();
    pages.push(page);
    current = pages.length - 1;

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
    await page.waitForTimeout(1000).catch(() => {});
    const title = await page.title().catch(() => url);
    const html = await page.content().catch(() => '');
    const hasPasswordField =
      (await page.locator('input[type="password"]').count().catch(() => 0)) > 0;
    return {
      title, url: page.url(), status, timedOut, hasPasswordField, html,
      tabIndex: current,
    };
  },

  async click({ selector, timeout }) {
    const page = currentPage();
    await page.click(selector, { timeout: timeout || 10000 });
    return { selector, url: page.url(), title: await page.title().catch(() => '') };
  },

  async fill({ selector, text, timeout }) {
    const page = currentPage();
    await page.fill(selector, String(text ?? ''), { timeout: timeout || 10000 });
    return { selector };
  },

  async scroll({ direction, amount }) {
    const page = currentPage();
    const dir = direction || 'down';
    if (dir === 'bottom') {
      // Repeatedly scroll and give lazy/infinite-scroll content a moment to
      // load, capped so a truly infinite feed can't hang this forever.
      await page.evaluate(async () => {
        await new Promise((resolve) => {
          let total = 0;
          const step = () => {
            window.scrollBy(0, 800);
            total += 800;
            if (total > document.body.scrollHeight || total > 20000) resolve();
            else setTimeout(step, 150);
          };
          step();
        });
      });
    } else if (dir === 'top') {
      await page.evaluate(() => window.scrollTo(0, 0));
    } else {
      const delta = (amount || 800) * (dir === 'up' ? -1 : 1);
      await page.mouse.wheel(0, delta);
    }
    await page.waitForTimeout(300).catch(() => {});
    return { direction: dir };
  },

  async wait_for_selector({ selector, timeout }) {
    const page = currentPage();
    await page.waitForSelector(selector, { timeout: timeout || 10000 });
    return { selector };
  },

  async extract_text() {
    const page = currentPage();
    const html = await page.content().catch(() => '');
    const title = await page.title().catch(() => '');
    return { html, title, url: page.url() };
  },

  // JPEG (not PNG) and viewport-only by default specifically to keep the
  // base64 payload bounded — this is embedded directly as a data: URI in
  // the chat message (see response_shapers.py's browser_screenshot
  // display shaper), not saved to disk, so its size is the message's size.
  async screenshot({ full_page }) {
    const page = currentPage();
    const buffer = await page.screenshot({ type: 'jpeg', quality: 55, fullPage: !!full_page });
    return { screenshot_base64: buffer.toString('base64') };
  },

  async new_tab({ url }) {
    return actions.navigate({ url });
  },

  async switch_tab({ index }) {
    if (index == null || !pages[index]) throw new Error(`No tab at index ${index}`);
    current = index;
    const page = pages[current];
    return { url: page.url(), title: await page.title().catch(() => ''), tabIndex: current };
  },

  async list_tabs() {
    const tabs = [];
    for (let i = 0; i < pages.length; i++) {
      tabs.push({ index: i, url: pages[i].url(), title: await pages[i].title().catch(() => '') });
    }
    return { tabs, current };
  },

  async go_back() {
    const page = currentPage();
    await page.goBack({ timeout: 10000 }).catch(() => {});
    return { url: page.url(), title: await page.title().catch(() => '') };
  },

  async go_forward() {
    const page = currentPage();
    await page.goForward({ timeout: 10000 }).catch(() => {});
    return { url: page.url(), title: await page.title().catch(() => '') };
  },

  async close() {
    if (browser) await browser.close().catch(() => {});
    return {};
  },
};

const rl = readline.createInterface({ input: process.stdin, terminal: false });

rl.on('line', async (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch (e) {
    process.stdout.write(JSON.stringify({ id: null, ok: false, error: 'Malformed command JSON' }) + '\n');
    return;
  }
  const { id, action, params } = req;
  const handler = actions[action];
  if (!handler) {
    process.stdout.write(JSON.stringify({ id, ok: false, error: `Unknown action: ${action}` }) + '\n');
    return;
  }
  try {
    const result = await handler(params || {});
    process.stdout.write(JSON.stringify({ id, ok: true, ...result }) + '\n');
  } catch (e) {
    process.stdout.write(JSON.stringify({ id, ok: false, error: String((e && e.message) || e) }) + '\n');
  }
  if (action === 'close') {
    process.exit(0);
  }
});

rl.on('close', async () => {
  if (browser) await browser.close().catch(() => {});
  process.exit(0);
});
