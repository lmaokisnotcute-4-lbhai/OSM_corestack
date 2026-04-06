# -*- coding: utf-8 -*-
"""
Pipeline: Given a Watershed ID → fetch all Microwatersheds (MWS) within it,
          including their water balance properties (DeltaG, ET, Precipitation,
          RunOff, G, WellDepth) per hydrological year.

Usage:
    python pipeline.py --watershed_id C2AGAN72

    Optional flags:
        --output my_output.geojson   (default: <watershed_id>_microwatersheds.geojson)
        --gee_project my-gcp-project (default: reads GEE_PROJECT env var)

API key is read from the CORESTACK_API_KEY environment variable.
See README.md for setup instructions.
"""
import argparse
import os
import sys
import json
import warnings
from dotenv import load_dotenv

load_dotenv()  # reads .env file automatically
import ee
import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import shape

warnings.filterwarnings("ignore")

# ── GEE asset paths (do not change unless CoREStack updates their assets) ──
WATERSHED_ASSET       = "projects/corestack-datasets/assets/datasets/hydrological_boundaries/watersheds"
TEHSIL_ASSET          = "projects/ext-datasets/assets/datasets/SOI_tehsil"
WATERSHED_ID_PROPERTY = "wsconc"

TEHSIL_STATE_PROP    = "STATE"
TEHSIL_DISTRICT_PROP = "District"
TEHSIL_NAME_PROP     = "TEHSIL"

BASE_URL           = "https://geoserver.core-stack.org/api/v1/"
LAYER_URL_ENDPOINT = "get_generated_layer_urls/"


# ─────────────────────────────────────────────────────────────────────────────
# GEE helpers
# ─────────────────────────────────────────────────────────────────────────────

def init_gee(project: str):
    try:
        ee.Initialize(project=project)
        print("✅ GEE initialised.")
    except Exception as e:
        print(f"❌ GEE init failed: {e}")
        print("   Run  `earthengine authenticate`  in your terminal first.")
        sys.exit(1)


def get_watershed_geometry(watershed_id: str) -> ee.Geometry:
    watersheds = ee.FeatureCollection(WATERSHED_ASSET)
    target = watersheds.filter(ee.Filter.eq(WATERSHED_ID_PROPERTY, watershed_id))
    if target.size().getInfo() == 0:
        print(f"❌ Watershed '{watershed_id}' not found in GEE asset.")
        print(f"   Check that the wsconc value is correct.")
        sys.exit(1)
    print(f"✅ Found watershed '{watershed_id}'.")
    return target.geometry()


def get_overlapping_tehsils(watershed_geom: ee.Geometry) -> list[dict]:
    tehsils = ee.FeatureCollection(TEHSIL_ASSET)
    overlapping = tehsils.filterBounds(watershed_geom)
    props = overlapping.select([
        TEHSIL_STATE_PROP,
        TEHSIL_DISTRICT_PROP,
        TEHSIL_NAME_PROP
    ]).getInfo()

    seen, unique = set(), []
    for feature in props["features"]:
        p = feature["properties"]
        entry = {
            "state":    p.get(TEHSIL_STATE_PROP, ""),
            "district": p.get(TEHSIL_DISTRICT_PROP, ""),
            "tehsil":   p.get(TEHSIL_NAME_PROP, ""),
        }
        key = (entry["state"], entry["district"], entry["tehsil"])
        if key not in seen:
            seen.add(key)
            unique.append(entry)

    print(f"✅ Found {len(unique)} overlapping tehsil(s).")
    for t in unique:
        print(f"   {t['state']} / {t['district']} / {t['tehsil']}")
    return unique


def fetch_mws_from_gee(asset_path: str, watershed_geom: ee.Geometry) -> gpd.GeoDataFrame:
    try:
        fc = ee.FeatureCollection(asset_path).filterBounds(watershed_geom)
        if fc.size().getInfo() == 0:
            return gpd.GeoDataFrame()
        geojson = fc.getInfo()
        return gpd.GeoDataFrame.from_features(geojson["features"], crs="EPSG:4326")
    except Exception as e:
        print(f"   ⚠️  GEE asset error '{asset_path}': {e}")
        return gpd.GeoDataFrame()


