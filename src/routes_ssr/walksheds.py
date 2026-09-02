"""Network walksheds around schools.

The walkshed is computed as a *network buffer*: the set of street segments
reachable within a network-distance threshold, buffered by a small corridor
width and dissolved. This is deliberately not a convex hull or an alpha shape.
Hulls bridge across rivers, rail cuttings and motorways -- precisely the
barriers that determine whether a child can actually walk to school -- and so
systematically overstate catchments in exactly the places that matter most here.

Routing runs on either flat network distance or slope-adjusted walking time,
selected by the caller (see ``terrain.py``). The time-weighted variant is the
one that matters in hilly cities: on flat distance Rotterdam and Genova sit
within 0.027 of each other, and on walking time they are 0.157 apart.
"""

from __future__ import annotations

import logging
import math

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
from shapely.ops import unary_union

from .config import City, params

log = logging.getLogger(__name__)

# Half-width of the corridor drawn around reachable segments when dissolving
# them into a polygon. 40 m approximates "the buildings fronting this street"
# without merging parallel streets across a block.
CORRIDOR_HALF_WIDTH_M = 40.0


def minutes_to_metres(minutes: float, speed_kmh: float) -> float:
    """Convert a walking-time threshold to network metres."""
    return minutes * (speed_kmh * 1000.0 / 60.0)


def snap_schools(G, schools: gpd.GeoDataFrame) -> pd.Series:
    """Nearest network node for each school, as a Series indexed like ``schools``.

    Schools that snap further than 250 m from the network are flagged rather
    than silently kept: in practice these are mis-tagged features or campuses
    outside the downloaded extent, and a walkshed drawn from them is fiction.
    """
    xs = schools.geometry.x.to_numpy()
    ys = schools.geometry.y.to_numpy()
    nodes, dists = ox.nearest_nodes(G, X=xs, Y=ys, return_dist=True)

    # Nullable Int64 rather than int64: far-snapping schools are set to NA below,
    # and pandas 3 no longer silently upcasts an int64 column to hold a null.
    snapped = pd.Series(nodes, index=schools.index, name="node").astype("Int64")
    far = pd.Series(dists, index=schools.index) > 250.0
    if far.any():
        log.warning(
            "%d school(s) snapped >250 m from the walk network and will be dropped: %s",
            int(far.sum()),
            list(schools.loc[far, "school_id"]),
        )
    snapped[far] = pd.NA
    return snapped


def reach_ratio(
    G,
    schools: gpd.GeoDataFrame,
    radius_m: float,
    weight: str = "length",
    budget: float | None = None,
) -> pd.Series:
    """Severance measure: reachable nodes / nodes within the crow-flies circle.

    For each school, the share of network nodes inside a circle of ``radius_m``
    that are actually reachable within a routing budget. 1.0 means the network
    imposes no penalty at this scale; 0.3 means two thirds of what looks nearby
    cannot be walked to.

    ``weight`` selects the edge cost. With the default ``"length"`` the budget is
    ``radius_m`` and the measure is purely about network detour. With
    ``"walk_time"`` the budget is in seconds and the measure additionally
    captures terrain: a 600 m climb costs more of the budget than 600 m on the
    flat, which is the whole reason for the slope model.

    The circle always uses ``radius_m``, so the denominator is unchanged and the
    two variants remain directly comparable.

    Why this and not walkshed area / circle area
    --------------------------------------------
    The area-based ratio was the original severance metric here, and it is
    invalid. Walkshed area scales with the corridor half-width used to dissolve
    reachable segments into a polygon, which is a free parameter. Sweeping it
    over a defensible range (10-80 m) moved the mean ratio from 0.21 to 0.61 on
    the same city and the same network -- so most of the "signal" was the buffer.

    This measure has no such parameter. It is also not merely a rescaling: on a
    25-school sample the two metrics rank schools differently, so the severance
    candidates identified by the area ratio were an artefact of the buffer.
    """
    nodes_gdf = ox.graph_to_gdfs(G, nodes=True, edges=False)
    snapped = snap_schools(G, schools)

    out = pd.Series(np.nan, index=schools.index, dtype=float)
    for idx in schools.index:
        if pd.isna(snapped[idx]):
            continue
        reachable = set(
            nx.ego_graph(
                G, int(snapped[idx]),
                radius=(budget if budget is not None else radius_m),
                distance=weight,
            ).nodes
        )
        in_circle = nodes_gdf.index[
            nodes_gdf.geometry.distance(schools.geometry.loc[idx]) <= radius_m
        ]
        if len(in_circle) == 0:
            continue
        out[idx] = len(reachable & set(in_circle)) / len(in_circle)
    return out


