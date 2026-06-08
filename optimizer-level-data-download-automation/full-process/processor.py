"""
processor.py
────────────
Core automation logic.

FLOW (mirrors the manual workflow exactly)
──────────────────────────────────────────
For each site  (discovered via API)
  → Navigate to site in monitoring platform
  → Click Analytics
  For each inverter  (discovered via API)
    → Click the inverter in the Analytics tree
    For each string under that inverter  (discovered in the Analytics UI)
      → Click the string
      → Click the first optimizer in the string
      → Set filter: Day → Previous day → Metric: Production → Energy
      → Check "Apply to all optimizers in this string"
    → Export CSV  (Previous day, 1 day resolution)
    → Save file with structured name

SELECTOR NOTES
──────────────
The selectors below (CSS / text / role) are BEST GUESSES based on typical
SolarEdge UI patterns.  You MUST verify them against the live site:

  1. Run:  playwright codegen https://monitoring.solaredge.com
  2. Click through the manual workflow while the codegen window records.
  3. Replace any selector marked  ⚠️ VERIFY  with what codegen gives you.
"""

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config
from browser import safe_screenshot, wait_and_click, wait_for_network
from logger import RunLogger
from models import ExportResult, Inverter, Site
from utils import csv_filename, make_download_dir, retry


# ──────────────────────────────────────────────────────────────────────────────
#  SELECTORS  — edit these after inspecting the live page
# ──────────────────────────────────────────────────────────────────────────────

# ⚠️ VERIFY: The main left-nav "Analytics" link inside an open site
SEL_ANALYTICS_NAV = '[data-testid="site-analysis"]'

# ⚠️ VERIFY: Each inverter row in the Analytics left panel tree
SEL_INVERTER_ITEM = '[aria-label*="Inverter"]'

# ⚠️ VERIFY: Each string row inside an expanded inverter
SEL_STRING_ITEM = '[aria-label*="String"]'

# ⚠️ VERIFY: First optimizer inside an expanded string
SEL_FIRST_OPTIMIZER = '[aria-label*="Optimizer"]'

# ⚠️ VERIFY: The time-range selector dropdown (Day / Week / Month)
SEL_TIMERANGE_DROPDOWN = "select[name='timeUnit'], [class*='timeRange'], button:has-text('Day')"

# ⚠️ VERIFY: The "Previous day" / date navigation back button
SEL_PREV_DAY_BTN = "button[aria-label='Previous day'], button:has-text('‹'), .date-nav-prev"

# ⚠️ VERIFY: The metric selector to choose "Energy"
SEL_METRIC_DROPDOWN = "select[name='metric'], [class*='metric-selector']"

# ⚠️ VERIFY: The "Apply to all optimizers in string" checkbox
SEL_APPLY_ALL_CB = 'text="Apply to all optimizers in"'

# ⚠️ VERIFY: The Export / Download button
SEL_EXPORT_BTN = '[aria-label="Export to CSV file"]'

# ⚠️ VERIFY: Inside the export dialog — "Previous day" option
SEL_EXPORT_PREV_DAY = "label:has-text('Previous day'), input[value='previousDay']"

# ⚠️ VERIFY: Inside the export dialog — "1 day" resolution option
SEL_EXPORT_1DAY_RES = "label:has-text('1 day'), input[value='DAY']"

# ⚠️ VERIFY: The final "Export" / "Download" confirm button inside the dialog
SEL_EXPORT_CONFIRM = "button:has-text('Export'), button:has-text('Download')"


# ──────────────────────────────────────────────────────────────────────────────
#  SITE-LEVEL ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def process_all_sites(
    sites: list[Site],
    page: Page,
    log: RunLogger,
    date_str: str,
) -> list[ExportResult]:
    """
    Iterate through every site.  A failure on one site is caught and logged;
    the browser is reset to the site-list URL before continuing.
    """
    results: list[ExportResult] = []
    download_dir = make_download_dir(date_str)
    log.info(f"Download directory: {download_dir}")

    for site in sites:
        log.site_start(site)

        try:
            _open_site(page, site, date_str, download_dir, log)
        except Exception as exc:
            _handle_site_failure(page, log, site, "Unknown", "Could not open or process site", exc, results)

        # Back to site list before next site
        try:
            page.goto(f"{config.MONITORING_URL}/", wait_until="networkidle", timeout=20_000)
        except Exception:
            pass

    return results


