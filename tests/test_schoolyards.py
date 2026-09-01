"""Site-scale schoolyard indicators."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Polygon

from routes_ssr.schoolyards import (
    UNMEASURABLE_INTERIOR,
    build_yard_indicators,
    frontage_exposure,
    yard_form,
)

CRS = "EPSG:28992"

# A deliberately coarse score table, so the tests do not silently depend on the
# real weights and break when those are tuned.
SCORES = {
    "trunk": 0.0,
    "secondary": 0.2,
    "residential": 0.75,
    "living_street": 1.0,
}


def square(x0, y0, side):
    return Polygon([(x0, y0), (x0 + side, y0), (x0 + side, y0 + side), (x0, y0 + side)])


def yards(*polys):
    return gpd.GeoDataFrame({"school_id": [f"t_{i}" for i in range(len(polys))]},
                            geometry=list(polys), crs=CRS)


def roads(*specs):
    return gpd.GeoDataFrame(
        {"highway_class": [c for c, _ in specs]},
        geometry=[g for _, g in specs],
        crs=CRS,
    )


# --- form ------------------------------------------------------------------


def test_area_and_perimeter():
    f = yard_form(yards(square(0, 0, 100)))
    assert f["yard_area_m2"].iloc[0] == pytest.approx(10_000)
    assert f["yard_perimeter_m"].iloc[0] == pytest.approx(400)


def test_compactness_is_highest_for_compact_shapes():
    """A square must score above a long thin strip of the same family.

    A low value usually means a verge beside a building rather than a usable
    yard, which is the distinction this indicator exists to draw.
    """
    sq = yard_form(yards(square(0, 0, 100)))["yard_compactness"].iloc[0]
    strip = yard_form(yards(Polygon([(0, 0), (400, 0), (400, 10), (0, 10)])))["yard_compactness"].iloc[0]
    assert sq > strip
    assert 0 < strip < sq <= 1.0


def test_compactness_of_a_square_is_pi_over_four():
    got = yard_form(yards(square(0, 0, 50)))["yard_compactness"].iloc[0]
    assert got == pytest.approx(np.pi / 4)


# --- frontage --------------------------------------------------------------


def test_yard_fronting_only_a_quiet_street_scores_high():
    y = yards(square(0, 0, 100))
    r = roads(("living_street", LineString([(-50, -5), (150, -5)])))
    f = frontage_exposure(y, r, SCORES)
    assert f["frontage_score"].iloc[0] == pytest.approx(1.0)
    assert f["frontage_worst"].iloc[0] == "living_street"


def test_yard_fronting_an_arterial_scores_low():
    y = yards(square(0, 0, 100))
    r = roads(("trunk", LineString([(-50, -5), (150, -5)])))
    f = frontage_exposure(y, r, SCORES)
    assert f["frontage_score"].iloc[0] == pytest.approx(0.0)
    assert f["frontage_worst"].iloc[0] == "trunk"


def test_worst_class_wins_contested_boundary():
    """Where a quiet street and an arterial both reach the same stretch of
    boundary, the child is exposed to the arterial. The partition must assign
    that stretch to the worse class, not average the two."""
    y = yards(square(0, 0, 100))
    r = roads(
        ("living_street", LineString([(-50, -5), (150, -5)])),
        ("trunk", LineString([(-50, -8), (150, -8)])),
    )
    f = frontage_exposure(y, r, SCORES)
    assert f["frontage_worst"].iloc[0] == "trunk"

    trunk_share = f["front_trunk"].iloc[0]
    quiet_share = f.get("front_living_street", pd.Series([0.0])).iloc[0]

    # The trunk takes the contested bottom edge. The living_street is 3 m nearer
    # the yard, so its buffer reaches a sliver of the two side edges that the
    # trunk buffer does not -- that stretch is genuinely fronted only by the
    # quiet street, and it should be credited as such. What must not happen is
    # both classes claiming the same boundary.
    assert trunk_share > 0.25
    assert quiet_share < 0.05
    assert trunk_share + quiet_share <= 1.0 + 1e-9


def test_shares_never_exceed_one():
    """Shares are a partition of the boundary, not overlapping claims."""
    y = yards(square(0, 0, 100))
    r = roads(
        ("residential", LineString([(-50, -5), (150, -5)])),
        ("residential", LineString([(-5, -50), (-5, 150)])),
        ("secondary", LineString([(-50, 105), (150, 105)])),
    )
    f = frontage_exposure(y, r, SCORES)
    share_cols = [c for c in f.columns if c.startswith("front_")]
    assert f[share_cols].sum(axis=1).iloc[0] <= 1.0 + 1e-9


def test_partly_fronted_yard_reports_partial_coverage():
    y = yards(square(0, 0, 100))
    r = roads(("residential", LineString([(-50, -5), (150, -5)])))
    f = frontage_exposure(y, r, SCORES)
    share = f["frontage_share_road"].iloc[0]
    # One side of four, plus buffer wrap at the corners.
    assert 0.2 < share < 0.6


def test_yard_touching_no_road_is_unknown_not_zero():
    """The recurring rule, at site scale.

    A yard with no mapped road near it has *unknown* frontage. Scoring it 0.0
    would rank it alongside a yard fronting a motorway; scoring it 1.0 would
    rank it as ideal. Both are claims the data does not support.
    """
    y = yards(square(0, 0, 100))
    r = roads(("residential", LineString([(5000, 5000), (5100, 5000)])))
    f = frontage_exposure(y, r, SCORES)
    assert np.isnan(f["frontage_score"].iloc[0])
    assert f["frontage_share_road"].iloc[0] == pytest.approx(0.0)


def test_no_scored_roads_at_all_yields_missing_not_zero():
    y = yards(square(0, 0, 100))
    r = roads(("some_unknown_class", LineString([(-50, -5), (150, -5)])))
    f = frontage_exposure(y, r, SCORES)
    assert np.isnan(f["frontage_score"].iloc[0])


def test_empty_yard_set_returns_empty_frame():
    empty = gpd.GeoDataFrame({"school_id": []}, geometry=[], crs=CRS)
    r = roads(("residential", LineString([(0, 0), (10, 0)])))
    assert frontage_exposure(empty, r, SCORES).empty


# --- assembly --------------------------------------------------------------


def test_unmeasurable_interior_columns_are_present_and_nan():
    """The interior indicators were queried and found unusable in Rotterdam
    (playground 5%, pitch 7%, trees 16%, grass 24%).

    They are carried as explicit NaN columns so the coverage report states the
    gap, rather than the question silently never appearing in the output.
    """
    y = yards(square(0, 0, 100))
    r = roads(("residential", LineString([(-50, -5), (150, -5)])))
    out = build_yard_indicators(y, r, SCORES)
    for col in UNMEASURABLE_INTERIOR:
        assert col in out.columns, f"{col} missing"
        assert out[col].isna().all(), f"{col} should be entirely NaN"


def test_build_preserves_school_id_and_geometry():
    y = yards(square(0, 0, 100), square(500, 500, 60))
    r = roads(("residential", LineString([(-50, -5), (150, -5)])))
    out = build_yard_indicators(y, r, SCORES)
    assert list(out["school_id"]) == ["t_0", "t_1"]
    assert out.geometry.geom_type.eq("Polygon").all()
    assert len(out) == 2
