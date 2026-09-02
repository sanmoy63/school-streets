"""Slope-adjusted walking speed.

Offline: the DEM is a small synthetic raster, never the remote tile.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from routes_ssr.terrain import (
    MAX_PLAUSIBLE_SLOPE,
    MIN_SPEED_KMH,
    MIN_STAIR_SLOPE,
    _tile_name,
    add_walk_time,
    sample_node_elevations,
    tobler_speed_kmh,
)

FLAT = 3.6


# --- Tobler ----------------------------------------------------------------


def test_flat_ground_returns_the_configured_child_speed():
    """The function is rescaled so level ground gives the study's own pace.

    Tobler's raw peak is 6 km/h for an adult hiker; using it unscaled would
    silently replace the 3.6 km/h child speed the rest of the project uses.
    """
    assert tobler_speed_kmh(np.array([0.0]), FLAT)[0] == pytest.approx(FLAT, rel=1e-6)


def test_uphill_is_slower_than_flat():
    up = tobler_speed_kmh(np.array([0.10]), FLAT)[0]
    assert up < FLAT


def test_steeper_uphill_is_monotonically_slower():
    s = np.array([0.0, 0.05, 0.10, 0.20, 0.35, 0.50])
    v = tobler_speed_kmh(s, FLAT)
    assert all(a >= b for a, b in zip(v, v[1:])), v


def test_speed_is_asymmetric_about_level_ground():
    """Tobler peaks at a slight downhill, not at zero.

    This is the empirical content of the function, and it is why the graph must
    be directed: climbing to school is not the trip home.
    """
    down = tobler_speed_kmh(np.array([-0.05]), FLAT)[0]
    up = tobler_speed_kmh(np.array([0.05]), FLAT)[0]
    assert down > up
    assert down > FLAT


def test_steep_descent_is_also_slow():
    """Going down a cliff is not fast either."""
    assert tobler_speed_kmh(np.array([-0.6]), FLAT)[0] < FLAT


def test_speed_never_falls_below_the_floor():
    v = tobler_speed_kmh(np.array([-5.0, 5.0, 100.0]), FLAT)
    assert (v >= MIN_SPEED_KMH).all()


# --- tiling ----------------------------------------------------------------


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (4.47917, 51.9225, "N51_00_E004_00"),   # Rotterdam
        (8.9463, 44.4056, "N44_00_E008_00"),    # Genova
        (4.4025, 51.2194, "N51_00_E004_00"),    # Antwerp
        (19.9450, 50.0647, "N50_00_E019_00"),   # Krakow
    ],
)
def test_tile_name_for_each_pilot_city(lon, lat, expected):
    assert _tile_name(lon, lat) == expected


# --- elevation sampling and edge times --------------------------------------


@pytest.fixture
def hill(tmp_path):
    """A DEM rising 100 m west-to-east, and a 3-node path graph across it.

    Node 1 sits at 0 m, node 2 at ~50 m, node 3 at ~100 m, each 500 m apart.
    """
    x0, y0, cell = 4.0, 52.0, 0.001            # ~100 m cells at this latitude
    cols = 20
    data = np.tile(np.linspace(0, 100, cols, dtype="float32"), (10, 1))
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=cols, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(x0, y0, cell, cell), nodata=-9999.0,
    ) as dst:
        dst.write(data, 1)

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    for n, lon in {1: 4.0005, 2: 4.0095, 3: 4.0185}.items():
        G.add_node(n, x=lon, y=51.9995)
    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2)]:
        G.add_edge(u, v, 0, length=500.0, highway="residential")
    return G, path


def test_elevation_is_sampled_and_increases_along_the_hill(hill):
    G, dem = hill
    elev = sample_node_elevations(G, dem)
    assert elev[1] < elev[2] < elev[3]
    assert elev[3] - elev[1] > 30


def test_uphill_edge_costs_more_time_than_the_same_edge_downhill(hill):
    """The core property: direction matters on a slope.

    A flat model gives both directions the same cost, which is what makes it
    overstate reachability in a hilly city.
    """
    G, dem = hill
    add_walk_time(G, dem, FLAT)
    up = G[1][2][0]["walk_time"]
    down = G[2][1][0]["walk_time"]
    assert up > down
    assert G[1][2][0]["slope"] > 0
    assert G[2][1][0]["slope"] < 0


def test_flat_terrain_leaves_time_at_the_flat_rate(tmp_path):
    """A flat city must be essentially unaffected.

    This is the control that makes the Genova result trustworthy: if a flat DEM
    changed the answer, the implementation would be wrong.
    """
    path = tmp_path / "flat.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(4.0, 52.0, 0.001, 0.001), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((8, 8), 5.0, dtype="float32"), 1)

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    G.add_node(1, x=4.001, y=51.999)
    G.add_node(2, x=4.004, y=51.999)
    G.add_edge(1, 2, 0, length=500.0, highway="residential")
    add_walk_time(G, path, FLAT)

    expected = 500.0 / (FLAT * 1000 / 3600)
    assert G[1][2][0]["walk_time"] == pytest.approx(expected, rel=0.02)
    assert G[1][2][0]["slope"] == pytest.approx(0.0, abs=1e-6)


def test_implausible_slopes_are_clamped(hill):
    """Guards the surface-model problem.

    GLO-30 includes buildings, so a node beside a tower can sample a rooftop and
    fabricate a cliff. Clamping keeps one bad pixel from disconnecting a street.
    """
    G, dem = hill
    G.add_edge(1, 2, 1, length=1.0, highway="residential")   # 1 m long, ~50 m rise
    summary = add_walk_time(G, dem, FLAT)
    assert summary["clamped"] >= 1
    assert abs(G[1][2][1]["slope"]) <= MAX_PLAUSIBLE_SLOPE + 1e-9


def test_stairs_get_a_minimum_gradient(tmp_path):
    """A 30 m DEM smooths a single flight of steps into almost nothing.

    Genova has 290 km of stairways; treating them as level would defeat the
    purpose of modelling terrain at all.
    """
    path = tmp_path / "flat.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=8, width=8, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(4.0, 52.0, 0.001, 0.001), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((8, 8), 5.0, dtype="float32"), 1)

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    G.add_node(1, x=4.001, y=51.999)
    G.add_node(2, x=4.004, y=51.999)
    G.add_edge(1, 2, 0, length=100.0, highway="steps")
    summary = add_walk_time(G, path, FLAT)

    assert summary["stairs_raised"] == 1
    assert abs(G[1][2][0]["slope"]) >= MIN_STAIR_SLOPE
    # And that must actually cost time.
    assert G[1][2][0]["walk_time"] > 100.0 / (FLAT * 1000 / 3600)


def test_missing_elevation_falls_back_to_flat_and_is_counted(hill):
    """A node off the DEM must not silently become an infinite climb."""
    G, dem = hill
    G.add_node(99, x=9.9, y=9.9)          # far outside the raster
    G.add_edge(3, 99, 0, length=200.0, highway="residential")
    summary = add_walk_time(G, dem, FLAT)
    assert summary["missing_elevation"] >= 1
    assert np.isfinite(G[3][99][0]["walk_time"])
