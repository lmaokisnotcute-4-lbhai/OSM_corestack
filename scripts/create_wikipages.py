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
    WIKI_USER       your OSM wiki username  e.g. Sweek10
    WIKI_BOT_NAME   your bot name           e.g. core_osm
    WIKI_BOT_PASS   your bot password

Requirements:
    pip install mwclient python-dotenv
"""

import argparse
import os
import sys
import json
import re
import time

import mwclient
from dotenv import load_dotenv

load_dotenv()

WIKI_HOST = "wiki.openstreetmap.org"

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
    Extract all yearly water balance entries from a feature's properties.
    Keys shaped like '2017_2018' with JSON string or dict values.
    Returns { '2017_2018': {DeltaG, ET, Precipitation, RunOff, G, WellDepth}, ... }
    sorted chronologically.
    """
    yearly = {}
    for key, value in props.items():
        if not (isinstance(key, str) and "_" in key and key.replace("_", "").isdigit()):
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                continue
        if not isinstance(value, dict):
            continue
        yearly[key] = value
    return dict(sorted(yearly.items()))


def fmt(val, decimals=2) -> str:
    """Format a metric value for display."""
    if val is None or val == "N/A":
        return "N/A"
    try:
        return f"{float(val):.{decimals}f}"
    except (TypeError, ValueError):
        return str(val)


def format_year_label(key: str) -> str:
    """'2017_2018' → '2017–2018'"""
    return key.replace("_", "–")


# ─────────────────────────────────────────────────────────────────────────────
# Wiki page content builders
# ─────────────────────────────────────────────────────────────────────────────

METRICS = ["Precipitation", "RunOff", "ET", "DeltaG", "G", "WellDepth"]


def build_mws_page(uid: str, props: dict, watershed_id: str) -> str:
    """
    Build wikitext for a single MWS page.

    Layout:
      == Micro-Watershed: <uid> ==
      Overview table (Area, Parent Watershed)

      === Water Balance Metrics ===
      One combined table: Year | Precipitation | RunOff | ET | DeltaG | G | WellDepth
    """
    ws_page = f"CoREStack/Watershed/{watershed_id}"
    area    = props.get("area_in_ha", props.get("area", "N/A"))
    yearly  = get_yearly_metrics(props)

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
    lines.append(f"| '''Area (ha)''' || {fmt(area, 2)}")
    lines.append("|}")
    lines.append("")

    # ── Water balance table ──────────────────────────────────────
    lines.append("=== Water Balance Metrics ===")
    lines.append("All values in mm unless stated otherwise.")
    lines.append("")

    if not yearly:
        lines.append("''No water balance data available for this microwatershed.''")
    else:
        # Single table with one row per year
        lines.append('{| class="wikitable sortable"')
        lines.append("! Year !! Precipitation (mm) !! RunOff (mm) !! ET (mm) !! DeltaG (mm) !! G (mm) !! WellDepth (m)")

        for year_key, metrics in yearly.items():
            label = format_year_label(year_key)
            lines.append("|-")
            row_vals = " || ".join(fmt(metrics.get(m)) for m in METRICS)
            lines.append(f"| {label} || {row_vals}")

        lines.append("|}")

    lines.append("")
    lines.append("----")
    lines.append("''This page was generated automatically as part of the CoREStack project.''")

    return "\n".join(lines)


def build_watershed_page(watershed_id: str, sub_basin: str, ws_code: str,
                          mws_uids: list[str]) -> str:
    """
    Build wikitext for the watershed summary page.

    Layout:
      == Watershed Properties ==
      Properties table (Sub Basin Code, Watershed Code)

      == Micro-Watershed (MWS) Directory ==
      Sortable table: MWS Unique ID | Registry Link
    """
    lines = []

    # ── Title ────────────────────────────────────────────────────
    lines.append(f"== Watershed Properties: {ws_code} ==")
    lines.append("")

    # ── Properties table ─────────────────────────────────────────
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

    # ── MWS directory table ──────────────────────────────────────
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
    time.sleep(0.5)  # be polite to the wiki server


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(watershed_id: str, geojson_path: str):
    # ── Load GeoJSON ─────────────────────────────────────────────
    print(f"\nLoading GeoJSON: {geojson_path}")
    with open(geojson_path, "r") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    if not features:
        print("❌ No features found in GeoJSON.")
        sys.exit(1)
    print(f"   {len(features)} MWS features loaded.")

    # ── Parse watershed metadata from wsconc ─────────────────────
    sub_basin, ws_code = parse_wsconc(watershed_id)
    print(f"   Sub Basin Code: {sub_basin}   Watershed Code: {ws_code}")

    # ── Wiki credentials from env ─────────────────────────────────
    wiki_user     = os.environ.get("WIKI_USER", "")
    wiki_bot_name = os.environ.get("WIKI_BOT_NAME", "")
    wiki_bot_pass = os.environ.get("WIKI_BOT_PASS", "")

    if not all([wiki_user, wiki_bot_name, wiki_bot_pass]):
        print("❌ Wiki credentials not set. Add to .env:")
        print("   WIKI_USER=your_username")
        print("   WIKI_BOT_NAME=your_bot_name")
        print("   WIKI_BOT_PASS=your_bot_password")
        sys.exit(1)

    site = login_wiki(wiki_user, wiki_bot_name, wiki_bot_pass)

    # ── Phase 1: Create individual MWS pages ─────────────────────
    print(f"\n── Phase 1: Creating {len(features)} MWS wiki pages ──")
    mws_uids = []

    for i, feature in enumerate(features, 1):
        props = feature.get("properties", {})

        uid = (
            props.get("uid")
            or props.get("mws_id")
            or props.get("MWS_ID")
            or props.get("id")
            or props.get("objectid")
        )
        if uid is None:
            print(f"   ⚠️  Feature {i} has no UID, skipping.")
            continue

        uid = str(uid)
        mws_uids.append(uid)
        title   = f"CoREStack/MWS/{uid}"
        content = build_mws_page(uid, props, watershed_id)
        summary = f"Bot: Creating MWS page for {uid} under watershed {watershed_id}"

        print(f"  [{i}/{len(features)}] {title}")
        try:
            save_page(site, title, content, summary)
        except Exception as e:
            print(f"   ⚠️  Failed to save {title}: {e}")

    # ── Phase 2: Create / update watershed summary page ──────────
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
  WIKI_USER       your OSM wiki username
  WIKI_BOT_NAME   your bot name
  WIKI_BOT_PASS   your bot password
        """
    )
    parser.add_argument("--watershed_id", required=True,
                        help="Watershed wsconc ID e.g. C2AGAN72")
    parser.add_argument("--geojson", required=True,
                        help="Path to the GeoJSON file from pipeline.py")
    args = parser.parse_args()

    run(watershed_id=args.watershed_id, geojson_path=args.geojson)