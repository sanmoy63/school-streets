"""Audit and validate missing segment attributes using open street-level imagery.

Usage
-----
    python scripts/07_imagery_validation.py rotterdam
    python scripts/07_imagery_validation.py rotterdam --sample 100 --token YOUR_MAPILLARY_TOKEN

Writes:
    outputs/tables/<city>_imagery_audit.csv
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make `src` importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd
import numpy as np
import pandas as pd

from routes_ssr import config, imagery, sidewalks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("imagery_val")


def main(city_key: str, sample_per_class: int = 20, client_token: str | None = None) -> None:
    config.ensure_dirs()
    city = config.get_city(city_key)
    log.info("=== Stratified Imagery Validation Audit: %s ===", city.place)

    seg_path = config.DATA_PROCESSED / f"{city.key}_segments.gpkg"
    if not seg_path.exists():
        raise SystemExit(f"Segment file not found: {seg_path}. Run 01_build_city.py {city_key} first.")

    segments = gpd.read_file(seg_path)
    log.info("Loaded %d total segments for %s", len(segments), city.key)

    # Filter untagged or unconfirmed segments on APPLICABLE road classes (excluding car-free footways)
    road_mask = segments["highway_class"].isin(sidewalks.ROAD_CLASSES)
    roads = segments.loc[road_mask].copy()

    untagged_mask = roads["maxspeed_kmh"].isna() | roads["s_sidewalk"].isna()
    untagged_segs = roads.loc[untagged_mask].copy()
    log.info("Applicable road segments: %d / %d total network segments", len(roads), len(segments))
    log.info("Untagged/unconfirmed road segments: %d / %d roads (%.1f%%)",
             len(untagged_segs), len(roads), 100 * len(untagged_segs) / len(roads))

    # Stratified sampling across highway classes
    sampled = imagery.stratified_sample_segments(
        untagged_segs,
        class_col="highway_class",
        n_per_class=sample_per_class,
        random_seed=42,
    )
    log.info("Stratified sample: %d untagged segments across %d highway classes",
             len(sampled), sampled["highway_class"].nunique())

    # Compute WGS84 bounding box for sampled segments
    sampled_wgs84 = sampled.to_crs(4326)
    minx, miny, maxx, maxy = sampled_wgs84.total_bounds

    # 1. Fetch traffic sign detections via Mapillary if token available
    signs_gdf = imagery.fetch_mapillary_signs((minx, miny, maxx, maxy), client_token=client_token)

    # 2. Fetch photo point coverage via KartaView as open fallback
    kv_photos = imagery.fetch_kartaview_coverage((minx, miny, maxx, maxy))

    # 3. Match signs to sampled segments
    if not signs_gdf.empty:
        matched = imagery.infer_missing_speeds(sampled, signs_gdf)
    else:
        matched = sampled.copy()
        matched["inferred_maxspeed_kmh"] = np.nan

    # 4. Compute overall coverage audit
    kv_audit = imagery.audit_imagery_coverage(sampled, kv_photos) if not kv_photos.empty else {
        "coverage_share": 0.0, "covered_segments": 0, "coverage_ci_lo": 0.0, "coverage_ci_hi": 0.0
    }

    # 5. Compute per-stratum breakdown
    rows = []
    # Overall row
    n_inferred = int(matched["inferred_maxspeed_kmh"].notna().sum())
    p_inf, lo_inf, hi_inf = imagery.wilson_score_interval(n_inferred, len(sampled))

    rows.append({
        "city": city.key,
        "stratum": "ALL_STRATIFIED",
        "n_sampled": len(sampled),
        "n_covered": kv_audit["covered_segments"],
        "coverage_rate": kv_audit["coverage_share"],
        "coverage_ci_lo": kv_audit.get("coverage_ci_lo", 0.0),
        "coverage_ci_hi": kv_audit.get("coverage_ci_hi", 0.0),
        "detected_signs": len(signs_gdf),
        "inferred_speed_n": n_inferred,
        "inferred_speed_rate": p_inf,
        "inferred_speed_ci_lo": lo_inf,
        "inferred_speed_ci_hi": hi_inf,
    })

    # Per-class strata
    for h_class, grp in sampled.groupby("highway_class", observed=True):
        grp_matched = matched.loc[grp.index]
        grp_audit = imagery.audit_imagery_coverage(grp, kv_photos) if not kv_photos.empty else {
            "coverage_share": 0.0, "covered_segments": 0, "coverage_ci_lo": 0.0, "coverage_ci_hi": 0.0
        }
        g_inf = int(grp_matched["inferred_maxspeed_kmh"].notna().sum())
        p_g, lo_g, hi_g = imagery.wilson_score_interval(g_inf, len(grp))

        rows.append({
            "city": city.key,
            "stratum": h_class,
            "n_sampled": len(grp),
            "n_covered": grp_audit["covered_segments"],
            "coverage_rate": grp_audit["coverage_share"],
            "coverage_ci_lo": grp_audit.get("coverage_ci_lo", 0.0),
            "coverage_ci_hi": grp_audit.get("coverage_ci_hi", 0.0),
            "detected_signs": len(signs_gdf),
            "inferred_speed_n": g_inf,
            "inferred_speed_rate": p_g,
            "inferred_speed_ci_lo": lo_g,
            "inferred_speed_ci_hi": hi_g,
        })

    out_df = pd.DataFrame(rows)
    out_file = config.OUT_TABLES / f"{city.key}_imagery_audit.csv"
    out_df.to_csv(out_file, index=False)
    log.info("Stratified audit report written to %s", out_file)
    print("\n" + out_df.to_string(index=False) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city", help="City key e.g. rotterdam or genova")
    ap.add_argument("--sample-per-class", type=int, default=20,
                    help="Number of untagged segments to sample per highway class")
    ap.add_argument("--token", type=str, default=None, help="Mapillary client API token")
    args = ap.parse_args()
    main(args.city, sample_per_class=args.sample_per_class, client_token=args.token)
