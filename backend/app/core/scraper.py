"""
Aegis — Web Scraping (Playwright-based)

Uses a real headless Chromium (via Playwright) rather than a plain HTTP fetch,
so JavaScript-rendered pages actually render before extraction. The browser
binary is NOT bundled into the packaged app (that would bloat the signed
bundle and add more native-binary surface for macOS's malware scanner to
flag) — it downloads on first use into AEGIS_DATA_DIR, the same place GGUF
models already land, well outside the app bundle.

Public sites only, deliberately: every visit is anonymous (no cookie
injection, no login flow) — there used to be a whole cookie-ask-and-retry
path for login-walled/private pages, removed because it added real UX
friction (pausing an entire plan to ask the user to dig a session cookie out
of devtools) for a case this app doesn't want to solve. Beyond extraction,
this module still runs a few cheap, deterministic checks against the
rendered page (bot-check pages, login walls, private-repo-shaped 404s,
near-empty renders) and reports them as human-readable warnings — the caller
surfaces these directly to the user, so the agent can say "this page needs
login, I can't access it" instead of silently returning a blank or
truncated result.
"""

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_data_dir = os.environ.get("AEGIS_DATA_DIR")
BROWSERS_DIR = Path(_data_dir) / "browsers" if _data_dir else BASE_DIR / "browsers"
BROWSERS_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(BROWSERS_DIR))

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Patterns specific enough to an actual interstitial/failover PAGE that they
# fire on their own, with no further corroboration needed — either
# challenge-page copy, or an asset path confirmed to appear only on a real
# block page (goindigo.in serves a static "Something went wrong" page from
# this exact Akamai asset path, no login form, status 200 — blocked used to
# stay False for it before these were added).
_BOT_CHECK_PATTERNS_STRONG = [
    "checking your browser", "just a moment", "verify you are human",
    "attention required", "unusual traffic", "enable javascript and cookies",
    "please stand by, while we are checking",
    "akamfailoverpage", "akamaighost",
    "cf-error-details", "cf-wrapper", "//challenges.cloudflare.com",
    "px-captcha", "_pxcaptcha",
]

# Vendor names/bare "captcha" — too common as a passive, always-loaded
# monitoring script (or, for "captcha", as an ordinary word in a page's own
# content or config) to mean anything on their own. Confirmed false-positive:
# Wikipedia articles match bare "captcha" via wgConfirmEditCaptchaNeededFor-
# GenericEdit in every page's own config JSON — nothing to do with THIS
# request being blocked. Only counted as a match when the page's extracted
# text is also near-empty (see build_scrape_result) — an actual challenge
# page has essentially no real content, whereas a normal page that merely
# mentions or passively loads one of these has plenty.
_BOT_CHECK_PATTERNS_WEAK = [
    "captcha", "perimeterx", "incapsula", "_incapsula_resource", "datadome",
]

# Hosts that deliberately return a plain 404 (not 401/403) for a private
# repo/page you're not authorized to see, specifically to avoid confirming
# its existence to an anonymous visitor — GitHub's own documented behavior,
# and GitLab/Bitbucket do the same. Without this, a private repo's 404 looks
# completely indistinguishable from a genuinely deleted/mistyped URL: no
# password field, no 401/403, no bot-check pattern — every other "blocked"
# signal in this file stays silent, and the agent has nothing to tell the
# user beyond a generic "no content found."
_PRIVATE_404_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}


@dataclass
class ScrapeResult:
    success: bool
    title: str = ""
    text: str = ""
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None
    # True when the page looks like it needs an authenticated session (a
    # login form was present, the response was 401/403/a private-repo-shaped
    # 404, or a bot-check page was detected) — a structured flag so callers
    # can tell the user plainly "this is private/blocked, I can't access it"
    # instead of having to pattern-match the human-readable warning text.
    # There is deliberately no retry-with-a-cookie path built on this flag —
    # see this module's docstring for why.
    needs_auth: bool = False


def is_chromium_installed() -> bool:
    return any(BROWSERS_DIR.glob("chromium*/**/chrome")) or any(BROWSERS_DIR.glob("chromium*/**/*.app"))