# ──────────────────────────────────────────────────────────────────────────────
#  NAVIGATION HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _open_site(page: Page, site: Site, date_str: str, download_dir: Path, log: RunLogger):
    """Navigate to a site, walk the Analytics tree, then export."""

    page.goto(
        f"{config.MONITORING_URL}/one#/site-list",
        wait_until="networkidle",
        timeout=30_000,
    )
    page.get_by_test_id("nameFilter-input-field").fill(site.name)
    page.wait_for_timeout(3000)
    page.get_by_role("link", name=site.name).click()
    page.wait_for_timeout(10_000)
    log.info(f"Site opened: {site.name}")

    page.get_by_test_id("site-analysis").click()
    page.wait_for_timeout(10_000)
    log.info("Analytics panel opened")

    # ── Baseline arrow count ──────────────────────────────────────────────────
    baseline_arrows = page.get_by_test_id("ChevronRightIcon").count()
    inverter_count  = baseline_arrows - 3          # your correction factor
    log.info(f"Inverters found: {inverter_count}  (baseline arrows: {baseline_arrows})")

    # ── Loop over every inverter ──────────────────────────────────────────────
    for inv_idx in range(inverter_count):

        inverter_arrow_pos = 1 + inv_idx
        log.info(f"── Inverter {inv_idx + 1} / {inverter_count} ──")

        # 1. Expand inverter
        page.get_by_test_id("ChevronRightIcon").nth(inverter_arrow_pos).click()
        page.wait_for_timeout(2000)

        after_inv_expand = page.get_by_test_id("ChevronRightIcon").count()
        string_count     = after_inv_expand - baseline_arrows
        log.info(f"  Strings found: {string_count}  (arrows after expand: {after_inv_expand})")

        # 2. Expand all strings (visibility-aware, re-query each time)
        for s_idx in range(string_count):
            all_arrows = page.get_by_test_id("ChevronRightIcon")
            total      = all_arrows.count()

            visible_string_arrows = []
            for a in range(inverter_arrow_pos + 1, total):
                if all_arrows.nth(a).is_visible():
                    visible_string_arrows.append(a)
                if len(visible_string_arrows) == string_count:
                    break

            if s_idx >= len(visible_string_arrows):
                log.warning(f"  Could not find arrow for string {s_idx + 1} — skipping")
                continue

            target_idx = visible_string_arrows[s_idx]
            log.info(f"  Expanding string {s_idx + 1} (arrow index {target_idx})")
            try:
                page.get_by_test_id("ChevronRightIcon").nth(target_idx).click()
                page.wait_for_timeout(1500)
            except Exception as e:
                log.warning(f"  Could not expand string {s_idx + 1}: {e}")

        # 3. Process each string's optimizers
        optimizer_offset = 0

        for s_idx in range(string_count):
            log.info(f"  Processing string {s_idx + 1}")

            all_optimizers = page.locator("text=/Optimizer/")
            total_opts     = all_optimizers.count()
            log.info(f"    Total optimizers visible: {total_opts}  (offset: {optimizer_offset})")

            if optimizer_offset >= total_opts:
                log.warning("    No optimizer at this offset — skipping string")
                continue

            # Click the first optimizer of THIS string
            target_opt = all_optimizers.nth(optimizer_offset)
            target_opt.click()
            page.wait_for_timeout(3000)
            log.info(f"    Clicked optimizer at index {optimizer_offset}")

            opts_before = total_opts

            # Select Production - Energy metric
            try:
                page.get_by_label("The solar energy produced by") \
                    .get_by_role("button", name="Production - Energy") \
                    .click()
                page.wait_for_timeout(3000)
                log.info("    Production - Energy selected")
            except Exception as e:
                log.warning(f"    Could not select Production - Energy: {e}")

            # Apply to all optimizers in this string
            try:
                page.get_by_text("Apply To All Optimizers In The String").click()
                page.wait_for_timeout(5000)
                log.info("    Apply-all clicked")
            except Exception as e:
                log.warning(f"    Could not click Apply-all: {e}")

            # Advance offset by how many optimizers were added
            opts_after = page.locator("text=/Optimizer/").count()
            if opts_after > opts_before:
                string_opt_count = opts_after - opts_before
            else:
                string_opt_count = _count_string_optimizers(
                    page, s_idx, string_count, inverter_arrow_pos
                )

            log.info(f"    String {s_idx + 1}: ~{string_opt_count} optimizers")
            optimizer_offset += string_opt_count

        # 4. Collapse inverter
        try:
            page.get_by_test_id("ChevronDownIcon").nth(inv_idx).click()
            page.wait_for_timeout(1500)
            log.info(f"  Inverter {inv_idx + 1} collapsed")
        except Exception:
            try:
                page.get_by_test_id("ChevronRightIcon").nth(inverter_arrow_pos).click()
                page.wait_for_timeout(1500)
                log.info(f"  Inverter {inv_idx + 1} collapsed (fallback method)")
            except Exception as e:
                log.warning(f"  Could not collapse inverter {inv_idx + 1}: {e}")

        arrows_now = page.get_by_test_id("ChevronRightIcon").count()
        log.info(f"  Arrows after collapse: {arrows_now}  (expected: {baseline_arrows})")

    # ── Export ────────────────────────────────────────────────────────────────
    _do_export(page, site, date_str, download_dir, log)


