"""
main.py
───────
Entry point.  Run this to start the automation.

USAGE
─────
# Normal run — previous day's data for all sites in your group:
    python main.py

# Process only one specific site (useful for testing / validation):
    python main.py --site-id 12345

# Dry run — enumerate sites and inverters via API, but skip the browser:
    python main.py --dry-run

# Dry run for a single site:
    python main.py --dry-run --site-id 12345

# Override the export date (YYYY-MM-DD):
    python main.py --date 2026-06-01

FIRST-TIME SETUP
────────────────
1. Copy  .env.example  →  .env  and fill in SE_API_KEY.
2. Run:  python main.py
3. If not already logged in, Chrome will open the monitoring platform.
   Log in manually.  Press Enter in this terminal when done.
4. From now on the session persists — no more manual login needed.
"""

import argparse
import sys
from datetime import datetime, timezone

import config          # validates .env on import
import api_client
import browser
import processor
from logger import get_logger, console
from utils import previous_day_str


def parse_args():
    p = argparse.ArgumentParser(description="SolarEdge hybrid automation tool")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate sites via API only; skip browser export",
    )
    p.add_argument(
        "--site-id",
        type=int,
        default=None,
        help="Process only a single site by ID (for testing)",
    )
    p.add_argument(
        "--date",
        type=str,
        default=None,
        help="Override export date (YYYY-MM-DD). Default: yesterday",
    )
    return p.parse_args()


def main():
    args     = parse_args()
    run_id = datetime.now().strftime("run_%Y-%m-%dT%H-%M-%S")
    date_str = datetime.now().strftime("%Y-%m-%d")
    log      = get_logger(run_id)

    log.section(f"SolarEdge Automation  |  {date_str}  |  {run_id}")
    log.info(f"Group filter  : {config.GROUP_NAME}")
    log.info(f"Download dir  : {config.DOWNLOAD_DIR}")
    log.info(f"Log file      : {config.LOG_DIR / run_id}.log")
    log.info(f"Mode          : {'DRY RUN' if args.dry_run else 'FULL RUN'}")
    if args.site_id:
        log.info(f"Site filter   : ID {args.site_id}")

    # ── STEP 1: Discover sites via API ────────────────────────────────────────
    log.section("Step 1 — Fetching site list via API")
    try:
        all_sites = api_client.get_all_sites()
    except Exception as exc:
        log.error(f"Fatal: could not fetch site list — {exc}")
        sys.exit(1)

    # If --site-id is given, restrict to that one site only
    if args.site_id:
        all_sites = [s for s in all_sites if s.id == args.site_id]
        if not all_sites:
            log.error(f"Site ID {args.site_id} not found in the fleet.")
            sys.exit(1)

    log.info(f"Sites to process: {len(all_sites)}")

    # ── STEP 2: Enrich each site with inverter inventory via API ──────────────
    log.section("Step 2 — Fetching inverter inventory via API")
    for site in all_sites:
        try:
            api_client.get_site_details(site)
            site.inverters = api_client.get_site_inventory(site)
            n_strings = sum(len(i.strings) for i in site.inverters)
            log.info(
                f"  {site.name}: "
                f"{len(site.inverters)} inverter(s), "
                f"{n_strings} string(s) via API"
            )
        except Exception as exc:
            log.warning(f"  Could not load inventory for {site.name}: {exc}. Skipping.")
            site.inverters = []   # browser will try to discover from the UI

    # ── Dry run: print a summary table and exit ───────────────────────────────
    if args.dry_run:
        log.section("Dry Run Complete")
        log.info("All sites and inverters enumerated.  Browser not launched.")
        _print_dry_run_table(all_sites)
        return

    # ── STEP 3: Check login, then run browser automation ─────────────────────
    log.section("Step 3 — Browser automation")

    with browser.get_browser() as (pw, ctx, page):

        _ensure_logged_in(page, log)

        processor.process_all_sites(
            sites    = all_sites,
            page     = page,
            log      = log,
            date_str = date_str,
        )

    # ── STEP 4: Summary ───────────────────────────────────────────────────────
    log.print_summary()


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_logged_in(page, log):
    """
    Navigate to the monitoring platform.  If the login page appears,
    pause and let the user log in manually (only needed once per profile).
    """
    log.info("Opening monitoring platform …")
    page.goto(config.MONITORING_URL, wait_until="networkidle", timeout=30_000)

    # ⚠️  VERIFY: adjust to a selector that only appears when logged IN
    logged_in_indicator = "nav, .site-list, [class*='dashboard']"

    try:
        page.wait_for_selector(logged_in_indicator, timeout=5_000)
        log.info("[green]Already logged in ✓[/green]")
    except Exception:
        log.warning(
            "[yellow]Not logged in.[/yellow]  "
            "Please log in to SolarEdge in the browser window that just opened, "
            "then press [bold]Enter[/bold] here to continue …"
        )
        input("  → Press Enter after logging in: ")
        page.wait_for_load_state("networkidle")
        log.info("[green]Continuing …[/green]")


def _print_dry_run_table(sites):
    from rich.table import Table

    t = Table(show_header=True, header_style="bold cyan")
    t.add_column("Site ID")
    t.add_column("Site Name")
    t.add_column("Status")
    t.add_column("Peak Power (kW)")
    t.add_column("Inverters")
    t.add_column("Strings (API)")

    for s in sites:
        n_strings = sum(len(i.strings) for i in s.inverters)
        t.add_row(
            str(s.id),
            s.name,
            s.status,
            str(s.peak_power),
            str(len(s.inverters)),
            str(n_strings),
        )

    console.print(t)


if __name__ == "__main__":
    main()