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

CHANGES FROM ORIGINAL
─────────────────────
• DownloadTracker is created here and threaded through process_all_sites().
• log.print_summary(tracker) receives the tracker so it can render the
  new per-site table.
• A run-summary CSV is written to logs/<run_id>_summary.csv after the run.
• Everything else (arg parsing, API steps, browser login) is unchanged.
"""

import csv
import argparse
import sys
from datetime import datetime, timezone

import config          # validates .env on import
import api_client
import browser
import processor
from logger import get_logger, console
from tracker import DownloadTracker
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
    run_id   = datetime.now().strftime("run_%Y-%m-%dT%H-%M-%S")
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    log      = get_logger(run_id)

    # ── Master download tracker ───────────────────────────────────────────────
    tracker = DownloadTracker()

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
            site.inverters = []
    # ── STEP 2b: Fetch additional API data and write api_deets CSV ───────────
    log.section("Step 2b — Writing api_deets CSV")
    _write_api_deets_csv(all_sites, date_str, log)

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
            tracker  = tracker,    # ← new: pass tracker in
        )

    # ── STEP 4: Summary ───────────────────────────────────────────────────────
    log.print_summary(tracker=tracker)    # ← new: tracker-aware summary

    # Write machine-readable run summary CSV
    summary_csv = config.LOG_DIR / f"{run_id}_summary.csv"
    tracker.write_run_csv(summary_csv)
    log.info(f"Run summary CSV: {summary_csv}")


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS  (both UNCHANGED from original)
# ──────────────────────────────────────────────────────────────────────────────

def _ensure_logged_in(page, log):
    """
    Navigate to the monitoring platform.  If the login page appears,
    pause and let the user log in manually (only needed once per profile).
    """
    log.info("Opening monitoring platform …")
    page.goto(config.MONITORING_URL, wait_until="networkidle", timeout=30_000)

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

def _write_api_deets_csv(sites, date_str: str, log):
    """
    Fetch overview, energy, and equipment telemetry for each site/inverter,
    then write downloads/<date_str>/api_deets_<date_str>.csv.

    Reuses already-enriched Site objects (details + inventory already fetched).
    Makes minimal additional API calls: overview, energy, telemetry per inverter.
    Does NOT modify any existing data structures or outputs.
    """
    from utils import make_download_dir

    download_dir = make_download_dir(date_str)
    out_path = download_dir / f"api_deets_{date_str}.csv"

    fieldnames = [
        "site_id", "site_name", "site_status", "peak_power", "alert_count",
        "site_timezone", "installed_capacity", "energy_today_kwh",
        "current_site_power_kw", "energy_yesterday_kwh", "inverter_name",
        "inverter_serial", "inverter_inventory_status", "connected_optimizers",
        "inverter_ac_power_kw", "inverter_dc_voltage", "inverter_mode",
        "telemetry_timestamp",
    ]

    rows = []

    for site in sites:
        # ── Data already in Site object (from Steps 1 & 2) ───────────────────
        base = {
            "site_id":          site.id,
            "site_name":        site.name,
            "site_status":      site.status,
            "peak_power":       site.peak_power,
            "site_timezone":    site.timezone,
            # alert_count not stored on Site model; fetched below via overview
            "installed_capacity": site.peak_power,   # fallback; overwritten below
        }

        # ── /site/{id}/overview ───────────────────────────────────────────────
        try:
            overview = api_client.get_site_overview(site.id)
            energy_today_wh   = (overview.get("lastDayData") or {}).get("energy", 0) or 0
            current_power_w   = (overview.get("currentPower") or {}).get("power", 0) or 0
            alert_count       = overview.get("alertQuantity", "")
            base["energy_today_kwh"]       = round(energy_today_wh / 1000, 4)
            base["current_site_power_kw"]  = round(current_power_w / 1000, 4)
            base["alert_count"]            = alert_count
        except Exception as exc:
            log.warning(f"  api_deets: overview failed for {site.name}: {exc}")
            base["energy_today_kwh"]      = ""
            base["current_site_power_kw"] = ""
            base["alert_count"]           = ""

        # ── /site/{id}/energy (yesterday) ────────────────────────────────────
        try:
            base["energy_yesterday_kwh"] = api_client.get_site_energy_yesterday(site.id, date_str)
        except Exception as exc:
            log.warning(f"  api_deets: energy failed for {site.name}: {exc}")
            base["energy_yesterday_kwh"] = ""

        # ── Per-inverter rows ─────────────────────────────────────────────────
        if not site.inverters:
            # No inverters — write one row with site-level data only
            row = dict(base)
            row.update({
                "inverter_name": "", "inverter_serial": "",
                "inverter_inventory_status": "", "connected_optimizers": "",
                "inverter_ac_power_kw": "", "inverter_dc_voltage": "",
                "inverter_mode": "", "telemetry_timestamp": "",
                "installed_capacity": "",
            })
            rows.append(row)
            continue

        for inv in site.inverters:
            row = dict(base)
            row["inverter_name"]   = inv.name
            row["inverter_serial"] = inv.serial

            # connected_optimizers = total optimizers across all strings
            total_opts = sum(len(s.optimizers) for s in inv.strings)
            row["inverter_inventory_status"] = ""   # not stored on Inverter model
            row["connected_optimizers"]      = total_opts if total_opts else ""

            # ── /equipment/{siteId}/{serial}/data ────────────────────────────
            try:
                telem = api_client.get_equipment_telemetry(site.id, inv.serial, date_str)
                if telem:
                    # AC power: sum phase values if present, else totalActivePower
                    l1 = (telem.get("L1Data") or {}).get("activePower", 0) or 0
                    l2 = (telem.get("L2Data") or {}).get("activePower", 0) or 0
                    l3 = (telem.get("L3Data") or {}).get("activePower", 0) or 0
                    ac_w = l1 + l2 + l3
                    if ac_w == 0:
                        ac_w = telem.get("totalActivePower") or telem.get("activePower") or 0
                    row["inverter_ac_power_kw"] = round(ac_w / 1000, 4) if ac_w else ""
                    row["inverter_dc_voltage"]  = telem.get("dcVoltage", "")
                    row["inverter_mode"]        = (
                        telem.get("inverterMode") or telem.get("status", "")
                    )
                    row["telemetry_timestamp"]  = (
                        telem.get("date") or telem.get("dateTime") or telem.get("time", "")
                    )
                    # installed_capacity from telemetry context not available here;
                    # keep peak_power as installed_capacity (already set in base)
                else:
                    row["inverter_ac_power_kw"] = ""
                    row["inverter_dc_voltage"]  = ""
                    row["inverter_mode"]        = ""
                    row["telemetry_timestamp"]  = ""
            except Exception as exc:
                log.warning(
                    f"  api_deets: telemetry failed for {site.name}/{inv.serial}: {exc}"
                )
                row["inverter_ac_power_kw"] = ""
                row["inverter_dc_voltage"]  = ""
                row["inverter_mode"]        = ""
                row["telemetry_timestamp"]  = ""

            rows.append(row)

    # ── Write CSV ─────────────────────────────────────────────────────────────
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    log.info(f"api_deets CSV written: {out_path}")


if __name__ == "__main__":
    main()