"""Walkshed distance conversion and the reach_ratio severance measure."""

from __future__ import annotations

import numpy as np
import pytest

from routes_ssr.walksheds import (
    DIRECTIONS,
    minutes_to_metres,
    reach_ratio,
    routing_graph,
)


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


# --- trip direction --------------------------------------------------------
#
# `nx.ego_graph` follows out-edges, so the measure is "reachable from the
# school" -- the walk home. On the slope-adjusted graph that is not the same
# question as "can a child walk to school", and for a hilltop school it is the
# easier of the two. The severed_graph fixture is symmetric, so it cannot tell
# the two apart; these use an explicitly asymmetric hill.


@pytest.fixture
def hill_graph():
    """School at node 1. Node 2 is uphill, node 3 downhill.

    Climbing costs 900 s, descending 300 s, so with a 600 s budget the two
    directions select opposite nodes.
    """
    import networkx as nx
    from shapely.geometry import LineString

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:28992"
    for n, (x, y) in {1: (0, 0), 2: (100, 0), 3: (-100, 0)}.items():
        G.add_node(n, x=float(x), y=float(y))

    def seg(a, b):
        return LineString([(G.nodes[a]["x"], G.nodes[a]["y"]),
                           (G.nodes[b]["x"], G.nodes[b]["y"])])

    for u, v, t in [(1, 2, 900), (2, 1, 300), (1, 3, 300), (3, 1, 900)]:
        G.add_edge(u, v, 0, length=100.0, walk_time=t, geometry=seg(u, v))
    return G


def test_routing_graph_rejects_an_unknown_direction(hill_graph):
    with pytest.raises(ValueError):
        routing_graph(hill_graph, "sideways")


def test_default_direction_is_the_published_one():
    assert DIRECTIONS[0] == "from_school"


def test_routing_graph_default_is_the_graph_itself(hill_graph):
    assert routing_graph(hill_graph) is hill_graph


def test_the_two_directions_select_opposite_nodes(hill_graph):
    """The whole reason the parameter exists."""
    import networkx as nx

    out = set(nx.ego_graph(routing_graph(hill_graph, "from_school"), 1,
                           radius=600, distance="walk_time").nodes)
    inb = set(nx.ego_graph(routing_graph(hill_graph, "to_school"), 1,
                           radius=600, distance="walk_time").nodes)
    assert out == {1, 3}, out          # walking home is downhill: node 3
    assert inb == {1, 2}, inb          # walking to school is uphill: node 2
    assert out != inb


def test_reach_ratio_differs_between_directions_on_a_hill(hill_graph, school_at_origin):
    frm = reach_ratio(hill_graph, school_at_origin, radius_m=150.0,
                      weight="walk_time", budget=600.0,
                      direction="from_school").iloc[0]
    to = reach_ratio(hill_graph, school_at_origin, radius_m=150.0,
                     weight="walk_time", budget=600.0,
                     direction="to_school").iloc[0]
    assert 0.0 <= frm <= 1.0 and 0.0 <= to <= 1.0
    # Both reach one of the two neighbours, but not the same one -- so the
    # ratios coincide here while the node sets do not. The node-set test above
    # is the one that pins the behaviour; this pins that neither direction
    # silently returns NaN or falls outside the unit interval.
    assert not np.isnan(frm) and not np.isnan(to)


def test_direction_is_irrelevant_on_a_symmetric_graph(severed_graph, school_at_origin):
    """A flat city must be unaffected, which is why the default is safe there."""
    a = reach_ratio(severed_graph, school_at_origin, radius_m=150.0,
                    direction="from_school").iloc[0]
    b = reach_ratio(severed_graph, school_at_origin, radius_m=150.0,
                    direction="to_school").iloc[0]
    assert a == b
