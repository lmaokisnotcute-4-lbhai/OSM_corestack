# -*- coding: utf-8 -*-
"""
create_wiki_pages.py

Creates OpenStreetMap Wiki pages for a watershed and all its microwatersheds
from the GeoJSON output of pipeline.py.

Creates:
  - One watershed summary page:  CoREStack/Watershed/<WATERSHED_ID>
  - One MWS page per feature:    CoREStack/MWS/<uid>

Usage:
    python create_wiki_pages.py --watershed_id C2AGAN72 --geojson C2AGAN72_microwatersheds.geojson

Credentials are read from environment variables (or .env file):
    WIKI_USER           your OSM wiki username  e.g. Sweek10
    WIKI_BOT_NAME       your bot name           e.g. core_osm
    WIKI_BOT_PASS       your bot password
    CORESTACK_API_KEY   your CoREStack API key

Requirements:
    pip install mwclient python-dotenv requests
"""

import argparse
import os
import sys
import json
import re
import time

import mwclient
import requests
from dotenv import load_dotenv

load_dotenv()

WIKI_HOST       = "wiki.openstreetmap.org"
BASE_URL        = "https://geoserver.core-stack.org/api/v1/"
REPORT_ENDPOINT = "get_mws_report/"

METRICS = ["Precipitation", "RunOff", "ET", "DeltaG", "G", "WellDepth"]

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_wsconc(wsconc: str) -> tuple[str, str]:
    """
    Parse a wsconc like 'C2AGAN72' into (sub_basin_code, watershed_code).
    Examples:
      C2AGAN72  →  sub_basin='GAN', ws_code='72'
      B3KON15   →  sub_basin='KON', ws_code='15'
    """
    m = re.search(r'([A-Z]+)(\d+)$', wsconc)
    if m:
        return m.group(1), m.group(2)
    return wsconc, ""


def get_yearly_metrics(props: dict) -> dict[str, dict]:
    """
    Reconstruct yearly water balance dicts from the flat GeoJSON columns.

    The GeoJSON from pipeline.py stores data as flat columns:
        DeltaG_2017_2018, ET_2017_2018, Precipitation_2017_2018, ...

    This function groups them back into:
        { '2017_2018': {'DeltaG': ..., 'ET': ..., ...}, ... }

    sorted chronologically.
    """
    yearly = {}

    for key, value in props.items():
        # Match pattern: MetricName_YYYY_YYYY  e.g. DeltaG_2017_2018
        m = re.match(r'^([A-Za-z]+)_(\d{4}_\d{4})$', key)
        if not m:
            continue
        metric    = m.group(1)   # e.g. "DeltaG"
        year_key  = m.group(2)   # e.g. "2017_2018"

        if metric not in METRICS:
            continue

        if year_key not in yearly:
            yearly[year_key] = {}
        yearly[year_key][metric] = value

    return dict(sorted(yearly.items()))  # chronological order


def get_most_recent_year(yearly: dict) -> tuple[str, dict] | tuple[None, None]:
    """Return (year_key, metrics) for the most recent year, or (None, None)."""
    if not yearly:
        return None, None
    most_recent = sorted(yearly.keys())[-1]
    return most_recent, yearly[most_recent]


def fmt(val) -> str:
    """Format a metric value to 2 decimal places."""
    if val is None or val == "N/A":
        return "N/A"
    try:
        return f"{float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def format_year_label(key: str) -> str:
    """'2017_2018' → '2017–2018'"""
    return key.replace("_", "–")


