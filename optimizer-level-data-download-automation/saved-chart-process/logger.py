"""
logger.py
─────────
Structured logging with three outputs:
  1. Rich-formatted console (human-readable, colour-coded)
  2. Plain-text log file with timestamps  (logs/<run_id>.log)
  3. JSON-lines file  (logs/<run_id>.jsonl)  — one JSON object per site result

CHANGES FROM ORIGINAL
─────────────────────
• print_summary() now accepts an optional DownloadTracker and delegates
  the Rich table to it.  When no tracker is passed the method falls back
  to the original ExportResult-based table (backwards compatible).
• Everything else is unchanged.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

import config
from models import ExportResult, Site

if TYPE_CHECKING:
    from tracker import DownloadTracker, SiteRecord

console = Console()

_FILE_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(run_id: str) -> "RunLogger":
    return RunLogger(run_id)


class RunLogger:
    """
    Wraps Python's stdlib `logging` module to provide:
      • pretty Rich output on the console
      • timestamped plain-text lines in  logs/<run_id>.log
      • structured JSONL records in      logs/<run_id>.jsonl
    """

    def __init__(self, run_id: str):
        self.run_id   = run_id
        self.results: list[ExportResult] = []   # kept for backward compat

        self.log_path   = config.LOG_DIR / f"{run_id}.log"
        self.jsonl_path = config.LOG_DIR / f"{run_id}.jsonl"
        self.log_path.touch()
        self.jsonl_path.touch()

        self._logger = logging.getLogger(f"solaredge.{run_id}")
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        if self._logger.handlers:
            self._logger.handlers.clear()

        # ── Handler 1: Rich console ───────────────────────────────────────────
        rich_handler = RichHandler(console=console, show_path=False, markup=True)
        rich_handler.setLevel(logging.DEBUG)
        self._logger.addHandler(rich_handler)

        # ── Handler 2: Plain-text file ────────────────────────────────────────
        file_handler = logging.FileHandler(self.log_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(fmt=_FILE_FORMAT, datefmt=_DATE_FORMAT)
        )
        self._logger.addHandler(file_handler)

    # ── Public logging helpers (all UNCHANGED) ────────────────────────────────

    def debug(self, msg: str):
        self._logger.debug(msg)

    def info(self, msg: str):
        self._logger.info(msg)

    def warning(self, msg: str):
        self._logger.warning(msg)

    def error(self, msg: str):
        self._logger.error(msg)

    def section(self, title: str):
        """Print a visual section divider to the console."""
        console.rule(f"[bold cyan]{title}[/bold cyan]")
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n{'─' * 60}\n  {title}\n{'─' * 60}\n")

    def site_start(self, site: Site):
        self._logger.info(
            f"[cyan]→ Processing:[/cyan] [bold]{site.name}[/bold] (ID: {site.id})"
        )

    # ── Legacy ExportResult helpers (kept for backward compat) ────────────────

    def site_ok(self, result: ExportResult):
        self.results.append(result)
        self._logger.info(
            f"[green]✓[/green] {result.site_name} / {result.inverter_name} — "
            f"{result.strings_found} strings, {result.optimizers_found} optimizers → "
            f"[green]{result.file_path}[/green]"
        )
        self._write_jsonl_export(result)

    def site_fail(self, result: ExportResult):
        self.results.append(result)
        self._logger.error(
            f"[red]✗[/red] {result.site_name} / {result.inverter_name} — "
            f"[red]{result.error}[/red]"
        )
        self._write_jsonl_export(result)

    # ── Summary (updated to support DownloadTracker) ──────────────────────────

    def print_summary(self, tracker: Optional["DownloadTracker"] = None):
        """
        Print a final summary after all sites are processed.

        If a DownloadTracker is provided it is used for the table (new workflow).
        Otherwise falls back to the original ExportResult-based table.
        """
        self.section("Run Summary")

        if tracker is not None:
            # ── New tracker-based summary ─────────────────────────────────────
            console.print(tracker.summary_table())
            s = tracker.stats()
            console.print(
                f"\n[bold]Total:[/bold] {s['total']} sites — "
                f"[green]{s['exported']} exported[/green]  "
                f"[yellow]{s['missing']} missing chart (empty CSV)[/yellow]  "
                f"[red]{s['failed']} failed[/red]\n"
            )
            self._logger.info(
                f"Run complete — {s['total']} sites: "
                f"{s['exported']} exported, "
                f"{s['missing']} missing chart, "
                f"{s['failed']} failed"
            )
            # Write tracker records to JSONL
            for rec in tracker.records:
                self._write_jsonl_site_record(rec)

        else:
            # ── Legacy ExportResult summary (unchanged from original) ──────────
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Site")
            table.add_column("Inverter")
            table.add_column("Strings")
            table.add_column("Optimizers")
            table.add_column("Status")
            table.add_column("File / Error")

            success = fail = 0
            for r in self.results:
                status_str = (
                    "[green]SUCCESS[/green]" if r.status == "SUCCESS"
                    else "[red]FAILED[/red]"
                )
                detail = r.file_path if r.status == "SUCCESS" else r.error
                table.add_row(
                    r.site_name, r.inverter_name,
                    str(r.strings_found), str(r.optimizers_found),
                    status_str, detail,
                )
                if r.status == "SUCCESS":
                    success += 1
                else:
                    fail += 1

            console.print(table)
            console.print(
                f"\n[bold]Total:[/bold] {len(self.results)} exports — "
                f"[green]{success} succeeded[/green], [red]{fail} failed[/red]\n"
            )
            self._logger.info(
                f"Run complete — {len(self.results)} exports: "
                f"{success} succeeded, {fail} failed"
            )

        self._logger.info(f"Log file : {self.log_path}")
        self._logger.info(f"JSONL    : {self.jsonl_path}")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write_jsonl_export(self, result: ExportResult):
        """Legacy: append one ExportResult as a JSON line."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id":    self.run_id,
            **result.__dict__,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def _write_jsonl_site_record(self, rec: "SiteRecord"):
        """New: append one SiteRecord as a JSON line."""
        entry = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "run_id":      self.run_id,
            "site_id":     rec.site_id,
            "site_name":   rec.site_name,
            "status":      rec.status,
            "chart_found": rec.chart_found,
            "exported":    rec.exported,
            "empty_csv":   rec.empty_csv,
            "failed":      rec.failed,
            "file_path":   rec.file_path,
            "error":       rec.error,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")