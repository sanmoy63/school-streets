"""Walkshed distance conversion and the reach_ratio severance measure."""

from __future__ import annotations

import pytest

from routes_ssr.walksheds import minutes_to_metres, reach_ratio


def test_minutes_to_metres_at_child_walking_speed():
    # 3.6 km/h = 60 m/min, so 10 minutes is exactly 600 m.
    assert minutes_to_metres(10, 3.6) == pytest.approx(600.0)
    assert minutes_to_metres(5, 3.6) == pytest.approx(300.0)
    assert minutes_to_metres(15, 3.6) == pytest.approx(900.0)


def test_adult_speed_would_inflate_the_catchment():
    """Why the config uses 3.6 rather than the conventional 4.8 km/h.

    The radius grows by a third, and catchment area by ~78%, if an adult speed
    is used for a traveller who is six.
    """
    child = minutes_to_metres(10, 3.6)
    adult = minutes_to_metres(10, 4.8)
    assert adult / child == pytest.approx(4.8 / 3.6)
    assert (adult / child) ** 2 == pytest.approx(1.78, abs=0.01)


def test_reach_ratio_detects_severance(severed_graph, school_at_origin):
    """Node D sits ~51 m from the school but has no edge reaching it.

    Within a 150 m radius the circle contains A, B and D; only A and B are
    walkable. The ratio must be 2/3, not 1.0.
    """
    rr = reach_ratio(severed_graph, school_at_origin, radius_m=150.0)
    assert rr.iloc[0] == pytest.approx(2 / 3)


def test_reach_ratio_is_one_when_nothing_is_severed(severed_graph, school_at_origin):
    """Shrink the radius below D's distance and the penalty disappears."""
    rr = reach_ratio(severed_graph, school_at_origin, radius_m=40.0)
    assert rr.iloc[0] == pytest.approx(1.0)


def test_reach_ratio_bounded_in_unit_interval(severed_graph, school_at_origin):
    for radius in (30.0, 60.0, 150.0, 400.0):
        v = reach_ratio(severed_graph, school_at_origin, radius_m=radius).iloc[0]
        assert 0.0 <= v <= 1.0, f"radius {radius} gave {v}"


def test_reach_ratio_has_no_free_width_parameter():
    """The metric it replaced did.

    `network_ratio` was walkshed area over circle area, and walkshed area scales
    with the corridor half-width used to dissolve reachable segments. Sweeping
    that width 10->80 m moved the mean from 0.21 to 0.61 on the same city. This
    signature takes only a radius, which is the analysis question, not a
    drawing choice.
    """
    import inspect

    params = set(inspect.signature(reach_ratio).parameters)
    # `weight` and `budget` were added so the same measure can run on a
    # terrain-adjusted graph. Neither is a drawing choice: the assertion is that
    # no corridor/buffer/width parameter exists, not that the signature is frozen.
    assert not any(
        k in p.lower() for p in params for k in ("width", "buffer", "corridor", "half")
    ), params
    assert {"G", "schools", "radius_m"} <= params
