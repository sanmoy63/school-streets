"""Sidewalk provision inferred from parallel footway geometry.

Why this module exists
----------------------
The obvious way to measure sidewalk provision is the OSM ``sidewalk=*`` tag on
the road. In Rotterdam that tag is present on **zero** of 68,464 road segments.
This is not missing data in the usual sense: the Netherlands, Finland and
increasingly Slovakia map sidewalks as *separate footway ways* alongside the
carriageway, so the information is present in the geometry and absent from the
tag.

Relying on the tag therefore does not merely lose coverage -- it induces a
selection effect. An index that requires the tag silently restricts itself to
ways that are themselves footways, which all score alike, producing a
near-constant index that looks like a finding and is an artefact.

This module recovers provision from geometry instead: a road is credited with a
sidewalk when a footway runs alongside it, close and roughly parallel, for a
reasonable share of its length.

Limitations, stated plainly
---------------------------
* A footway crossing a road at a shallow angle can be miscounted; the angle
  tolerance is the control for that.
* Segregated cycle tracks tagged as footways will be counted as walking
  provision. In NL this is a genuine source of over-counting.
* The two-sided test is a length-ratio heuristic, not a true side-of-road
  determination. It cannot distinguish one wide bidirectional footway from two
  narrow ones.

These are recorded rather than hidden because the resulting indicator is used
for cross-city comparison, and a reader needs to know which way it errs.
"""

from __future__ import annotations

import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Classes that carry motor traffic and therefore need a sidewalk to be walkable.
ROAD_CLASSES = {
    "residential", "service", "unclassified", "tertiary", "tertiary_link",
    "secondary", "secondary_link", "primary", "primary_link",
    "trunk", "trunk_link", "busway",
}

# Classes that constitute walking provision in their own right.
FOOT_CLASSES = {"footway", "path", "pedestrian", "living_street", "steps", "track"}

# A footway further than this from the carriageway centreline is a separate
# route, not this road's sidewalk.
#
# A single global buffer was the largest source of instability in this
# indicator: sweeping it over 15-35 m moved the "no sidewalk" share by 31
# percentage points (83% -> 52%), because one distance cannot be right for both
# a 6 m service alley and a dual-carriageway primary road. Too small and wide
# roads lose their real sidewalks; too large and narrow streets are credited
# with footways from the next block.
#
# The buffer is therefore scaled to the road's expected half-profile: roughly
# (carriageway + parking + verge) / 2 for each class. These are geometric
# estimates of Dutch/Northern European urban cross-sections, not measurements,
# and they remain the indicator's main assumption.
BUFFER_M = 25.0  # fallback for classes not listed below

BUFFER_BY_CLASS = {
    "service": 10.0,        # alleys, parking aisles, driveways
    "residential": 14.0,    # single carriageway + parking strip
    "unclassified": 14.0,
    "living_street": 12.0,
    "tertiary": 18.0,
    "tertiary_link": 18.0,
    "secondary": 22.0,      # often 2+2 with median
    "secondary_link": 22.0,
    "primary": 28.0,
    "primary_link": 28.0,
    "trunk": 34.0,          # dual carriageway
    "trunk_link": 34.0,
    "busway": 16.0,
}

# Maximum bearing difference for a footway to count as running "alongside".
ANGLE_TOL_DEG = 30.0

# Parallel footway length, as a multiple of road length, implying provision on
# both sides / one side. Two full-length sidewalks give a ratio near 2.0; the
# thresholds sit below the ideal to tolerate gaps at junctions.
BOTH_SIDES_RATIO = 1.50
ONE_SIDE_RATIO = 0.65


def _bearing(geom) -> float:
    """Orientation of a line in degrees, folded to [0, 180).

    Endpoint-to-endpoint rather than per-vertex: we want the overall run of the
    segment, and OSMnx segments are short enough that curvature is minor.
    Folding to 180 makes direction irrelevant -- a sidewalk drawn the other way
    round is still parallel.
    """
    coords = list(geom.coords)
    x0, y0 = coords[0]
    x1, y1 = coords[-1]
    return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180.0


