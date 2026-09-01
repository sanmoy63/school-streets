"""Speed-limit parsing and scoring."""

from __future__ import annotations

import math

import numpy as np
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


def test_takes_first_element_of_list_tags():
    # OSM tags arrive as lists when OSMnx merges several ways into one segment.
    assert parse_maxspeed(["30", "50"]) == 30.0
    assert np.isnan(parse_maxspeed([]))


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