def _walkshed_polygon(G, source_node, radius_m: float, weight: str = "length",
                      budget: float | None = None):
    """Dissolved network-buffer polygon for one origin at one radius."""
    sub = nx.ego_graph(
        G, source_node,
        radius=(budget if budget is not None else radius_m),
        distance=weight,
    )
    if sub.number_of_edges() == 0:
        return None
    edges = ox.graph_to_gdfs(sub, nodes=False, edges=True)
    corridor = edges.geometry.buffer(CORRIDOR_HALF_WIDTH_M)
    return unary_union(corridor.to_numpy())


def build_walksheds(
    G,
    schools: gpd.GeoDataFrame,
    city: City,
    minutes: list[int] | None = None,
    weight: str = "length",
) -> gpd.GeoDataFrame:
    """Walkshed polygons for every school at every time threshold.

    Returns one row per (school, threshold) with the polygon and its area. The
    long format is intentional -- it is what the multi-scale comparison and the
    cross-city panel both consume, and it keeps the 5/10/15-minute cuts
    comparable rather than nesting them in columns.
    """
    spec = params("walkshed")
    minutes = minutes or spec["minutes"]
    speed = float(spec["walk_speed_kmh"])

    nodes = snap_schools(G, schools)
    valid = nodes.notna()
    log.info(
        "%s: building walksheds for %d/%d schools x %d thresholds",
        city.key,
        int(valid.sum()),
        len(schools),
        len(minutes),
    )

    records = []
    for minute in minutes:
        radius = minutes_to_metres(minute, speed)
        # On a time-weighted graph the budget is the threshold itself, in
        # seconds. On a distance-weighted graph it is the equivalent distance.
        budget = minute * 60.0 if weight == "walk_time" else radius
        for idx in schools.index[valid]:
            # int() rather than the raw Int64 scalar: graph node keys are Python
            # ints, and an explicit cast keeps the lookup from depending on
            # numpy/pandas scalar hashing equivalence.
            poly = _walkshed_polygon(G, int(nodes[idx]), radius,
                                     weight=weight, budget=budget)
            if poly is None or poly.is_empty:
                continue
            records.append(
                {
                    "school_id": schools.at[idx, "school_id"],
                    "name": schools.at[idx, "name"],
                    "minutes": minute,
                    "radius_m": radius,
                    "geometry": poly,
                }
            )

    out = gpd.GeoDataFrame(records, geometry="geometry", crs=city.crs)
    out["area_km2"] = out.geometry.area / 1e6

    # Retained as a descriptive area, but NOT as a severance ratio: the area is
    # proportional to CORRIDOR_HALF_WIDTH_M, so any ratio built from it inherits
    # that arbitrary choice. See `reach_ratio` for the severance measure.
    out["circle_km2"] = (math.pi * out["radius_m"] ** 2) / 1e6

    # Severance, computed once per threshold on the node sets rather than areas.
    id_to_idx = {schools.at[i, "school_id"]: i for i in schools.index}
    for minute in minutes:
        radius = minutes_to_metres(minute, speed)
        rr = reach_ratio(
            G, schools, radius, weight=weight,
            budget=(minute * 60.0 if weight == "walk_time" else radius),
        )
        sel = out["minutes"] == minute
        out.loc[sel, "reach_ratio"] = (
            out.loc[sel, "school_id"].map(lambda s: rr.get(id_to_idx.get(s), np.nan))
        )

    return out