def install_chromium() -> tuple[bool, str]:
    """
    Blocking — downloads the Chromium browser binary. Callers must run this
    off the event loop (e.g. via anyio.to_thread.run_sync).

    Deliberately does NOT shell out to `sys.executable -m playwright install`:
    inside a PyInstaller-frozen app, sys.executable is the frozen app binary
    itself, not a real Python interpreter, so `-m` silently does nothing
    there (confirmed against a real packaged build). Instead this invokes
    Playwright's own bundled Node driver directly — the same executable
    `playwright install` uses internally — via the path playwright's own
    _driver.compute_driver_executable() resolves, which works correctly in
    both dev and frozen builds since it's relative to the installed package.
    """
    import subprocess
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        node_path, cli_path = compute_driver_executable()
        proc = subprocess.run(
            [node_path, cli_path, "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=900,
            env={**get_driver_env(), "PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS_DIR)},
        )
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout)[-2000:]
        return True, ""
    except Exception as e:
        return False, str(e)


def _run_driver_script(url: str) -> dict:
    """
    Blocking — runs scraper_driver.js in a single plain subprocess (via
    subprocess.run, NOT asyncio) and parses its one-line JSON result.

    Deliberately does not use Playwright's own Python API (sync or async) to
    drive the browser: both are hardwired internally to asyncio's subprocess
    transport for talking to the Node driver, and asyncio.create_subprocess_exec
    was confirmed (via a dedicated selftest CLI entry point in main.py, run
    directly against a real packaged PyInstaller build — reproduced even with
    a trivial `/bin/echo` and no Playwright involved at all) to hang
    indefinitely inside this frozen macOS build, with no timeout and no
    error. Plain subprocess.run works fine (already proven by
    install_chromium below), so all actual browser automation happens inside
    scraper_driver.js — a single self-contained Node process — and Python
    only ever does one blocking subprocess.run of it.
    """
    import subprocess
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node_path, cli_path = compute_driver_executable()
    playwright_core_index = str(Path(cli_path).parent / "index.js")
    driver_script = str(Path(__file__).parent / "scraper_driver.js")

    # Deliberately plain subprocess.run with NO start_new_session, NO
    # preexec_fn, NO custom close_fds — those all force CPython's subprocess
    # module off its posix_spawn fast path and onto traditional fork()+exec()
    # instead (see CPython's subprocess.py: several Popen options disable
    # posix_spawn support). fork() in a process that already has background
    # threads running (this backend creates several ThreadPoolExecutors at
    # import time) plus hundreds of loaded native libraries is a well-known
    # macOS hazard — confirmed here by reproducing a genuinely flaky hang
    # (same binary, same command, intermittently hung vs succeeded across
    # repeated runs) that went away once start_new_session was removed,
    # letting posix_spawn (which doesn't duplicate the parent's thread/lock
    # state) handle it instead.
    proc = subprocess.run(
        [node_path, driver_script, playwright_core_index, url],
        capture_output=True,
        text=True,
        timeout=60,
        env={**get_driver_env(), "PLAYWRIGHT_BROWSERS_PATH": str(BROWSERS_DIR)},
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError((proc.stderr or proc.stdout or "scraper_driver.js produced no output")[-2000:])

    import json
    return json.loads(proc.stdout.strip().splitlines()[-1])


def build_scrape_result(url: str, driver_result: dict) -> ScrapeResult:
    """
    Interprets a driver response shaped like {title, html, status, timedOut,
    hasPasswordField} — extracted from scrape_url below so
    app.core.agents.chat's browser_navigate tool can run the exact same
    bot-check/login-wall detection against app/core/browser_driver.js's
    persistent-session navigate response, which has the identical shape.
    Without this shared path, browser_navigate would silently have none of
    web_scrape's "this is blocked" detection, and the two tools would behave
    inconsistently for no reason a user could tell.
    """
    import trafilatura

    warnings: List[str] = []
    title = driver_result.get("title") or url
    html = driver_result.get("html") or ""
    status = driver_result.get("status")
    needs_auth = False
    bot_check_matched = False

    if driver_result.get("timedOut"):
        warnings.append(
            "This page kept loading past the timeout — it may use infinite scroll "
            "or heavy scripts, so some content could be missing."
        )
    if status in (401, 403, 429):
        warnings.append(
            f"The site responded with HTTP {status}, which usually means it's "
            "blocking automated access."
        )
        if status in (401, 403):
            needs_auth = True
    elif status == 404:
        from urllib.parse import urlparse
        try:
            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        except Exception:
            host = ""
        if host in _PRIVATE_404_HOSTS:
            warnings.append(
                "This page returned \"not found\" — on this site that's also what a "
                "private repo/page you don't have access to looks like, not just a "
                "genuinely missing one."
            )
            needs_auth = True
    if driver_result.get("hasPasswordField"):
        warnings.append(
            "This page contains a login form — it likely requires signing in to "
            "see the full content, so only the public portion (if any) was captured."
        )
        needs_auth = True

    # Extracted before the bot-check so the weak-pattern tier below can use
    # "is there actually any content here" as its corroborating signal.
    text = trafilatura.extract(html, favor_recall=True) or ""
    text_is_sparse = len(text.strip()) < 200  # true for empty pages too

    lower_html = html.lower()
    strong_match = any(pat in lower_html for pat in _BOT_CHECK_PATTERNS_STRONG)
    # Weak patterns (bare "captcha", vendor names) are common as passive,
    # always-loaded background scripts or ordinary page content/config —
    # only trust them when the page is also suspiciously empty, which an
    # actual challenge/failover page always is. See _BOT_CHECK_PATTERNS_WEAK.
    weak_match = text_is_sparse and any(pat in lower_html for pat in _BOT_CHECK_PATTERNS_WEAK)
    if strong_match or weak_match:
        bot_check_matched = True
        warnings.append(
            "This page shows signs of a bot-verification challenge (e.g. "
            "Cloudflare/Akamai/CAPTCHA) — the scraped content may be incomplete or "
            "just the challenge/block page itself."
        )
        needs_auth = True

    if text_is_sparse and text.strip():
        warnings.append(
            "Very little readable text could be extracted from this page — it may "
            "require further interaction, login, or isn't primarily text content."
        )

    if not text.strip():
        return ScrapeResult(
            success=False, title=title, warnings=warnings,
            error="No readable content could be extracted from this page.",
            needs_auth=needs_auth,
        )

    return ScrapeResult(success=True, title=title, text=text, warnings=warnings, needs_auth=needs_auth)


async def scrape_url(url: str) -> ScrapeResult:
    """
    Launches headless Chromium, renders the page anonymously, extracts the
    main text content, and flags site-specific obstacles (login walls,
    bot-check pages, private-repo-shaped 404s) as warnings — see this
    module's docstring for why there's no cookie/login retry path. See
    _run_driver_script's docstring for why the actual browser automation
    happens in a Node subprocess rather than through Playwright's Python API.
    """
    import anyio

    try:
        result = await anyio.to_thread.run_sync(_run_driver_script, url)
    except Exception as e:
        logger.error(f"Scrape failed for {url}: {e}")
        return ScrapeResult(success=False, error=str(e))

    if not result.get("ok"):
        return ScrapeResult(success=False, error=result.get("error", "Unknown scraping error"))

    return build_scrape_result(url, result)
