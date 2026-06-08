# SolarEdge Hybrid Automation

Automates optimizer/string-level Analytics CSV exports from the SolarEdge
Monitoring Platform for all sites in a group.

**Architecture:** SolarEdge REST API handles fleet discovery (sites, inverters).
Playwright handles only what the API cannot: Analytics navigation + CSV export.

---

## Project Structure

```
solaredge-automation/
├── main.py                  ← Entry point. Run this.
├── config.py                ← Reads settings from .env
├── api_client.py            ← SolarEdge REST API wrapper
├── browser.py               ← Playwright context manager
├── processor.py             ← Core automation logic + ALL SELECTORS
├── models.py                ← Data structures (Site, Inverter, etc.)
├── logger.py                ← Console + JSON file logging
├── utils.py                 ← Retry decorator, date helpers, filename builder
├── discover_selectors.py    ← Helper to find correct CSS selectors
├── .env.example             ← Template — copy to .env and fill in
├── requirements.txt
├── downloads/               ← CSVs land here (created automatically)
│   └── YYYY-MM-DD/
│       └── SiteName-Inverter1-YYYY-MM-DD.csv
└── logs/
    └── run_YYYY-MM-DDTHH-MM-SS.jsonl
```

---

## Setup (one time)

### 1. Install Python dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Create your .env file

```bash
cp .env.example .env
```

Edit `.env`:
```
SE_API_KEY=your_actual_api_key_here
SE_GROUP_NAME=STS Installed
```

Get your API key from:  
*SolarEdge Monitoring Portal → Admin → API Access*

### 3. First login

```bash
python main.py
```

Chrome opens. Log in to SolarEdge manually. Press **Enter** in the terminal.  
From now on, the session persists — no further logins needed.

---

## Usage

```bash
# Normal run — previous day data for all sites in your group
python main.py

# Test with one site first (get the site ID from the URL when you open it)
python main.py --site-id 12345

# Dry run — enumerate sites via API only, no browser
python main.py --dry-run

# Override the date
python main.py --date 2026-06-01
```

---

## ⚠️ Before First Full Run: Verify Selectors

The selectors in `processor.py` are best guesses.  
**You must verify them against your live account's UI.**

```bash
python discover_selectors.py
```

This opens Chrome + Playwright Inspector.  
Click the crosshair → click any element → copy the selector → paste into `processor.py`.

Selectors to verify (all marked `# ⚠️ VERIFY` in processor.py):

| Variable | What it targets |
|---|---|
| `SEL_ANALYTICS_NAV` | Left nav "Analytics" link |
| `SEL_INVERTER_ITEM` | Inverter row in tree |
| `SEL_STRING_ITEM` | String row in expanded inverter |
| `SEL_FIRST_OPTIMIZER` | First optimizer in a string |
| `SEL_TIMERANGE_DROPDOWN` | Day/Week/Month selector |
| `SEL_PREV_DAY_BTN` | Back-arrow date navigator |
| `SEL_METRIC_DROPDOWN` | Production → Energy selector |
| `SEL_APPLY_ALL_CB` | "Apply to all optimizers" checkbox |
| `SEL_EXPORT_BTN` | Export button |
| `SEL_EXPORT_PREV_DAY` | "Previous day" in export dialog |
| `SEL_EXPORT_1DAY_RES` | "1 day" resolution in export dialog |
| `SEL_EXPORT_CONFIRM` | Final Export/Download button |

---

## Output

### Downloads

```
downloads/
└── 2026-06-03/
    ├── Greenfield_Primary_School-Inverter_1-2026-06-03.csv
    ├── Greenfield_Primary_School-Inverter_2-2026-06-03.csv
    └── City_Library-Inverter_1-2026-06-03.csv
```

### Logs

Console shows live progress with colour-coded status.  
JSON log for each run:

```jsonl
{"timestamp":"2026-06-04T03:00:01Z","run_id":"run_2026-06-04T03-00-00","site_name":"Greenfield Primary","inverter_name":"Inverter 1","strings_found":4,"optimizers_found":40,"status":"SUCCESS","file_path":"downloads/2026-06-03/...csv"}
{"timestamp":"2026-06-04T03:02:11Z","run_id":"run_2026-06-04T03-00-00","site_name":"City Library","inverter_name":"Inverter 1","strings_found":0,"optimizers_found":0,"status":"FAILED","error":"Timeout waiting for SEL_STRING_ITEM"}
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing required environment variable: SE_API_KEY` | Copy `.env.example` → `.env` and set `SE_API_KEY` |
| `Not logged in` on every run | Your profile dir changed, or cookies expired — log in once manually |
| Download dialog appears (OS file picker) | `accept_downloads=True` should prevent this; check Playwright version |
| All sites fail with TimeoutError | A selector is wrong — run `discover_selectors.py` |
| CAPTCHA appears | Add `--disable-blink-features=AutomationControlled` in `browser.py` args |
| Group filter not working | Set `SE_GROUP_NAME` to exactly what appears in the UI, including caps |

---

## Customisation

### Change file naming

Edit `utils.py → csv_filename()`.

### Slow down for stability

In `.env`: `SE_SLOW_MO=600`

### Add email/WhatsApp alert on failure

In `main.py`, after `log.print_summary()`, inspect `results` and call your
notification service for any `r.status == "FAILED"` entries.

### Schedule it

Since Chrome stays logged in, you can run `python main.py` from Task Scheduler
(Windows), cron (Linux/macOS), or any workflow scheduler.
