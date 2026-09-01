"""Site-scale analysis: the schoolyard footprint and its street context.

Why this module is shaped the way it is
---------------------------------------
ROUTES is about schoolyards *and* school streets, and its stated distinctiveness
is the combination of the two. A site-scale analysis is therefore not optional.

But probing OSM for what is *inside* a Rotterdam schoolyard returns almost
nothing usable. Of 241 school polygons:

    landuse=grass          24%
    barrier on boundary    25%
    natural=tree           16%
    barrier=gate           19%
    leisure=pitch           7%
    leisure=playground      5%

Every interior indicator sits far below the comparability threshold, and the
missingness is not random -- yards mapped in detail are systematically the
newer, larger, better-surveyed ones. This is the sidewalk finding again in a
different layer, and it is handled the same way: those indicators are *reported*
as unmeasurable rather than scored.

What survives is the footprint itself, complete for every polygon school, and
everything derivable from its geometry and its relationship to the street
network -- which is also complete. That yields the one site-scale indicator
worth having:

**Frontage exposure.** Decompose a yard's boundary by the class of road it
fronts onto. A yard whose perimeter touches a secondary road is a different
proposition from one enclosed by living streets, and that difference is exactly
what a school-street intervention changes. It is measurable, comparable across
cities, and it links the two halves of the project.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import unary_union

log = logging.getLogger(__name__)

# How far from the yard boundary a road counts as "fronting" it. 20 m spans a
# pavement plus parking lane without reaching the far side of a city block.
FRONTAGE_BUFFER_M = 20.0

# Interior features probed and found unusable in Rotterdam. Named here so the
# coverage report can state what was looked for and not found, rather than the
# question silently never appearing.
UNMEASURABLE_INTERIOR = {
    "yard_playground": "leisure=playground",
    "yard_pitch": "leisure=pitch",
    "yard_trees": "natural=tree",
    "yard_grass": "landuse=grass",
    "yard_fenced": "barrier=* on boundary",
    "yard_gates": "barrier=gate",
}


def yard_form(yards: gpd.GeoDataFrame) -> pd.DataFrame:
    """Area, perimeter and compactness of each yard.

    Compactness is the Polsby-Popper score, 4*pi*A / P^2: 1.0 for a circle,
    falling toward 0 for long thin or highly indented shapes. A low value on a
    schoolyard usually means a strip of land beside a building rather than an
    enclosed yard, which matters for whether the space is usable at break time.
    """
    g = yards.geometry
    area = g.area
    perim = g.length

    with np.errstate(divide="ignore", invalid="ignore"):
        compact = np.where(perim > 0, 4 * np.pi * area / (perim**2), np.nan)

    return pd.DataFrame(
        {
            "yard_area_m2": area.to_numpy(),
            "yard_perimeter_m": perim.to_numpy(),
            "yard_compactness": compact,
        },
        index=yards.index,
    )


def frontage_exposure(
    yards: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    highway_score: dict[str, float],
    buffer_m: float = FRONTAGE_BUFFER_M,
) -> pd.DataFrame:
    """Decompose each yard's boundary by the class of road fronting it.

    Each stretch of boundary is assigned to the **worst** (lowest-scoring) road
    class within ``buffer_m``. A corner where a quiet street meets an arterial
    is exposed to the arterial, so taking the minimum is the safety-relevant
    choice rather than an averaging artefact. Classes are consumed worst-first
    with already-claimed boundary subtracted, which makes the shares a true
    partition instead of double-counting corners.

    Returns, per yard:
        frontage_score       perimeter-weighted mean road score (1 = best)
        frontage_worst       lowest-scoring class touching the boundary
        frontage_share_road  share of perimeter fronting any mapped road
        front_<class>        share of perimeter fronting each class
    """
    if yards.empty:
        return pd.DataFrame(index=yards.index)

    roads = edges.loc[edges["highway_class"].isin(highway_score)].copy()
    if roads.empty:
        log.warning("no scored road classes in edges; frontage left missing")
        return pd.DataFrame(
            {"frontage_score": np.nan, "frontage_worst": None, "frontage_share_road": 0.0},
            index=yards.index,
        )

    # Worst first, so the most hostile class claims contested boundary.
    classes = sorted(roads["highway_class"].unique(), key=lambda c: highway_score[c])

    boundary = yards.geometry.boundary
    sindex = roads.sindex
    rows: list[dict] = []

    for _, ring in boundary.items():
        if ring is None or ring.is_empty:
            rows.append({})
            continue

        total_len = ring.length
        if total_len <= 0:
            rows.append({})
            continue

        near = roads.iloc[list(sindex.query(ring.buffer(buffer_m), predicate="intersects"))]
        rec: dict[str, float] = {}
        remaining = ring

        for cls in classes:
            if remaining.is_empty:
                break
            sub = near.loc[near["highway_class"] == cls]
            if sub.empty:
                continue
            corridor = unary_union(sub.geometry.buffer(buffer_m).to_numpy())
            claimed = remaining.intersection(corridor)
            if claimed.is_empty:
                continue
            rec[f"front_{cls}"] = claimed.length / total_len
            remaining = remaining.difference(corridor)

        rows.append(rec)

    front = pd.DataFrame(rows, index=yards.index).fillna(0.0)
    share_cols = [c for c in front.columns if c.startswith("front_")]

    if not share_cols:
        # No yard fronts any mapped road. Not "score zero" -- unmeasurable.
        log.warning("no yard fronts a mapped road; frontage indicators left missing")
        front["frontage_score"] = np.nan
        front["frontage_worst"] = None
        front["frontage_share_road"] = 0.0
        return front

    shares = front[share_cols]
    scores = np.array([highway_score[c.removeprefix("front_")] for c in share_cols])

    covered = shares.sum(axis=1).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        weighted = (shares.to_numpy() * scores).sum(axis=1)
        front["frontage_score"] = np.where(covered > 0, weighted / covered, np.nan)

    front["frontage_share_road"] = covered

    # Worst class actually present on the boundary. `share_cols` is not ordered
    # by hostility, so pick by score rather than by first-nonzero position.
    order = np.argsort(scores)
    ordered_cols = [share_cols[i] for i in order]
    present = shares[ordered_cols].to_numpy() > 0
    worst_idx = np.where(present.any(axis=1), present.argmax(axis=1), -1)
    front["frontage_worst"] = [
        ordered_cols[i].removeprefix("front_") if i >= 0 else None for i in worst_idx
    ]

    n_fronting = int((covered > 0).sum())
    log.info(
        "frontage: %d/%d yards front a mapped road; mean score %.3f",
        n_fronting, len(front), float(np.nanmean(front["frontage_score"])),
    )
    return front


def build_yard_indicators(
    yards: gpd.GeoDataFrame,
    edges: gpd.GeoDataFrame,
    highway_score: dict[str, float],
) -> gpd.GeoDataFrame:
    """Full site-scale indicator set for a city's schoolyards."""
    out = yards.copy()
    out = out.join(yard_form(yards))
    out = out.join(frontage_exposure(yards, edges, highway_score))

    # Interior indicators: declared, queried, found unusable. The columns exist
    # and are NaN so the coverage report states the gap explicitly rather than
    # the question simply never appearing.
    for col in UNMEASURABLE_INTERIOR:
        out[col] = np.nan

    return out