def _angle_diff(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest absolute difference between two folded bearings."""
    d = np.abs(a - b) % 180.0
    return np.minimum(d, 180.0 - d)


def sidewalk_provision(
    edges: gpd.GeoDataFrame,
    buffer_m: float | None = None,
    angle_tol: float = ANGLE_TOL_DEG,
    buffer_scale: float = 1.0,
) -> pd.Series:
    """Score sidewalk provision per road segment, aligned to ``edges.index``.

    Returns 1.0 (both sides) or 0.55 (one side) where a parallel footway is
    found, and **NaN everywhere else** -- including on footways themselves,
    where the question is not applicable rather than trivially satisfied.

    Why absence is NaN and not 0.0
    ------------------------------
    An earlier version scored "no parallel footway" as 0.0, i.e. as a confirmed
    absence of sidewalk. That is only valid if the city maps sidewalks as
    separate ways *completely*. Rotterdam does not: total foot-class length is
    0.61x road length, where complete two-sided mapping would give roughly 2.0x.
    Most of that footway length is park path, cycle link and through-block
    connection rather than sidewalk, and the ``footway=*`` subtype needed to
    tell them apart is absent from the routing graph.

    Scoring absence as zero therefore asserted "no sidewalk" for 81% of
    Rotterdam roads on the strength of data that cannot support the claim --
    the same missing-is-not-zero error this project was built to avoid, in a
    different disguise. Positive evidence is scored; its absence is recorded as
    unknown and shows up as reduced coverage in the harmonisation matrix, which
    is where a reader can act on it.
    """
    if "highway_class" not in edges.columns:
        raise KeyError("edges must carry `highway_class` (run build_segment_indicators first)")

    geom_ok = edges.geometry.notna() & (edges.geometry.geom_type == "LineString")
    is_road = edges["highway_class"].isin(ROAD_CLASSES) & geom_ok
    is_foot = edges["highway_class"].isin(FOOT_CLASSES) & geom_ok

    roads = edges.loc[is_road, ["geometry"]].copy()
    foots = edges.loc[is_foot, ["geometry"]].copy()

    out = pd.Series(np.nan, index=edges.index, dtype=float)

    # Footways are deliberately NOT self-scored 1.0 here. It is true that a
    # footway is walking infrastructure, but it is not an *observation* about
    # sidewalk provision -- and counting 52,315 tautological rows inflated this
    # domain's apparent coverage from 0.19 to 0.54, very nearly carrying it
    # through the comparability gate on segments that told us nothing. Footways
    # are marked not-applicable in `segment_index.applicability` instead.

    if roads.empty or foots.empty:
        log.warning("sidewalk_provision: %d roads, %d footways -- skipping",
                    len(roads), len(foots))
        return out

    # Whether separate-sidewalk mapping is complete enough for absence to be
    # informative. Reported so the caller can see the assumption, not used to
    # silently switch behaviour.
    foot_km = foots.geometry.length.sum() / 1000
    road_km = roads.geometry.length.sum() / 1000
    completeness = foot_km / road_km if road_km else 0.0
    if completeness < 1.0:
        log.warning(
            "foot-class length is %.2fx road length (complete two-sided mapping "
            "would be ~2.0x). Absence of a parallel footway is therefore recorded "
            "as UNKNOWN, not as absent sidewalk.",
            completeness,
        )

    roads["bearing"] = roads.geometry.map(_bearing)
    foots["bearing"] = foots.geometry.map(_bearing)
    roads["road_len"] = roads.geometry.length

    # Per-class corridor width, unless a single width is forced (which the
    # sensitivity diagnostics do, to sweep the parameter).
    if buffer_m is not None:
        widths = pd.Series(float(buffer_m), index=roads.index)
    else:
        widths = (
            edges.loc[roads.index, "highway_class"]
            .map(BUFFER_BY_CLASS)
            .fillna(BUFFER_M)
            .astype(float)
        )
    widths = widths * float(buffer_scale)

    # Candidate pairs: footways whose geometry falls within the road corridor.
    corridor = gpd.GeoDataFrame(
        roads[["bearing", "road_len"]],
        geometry=roads.geometry.buffer(widths.to_numpy()),
        crs=edges.crs,
    )
    pairs = gpd.sjoin(
        foots, corridor, how="inner", predicate="intersects", lsuffix="f", rsuffix="r"
    )
    if pairs.empty:
        return out  # no evidence anywhere -> all roads stay unknown

    # Keep only roughly parallel pairs.
    keep = _angle_diff(
        pairs["bearing_f"].to_numpy(dtype=float),
        pairs["bearing_r"].to_numpy(dtype=float),
    ) <= angle_tol
    pairs = pairs.loc[keep]

    if pairs.empty:
        return out

    # Length of each footway actually inside the corridor -- not the footway's
    # full length, which would credit a long path that merely clips the buffer.
    road_buffers = corridor.geometry.loc[pairs["index_r"].to_numpy()]
    clipped = gpd.GeoSeries(
        pairs.geometry.to_numpy(), crs=edges.crs
    ).intersection(gpd.GeoSeries(road_buffers.to_numpy(), crs=edges.crs))

    contrib = pd.DataFrame(
        {"road_idx": pairs["index_r"].to_numpy(), "flen": clipped.length.to_numpy()}
    )
    parallel_len = contrib.groupby("road_idx")["flen"].sum()

    ratio = (parallel_len / roads["road_len"].reindex(parallel_len.index)).fillna(0.0)

    # Only positive evidence is written; roads with no qualifying parallel
    # footway keep the NaN they started with.
    scores = pd.Series(np.nan, index=roads.index, dtype=float)
    scores.loc[ratio.index[ratio >= ONE_SIDE_RATIO]] = 0.55
    scores.loc[ratio.index[ratio >= BOTH_SIDES_RATIO]] = 1.0
    out.loc[scores.index] = scores.to_numpy()

    n_both = int((scores == 1.0).sum())
    n_one = int((scores == 0.55).sum())
    n_unknown = int(scores.isna().sum())
    log.info(
        "sidewalk provision: %d roads -> both %d (%.0f%%), one %d (%.0f%%), "
        "UNKNOWN %d (%.0f%%)",
        len(roads),
        n_both, 100 * n_both / len(roads),
        n_one, 100 * n_one / len(roads),
        n_unknown, 100 * n_unknown / len(roads),
    )
    return out