def fetch_water_balance_from_gee(deltag_asset: str) -> dict:
    try:
        fc = ee.FeatureCollection(deltag_asset)
        if fc.size().getInfo() == 0:
            return {}
        geojson = fc.getInfo()
        return parse_water_balance_features(geojson["features"])
    except Exception as e:
        print(f"   ⚠️  Error loading deltaG GEE asset '{deltag_asset}': {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# CoREStack API helpers
# ─────────────────────────────────────────────────────────────────────────────

def fetch_layer_info(tehsil_info: dict, api_key: str) -> dict:
    """
    Call CoREStack API for a tehsil and return:
      - mws_asset   : GEE asset path for MWS boundary FeatureCollection
      - deltag_asset: GEE asset path for deltaG water balance FeatureCollection
      - deltag_url  : Direct URL fallback for deltaG layer
    """
    headers = {"X-API-Key": api_key}
    params  = {
        "state":    tehsil_info["state"],
        "district": tehsil_info["district"],
        "tehsil":   tehsil_info["tehsil"],
    }

    try:
        resp = requests.get(BASE_URL + LAYER_URL_ENDPOINT, params=params,
                            headers=headers, timeout=30)
    except requests.RequestException as e:
        print(f"   ⚠️  Network error for {tehsil_info['tehsil']}: {e}")
        return {}

    if resp.status_code != 200:
        print(f"   ⚠️  API {resp.status_code} for {tehsil_info['tehsil']}: {resp.text[:200]}")
        return {}

    result = {}
    for layer in resp.json():
        dataset = layer.get("dataset_name", "")
        ltype   = layer.get("layer_type", "")
        url     = layer.get("layer_url", "")
        asset   = layer.get("gee_asset_path", "")

        if dataset == "MWS" and ltype == "vector" and asset:
            result["mws_asset"] = asset

        if "deltaG" in url:
            if not result.get("deltag_url"):
                result["deltag_url"] = url
            if asset and not result.get("deltag_asset"):
                result["deltag_asset"] = asset

    return result


def fetch_water_balance_from_url(deltag_url: str) -> dict:
    """Fallback: fetch deltaG data via direct URL (WFS GeoJSON response)."""
    try:
        resp = requests.get(deltag_url, verify=False, timeout=60)
        if resp.status_code != 200:
            print(f"   ⚠️  deltaG URL returned HTTP {resp.status_code}")
            return {}
        data = resp.json()
        features = data.get("features", [])
        return parse_water_balance_features(features) if features else {}
    except Exception as e:
        print(f"   ⚠️  Error fetching deltaG URL: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Water balance parsing & joining
# ─────────────────────────────────────────────────────────────────────────────

def parse_water_balance_features(features: list) -> dict:
    """
    Parse GeoJSON features from a deltaG FeatureCollection.
    Each feature = one MWS; properties contain yearly water balance data
    as JSON strings keyed like "2017_2018".

    Returns: { str(uid): { "2017_2018": {DeltaG, ET, Precipitation, RunOff, G, WellDepth} } }
    """
    wb_by_mws = {}
    for feature in features:
        props = feature.get("properties", {})
        uid = (
            props.get("uid") or props.get("mws_id") or props.get("MWS_ID")
            or props.get("id") or props.get("objectid") or props.get("system:index")
        )
        if uid is None:
            continue

        yearly_data = {}
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
            yearly_data[key] = {
                "DeltaG":        value.get("DeltaG",        None),
                "ET":            value.get("ET",            None),
                "Precipitation": value.get("Precipitation", None),
                "RunOff":        value.get("RunOff",        None),
                "G":             value.get("G",             None),
                "WellDepth":     value.get("WellDepth",     None),
            }

        if yearly_data:
            wb_by_mws[str(uid)] = yearly_data

    return wb_by_mws


def clip_to_watershed(gdf: gpd.GeoDataFrame, watershed_shape) -> gpd.GeoDataFrame:
    if gdf.empty:
        return gdf
    return gdf[gdf.geometry.intersects(watershed_shape)].copy()


def attach_properties_to_gdf(gdf: gpd.GeoDataFrame, wb_by_mws: dict) -> gpd.GeoDataFrame:
    """
    Join water balance data onto the MWS GeoDataFrame by UID.
    Adds flat columns: DeltaG_2017_2018, ET_2017_2018, ...
    Also adds 'water_balance_json' with full nested structure.
    """
    if gdf.empty or not wb_by_mws:
        return gdf

    uid_col = None
    for candidate in ["uid", "mws_id", "MWS_ID", "id", "objectid", "system:index"]:
        if candidate in gdf.columns:
            uid_col = candidate
            break

    if uid_col is None:
        print(f"   ⚠️  No UID column found. Available columns: {list(gdf.columns)}")
        return gdf

    def flatten_wb(uid):
        uid = str(uid)
        if uid not in wb_by_mws:
            return {}
        flat = {"water_balance_json": json.dumps(wb_by_mws[uid])}
        for year_key, metrics in wb_by_mws[uid].items():
            for metric, val in metrics.items():
                flat[f"{metric}_{year_key}"] = val
        return flat

    wb_df    = pd.DataFrame(list(gdf[uid_col].apply(flatten_wb)), index=gdf.index)
    new_cols = [c for c in wb_df.columns if c not in gdf.columns]
    gdf      = pd.concat([gdf, wb_df[new_cols]], axis=1)

    matched = wb_df["water_balance_json"].notna().sum() if "water_balance_json" in wb_df.columns else 0
    print(f"   → Attached water balance to {matched}/{len(gdf)} MWS features.")
    return gdf


# ─────────────────────────────────────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────────────────────────────────────

def get_microwatersheds_pipeline(watershed_id: str, output_filename: str,
                                  api_key: str, gee_project: str):
    print(f"\n{'='*60}")
    print(f"  Pipeline: Microwatersheds for Watershed {watershed_id}")
    print(f"{'='*60}\n")

    # Step 1: Watershed geometry
    print("── Step 1: Getting watershed geometry from GEE ──")
    init_gee(gee_project)
    watershed_geom_ee = get_watershed_geometry(watershed_id)
    watershed_shape   = shape(watershed_geom_ee.getInfo())

    # Step 2: Overlapping tehsils
    print("\n── Step 2: Finding overlapping tehsils in GEE ──")
    tehsils = get_overlapping_tehsils(watershed_geom_ee)
    if not tehsils:
        print("❌ No overlapping tehsils found.")
        return

    # Step 3: Layer info from CoREStack API
    print("\n── Step 3: Fetching layer info from CoREStack API ──")
    tehsil_layer_info = []
    for tehsil_info in tehsils:
        t    = tehsil_info["tehsil"]
        info = fetch_layer_info(tehsil_info, api_key)
        if not info:
            print(f"   ⚠️  No layer info for {t}, skipping.")
            continue
        has_mws    = "✅" if info.get("mws_asset")                              else "❌"
        has_deltag = "✅" if info.get("deltag_asset") or info.get("deltag_url") else "❌"
        print(f"   {t}: MWS asset={has_mws}  deltaG={has_deltag}")
        tehsil_layer_info.append({**tehsil_info, **info})

    # Step 4: Load geometries + water balance
    print("\n── Step 4: Loading MWS from GEE and fetching water balance ──")
    all_gdfs = []
    for entry in tehsil_layer_info:
        t = entry["tehsil"]
        print(f"\n  [{t}]")

        if not entry.get("mws_asset"):
            print(f"   ⚠️  No MWS asset, skipping.")
            continue

        gdf = fetch_mws_from_gee(entry["mws_asset"], watershed_geom_ee)
        gdf = clip_to_watershed(gdf, watershed_shape)
        if gdf.empty:
            print(f"   → 0 MWS features after clipping, skipping.")
            continue
        print(f"   → {len(gdf)} MWS features after clipping.")

        # Prefer GEE asset for water balance, fall back to direct URL
        wb_by_mws = {}
        if entry.get("deltag_asset"):
            print(f"   Loading water balance from GEE asset...")
            wb_by_mws = fetch_water_balance_from_gee(entry["deltag_asset"])
            print(f"   → Water balance found for {len(wb_by_mws)} MWS.")

        if not wb_by_mws and entry.get("deltag_url"):
            print(f"   Trying direct URL fallback for water balance...")
            wb_by_mws = fetch_water_balance_from_url(entry["deltag_url"])
            print(f"   → Water balance found for {len(wb_by_mws)} MWS.")

        if wb_by_mws:
            gdf = attach_properties_to_gdf(gdf, wb_by_mws)
        else:
            print(f"   ⚠️  No water balance data for {t} — geometry only.")

        all_gdfs.append(gdf)

    if not all_gdfs:
        print("\n❌ No MWS features collected.")
        return

    # Step 5: Merge & export
    print("\n── Step 5: Merging and saving output ──")
    final_gdf = gpd.GeoDataFrame(pd.concat(all_gdfs, ignore_index=True), crs="EPSG:4326")

    before = len(final_gdf)
    final_gdf = final_gdf.drop_duplicates(subset=["geometry"])
    after = len(final_gdf)
    if before != after:
        print(f"  Removed {before - after} duplicate MWS features.")

    final_gdf.to_file(output_filename, driver="GeoJSON")

    wb_col  = "water_balance_json"
    with_wb = final_gdf[wb_col].notna().sum() if wb_col in final_gdf.columns else 0
    print(f"\n✅ Done!")
    print(f"   Total MWS saved    : {len(final_gdf)}")
    print(f"   With water balance : {with_wb}")
    print(f"   Output file        : {output_filename}")
    print(f"   Property columns   : {[c for c in final_gdf.columns if c != 'geometry']}")
    return final_gdf


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch microwatersheds and water balance data for a CoREStack watershed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python pipeline.py --watershed_id C2AGAN72
  python pipeline.py --watershed_id C2AGAN72 --output my_results.geojson
  python pipeline.py --watershed_id C2AGAN72 --gee_project my-gcp-project-id

Environment variables:
  CORESTACK_API_KEY   Your CoREStack API key (required)
  GEE_PROJECT         Your Google Cloud project ID (can use --gee_project instead)
        """
    )
    parser.add_argument(
        "--watershed_id",
        required=True,
        help="Watershed ID (wsconc value) e.g. C2AGAN72"
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output GeoJSON filename (default: <watershed_id>_microwatersheds.geojson)"
    )
    parser.add_argument(
        "--gee_project",
        default=None,
        help="Google Cloud project ID for GEE (overrides GEE_PROJECT env var)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # ── API key: must be set as environment variable, never hardcoded ─────────
    api_key = os.environ.get("CORESTACK_API_KEY", "")
    if not api_key:
        print("❌ CORESTACK_API_KEY environment variable is not set.")
        print("   Set it with:")
        print("     Linux/macOS:  export CORESTACK_API_KEY='your_key_here'")
        print("     Windows:      set CORESTACK_API_KEY=your_key_here")
        sys.exit(1)

    # ── GEE project: CLI flag > env var > error ───────────────────────────────
    gee_project = args.gee_project or os.environ.get("GEE_PROJECT", "")
    if not gee_project:
        print("❌ GEE project not specified.")
        print("   Use --gee_project my-project-id  or  export GEE_PROJECT=my-project-id")
        sys.exit(1)

    # ── Output filename ───────────────────────────────────────────────────────
    output_file = args.output or f"{args.watershed_id}_microwatersheds.geojson"

    get_microwatersheds_pipeline(
        watershed_id    = args.watershed_id,
        output_filename = output_file,
        api_key         = api_key,
        gee_project     = gee_project,
    )
