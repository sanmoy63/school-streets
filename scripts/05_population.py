"""Population within school catchments, and population-weighted severance.

Usage
-----
    python scripts/05_population.py rotterdam

Requires ``01_build_city.py`` to have run (uses the cached walk graph and the
walkshed polygons it wrote).

The headline output is `pop_reach_ratio`: of the residents living along streets
within *straight-line* reach of a school, what share live along streets that can
actually be *walked* to within the same distance.

Why the denominator is built the same way as the numerator
----------------------------------------------------------
The obvious denominator is population inside a circle. It would be wrong, and
wrong in a way this project has already been burned by. The numerator is a
network corridor roughly 80 m wide; a circle is a solid disc. Their ratio would
mostly measure how much of the disc a thin corridor covers -- i.e. the corridor
width -- which is exactly the free parameter that invalidated the original
`network_ratio` severance metric.

So the denominator is the *same* corridor construction applied to every street
within Euclidean radius, reachable or not. The buffer width then appears in both
terms and largely cancels, leaving severance. `--check-buffer` verifies that
empirically rather than asserting it.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import networkx as nx  # noqa: E402
import numpy as np  # noqa: E402
import osmnx as ox  # noqa: E402
import pandas as pd  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from routes_ssr import config, osm_extract, population, terrain, walksheds  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("pop")


def corridors(G, edges, schools, nodes, radius_m, half_width,
              weight="length", budget=None):
    """Reachable and straight-line corridors per school, built identically.

    ``weight``/``budget`` mirror the walkshed routing. They must match what
    01_build_city.py used: routing walksheds on terrain-adjusted time while
    computing population on flat distance would report residents for catchments
    the child cannot actually reach.
    """
    sindex = edges.sindex
    reach, circle = [], []

    for idx in schools.index:
        node = nodes[idx]
        pt = schools.geometry.loc[idx]

        if pd.isna(node):
            reach.append(None)
            circle.append(None)
            continue

        sub = nx.ego_graph(
            G, int(node),
            radius=(budget if budget is not None else radius_m),
            distance=weight,
        )
        if sub.number_of_edges():
            re = ox.graph_to_gdfs(sub, nodes=False, edges=True)
            reach.append(unary_union(re.geometry.buffer(half_width).to_numpy()))
        else:
            reach.append(None)

        disc = pt.buffer(radius_m)
        cand = edges.iloc[list(sindex.query(disc, predicate="intersects"))]
        cand = cand[cand.geometry.intersects(disc)]
        circle.append(
            unary_union(cand.geometry.buffer(half_width).to_numpy()) if len(cand) else None
        )

    return reach, circle


def main(city_key: str, check_buffer: bool = False, slope: bool = False) -> None:
    config.ensure_dirs()
    city = config.get_city(city_key)
    log.info("=== population: %s ===", city.place)

    tif = population.fetch_ghs_pop(city)

    schools = osm_extract.fetch_schools(city)
    G = osm_extract.fetch_walk_graph(city)
    edges = osm_extract.graph_to_edges(G)[["geometry"]]
    nodes = walksheds.snap_schools(G, schools)

    spec = config.params("walkshed")
    speed = float(spec["walk_speed_kmh"])

    weight = "length"
    if slope:
        dem = terrain.fetch_dem(city)
        terrain.add_walk_time(G, dem, speed)
        weight = "walk_time"
    widths = [walksheds.CORRIDOR_HALF_WIDTH_M] + ([20.0] if check_buffer else [])

    frames = []
    for half_width in widths:
        for minute in spec["minutes"]:
            radius = walksheds.minutes_to_metres(minute, speed)
            log.info("corridors: %d min (r=%.0f m), half-width %.0f m", minute, radius, half_width)
            reach, circle = corridors(
                G, edges, schools, nodes, radius, half_width, weight=weight,
                budget=(minute * 60.0 if weight == "walk_time" else radius),
            )

            gr = gpd.GeoDataFrame(geometry=reach, crs=city.crs)
            gc = gpd.GeoDataFrame(geometry=circle, crs=city.crs)

            pop_r = population.zonal_population(gr, tif)
            pop_c = population.zonal_population(gc, tif)

            with np.errstate(invalid="ignore", divide="ignore"):
                ratio = np.where(pop_c > 0, pop_r / pop_c, np.nan)

            frames.append(pd.DataFrame({
                "school_id": schools["school_id"].to_numpy(),
                "name": schools["name"].to_numpy(),
                "minutes": minute,
                "half_width_m": half_width,
                "pop_reachable": pop_r.to_numpy(),
                "pop_straightline": pop_c.to_numpy(),
                "pop_reach_ratio": ratio,
            }))

    out = pd.concat(frames, ignore_index=True)
    out.to_csv(config.OUT_TABLES / f"{city.key}_population.csv", index=False)

    main_w = walksheds.CORRIDOR_HALF_WIDTH_M
    core = out[out["half_width_m"] == main_w]

    log.info("--- residents within reach ---")
    for minute, g in core.groupby("minutes"):
        log.info("  %2d min: median %6.0f residents  (IQR %.0f-%.0f)  n=%d",
                 minute, g["pop_reachable"].median(),
                 g["pop_reachable"].quantile(.25), g["pop_reachable"].quantile(.75),
                 g["pop_reachable"].notna().sum())

    log.info("--- population-weighted severance (pop_reach_ratio) ---")
    for minute, g in core.groupby("minutes"):
        r = g["pop_reach_ratio"].dropna()
        log.info("  %2d min: mean %.3f  sd %.3f  p10 %.3f  p90 %.3f",
                 minute, r.mean(), r.std(), r.quantile(.10), r.quantile(.90))

    ten = core[core["minutes"] == 10].dropna(subset=["pop_reach_ratio"])
    if not ten.empty:
        lost = (ten["pop_straightline"] - ten["pop_reachable"]).sum()
        log.info("  across %d schools at 10 min, %.0f resident-adjacencies are "
                 "within straight-line range but not walkable",
                 len(ten), lost)
        log.info("  most severed by population:")
        for _, r in ten.nsmallest(5, "pop_reach_ratio").iterrows():
            log.info("    %-40s %.3f  (%.0f of %.0f residents reachable)",
                     str(r["name"])[:40], r["pop_reach_ratio"],
                     r["pop_reachable"], r["pop_straightline"])

    if check_buffer:
        log.info("--- buffer-width check (the failure mode of network_ratio) ---")
        piv = out.pivot_table(index="minutes", columns="half_width_m",
                              values="pop_reach_ratio", aggfunc="mean")
        log.info("\n%s", piv.round(4).to_string())
        cols = list(piv.columns)
        if len(cols) == 2:
            drift = (piv[cols[1]] - piv[cols[0]]).abs().max()
            log.info("  max drift across %s m vs %s m half-width: %.4f", cols[0], cols[1], drift)
            log.info("  (area-based network_ratio moved 0.21 -> 0.61 over a comparable sweep)")

    log.info("-> outputs/tables/%s_population.csv", city.key)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city")
    ap.add_argument("--check-buffer", action="store_true",
                    help="also run at 20 m half-width to verify the buffer cancels")
    ap.add_argument("--slope", action="store_true",
                    help="route on terrain-adjusted time; must match 01_build_city.py")
    args = ap.parse_args()
    main(args.city, check_buffer=args.check_buffer, slope=args.slope)
