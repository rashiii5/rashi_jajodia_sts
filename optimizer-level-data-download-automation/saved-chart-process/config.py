"""
config.py
─────────
Loads all configuration from the .env file.
Edit .env (not this file) to change settings.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (same directory as this file)
load_dotenv(Path(__file__).parent / ".env")


def _require(key: str) -> str:
    """Read an env var; raise a clear error if it's missing."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"\n\n  Missing required environment variable: {key}\n"
            f"  → Copy .env.example → .env and fill in '{key}'\n"
        )
    return val


# ── SolarEdge API ─────────────────────────────────────────────────────────────
API_KEY      = _require("SE_API_KEY")          # Your installer API key
API_BASE_URL = os.getenv("SE_API_BASE", "https://monitoringapi.solaredge.com")

# ── Site filtering ────────────────────────────────────────────────────────────
# Must match the "Group" label exactly as shown in the monitoring platform
GROUP_NAME = os.getenv("SE_GROUP_NAME", "STS Installed")

# ── Playwright browser settings ───────────────────────────────────────────────
PROFILE_DIR   = Path(os.path.expanduser(
    os.getenv("SE_PROFILE_DIR", "~/.solaredge-browser-profile")
))
HEADLESS      = os.getenv("SE_HEADLESS", "false").lower() == "true"
SLOW_MO       = int(os.getenv("SE_SLOW_MO", "300"))   # ms between actions
MAX_RETRIES   = int(os.getenv("SE_MAX_RETRIES", "3"))

# ── File paths ────────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent
DOWNLOAD_DIR  = Path(os.getenv("SE_DOWNLOAD_DIR", str(PROJECT_ROOT / "downloads")))
LOG_DIR       = PROJECT_ROOT / "logs"

# Create directories if they don't exist
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

# ── SolarEdge Monitoring URL ──────────────────────────────────────────────────
# ⚠️  CHANGE THIS if the URL in your browser differs after login
MONITORING_URL = "https://monitoring.solaredge.com"
