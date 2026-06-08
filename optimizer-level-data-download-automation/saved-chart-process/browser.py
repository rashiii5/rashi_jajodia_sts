"""
browser.py
──────────
Manages the Playwright browser context.

KEY DESIGN DECISIONS
────────────────────
• Persistent context  →  Chrome stays logged in between runs.
  Your cookies live in SE_PROFILE_DIR.  First run: log in manually.
  Subsequent runs: session is already active.

• accept_downloads=True  →  Playwright intercepts the CSV download
  instead of letting the OS dialog appear.

• Context manager pattern  →  `with get_browser() as (pw, ctx, page):`
  guarantees the browser is always closed cleanly.
"""

import contextlib
from pathlib import Path

from playwright.sync_api import Playwright, BrowserContext, Page, sync_playwright

import config


@contextlib.contextmanager
def get_browser():
    """
    Context manager that yields (playwright, browser_context, page).

    Usage:
        with get_browser() as (pw, ctx, page):
            page.goto("https://monitoring.solaredge.com")
            ...
    """
    pw: Playwright = sync_playwright().start()

    ctx: BrowserContext = pw.chromium.launch_persistent_context(
        user_data_dir    = str(config.PROFILE_DIR),
        headless         = config.HEADLESS,
        slow_mo          = config.SLOW_MO,
        downloads_path   = str(config.DOWNLOAD_DIR),
        accept_downloads = True,
        viewport         = {"width": 1440, "height": 900},
        # ⚠️  If SolarEdge detects automation and shows a CAPTCHA, try:
        # args=["--disable-blink-features=AutomationControlled"],
    )

    page: Page = ctx.new_page()

    # Block analytics/tracking calls to speed up page loads
    # ⚠️  Remove this if it causes login issues
    # page.route("**/*", _maybe_block)

    try:
        yield pw, ctx, page
    finally:
        ctx.close()
        pw.stop()


def _maybe_block(route, request):
    """Block irrelevant third-party resources to speed up navigation."""
    blocked = ("google-analytics", "hotjar", "intercom", "segment.io", "mixpanel")
    if any(b in request.url for b in blocked):
        route.abort()
    else:
        route.continue_()


# ── Page-level helpers ────────────────────────────────────────────────────────

def wait_and_click(page: Page, selector: str, timeout: int = 15_000):
    """
    Wait for an element to be visible then click it.
    Raises TimeoutError if it doesn't appear within `timeout` ms.
    """
    page.wait_for_selector(selector, state="visible", timeout=timeout)
    page.click(selector)


def wait_for_network(page: Page):
    """Wait until all in-flight XHR/fetch requests have settled."""
    page.wait_for_load_state("networkidle", timeout=30_000)


def safe_screenshot(page: Page, name: str):
    """Save a debug screenshot to the logs directory."""
    path = config.LOG_DIR / f"{name}.png"
    page.screenshot(path=str(path))
    return str(path)
