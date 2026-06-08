"""
discover_selectors.py
─────────────────────
Run this BEFORE running main.py to discover the correct CSS/text selectors
for your specific SolarEdge account's UI.

This script:
  1. Opens the persistent Chrome profile (already logged in)
  2. Navigates to the monitoring platform
  3. Pauses at Playwright Inspector so you can click elements
     and copy the generated selectors into processor.py

HOW TO USE
──────────
  1. Make sure you've already logged into SolarEdge once:
         python main.py --dry-run
     Then open Chrome manually with your profile.

  2. Run:
         python discover_selectors.py

  3. Playwright Inspector opens alongside Chrome.
     Navigate to any site → Analytics → Inverter.
     Click the "Pick" (crosshair) button in the Inspector,
     then click any element on the SolarEdge page.
     The Inspector shows the selector.  Copy it into processor.py.

  4. Press Resume in the Inspector when done with each pause point.
"""

import config
from playwright.sync_api import sync_playwright

PAUSE_POINTS = [
    "Open site and click Analytics — then press Resume",
    "Click an Inverter in the tree — then press Resume",
    "Click a String — then press Resume",
    "Click the first Optimizer — then press Resume",
    "Set filters (Day, Prev Day, Energy metric) — then press Resume",
    "Click Export and inspect dialog — then press Resume",
]


def main():
    pw  = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir  = str(config.PROFILE_DIR),
        headless       = False,
        slow_mo        = 200,
    )
    page = ctx.new_page()
    page.goto(config.MONITORING_URL, wait_until="networkidle")

    print("\n─── Selector Discovery Mode ───────────────────────────────────────")
    print("Use Playwright Inspector (crosshair icon) to click any UI element.")
    print("The selector is shown in the Inspector window.\n")

    for i, instruction in enumerate(PAUSE_POINTS, 1):
        print(f"  [{i}/{len(PAUSE_POINTS)}] {instruction}")
        page.pause()   # opens the Playwright Inspector UI

    print("\nDone!  Copy the selectors into processor.py.\n")
    ctx.close()
    pw.stop()


if __name__ == "__main__":
    main()
