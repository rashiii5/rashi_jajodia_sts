"""
utils.py
────────
Shared utility functions used across the project.
"""

import functools
import re
import time
from datetime import date, timedelta
from pathlib import Path

import config


# ── Retry decorator ───────────────────────────────────────────────────────────

def retry(max_attempts: int = 3, delay: float = 2.0, exceptions: tuple = (Exception,)):
    """
    Decorator: retries the wrapped function up to `max_attempts` times.
    Waits `delay * attempt_number` seconds between tries (exponential back-off).

    Usage:
        @retry(max_attempts=3, delay=2.0)
        def flaky_function(): ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        wait = delay * attempt
                        print(f"  ↩ {fn.__name__} attempt {attempt}/{max_attempts} failed "
                              f"({exc}). Retrying in {wait:.0f}s …")
                        time.sleep(wait)
            raise last_exc
        return wrapper
    return decorator


# ── Date helpers ──────────────────────────────────────────────────────────────

def previous_day() -> date:
    """Returns yesterday's date."""
    return date.today() - timedelta(days=1)


def fmt_date(d: date) -> str:
    """Format a date as YYYY-MM-DD (the format SolarEdge API accepts)."""
    return d.strftime("%Y-%m-%d")


def previous_day_str() -> str:
    return fmt_date(previous_day())


# ── File naming ───────────────────────────────────────────────────────────────

def _safe(text: str) -> str:
    """Strip characters that are illegal in filenames."""
    return re.sub(r'[<>:"/\\|?*]', "_", text).strip()


def make_download_dir(date_str: str) -> Path:
    """
    Create and return  downloads/YYYY-MM-DD/
    All CSVs for a run go into the same date folder.
    """
    folder = config.DOWNLOAD_DIR / date_str
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def csv_filename(site_name: str, inverter_name: str, date_str: str) -> str:
    """
    Build a human-readable CSV filename.
    Example:  Greenfield_Primary_School-Inverter_1-2026-06-03.csv
    """
    return f"{_safe(site_name)}-{_safe(inverter_name)}-{date_str}.csv"
