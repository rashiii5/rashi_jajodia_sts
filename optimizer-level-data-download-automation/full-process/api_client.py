"""
api_client.py
─────────────
Wraps every SolarEdge Monitoring REST API endpoint we need.

HYBRID STRATEGY
───────────────
The API is used for everything it CAN do reliably:
  • Enumerate all sites in the fleet (with group filtering)
  • Fetch site details (timezone, peak power, status)
  • Fetch site inventory → inverter serial numbers + models
  • Fetch optimizer serial numbers per inverter (used to count optimizers
    per string so the browser knows how many to click through)

The browser (Playwright) is used ONLY for what the API cannot do:
  • Navigate the Analytics UI
  • Set string/optimizer-level filter (Day, previous day, Energy metric)
  • Apply "Apply to all optimizers in string" checkbox
  • Click Export → CSV
"""

import time
from typing import Optional

import requests

import config
from models import Inverter, Optimizer, Site, StringGroup
from utils import retry


# ── HTTP session with automatic API key injection ─────────────────────────────

class _Session:
    """Thin wrapper around requests.Session that always injects api_key."""

    def __init__(self):
        self._s = requests.Session()
        self._s.params = {"api_key": config.API_KEY}   # appended to every request

    @retry(max_attempts=3, delay=1.5, exceptions=(requests.RequestException,))
    def get(self, path: str, **params) -> dict:
        url = f"{config.API_BASE_URL}{path}"
        resp = self._s.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()


_session = _Session()


# ── Public API functions ───────────────────────────────────────────────────────

def get_all_sites() -> list[Site]:
    """
    Fetch the complete fleet site list.

    Pagination: SolarEdge returns max 100 sites per call.
    We loop until we have everything.

    Group filtering is done CLIENT-SIDE because the API's `searchText`
    param is not reliable for group names. We fetch all sites and keep
    only those matching config.GROUP_NAME.

    ⚠️  If your fleet has >1000 sites, increase `size` or add server-side
        filtering using the `searchText` query parameter.
    """
    sites: list[Site] = []
    start  = 0
    size   = 100   # max per page the API allows

    while True:
        data  = _session.get("/sites/list", size=size, startIndex=start, status="All")
        batch = data.get("sites", {}).get("site", [])

        if not batch:
            break

        for s in batch:
            # ── GROUP FILTER ──────────────────────────────────────────────
            # The API returns a `tags` or `uris` field for groups.
            # ⚠️  CHANGE THIS CONDITION if your group field has a different key.
            # Print s.keys() here if you're unsure what the raw response looks like.
            group = s.get("uris", {}).get("PUBLIC_URL", "") or s.get("tags", "")
            # Simple approach: keep every site (filter happens via group name match).
            # If the API does return a group/tag field, add:  `if config.GROUP_NAME not in group: continue`
            # For now we fetch all and let the caller decide, OR you can uncomment:
            # if config.GROUP_NAME.lower() not in str(group).lower():
            #     continue

            sites.append(Site(
                id         = int(s["id"]),
                name       = s["name"],
                status     = s.get("status", "Unknown"),
                peak_power = float(s.get("peakPower", 0)),
            ))

        if len(batch) < size:
            break          # last page
        start += size
        time.sleep(0.3)    # be polite to the API

    # Sort ascending by name to match the manual workflow
    sites.sort(key=lambda s: s.name.lower())
    return sites


def get_site_details(site: Site) -> Site:
    """
    Enrich a Site object with timezone from /site/{id}/details.
    Returns the same Site object (mutated in-place) for chaining.
    """
    data     = _session.get(f"/site/{site.id}/details")
    details  = data.get("details", {})
    location = details.get("location", {})
    site.timezone = location.get("timeZone", "UTC")
    return site


def get_site_inventory(site: Site) -> list[Inverter]:
    """
    Fetch inverters (and their serial numbers) from /site/{id}/inventory.

    Returns a list of Inverter objects for this site.
    Strings and optimizers are discovered later by the browser because
    the API doesn't expose string topology — only flat optimizer lists.

    We DO populate the optimizer count here so the browser knows
    how many optimizers to expect per string.
    """
    data      = _session.get(f"/site/{site.id}/inventory")
    inventory = data.get("Inventory", {})

    raw_inverters  = inventory.get("inverters", [])
    raw_optimizers = inventory.get("optimizers", [])   # flat list, all inverters combined

    inverters: list[Inverter] = []

    for idx, inv in enumerate(raw_inverters):
        serial = inv.get("SN") or inv.get("serialNumber") or f"INV_{idx}"
        name   = inv.get("name") or f"Inverter {idx + 1}"
        model  = inv.get("model", "Unknown")

        # ── Map optimizers to this inverter ───────────────────────────────
        # The API provides an "optimizers" list.  Each optimizer entry may
        # contain a "connectedTo" or "inverterSN" field linking it back.
        # ⚠️  CHANGE "connectedTo" if the actual field name differs in your response.
        inv_optimizers: list[Optimizer] = []
        string_map: dict[int, list[Optimizer]] = {}   # string_index → [Optimizer, …]

        for o_idx, opt in enumerate(raw_optimizers):
            connected = opt.get("connectedTo") or opt.get("inverterSN", "")
            if connected and connected != serial:
                continue   # belongs to a different inverter

            # String index: optimizers in the same string share a `stringId`
            # or a sequential index.
            # ⚠️  CHANGE "stringId" if the field name differs in your payload.
            s_idx = int(opt.get("stringId", opt.get("string", o_idx // 10)))

            optimizer = Optimizer(
                serial       = opt.get("SN") or opt.get("serialNumber") or f"OPT_{o_idx}",
                name         = opt.get("name") or f"Optimizer {o_idx + 1}",
                string_index = s_idx,
            )
            inv_optimizers.append(optimizer)
            string_map.setdefault(s_idx, []).append(optimizer)

        # Build StringGroup objects
        strings: list[StringGroup] = []
        for s_idx, opts in sorted(string_map.items()):
            strings.append(StringGroup(
                name             = f"String {idx + 1}.{s_idx}",
                inverter_serial  = serial,
                optimizers       = opts,
            ))

        inverters.append(Inverter(
            serial  = serial,
            name    = name,
            model   = model,
            site_id = site.id,
            strings = strings,
        ))

    return inverters