# ─────────────────────────────────────────────────────────────────────────────
# CoREStack MWS Report API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_mws_report_url(props: dict, api_key: str) -> str | None:
    """
    Call get_mws_report/ API and return the report URL or None.
    Reads state, district, tehsil, mws_id from GeoJSON feature properties.
    """
    state    = props.get("state")    or props.get("STATE")    or props.get("State")
    district = props.get("district") or props.get("District") or props.get("DISTRICT")
    tehsil   = props.get("tehsil")   or props.get("Tehsil")   or props.get("TEHSIL")
    mws_id   = props.get("uid")      or props.get("mws_id")   or props.get("id")

    if not all([state, district, tehsil, mws_id]):
        return None

    params  = {"state": state, "district": district, "tehsil": tehsil, "mws_id": str(mws_id)}
    headers = {"X-API-Key": api_key}

    try:
        resp = requests.get(BASE_URL + REPORT_ENDPOINT, params=params,
                            headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("Mws_report_url")
            if isinstance(data, dict):
                return data.get("Mws_report_url")
    except Exception as e:
        print(f"   ⚠️  Report API error for {mws_id}: {e}")

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Wiki page content builders
# ─────────────────────────────────────────────────────────────────────────────

def build_mws_page(uid: str, props: dict, watershed_id: str,
                   report_url: str | None) -> str:
    """
    Build wikitext for a single MWS page.

    Layout:
      == Micro-Watershed: <uid> ==
      Overview table: MWS ID, Parent Watershed, Area, CoREStack Report

      === Most Recent Water Balance (YYYY–YYYY) ===
      Table with the latest year's metrics

      === Full Water Balance History ===
      Sortable table with all years
    """
    ws_page = f"CoREStack/Watershed/{watershed_id}"
    area    = props.get("area_in_ha", props.get("area", "N/A"))
    yearly  = get_yearly_metrics(props)
    recent_year, recent_metrics = get_most_recent_year(yearly)

    lines = []

    # ── Title ────────────────────────────────────────────────────
    lines.append(f"== Micro-Watershed: {uid} ==")
    lines.append("")

    # ── Overview table ───────────────────────────────────────────
    lines.append('{| class="wikitable"')
    lines.append("! Property !! Value")
    lines.append("|-")
    lines.append(f"| '''MWS ID''' || {uid}")
    lines.append("|-")
    lines.append(f"| '''Parent Watershed''' || [[{ws_page}|{watershed_id}]]")
    lines.append("|-")
    lines.append(f"| '''Area (ha)''' || {fmt(area)}")
    lines.append("|-")
    if report_url:
        lines.append(f"| '''CoREStack Report''' || [{report_url} View Full Report »]")
    else:
        lines.append("| '''CoREStack Report''' || ''Not available''")
    lines.append("|}")
    lines.append("")

    # ── Most recent year summary ──────────────────────────────────
    if recent_year and recent_metrics:
        lines.append(f"=== Hydrological Properties ({format_year_label(recent_year)}) ===")
        lines.append("")
        lines.append('{| class="wikitable"')
        lines.append("! Metric !! Value")
        for metric in METRICS:
            val = fmt(recent_metrics.get(metric))
            unit = "(m)" if metric == "WellDepth" else "(mm)"
            lines.append("|-")
            lines.append(f"| '''{metric} {unit}''' || {val}")
        lines.append("|}")
        lines.append("")

    # # ── Full history table ────────────────────────────────────────
    # lines.append("=== Full Water Balance History ===")
    # lines.append("All values in mm unless stated otherwise.")
    # lines.append("")

    # if not yearly:
    #     lines.append("''No water balance data available for this microwatershed.''")
    # else:
    #     lines.append('{| class="wikitable sortable"')
    #     lines.append("! Year !! Precipitation (mm) !! RunOff (mm) !! ET (mm) !! DeltaG (mm) !! G (mm) !! WellDepth (m)")

    #     for year_key, metrics in yearly.items():
    #         label    = format_year_label(year_key)
    #         row_vals = " || ".join(fmt(metrics.get(m)) for m in METRICS)
    #         lines.append("|-")
    #         lines.append(f"| {label} || {row_vals}")

    #     lines.append("|}")

    lines.append("")
    lines.append("----")
    lines.append("''This page was generated automatically as part of the CoREStack project.''")

    return "\n".join(lines)


def build_watershed_page(watershed_id: str, sub_basin: str, ws_code: str,
                          mws_uids: list[str]) -> str:
    """Build wikitext for the watershed summary page."""
    lines = []

    lines.append(f"== Watershed Properties: {ws_code} ==")
    lines.append("")
    lines.append('{| class="wikitable"')
    lines.append("! Property !! Value")
    lines.append("|-")
    lines.append(f"| '''Watershed ID''' || {watershed_id}")
    lines.append("|-")
    lines.append(f"| '''Sub Basin Code''' || {sub_basin}")
    lines.append("|-")
    lines.append(f"| '''Watershed Code''' || {ws_code}")
    lines.append("|-")
    lines.append(f"| '''Total MWS Count''' || {len(set(mws_uids))}")
    lines.append("|}")
    lines.append("")

    lines.append("== Micro-Watershed (MWS) Directory ==")
    lines.append("")
    lines.append('{| class="wikitable sortable"')
    lines.append("! MWS Unique ID !! Registry Link")

    for uid in sorted(set(mws_uids)):
        mws_page = f"CoREStack/MWS/{uid}"
        lines.append("|-")
        lines.append(f"| {uid} || [[{mws_page}|View Full Data »]]")

    lines.append("|}")
    lines.append("")
    lines.append("----")
    lines.append("''This page was generated automatically as part of the CoREStack project.''")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Wiki upload
# ─────────────────────────────────────────────────────────────────────────────

def login_wiki(user: str, bot_name: str, bot_pass: str) -> mwclient.Site:
    site = mwclient.Site(WIKI_HOST, path="/w/")
    site.login(f"{user}@{bot_name}", bot_pass)
    print(f"✅ Logged in to {WIKI_HOST} as {user}@{bot_name}")
    return site


def save_page(site: mwclient.Site, title: str, content: str, summary: str):
    page = site.pages[title]
    page.save(content, summary=summary)
    print(f"   ✅ Saved: {title}")
    time.sleep(0.5)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(watershed_id: str, geojson_path: str):
    print(f"\nLoading GeoJSON: {geojson_path}")
    with open(geojson_path, "r") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    if not features:
        print("❌ No features found in GeoJSON.")
        sys.exit(1)
    print(f"   {len(features)} MWS features loaded.")

    sub_basin, ws_code = parse_wsconc(watershed_id)
    print(f"   Sub Basin Code: {sub_basin}   Watershed Code: {ws_code}")

    wiki_user     = os.environ.get("WIKI_USER", "")
    wiki_bot_name = os.environ.get("WIKI_BOT_NAME", "")
    wiki_bot_pass = os.environ.get("WIKI_BOT_PASS", "")
    api_key       = os.environ.get("CORESTACK_API_KEY", "")

    if not all([wiki_user, wiki_bot_name, wiki_bot_pass]):
        print("❌ Wiki credentials not set. Add WIKI_USER, WIKI_BOT_NAME, WIKI_BOT_PASS to .env")
        sys.exit(1)

    if not api_key:
        print("⚠️  CORESTACK_API_KEY not set — report URLs will show as 'Not available'.")

    site = login_wiki(wiki_user, wiki_bot_name, wiki_bot_pass)

    # ── Phase 1: MWS pages ────────────────────────────────────────
    print(f"\n── Phase 1: Creating {len(features)} MWS wiki pages ──")
    mws_uids = []

    for i, feature in enumerate(features, 1):
        props = feature.get("properties", {})

        uid = (
            props.get("uid") or props.get("mws_id") or props.get("MWS_ID")
            or props.get("id") or props.get("objectid")
        )
        if uid is None:
            print(f"   ⚠️  Feature {i} has no UID, skipping.")
            continue

        uid = str(uid)
        mws_uids.append(uid)

        report_url = None
        if api_key:
            report_url = fetch_mws_report_url(props, api_key)
            status = "✅ report found" if report_url else "⚠️  no report"
        else:
            status = "⚠️  no API key"

        title   = f"CoREStack/MWS/{uid}"
        content = build_mws_page(uid, props, watershed_id, report_url)
        summary = f"Bot: Creating MWS page for {uid} under watershed {watershed_id}"

        print(f"  [{i}/{len(features)}] {title} — {status}")
        try:
            save_page(site, title, content, summary)
        except Exception as e:
            print(f"   ⚠️  Failed to save {title}: {e}")

    # ── Phase 2: Watershed page ───────────────────────────────────
    print(f"\n── Phase 2: Creating watershed summary page ──")
    ws_title   = f"CoREStack/Watershed/{watershed_id}"
    ws_content = build_watershed_page(watershed_id, sub_basin, ws_code, mws_uids)
    ws_summary = f"Bot: Creating watershed summary page for {watershed_id} with {len(mws_uids)} MWS"

    try:
        save_page(site, ws_title, ws_content, ws_summary)
    except Exception as e:
        print(f"   ⚠️  Failed to save watershed page: {e}")

    print(f"\n✅ Done!")
    print(f"   MWS pages created  : {len(mws_uids)}")
    print(f"   Watershed page     : https://wiki.openstreetmap.org/wiki/{ws_title}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create OSM Wiki pages for a watershed and its microwatersheds.",
        epilog="""
Example:
  python create_wiki_pages.py --watershed_id C2AGAN72 --geojson C2AGAN72_microwatersheds.geojson

Environment variables (add to .env):
  WIKI_USER             your OSM wiki username
  WIKI_BOT_NAME         your bot name
  WIKI_BOT_PASS         your bot password
  CORESTACK_API_KEY     your CoREStack API key (for report URLs)
        """
    )
    parser.add_argument("--watershed_id", required=True,
                        help="Watershed wsconc ID e.g. C2AGAN72")
    parser.add_argument("--geojson", required=True,
                        help="Path to the GeoJSON file from pipeline.py")
    args = parser.parse_args()

    run(watershed_id=args.watershed_id, geojson_path=args.geojson)