def _count_string_optimizers(
    page: Page,
    s_idx: int,
    string_count: int,
    inverter_arrow_pos: int,
) -> int:
    """
    Fallback: estimate optimizers per string when the apply-all count diff
    is unavailable. Returns at least 1 so the offset always advances.
    """
    try:
        all_arrows = page.get_by_test_id("ChevronRightIcon")
        visible = []
        for a in range(inverter_arrow_pos + 1, all_arrows.count()):
            if all_arrows.nth(a).is_visible():
                visible.append(a)
            if len(visible) == string_count:
                break

        if s_idx + 1 >= len(visible):
            return 1  # last string — offset doesn't matter after this

        opts = page.locator("text=/Optimizer/")
        count = opts.count()
        return max(1, count // string_count)
    except Exception:
        return 1


def _do_export(page: Page, site: Site, date_str: str, download_dir: Path, log: RunLogger):
    """Open the export dialog and download the CSV into download_dir."""

    page.get_by_label("Export to CSV file").get_by_role("button").click()
    page.wait_for_timeout(3000)
    log.info("Export dialog opened")

    page.get_by_role("button", name="open calendar").click()
    page.wait_for_timeout(1000)

    # Navigate calendar to previous day
    prev_day = str((
        datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).day
    )
    page.get_by_role("gridcell", name=prev_day, exact=True).nth(1).click()
    page.wait_for_timeout(500)
    page.get_by_role("gridcell", name=prev_day, exact=True).nth(1).click()
    page.wait_for_timeout(2000)
    log.info(f"Date set to day {prev_day} of current month")

    page.get_by_role("combobox").click()
    page.get_by_role("option", name="Day").click()

    # Build a filesystem-safe filename
    safe_site_name = (
        site.name
        .replace(",", "")
        .replace("/", "-")
        .replace("\\", "-")
    )
    filename  = f"{safe_site_name}_{prev_day}.csv"
    save_path = download_dir / filename

    page.get_by_placeholder(re.compile(r"Chart.*\.csv.*")).fill(filename)
    page.wait_for_timeout(1000)
    log.info(f"Filename set: {filename}")

    with page.expect_download() as download_info:
        page.get_by_role("button", name="Export").click()

    download = download_info.value
    download.save_as(str(save_path))
    log.info(f"File saved: {save_path}")


# ──────────────────────────────────────────────────────────────────────────────
#  ERROR HANDLING
# ──────────────────────────────────────────────────────────────────────────────

def _handle_site_failure(
    page: Page,
    log: RunLogger,
    site: Site,
    inverter_name: str,
    reason: str,
    exc: Exception,
    results: list,
):
    err_msg = f"{reason}: {exc}"
    log.error(f"✗ {site.name} / {inverter_name} — {err_msg}")

    shot = safe_screenshot(page, f"error_{site.id}_{inverter_name.replace(' ', '_')}")
    log.info(f"  Screenshot saved: {shot}")

    results.append(ExportResult(
        site_id         = site.id,
        site_name       = site.name,
        inverter_serial = "",
        inverter_name   = inverter_name,
        status          = "FAILED",
        error           = err_msg,
    ))

    try:
        page.goto(config.MONITORING_URL, wait_until="networkidle", timeout=15_000)
    except Exception:
        pass