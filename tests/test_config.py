"""Configuration integrity.

These are cheap and catch the class of error that only shows up minutes into a
download: a bad CRS, a malformed regex, weights that do not sum.
"""

from __future__ import annotations

import pytest
from pyproj import CRS

from routes_ssr.config import cities, get_city, params
from routes_ssr.segment_index import DOMAIN_INDICATORS

PILOTS = {"rotterdam", "espoo", "bratislava", "tirana"}


def test_all_four_pilot_cities_are_configured():
    assert set(cities()) == PILOTS


def test_observer_cities_are_separate_from_pilots():
    allc = cities(include_observers=True)
    assert PILOTS <= set(allc)
    assert {c.key for c in allc.values() if c.status == "observer"}


@pytest.mark.parametrize("key", sorted(PILOTS))
def test_each_city_has_a_projected_metric_crs(key):
    """Every metric operation happens in this CRS.

    A geographic CRS here would silently compute buffers and lengths in degrees,
    which is the kind of error that produces plausible-looking wrong numbers
    rather than a crash.
    """
    city = get_city(key)
    crs = CRS.from_user_input(city.crs)
    assert crs.is_projected, f"{key} uses {city.crs}, which is not projected"
    axis = crs.axis_info[0]
    assert axis.unit_name in {"metre", "meter"}, f"{key} axis unit is {axis.unit_name}"


def test_domain_weights_cover_every_domain():
    weights = params("segment_index")["domain_weights"]
    assert set(weights) == set(DOMAIN_INDICATORS)


def test_domain_weights_sum_to_one():
    weights = params("segment_index")["domain_weights"]
    assert sum(weights.values()) == pytest.approx(1.0)


def test_walkshed_thresholds_are_ascending():
    minutes = params("walkshed")["minutes"]
    assert minutes == sorted(minutes)
    assert all(m > 0 for m in minutes)


def test_walking_speed_is_a_child_pace():
    """Guard against someone 'fixing' this to the adult 4.8 km/h default."""
    speed = params("walkshed")["walk_speed_kmh"]
    assert 2.5 <= speed <= 4.0, f"{speed} km/h is not a child's walking pace"


def test_unknown_city_raises_with_a_helpful_message():
    with pytest.raises(KeyError, match="Known cities"):
        get_city("atlantis")
