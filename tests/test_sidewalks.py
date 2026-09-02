"""Sidewalk inference from parallel footway geometry.

The central property under test is the semantics of *absence*: finding no
parallel footway must mean "unknown", never "no sidewalk".
"""

from __future__ import annotations

import numpy as np
import pytest

from routes_ssr.sidewalks import (
    BOTH_SIDES_RATIO,
    ONE_SIDE_RATIO,
    _angle_diff,
    _bearing,
    sidewalk_provision,
)
from shapely.geometry import LineString


# --- geometry helpers ------------------------------------------------------


@pytest.mark.parametrize(
    "coords,expected",
    [
        ([(0, 0), (10, 0)], 0.0),      # east
        ([(0, 0), (0, 10)], 90.0),     # north
        ([(0, 0), (10, 10)], 45.0),
        ([(10, 0), (0, 0)], 0.0),      # west -- folded to the same axis
        ([(0, 10), (0, 0)], 90.0),     # south -- folded
    ],
)
def test_bearing_folds_direction_away(coords, expected):
    """A sidewalk drawn the other way round is still parallel to its road."""
    assert _bearing(LineString(coords)) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    "a,b,expected",
    [(0, 0, 0), (0, 30, 30), (0, 170, 10), (170, 0, 10), (90, 0, 90), (10, 175, 15)],
)
def test_angle_diff_wraps_at_180(a, b, expected):
    got = _angle_diff(np.array([a], float), np.array([b], float))[0]
    assert got == pytest.approx(expected)


def test_angle_diff_never_exceeds_90():
    a = np.arange(0, 180, 7, dtype=float)
    b = np.arange(0, 180, 7, dtype=float)[::-1]
    assert (_angle_diff(a, b) <= 90.0 + 1e-9).all()


# --- provision semantics ---------------------------------------------------


def road_score(edges, result):
    """Score of the single road row in a fixture frame."""
    road = edges["highway_class"].isin(["residential"])
    return result[road].iloc[0]


def test_footways_both_sides_scores_one(straight_road_with_two_sidewalks):
    s = sidewalk_provision(straight_road_with_two_sidewalks)
    assert road_score(straight_road_with_two_sidewalks, s) == pytest.approx(1.0)


def test_footway_one_side_scores_partial(straight_road_one_sidewalk):
    s = sidewalk_provision(straight_road_one_sidewalk)
    assert road_score(straight_road_one_sidewalk, s) == pytest.approx(0.55)


def test_no_parallel_footway_is_unknown_not_zero(road_with_distant_footway):
    """THE regression test for this module.

    Scoring absence as 0.0 asserted "no sidewalk" for 81% of Rotterdam roads on
    data that cannot support the claim -- Rotterdam records only 30 km of
    explicit sidewalk against 3,551 km of road. Absence in the map says nothing
    about absence on the ground.
    """
    s = sidewalk_provision(road_with_distant_footway)
    assert np.isnan(road_score(road_with_distant_footway, s))


def test_perpendicular_footway_does_not_count(road_with_crossing_footway):
    """A footway crossing the road is a crossing, not a sidewalk."""
    s = sidewalk_provision(road_with_crossing_footway)
    assert np.isnan(road_score(road_with_crossing_footway, s))


def test_footways_are_not_self_scored(straight_road_with_two_sidewalks):
    """Regression: footways used to self-score 1.0 ('a footway is its own
    sidewalk'). True, but not an observation -- and it inflated the domain's
    coverage from 0.19 to 0.54."""
    s = sidewalk_provision(straight_road_with_two_sidewalks)
    foot = straight_road_with_two_sidewalks["highway_class"] == "footway"
    assert s[foot].isna().all()


def test_wider_buffer_finds_more_sidewalks(straight_road_one_sidewalk):
    """Sanity check on the parameter whose sweep moved results 31 points."""
    near = sidewalk_provision(straight_road_one_sidewalk, buffer_m=3.0)
    far = sidewalk_provision(straight_road_one_sidewalk, buffer_m=30.0)
    assert np.isnan(road_score(straight_road_one_sidewalk, near))
    assert road_score(straight_road_one_sidewalk, far) == pytest.approx(0.55)


def test_ratio_thresholds_are_ordered():
    assert 0 < ONE_SIDE_RATIO < BOTH_SIDES_RATIO


def test_empty_footway_set_yields_all_unknown(road_with_no_footways):
    """No footway layer at all still means unknown, not zero.

    Uses a fixture rather than importing tests.conftest directly: `pytest`
    invoked as a console script does not put the working directory on sys.path,
    so `import tests.conftest` works under `python -m pytest` and fails under
    bare `pytest`. CI runs the latter.
    """
    s = sidewalk_provision(road_with_no_footways)
    assert s.isna().all()
