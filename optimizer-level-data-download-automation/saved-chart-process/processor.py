"""
processor.py
────────────
Core automation logic.

FLOW (new Saved Charts workflow)
────────────────────────────────
For each site  (discovered via API)
  → Navigate to site in monitoring platform          [UNCHANGED]
  → Click Analytics tab                              [UNCHANGED]
  → Click "Saved Charts" tab
  → For each chart in CHART_NAMES ("ProdEnergy", "InverterProdPower"):
      ├─ Found  →  open it → Export CSV → save file
      └─ Missing →  write empty placeholder CSV, continue

Both charts are exported per site before moving to the next site.
The DownloadTracker records results for each chart independently.

SELECTOR NOTES
──────────────
Selectors below marked ⚠️ VERIFY should be confirmed with:
    playwright codegen https://monitoring.solaredge.com
or by running discover_selectors.py against the live UI.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

import config
from browser import safe_screenshot
from logger import RunLogger
from models import Site
from tracker import DownloadTracker
from utils import make_download_dir


# ──────────────────────────────────────────────────────────────────────────────
#  TIMING CONSTANTS
#  Prefer locator waits everywhere; these are fallback minimums only.
# ──────────────────────────────────────────────────────────────────────────────

_SHORT   = 1_500   # ms — after a click that triggers a lightweight state change
_MEDIUM  = 3_000   # ms — after a click that triggers a network load
_LONG    = 8_000   # ms — after navigation / full page load

# Timeout for individual locator waits (Playwright raises if exceeded)
_WAIT_TIMEOUT  = 20_000   # ms
_DOWNLOAD_TIMEOUT = 60_000  # ms — export downloads can be slow


# ──────────────────────────────────────────────────────────────────────────────
#  SELECTORS
#  Only those still relevant to the new workflow are retained.
# ──────────────────────────────────────────────────────────────────────────────

# ⚠️ VERIFY: the "Saved Charts" tab inside the Analytics panel
SEL_SAVED_CHARTS_TAB = 'tab[name="Saved Charts"]'   # role-based; used via get_by_role

# Charts to download, in order.  Add or remove entries here to change the set.
CHART_NAMES = [
    "ProdEnergy",
    "InverterProdPower",
]

# ⚠️ VERIFY: Export button (inside an open chart view)
SEL_EXPORT_BTN = '[aria-label="Export to CSV file"]'


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def process_all_sites(
    sites:     list[Site],
    page:      Page,
    log:       RunLogger,
    date_str:  str,
    tracker:   DownloadTracker,
) -> None:
    """
    Iterate through every site.

    A failure on one site is caught and logged; the browser is reset to the
    site-list URL before the next site is attempted.  The DownloadTracker
    is updated for every site regardless of outcome.
    """
    download_dir = make_download_dir(date_str)
    log.info(f"Download directory: {download_dir}")

    for site in sites:
        log.section(f"Site: {site.name}  (ID: {site.id})")
        log.site_start(site)

        try:
            _process_site(page, site, date_str, download_dir, log, tracker)
        except Exception as exc:
            _handle_site_failure(page, log, site, exc, download_dir, date_str, tracker)

        # Return to site list before next iteration
        try:
            page.goto(
                f"{config.MONITORING_URL}/one#/site-list",
                wait_until="networkidle",
                timeout=20_000,
            )
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
#  PER-SITE LOGIC
# ──────────────────────────────────────────────────────────────────────────────

def _process_site(
    page:         Page,
    site:         Site,
    date_str:     str,
    download_dir: Path,
    log:          RunLogger,
    tracker:      DownloadTracker,
) -> None:
    """
    Full workflow for a single site.

    Opens the Saved Charts tab once, then iterates over CHART_NAMES.
    For each chart: found → export CSV; missing → write empty placeholder.
    After exporting a chart, we navigate back to the Saved Charts tab
    so the list is available again for the next chart.
    """

    # ── 1. Open site ──────────────────────────────────────────────────────────
    _open_site(page, site, log)

    # ── 2. Open Analytics tab (UNCHANGED from original) ───────────────────────
    _open_analytics(page, site, log)

    # ── 3. Click Saved Charts tab ─────────────────────────────────────────────
    _open_saved_charts(page, site, log)

    # ── 4. Download each chart in turn ────────────────────────────────────────
    for chart_name in CHART_NAMES:
        log.info(f"── Chart: {chart_name} ──────────────────────────────────")

        chart_found = _find_chart(page, site, log, chart_name)

        if not chart_found:
            # ── 4a. No chart — write empty CSV placeholder ─────────────────
            empty_path = _write_empty_csv(site, chart_name, date_str, download_dir, log)
            tracker.record_missing_chart(site.id, site.name, str(empty_path))
            log.info(f"Chart skipped (not found): {chart_name}")

        else:
            # ── 5. Export CSV ──────────────────────────────────────────────
            file_path = _do_export(page, site, chart_name, date_str, download_dir, log)
            tracker.record_exported(site.id, site.name, str(file_path))
            log.info(f"Chart exported: {chart_name}  →  {file_path}")

            # After export, return to the Saved Charts tab so the chart list
            # is available for the next chart in the loop.
            _return_to_saved_charts(page, site, log)

    log.info(f"Site completed: {site.name}")


# ──────────────────────────────────────────────────────────────────────────────
#  NAVIGATION HELPERS  (steps 1–3)
# ──────────────────────────────────────────────────────────────────────────────

def _open_site(page: Page, site: Site, log: RunLogger) -> None:
    """
    Navigate to the site list, filter by name, open the site.
    This is IDENTICAL to the original implementation.
    """
    page.goto(
        f"{config.MONITORING_URL}/one#/site-list",
        wait_until="networkidle",
        timeout=30_000,
    )

    # Filter the site list by name
    name_filter = page.get_by_test_id("nameFilter-input-field")
    name_filter.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    name_filter.fill(site.name)

    # Wait for the filtered list to update
    site_link = page.get_by_role("link", name=site.name)
    site_link.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    site_link.click()

    # Wait for the site dashboard to load
    page.wait_for_load_state("networkidle", timeout=_LONG * 2)
    log.info(f"Site opened: {site.name}")


def _open_analytics(page: Page, site: Site, log: RunLogger) -> None:
    """
    Click the Analytics nav item.
    IDENTICAL to the original implementation.
    """
    analytics_btn = page.get_by_test_id("site-analysis")
    analytics_btn.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    analytics_btn.click()
    page.wait_for_load_state("networkidle", timeout=_LONG * 2)
    log.info("Analytics panel opened")


def _open_saved_charts(page: Page, site: Site, log: RunLogger) -> None:
    """
    Click the 'Saved Charts' tab inside the Analytics panel.

    Waits for the tab to be visible and clickable before acting.
    """
    saved_charts_tab = page.get_by_role("tab", name="Saved Charts")
    try:
        saved_charts_tab.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    except PlaywrightTimeout:
        # Take a screenshot to aid debugging before re-raising
        safe_screenshot(page, f"no_saved_charts_tab_{site.id}")
        raise RuntimeError(
            f"'Saved Charts' tab did not appear for site '{site.name}'. "
            "Verify the tab exists and the selector is correct."
        )

    saved_charts_tab.click()

    # Wait for the tab content to settle (chart list renders via XHR)
    page.wait_for_load_state("networkidle", timeout=_MEDIUM * 3)
    log.info("Saved Charts tab opened")


def _return_to_saved_charts(page: Page, site: Site, log: RunLogger) -> None:
    """
    After an export dialog closes, navigate back to the Saved Charts tab
    so subsequent charts in the loop can be found in the list.

    Re-uses _open_analytics + _open_saved_charts to guarantee a clean state.
    """
    log.info("Returning to Saved Charts tab for next chart …")
    _open_analytics(page, site, log)
    _open_saved_charts(page, site, log)


# ──────────────────────────────────────────────────────────────────────────────
#  CHART SELECTION  (step 4)
# ──────────────────────────────────────────────────────────────────────────────

def _find_chart(
    page:       Page,
    site:       Site,
    log:        RunLogger,
    chart_name: str,
) -> bool:
    """
    Look for a named chart in the Saved Charts list.

    Returns True if found and clicked (chart is now open).
    Returns False gracefully if the chart does not exist.
    Any other unexpected error is re-raised.
    """
    chart_locator = page.get_by_text(chart_name, exact=True)

    try:
        chart_locator.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    except PlaywrightTimeout:
        log.warning(
            f"'{chart_name}' chart NOT found in Saved Charts for site '{site.name}'. "
            "An empty CSV will be created."
        )
        safe_screenshot(page, f"no_chart_{chart_name}_{site.id}")
        return False

    # Chart exists — click to open it
    log.info(f"'{chart_name}' chart found for site '{site.name}'")
    chart_locator.click()

    # Wait for chart data to load before we attempt the export
    page.wait_for_load_state("networkidle", timeout=_LONG * 2)
    log.info(f"'{chart_name}' chart opened — waiting for data to render")

    # Optional extra guard: wait until the Export button is enabled/visible,
    # which confirms the chart has fully rendered its data.
    try:
        export_btn = page.get_by_label("Export to CSV file").get_by_role("button")
        export_btn.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    except PlaywrightTimeout:
        # Export button not visible yet — take a screenshot, then re-raise
        # so the outer error handler can log a FAILED record.
        safe_screenshot(page, f"no_export_btn_{chart_name}_{site.id}")
        raise RuntimeError(
            f"Export button did not appear after opening '{chart_name}' chart "
            f"for site '{site.name}'."
        )

    return True


# ──────────────────────────────────────────────────────────────────────────────
#  EXPORT  (step 5)
# ──────────────────────────────────────────────────────────────────────────────

def _do_export(
    page:         Page,
    site:         Site,
    chart_name:   str,
    date_str:     str,
    download_dir: Path,
    log:          RunLogger,
) -> Path:
    """
    Open the export dialog for the currently-open chart and download the CSV.

    Date handling strategy
    ──────────────────────
    date_str is the run date (YYYY-MM-DD).  We export the PREVIOUS day.
    The date is typed directly into the MUI DateRangePicker text input as
    MM/DD/YYYY - MM/DD/YYYY (start = end = previous day), bypassing the
    calendar popup entirely.

    The chart_name is embedded in the saved filename so both charts can
    coexist in the same download directory without colliding.

    Returns the Path where the file was saved.
    """
    log.info(f"Starting export for site '{site.name}', chart '{chart_name}'")

    # Compute the target date (previous calendar day)
    target_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)

    log.info(f"Target export date: {target_dt.strftime('%Y-%m-%d')}")

    # ── Open export dialog ────────────────────────────────────────────────────
    export_btn = page.get_by_label("Export to CSV file").get_by_role("button")
    export_btn.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    export_btn.click()
    page.wait_for_load_state("networkidle", timeout=_MEDIUM * 2)
    log.info("Export dialog opened")

    # ── Type date directly into the date range input ──────────────────────────
    # Format: MM/DD/YYYY - MM/DD/YYYY  (start and end are the same day)
    date_input_str = target_dt.strftime("%d/%m/%Y")
    date_range_str = f"{date_input_str} - {date_input_str}"

    # Click the outer wrapper to ensure the input is focused/active
    date_wrapper = page.locator(".sc-bjMIFn.jGMeVW > .MuiFormControl-root > .MuiInputBase-root")
    date_wrapper.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    date_wrapper.click()
    page.wait_for_timeout(_SHORT)

    # MUI masked date inputs ignore fill() -- type character by character instead.
    # press_sequentially() fires real keydown/keypress/keyup events that the mask
    # processes one digit at a time, exactly like a real user would type.
    date_textbox = page.get_by_role("textbox", name="Start Date - End Date")
    date_textbox.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    date_textbox.click()
    date_textbox.press("Control+a")
    date_textbox.press_sequentially(date_range_str, delay=50)   # 50ms between keys
    page.wait_for_timeout(500)

    # Force-close the MUI calendar popup
    # Without this the picker overlay blocks all subsequent clicks (combobox, etc).
    page.get_by_text("Date Range").click()
    page.wait_for_timeout(_SHORT)
    page.get_by_text("Date Range").click()
    log.info(f"Date set to {target_dt.strftime('%Y-%m-%d')} via direct input")

    if chart_name == "ProdEnergy":
        # ── Set resolution to Day ─────────────────────────────────────────────────
        # Two comboboxes exist on the page; target the resolution one specifically.
        resolution_box = page.get_by_role("combobox")
        resolution_box.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
        resolution_box.click()
        day = page.get_by_role("option", name="Day")
        day.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
        day.click()
        page.wait_for_timeout(_SHORT)
        log.info("Resolution set to Day")
    elif chart_name == "InverterProdPower":
        # ── Set resolution to 5 Minutes ─────────────────────────────────────────────────
        # Two comboboxes exist on the page; target the resolution one specifically.
        resolution_box = page.get_by_role("combobox")
        resolution_box.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
        resolution_box.click()
        day = page.get_by_role("option", name="5 minutes", exact=True)
        day.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
        day.click()
        page.wait_for_timeout(_SHORT)
        log.info("Resolution set to Day")

    # ── Set filename ──────────────────────────────────────────────────────────
    # Chart name is included so ProdEnergy and InverterProdPower files
    # don't overwrite each other in the same download directory.
    safe_site_name = (
        site.name
        .replace(",", "")
        .replace("/", "-")
        .replace("\\", "-")
    )
    filename  = f"{safe_site_name}_{chart_name}_{target_dt.strftime('%Y-%m-%d')}.csv"
    save_path = download_dir / filename

    filename_input = page.get_by_placeholder(re.compile(r"Chart.*\.csv.*"))
    filename_input.wait_for(state="visible", timeout=_WAIT_TIMEOUT)
    filename_input.fill(filename)
    page.wait_for_timeout(500)
    log.info(f"Filename set: {filename}")

    # ── Trigger download ──────────────────────────────────────────────────────
    log.info("Clicking Export button — waiting for download …")
    with page.expect_download(timeout=_DOWNLOAD_TIMEOUT) as download_info:
        page.get_by_role("button", name="Export").click()

    download = download_info.value
    download.save_as(str(save_path))
    log.info(f"Export completed — file saved: {save_path}")

    return save_path


# ──────────────────────────────────────────────────────────────────────────────
#  MISSING CHART PLACEHOLDER  (step 4a)
# ──────────────────────────────────────────────────────────────────────────────

def _write_empty_csv(
    site:         Site,
    chart_name:   str,
    date_str:     str,
    download_dir: Path,
    log:          RunLogger,
) -> Path:
    """
    Write an empty CSV placeholder when a chart is not available.

    Filename convention mirrors the export naming:
        <SiteName>_<ChartName>_<day>.csv

    An empty file (zero bytes) is written so downstream consumers can detect
    the gap without causing KeyErrors on missing files.
    """
    target_dt = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    safe_site_name = (
        site.name
        .replace(",", "")
        .replace("/", "-")
        .replace("\\", "-")
    )
    filename  = f"{safe_site_name}_{chart_name}_{target_dt.strftime('%Y-%m-%d')}.csv"
    file_path = download_dir / filename

    file_path.touch()   # creates an empty file; safe if it already exists
    log.info(
        f"Empty CSV placeholder created for '{site.name}' / '{chart_name}': {file_path}"
    )
    return file_path


# ──────────────────────────────────────────────────────────────────────────────
#  ERROR HANDLING
# ──────────────────────────────────────────────────────────────────────────────

def _handle_site_failure(
    page:         Page,
    log:          RunLogger,
    site:         Site,
    exc:          Exception,
    download_dir: Path,
    date_str:     str,
    tracker:      DownloadTracker,
) -> None:
    """
    Catch-all for unexpected errors on a single site.

    Logs the error, saves a debug screenshot, records a FAILED entry in the
    tracker, and navigates away so the next site is not affected.
    """
    err_msg = str(exc)
    log.error(f"✗ {site.name} — {err_msg}")

    shot = safe_screenshot(page, f"error_{site.id}")
    log.info(f"  Debug screenshot saved: {shot}")

    tracker.record_failed(site.id, site.name, err_msg)

    # Best-effort recovery: go back to monitoring home
    try:
        page.goto(config.MONITORING_URL, wait_until="networkidle", timeout=15_000)
    except Exception:
        pass