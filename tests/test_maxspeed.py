"""Speed-limit parsing and scoring."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from routes_ssr.segment_index import IMPLICIT_SPEED, parse_maxspeed, score_speed


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("30", 30.0),
        ("50 km/h", 50.0),
        ("50km/h", 50.0),
        ("  30  ", 30.0),
        ("NL:zone30", 30.0),
        ("NL:urban", 50.0),
        ("BE:urban", 50.0),
        ("BE-VLG:urban", 50.0),
        ("PL:zone30", 30.0),
        ("IT:urban", 50.0),
        ("walk", 7.0),
        ("living_street", 20.0),
    ],
)
def test_parses_known_forms(raw, expected):
    assert parse_maxspeed(raw) == pytest.approx(expected)


def test_parses_mph_with_conversion():
    # 30 mph is 48.28 km/h -- above the 40 km/h band boundary, so getting the
    # conversion wrong would move the segment into a different score class.
    assert parse_maxspeed("30 mph") == pytest.approx(48.28, abs=0.01)


@pytest.mark.parametrize("raw", [None, float("nan"), "", "signals", "variable", "DE:autobahn", "FI:urban"])
def test_unparseable_yields_nan_not_a_guess(raw):
    """Anything unrecognised must be NaN.

    Guessing a default here would be the missing-is-not-zero error in its
    original form: an unreadable tag silently becoming a confident number.
    `FI:urban` is included deliberately: it is a perfectly valid OSM code,
    but for a country outside the study area, so it must not resolve.
    """
    assert np.isnan(parse_maxspeed(raw))


def test_merged_segment_takes_the_highest_limit_not_the_first():
    """OSM tags arrive as lists when OSMnx merges several ways into one segment.

    This asserted 30.0 -- the first element -- which was both optimistic and
    non-deterministic: Overpass does not fix the order ways come back in, so
    the same segment scored 30 or 50 depending on the response. The highest
    limit governs, being the fastest traffic the child meets anywhere along
    the segment, and it does not depend on ordering.
    """
    assert parse_maxspeed(["30", "50"]) == 50.0
    assert parse_maxspeed(["50", "30"]) == 50.0
    assert np.isnan(parse_maxspeed([]))


def test_an_unreadable_component_does_not_hide_a_readable_one():
    assert parse_maxspeed(["walk", "50"]) == 50.0
    assert parse_maxspeed(["nonsense", "banana"]) != parse_maxspeed(["30"])


def test_implicit_speed_table_is_all_numeric():
    assert all(isinstance(v, (int, float)) for v in IMPLICIT_SPEED.values())


# --- scoring ---------------------------------------------------------------


@pytest.mark.parametrize(
    "kmh,expected",
    [(5, 1.00), (20, 1.00), (30, 0.85), (40, 0.50), (50, 0.20), (80, 0.00)],
)
def test_score_speed_bands(kmh, expected):
    assert score_speed(kmh, hostile_kmh=50) == pytest.approx(expected)


def test_score_speed_is_monotone_non_increasing():
    """Faster traffic must never score better for a walking child."""
    scores = [score_speed(v, 50) for v in range(5, 101, 5)]
    assert all(a >= b for a, b in zip(scores, scores[1:])), scores


def test_score_speed_drops_sharply_across_30_to_50():
    """The 30->50 band is where pedestrian fatality risk rises steeply.

    A linear score would flatten exactly the distinction that motivates 30 km/h
    zones as the standard school-street measure.
    """
    assert score_speed(30, 50) - score_speed(50, 50) >= 0.6


def test_score_speed_propagates_nan():
    assert math.isnan(score_speed(float("nan"), 50))


def test_every_study_country_has_an_urban_default():
    """Each pilot country's implicit urban code must parse.

    A missing code is not a crash -- it silently becomes NaN, and the segment
    then leans on road class alone. That is a quiet loss of a whole indicator
    across an entire city, so it is checked rather than assumed.
    """
    for code in ["NL:urban", "BE:urban", "PL:urban", "IT:urban"]:
        assert parse_maxspeed(code) == 50.0, f"{code} did not parse"


# --- merged-segment tag resolution must not depend on order -----------------
#
# 3,982 Rotterdam segments and 5,826 Genova segments carry list-valued tags,
# every one with more than one distinct value. Reading element [0] made the
# result depend on the order Overpass replied in: refetching Genova's graph
# returned 5,131 of 5,826 lists reordered with identical contents, silently
# reclassifying 1,265 Genova and 552 Rotterdam segments between runs.


def test_highway_class_takes_the_least_favourable_component():
    """A part-residential, part-pedestrian segment exposes the child to the road."""
    from routes_ssr.segment_index import HIGHWAY_SCORE, _governing

    rank = lambda p: HIGHWAY_SCORE.get(str(p))
    assert _governing(["residential", "pedestrian"], rank) == "residential"
    assert _governing(["pedestrian", "residential"], rank) == "residential"
    assert _governing("footway", rank) == "footway"


def test_governing_is_order_independent_for_every_permutation():
    import itertools

    from routes_ssr.segment_index import HIGHWAY_SCORE, _governing

    rank = lambda p: HIGHWAY_SCORE.get(str(p))
    tags = ["primary", "residential", "footway"]
    answers = {_governing(list(p), rank) for p in itertools.permutations(tags)}
    assert len(answers) == 1, answers
    assert answers.pop() == "primary"


def test_governing_handles_unscored_and_empty_components():
    from routes_ssr.segment_index import HIGHWAY_SCORE, _governing

    rank = lambda p: HIGHWAY_SCORE.get(str(p))
    assert _governing([], rank) is None
    assert _governing(None, rank) is None
    # an unrecognised class must not be silently preferred over a known one
    assert _governing(["not_a_highway", "primary"], rank) == "primary"


def test_presence_tags_are_detected_on_any_component():
    from routes_ssr.segment_index import _has_tag

    s = pd.Series([["no", "yes"], ["yes", "no"], ["no", "no"], None])
    got = _has_tag(s)
    assert got.iloc[0] == got.iloc[1] == 1.0, "order must not matter"
    assert got.iloc[2] == 0.0
    assert np.isnan(got.iloc[3]), "untagged stays unknown, not absent"


def test_tied_scores_resolve_deterministically():
    """The bug inside the first version of the fix.

    footway, steps, pedestrian and living_street all score 1.00, so ranking on
    score alone left min() returning whichever tied component happened to come
    first. That is the ordering dependence the function exists to remove, and
    it was not cosmetic: `footway` triggers the implicit 20 km/h fill in
    build_segment_indicators and `steps` does not, so a ['footway','steps']
    segment changed its speed score with the order Overpass replied in.
    """
    from routes_ssr.segment_index import HIGHWAY_SCORE, _governing

    rank = lambda p: HIGHWAY_SCORE.get(str(p))
    assert HIGHWAY_SCORE["footway"] == HIGHWAY_SCORE["steps"], "precondition: tied"
    assert _governing(["footway", "steps"], rank) == _governing(["steps", "footway"], rank)


def test_implicit_speed_fill_is_order_independent():
    """End to end: the tie must not move s_speed."""
    import geopandas as gpd
    from shapely.geometry import LineString

    from routes_ssr.segment_index import build_segment_indicators

    def frame(tags):
        return gpd.GeoDataFrame(
            {"highway": [list(tags)], "maxspeed": [None]},
            geometry=[LineString([(0, 0), (100, 0)])], crs="EPSG:28992",
        )

    a = build_segment_indicators(frame(["footway", "steps"]))
    b = build_segment_indicators(frame(["steps", "footway"]))
    assert a["highway_class"].iloc[0] == b["highway_class"].iloc[0]
    assert (
        a["maxspeed_kmh"].iloc[0] == b["maxspeed_kmh"].iloc[0]
        or (np.isnan(a["maxspeed_kmh"].iloc[0]) and np.isnan(b["maxspeed_kmh"].iloc[0]))
    )
