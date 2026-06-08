"""
tracker.py
──────────
Centralized download and run tracking.

DESIGN RATIONALE
────────────────
Rather than introducing a foreign pattern, this module extends the existing
ExportResult → RunLogger.results → print_summary() pipeline cleanly:

  • SiteRecord  — one record per site, holds outcome fields + the file path
  • DownloadTracker  — owns the list of SiteRecords, provides query helpers
                       and a final summary dict consumed by RunLogger

DownloadTracker is created once in main.py and passed into process_all_sites().
RunLogger.print_summary() delegates the table to DownloadTracker.summary_table()
so no duplicate logic exists.

Status values (string literals kept consistent throughout):
    "EXPORTED"    — ProdEnergy found, CSV downloaded successfully
    "EMPTY_CSV"   — ProdEnergy not found, empty placeholder CSV written
    "FAILED"      — unexpected error during the attempt
    "SKIPPED"     — site had no inverters / explicitly skipped upstream
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rich.table import Table
from rich.console import Console

console = Console()


# ──────────────────────────────────────────────────────────────────────────────
#  DATA MODEL
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SiteRecord:
    """One record per site processed in a run."""

    site_id:         int
    site_name:       str

    # Outcome flags (set by processor)
    chart_found:     bool  = False   # was ProdEnergy chart found in Saved Charts?
    exported:        bool  = False   # was CSV download successful?
    empty_csv:       bool  = False   # was an empty placeholder CSV written?
    failed:          bool  = False   # did an unexpected error occur?

    # Paths and messages
    file_path:       str   = ""      # absolute path to downloaded/created file
    error:           str   = ""      # error description if failed=True

    @property
    def status(self) -> str:
        """Human-readable status string derived from the flags."""
        if self.exported:
            return "EXPORTED"
        if self.empty_csv:
            return "EMPTY_CSV"
        if self.failed:
            return "FAILED"
        return "SKIPPED"

    @property
    def chart_status(self) -> str:
        return "✓ found" if self.chart_found else "✗ missing"


# ──────────────────────────────────────────────────────────────────────────────
#  TRACKER
# ──────────────────────────────────────────────────────────────────────────────

class DownloadTracker:
    """
    Central registry for every site processed in a run.

    Usage (in processor.py):
        tracker.record_exported(site, path)
        tracker.record_missing_chart(site, path)
        tracker.record_failed(site, error)

    Usage (in logger.py / main.py):
        tracker.print_summary()
        stats = tracker.stats()
    """

    def __init__(self):
        self._records: list[SiteRecord] = []

    # ── Write helpers (called by processor) ───────────────────────────────────

    def record_exported(self, site_id: int, site_name: str, file_path: str) -> SiteRecord:
        """ProdEnergy chart was found and CSV was downloaded."""
        rec = SiteRecord(
            site_id    = site_id,
            site_name  = site_name,
            chart_found = True,
            exported   = True,
            file_path  = file_path,
        )
        self._records.append(rec)
        return rec

    def record_missing_chart(self, site_id: int, site_name: str, file_path: str) -> SiteRecord:
        """ProdEnergy chart was NOT found; an empty CSV placeholder was written."""
        rec = SiteRecord(
            site_id    = site_id,
            site_name  = site_name,
            chart_found = False,
            empty_csv  = True,
            file_path  = file_path,
        )
        self._records.append(rec)
        return rec

    def record_failed(self, site_id: int, site_name: str, error: str) -> SiteRecord:
        """An unexpected error occurred for this site."""
        rec = SiteRecord(
            site_id   = site_id,
            site_name = site_name,
            failed    = True,
            error     = error,
        )
        self._records.append(rec)
        return rec

    # ── Query helpers ──────────────────────────────────────────────────────────

    @property
    def records(self) -> list[SiteRecord]:
        return list(self._records)   # defensive copy

    def stats(self) -> dict:
        """Return a summary dict — consumed by RunLogger.print_summary()."""
        total    = len(self._records)
        exported = sum(1 for r in self._records if r.exported)
        missing  = sum(1 for r in self._records if r.empty_csv)
        failed   = sum(1 for r in self._records if r.failed)
        skipped  = total - exported - missing - failed
        return {
            "total":    total,
            "exported": exported,
            "missing":  missing,
            "failed":   failed,
            "skipped":  skipped,
        }

    # ── Display helpers ────────────────────────────────────────────────────────

    def summary_table(self) -> Table:
        """Build a Rich table for the final run summary."""
        table = Table(show_header=True, header_style="bold magenta", expand=False)
        table.add_column("Site ID",     style="dim",         no_wrap=True)
        table.add_column("Site Name",                        no_wrap=False)
        table.add_column("Chart",       justify="center")
        table.add_column("Status",      justify="center")
        table.add_column("File / Error")

        STATUS_STYLE = {
            "EXPORTED":  "[green]EXPORTED[/green]",
            "EMPTY_CSV": "[yellow]EMPTY_CSV[/yellow]",
            "FAILED":    "[red]FAILED[/red]",
            "SKIPPED":   "[dim]SKIPPED[/dim]",
        }

        for r in self._records:
            detail = r.file_path if not r.failed else r.error
            table.add_row(
                str(r.site_id),
                r.site_name,
                r.chart_status,
                STATUS_STYLE.get(r.status, r.status),
                detail,
            )

        return table

    def print_summary(self):
        """Print the full summary table + totals to the Rich console."""
        console.rule("[bold cyan]Run Summary[/bold cyan]")
        console.print(self.summary_table())

        s = self.stats()
        parts = [
            f"[bold]Total sites:[/bold] {s['total']}",
            f"[green]{s['exported']} exported[/green]",
            f"[yellow]{s['missing']} missing chart (empty CSV)[/yellow]",
            f"[red]{s['failed']} failed[/red]",
        ]
        if s["skipped"]:
            parts.append(f"[dim]{s['skipped']} skipped[/dim]")

        console.print("  " + "  •  ".join(parts) + "\n")

    # ── Persistence ───────────────────────────────────────────────────────────

    def write_run_csv(self, path: Path):
        """
        Write the tracker contents to a machine-readable run-summary CSV.
        Useful for post-processing / auditing.
        """
        fieldnames = [
            "site_id", "site_name", "status",
            "chart_found", "exported", "empty_csv", "failed",
            "file_path", "error",
        ]
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in self._records:
                writer.writerow({
                    "site_id":    r.site_id,
                    "site_name":  r.site_name,
                    "status":     r.status,
                    "chart_found": r.chart_found,
                    "exported":   r.exported,
                    "empty_csv":  r.empty_csv,
                    "failed":     r.failed,
                    "file_path":  r.file_path,
                    "error":      r.error,
                })