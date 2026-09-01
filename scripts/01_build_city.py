"""Build the school-street readiness baseline for one city.

Usage
-----
    python scripts/01_build_city.py rotterdam
    python scripts/01_build_city.py rotterdam --refresh

Writes to ``data/processed``:
    <city>_schools.gpkg      schools with walkshed summary attributes
    <city>_walksheds.gpkg    one polygon per (school, time threshold)
    <city>_segments.gpkg     street segments with indicator + composite scores

and a coverage report to ``outputs/tables/<city>_coverage.csv`` -- which
indicators were observable in this city, and for what share of segments. That
table is the input to the cross-city harmonisation matrix.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Make `src` importable without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from routes_ssr import config, osm_extract, segment_index, viz, walksheds  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("build")


def coverage_report(segments: gpd.GeoDataFrame, city_key: str) -> pd.DataFrame:
    """Share of *applicable* segments with a non-missing score, per indicator.

    This is the honest core of the cross-city story: it says what we could
    measure where, before any comparison is attempted.

    Both numerator and denominator are restricted to applicable segments. Using
    all segments as the denominator penalises an indicator for rows where its
    question does not arise -- it drove `s_speed` down to 0.52 and out of the
    comparable set, because ~52,000 car-free segments have no speed limit to
    observe. On the roads where the question is meaningful it is ~0.80.
    """
    indicator_cols = [c for c in segments.columns if c.startswith(("s_", "d_"))]
    applies = segment_index.applicability(segments)
    rows = []

    for col in indicator_cols:
        # Domain columns carry their own applicability flag; indicators use the
        # per-indicator mask. Anything unrecognised is treated as applicable
        # everywhere, which is the conservative choice.
        if col.startswith("d_"):
            flag = f"applicable_{col[2:]}"
            app = segments[flag] if flag in segments.columns else pd.Series(True, index=segments.index)
        else:
            app = applies.get(col, pd.Series(True, index=segments.index))

        sub = segments.loc[app]
        observed = sub[col].notna()
        n_app = len(sub)

        # Weight by street length, not segment count -- OSM segment lengths vary
        # by an order of magnitude and unweighted counts overstate the coverage
        # of short, well-mapped inner-city ways.
        if "length" in segments and n_app:
            app_len = sub["length"].sum()
            share_len = sub.loc[observed, "length"].sum() / app_len if app_len else float("nan")
        else:
            share_len = float(observed.mean()) if n_app else float("nan")

        rows.append(
            {
                "city": city_key,
                "indicator": col,
                "share_segments": round(float(observed.mean()), 4) if n_app else float("nan"),
                "share_length": round(float(share_len), 4),
                "n_observed": int(observed.sum()),
                "n_applicable": int(n_app),
                "n_total": int(len(segments)),
            }
        )
    return pd.DataFrame(rows)


def main(city_key: str, refresh: bool = False) -> None:
    t0 = time.time()
    config.ensure_dirs()
    city = config.get_city(city_key)
    log.info("=== %s (%s, %s) ===", city.place, city.status, city.crs)

    # 1. Schools ----------------------------------------------------------
    schools = osm_extract.fetch_schools(city, refresh=refresh)
    log.info("schools: %d", len(schools))

    # 2. Pedestrian network -----------------------------------------------
    G = osm_extract.fetch_walk_graph(city, refresh=refresh)
    log.info("network: %d nodes / %d edges", G.number_of_nodes(), G.number_of_edges())

    # 3. Walksheds ---------------------------------------------------------
    sheds = walksheds.build_walksheds(G, schools, city)
    sheds.to_file(config.DATA_PROCESSED / f"{city.key}_walksheds.gpkg", driver="GPKG")

    summary = (
        sheds.pivot_table(index="school_id", columns="minutes", values="area_km2")
        .add_prefix("walkshed_km2_")
    )
    ratio = (
        sheds.pivot_table(index="school_id", columns="minutes", values="reach_ratio")
        .add_prefix("reach_ratio_")
    )
    schools_out = schools.merge(
        summary.join(ratio), left_on="school_id", right_index=True, how="left"
    )
    schools_out.to_file(config.DATA_PROCESSED / f"{city.key}_schools.gpkg", driver="GPKG")

    # 4. Segment indicators + composite ------------------------------------
    edges = osm_extract.graph_to_edges(G)
    calming = osm_extract.fetch_traffic_calming(city, refresh=refresh)
    segments = segment_index.build_segment_indicators(edges, calming=calming)
    segments = segment_index.composite_index(segments)

    # Flag segments falling inside any 10-minute school walkshed: these are the
    # streets a school-street programme could plausibly act on.
    ten = sheds.loc[sheds["minutes"] == 10]
    if not ten.empty:
        catchment = ten.dissolve().geometry.iloc[0]
        segments["in_school_catchment"] = segments.geometry.intersects(catchment)
    else:
        segments["in_school_catchment"] = False

    keep = [
        c
        for c in segments.columns
        if c.startswith(("s_", "d_", "applicable_"))
        or c
        in {
            "u", "v", "key", "osmid", "name", "highway_class", "length",
            "maxspeed_kmh", "ssr_index", "coverage", "sidewalk_source",
            "is_service", "in_analysis_set", "in_school_catchment", "geometry",
        }
    ]
    segments = gpd.GeoDataFrame(segments[keep], geometry="geometry", crs=city.crs)

    # osmid/name arrive as lists when segments merge several ways; GPKG cannot
    # hold list columns, so stringify on the way out.
    for col in ("osmid", "name"):
        if col in segments.columns:
            segments[col] = segments[col].astype(str)

    segments.to_file(config.DATA_PROCESSED / f"{city.key}_segments.gpkg", driver="GPKG")

    # 5. Coverage report ---------------------------------------------------
    cov = coverage_report(segments, city.key)
    cov.to_csv(config.OUT_TABLES / f"{city.key}_coverage.csv", index=False)

    # 6. Interactive map ---------------------------------------------------
    try:
        viz.school_street_map(segments, schools_out, sheds, city)
    except Exception as exc:  # noqa: BLE001
        # The map is a presentation layer; a failure here must not invalidate
        # the analytical outputs already written to disk.
        log.warning("map generation failed (%s: %s)", type(exc).__name__, exc)

    log.info("--- %s done in %.0fs ---", city.key, time.time() - t0)
    log.info("segments: %d (%d in a 10-min school catchment)",
             len(segments), int(segments["in_school_catchment"].sum()))

    # Headline statistics exclude service roads: see `in_analysis_set`.
    catch = segments["in_school_catchment"]
    for label, mask in (
        ("all classes   ", catch),
        ("excl. service ", catch & segments["in_analysis_set"]),
    ):
        vals = segments.loc[mask, "ssr_index"].dropna()
        if not vals.empty:
            log.info(
                "SSR index in catchments (%s): n=%6d mean %.3f  p10 %.3f  p90 %.3f",
                label, len(vals), vals.mean(), vals.quantile(0.10), vals.quantile(0.90),
            )

    rr = sheds.loc[sheds["minutes"] == 10, "reach_ratio"].dropna()
    if not rr.empty:
        log.info("reach ratio (10 min): mean %.3f  sd %.3f  min %.3f  max %.3f",
                 rr.mean(), rr.std(), rr.min(), rr.max())
    log.info("coverage report -> outputs/tables/%s_coverage.csv", city.key)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city", help="city key from config/cities.yml, e.g. rotterdam")
    ap.add_argument("--refresh", action="store_true", help="ignore cached OSM downloads")
    args = ap.parse_args()
    main(args.city, refresh=args.refresh)
