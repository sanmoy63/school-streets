"""Shared fixtures.

Everything here is synthetic and metric (EPSG:28992, metres). No test touches
the network, Overpass, or the data/ tree -- the suite must run offline in CI.
"""

from __future__ import annotations

import geopandas as gpd
import networkx as nx
import numpy as np
import pytest
from shapely.geometry import LineString, Point

CRS = "EPSG:28992"


def line(x0, y0, x1, y1) -> LineString:
    return LineString([(x0, y0), (x1, y1)])


def edge_frame(rows: list[dict]) -> gpd.GeoDataFrame:
    """Build a minimal edges GeoDataFrame.

    Each row needs `highway_class` and `geometry`; any indicator columns given
    are passed through unchanged.
    """
    geoms = [r.pop("geometry") for r in rows]
    return gpd.GeoDataFrame(rows, geometry=geoms, crs=CRS)


@pytest.fixture
def straight_road_with_two_sidewalks() -> gpd.GeoDataFrame:
    """A 100 m residential street with a parallel footway on each side."""
    return edge_frame([
        {"highway_class": "residential", "geometry": line(0, 0, 100, 0)},
        {"highway_class": "footway", "geometry": line(0, 8, 100, 8)},
        {"highway_class": "footway", "geometry": line(0, -8, 100, -8)},
    ])


@pytest.fixture
def straight_road_one_sidewalk() -> gpd.GeoDataFrame:
    return edge_frame([
        {"highway_class": "residential", "geometry": line(0, 0, 100, 0)},
        {"highway_class": "footway", "geometry": line(0, 8, 100, 8)},
    ])


@pytest.fixture
def road_with_distant_footway() -> gpd.GeoDataFrame:
    """A road, and a footway 500 m away that is nothing to do with it."""
    return edge_frame([
        {"highway_class": "residential", "geometry": line(0, 0, 100, 0)},
        {"highway_class": "footway", "geometry": line(0, 500, 100, 500)},
    ])


@pytest.fixture
def road_with_crossing_footway() -> gpd.GeoDataFrame:
    """A road crossed perpendicularly by a footway -- not a sidewalk."""
    return edge_frame([
        {"highway_class": "residential", "geometry": line(0, 0, 100, 0)},
        {"highway_class": "footway", "geometry": line(50, -30, 50, 30)},
    ])


@pytest.fixture
def severed_graph():
    """Graph where one node is close as the crow flies but unreachable.

        1(0,0) --100m-- 2(100,0) --100m-- 3(200,0)
        Node 4 at (50,10) is isolated: ~51 m from the school, but no edge
        reaches it.

    This is the situation `reach_ratio` exists to detect: a barrier makes
    something that looks adjacent impossible to walk to.
    """
    # Integer node ids, as OSMnx always produces: snap_schools casts them to
    # a nullable Int64 column, which string ids would break.
    G = nx.MultiDiGraph()
    G.graph["crs"] = CRS
    for n, (x, y) in {1: (0, 0), 2: (100, 0), 3: (200, 0), 4: (50, 10)}.items():
        G.add_node(n, x=float(x), y=float(y))
    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2)]:
        G.add_edge(u, v, 0, length=100.0, geometry=line(*_xy(G, u), *_xy(G, v)))
    return G


def _xy(G, n):
    return G.nodes[n]["x"], G.nodes[n]["y"]


@pytest.fixture
def school_at_origin() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"school_id": ["t_0000"], "name": ["Test School"]},
        geometry=[Point(0, 0)],
        crs=CRS,
    )


@pytest.fixture
def indicator_frame() -> gpd.GeoDataFrame:
    """Six segments with hand-set indicator values, for composite tests.

    Three roads (traffic indicators apply, sidewalk applies) and three car-free
    ways (traffic indicators do NOT apply, sidewalk does NOT apply).
    """
    rows = []
    for cls in ["residential", "secondary", "trunk"]:
        rows.append({
            "highway_class": cls, "geometry": line(0, 0, 10, 0),
            "s_speed": 0.85, "s_highway": 0.5, "s_calming": 0.0,
            "s_sidewalk": np.nan, "s_lit": np.nan,
            "s_green": np.nan, "s_enclosure": np.nan,
        })
    for cls in ["footway", "steps", "path"]:
        rows.append({
            "highway_class": cls, "geometry": line(0, 0, 10, 0),
            "s_speed": np.nan, "s_highway": 1.0, "s_calming": np.nan,
            "s_sidewalk": np.nan, "s_lit": np.nan,
            "s_green": np.nan, "s_enclosure": np.nan,
        })
    return edge_frame(rows)
