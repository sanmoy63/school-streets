"""Sensitivity and validation diagnostics for one city.

Usage
-----
    python scripts/90_diagnostics.py rotterdam
    python scripts/90_diagnostics.py rotterdam --sample 40

Every headline number in this project depends on a threshold someone chose. This
script varies those thresholds and reports how much the answer moves. A result
that swings wildly across a defensible parameter range is not a result, and the
point of running this before adding a second city is to avoid propagating a
parameter artefact into a cross-city comparison, where it would be much harder
to see.

Sections
--------
A  walkshed corridor width   -> is `network_ratio` measuring severance or my buffer?
B  corridor-free severance   -> node reach ratio, which has no width parameter
C  sidewalk inference        -> how stable is the 69% "no sidewalk" figure?
D  school filter             -> did the ISCED/name filter keep the right places?
E  missing speed limits      -> what are the 14k roads with no maxspeed?
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402
import osmnx as ox  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from routes_ssr import config, osm_extract, sidewalks, walksheds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
logging.getLogger("routes_ssr").setLevel(logging.WARNING)
log = logging.getLogger("diag")

pd.set_option("display.width", 200)


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


# ---------------------------------------------------------------------------
# A. Does corridor width drive `network_ratio`?
# ---------------------------------------------------------------------------

def diag_corridor_width(G, schools, city, sample_idx, minutes=10) -> pd.DataFrame:
    section("A. Walkshed corridor width -> network_ratio")
    print(
        "network_ratio = walkshed area / circle of equal radius.\n"
        "Walkshed area scales with the corridor half-width, which is a free\n"
        "parameter (currently 40 m). If the ratio tracks it, the 'reaches only\n"
        "44% of the circle' claim is partly a statement about my buffer.\n"
    )
    speed = float(config.params("walkshed")["walk_speed_kmh"])
    radius = walksheds.minutes_to_metres(minutes, speed)
    circle_km2 = (math.pi * radius**2) / 1e6

    nodes = walksheds.snap_schools(G, schools)
    rows = []
    for half_width in (10, 20, 30, 40, 60, 80):
        areas = []
        for idx in sample_idx:
            if pd.isna(nodes[idx]):
                continue
            sub = nx.ego_graph(G, int(nodes[idx]), radius=radius, distance="length")
            if sub.number_of_edges() == 0:
                continue
            edges = ox.graph_to_gdfs(sub, nodes=False, edges=True)
            poly = unary_union(edges.geometry.buffer(half_width).to_numpy())
            areas.append(poly.area / 1e6)
        rows.append(
            {
                "half_width_m": half_width,
                "mean_area_km2": np.mean(areas),
                "mean_network_ratio": np.mean(areas) / circle_km2,
            }
        )
    df = pd.DataFrame(rows)
    df["ratio_vs_40m"] = df["mean_network_ratio"] / df.loc[
        df["half_width_m"] == 40, "mean_network_ratio"
    ].iloc[0]
    print(df.round(3).to_string(index=False))
    print(
        "\nVERDICT: if mean_network_ratio moves roughly proportionally with\n"
        "half_width_m, the metric is width-driven and must not be reported as\n"
        "a property of the city."
    )
    return df


# ---------------------------------------------------------------------------
# B. A severance measure with no width parameter
# ---------------------------------------------------------------------------

def diag_node_reach(G, schools, city, sample_idx, minutes=10) -> pd.DataFrame:
    section("B. Corridor-free severance: node reach ratio")
    print(
        "reach_ratio = (network-reachable nodes within r) / (nodes within r as the\n"
        "crow flies). No buffer, no area, no free parameter beyond r itself.\n"
        "This is what `network_ratio` was trying to be.\n"
    )
    speed = float(config.params("walkshed")["walk_speed_kmh"])
    radius = walksheds.minutes_to_metres(minutes, speed)

    nodes_gdf = ox.graph_to_gdfs(G, nodes=True, edges=False)
    snapped = walksheds.snap_schools(G, schools)

    rows = []
    for idx in sample_idx:
        if pd.isna(snapped[idx]):
            continue
        node = int(snapped[idx])
        pt = schools.geometry.loc[idx]

        reachable = set(nx.ego_graph(G, node, radius=radius, distance="length").nodes)
        in_circle = nodes_gdf.index[nodes_gdf.geometry.distance(pt) <= radius]
        if len(in_circle) == 0:
            continue
        rows.append(
            {
                "school_id": schools.at[idx, "school_id"],
                "name": schools.at[idx, "name"],
                "n_circle": len(in_circle),
                "n_reached": len(reachable & set(in_circle)),
                "reach_ratio": len(reachable & set(in_circle)) / len(in_circle),
            }
        )
    df = pd.DataFrame(rows)
    print(df["reach_ratio"].describe().round(3).to_string())
    print("\nMost severed schools in sample:")
    print(df.nsmallest(6, "reach_ratio")[["name", "n_circle", "n_reached", "reach_ratio"]].to_string(index=False))
    return df


# ---------------------------------------------------------------------------
# C. Sidewalk inference stability
# ---------------------------------------------------------------------------

def diag_sidewalk_thresholds(edges) -> pd.DataFrame:
    section("C. Sidewalk inference: threshold sensitivity")
    print(
        "The 69% 'no sidewalk' figure rests on a 25 m corridor and a 30 deg\n"
        "parallel tolerance. Sweeping both shows whether that number is a\n"
        "property of Rotterdam or of my thresholds.\n"
    )
    rows = []
    for buf in (15.0, 20.0, 25.0, 35.0):
        for ang in (20.0, 30.0, 45.0):
            s = sidewalks.sidewalk_provision(edges, buffer_m=buf, angle_tol=ang)
            roads = edges["highway_class"].isin(sidewalks.ROAD_CLASSES)
            sr = s[roads].dropna()
            rows.append(
                {
                    "buffer_m": buf,
                    "angle_tol": ang,
                    "pct_none": 100 * float((sr == 0.0).mean()),
                    "pct_one": 100 * float((sr == 0.55).mean()),
                    "pct_both": 100 * float((sr == 1.0).mean()),
                }
            )
    df = pd.DataFrame(rows)
    print(df.round(1).to_string(index=False))
    spread = df["pct_none"].max() - df["pct_none"].min()
    print(f"\npct_none ranges over {spread:.1f} percentage points across the sweep.")
    print(
        "VERDICT: a spread above ~10 points means the headline share is not\n"
        "reportable without either validation against imagery or a stated band."
    )
    return df


# ---------------------------------------------------------------------------
# D. School filter
# ---------------------------------------------------------------------------

def diag_school_filter(city) -> None:
    section("D. School filter: what did we keep?")
    schools = osm_extract.fetch_schools(city)
    print(f"kept: {len(schools)}")
    print("\nby amenity:")
    print(schools["amenity"].value_counts(dropna=False).to_string())
    print("\nISCED tag availability:")
    print(f"  isced_level present: {int(schools['isced_level'].notna().sum())} / {len(schools)}")
    print("\ngeometry type (polygon = has a schoolyard footprint):")
    print(schools["geom_type"].value_counts().to_string())
    print("\nyard area (m2) where polygon present:")
    print(schools["yard_area_m2"].describe().round(0).to_string())
    print("\nsample of kept names (20):")
    names = schools["name"].dropna().head(20).tolist()
    for n in names:
        print(f"  {n}")
    n_unnamed = int(schools["name"].isna().sum())
    print(f"\nunnamed features kept: {n_unnamed} "
          f"({100 * n_unnamed / len(schools):.0f}%) -- these bypass the name filter entirely")


# ---------------------------------------------------------------------------
# E. Missing speed limits
# ---------------------------------------------------------------------------

def diag_missing_speed(segments) -> None:
    section("E. Roads with no maxspeed")
    roads = segments[segments["highway_class"].isin(sidewalks.ROAD_CLASSES)]
    missing = roads[roads["maxspeed_kmh"].isna()]
    print(f"roads: {len(roads)}, missing maxspeed: {len(missing)} "
          f"({100 * len(missing) / len(roads):.1f}%)")
    print("\nmissing by class:")
    tab = pd.crosstab(roads["highway_class"], roads["maxspeed_kmh"].isna())
    tab.columns = ["has_speed", "missing"]
    tab["pct_missing"] = (100 * tab["missing"] / (tab["has_speed"] + tab["missing"])).round(1)
    print(tab.sort_values("pct_missing", ascending=False).to_string())
    print(
        "\nNOTE: s_speed is NaN for these, so they lean on highway class and\n"
        "calming alone. If missingness concentrates in one class, the traffic\n"
        "domain is measuring something different there than elsewhere."
    )


def main(city_key: str, sample: int) -> None:
    city = config.get_city(city_key)
    print(f"Diagnostics for {city.place} ({city.crs})")

    schools = osm_extract.fetch_schools(city)
    G = osm_extract.fetch_walk_graph(city)
    segments = gpd.read_file(config.DATA_PROCESSED / f"{city.key}_segments.gpkg")
    edges = osm_extract.graph_to_edges(G)
    edges["highway_class"] = edges.get("highway").map(
        lambda v: v[0] if isinstance(v, (list, tuple)) and v else v
    )

    rng = np.random.default_rng(42)
    sample_idx = rng.choice(schools.index, size=min(sample, len(schools)), replace=False)

    diag_corridor_width(G, schools, city, sample_idx)
    diag_node_reach(G, schools, city, sample_idx)
    diag_sidewalk_thresholds(edges)
    diag_school_filter(city)
    diag_missing_speed(segments)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city")
    ap.add_argument("--sample", type=int, default=25,
                    help="schools to sample for the routing-heavy sections")
    args = ap.parse_args()
    main(args.city, args.sample)
