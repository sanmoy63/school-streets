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
    DEM_NODATA,
    MAX_PLAUSIBLE_SLOPE,
    MIN_SPEED_KMH,
    MIN_STAIR_SLOPE,
    _tile_name,
    add_walk_time,
    sample_node_elevations,
    tiles_for_bounds,
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


# --- DEM tile coverage -----------------------------------------------------
#
# These exist because the original code chose a single tile from the bounding
# box centre. Genova is 0.47 deg wide and straddles 9 deg E, so ~9 km of the
# eastern city fell outside the fetched DEM -- and because the Copernicus COGs
# declare no nodata, GDAL returned 0.0 there rather than an error. Eastern
# Genova was modelled as sea level, and edges crossing the cut got a fabricated
# 700 m cliff that was then silently absorbed by the slope clamp.


def test_bbox_within_one_tile_needs_only_that_tile():
    assert tiles_for_bounds(8.2, 44.2, 8.8, 44.8) == ["N44_00_E008_00"]


def test_genova_spans_two_tiles_in_longitude():
    """The regression. Centre-of-bbox tile selection returned only E008."""
    tiles = tiles_for_bounds(8.6457, 44.3585, 9.1156, 44.5398)
    assert tiles == ["N44_00_E008_00", "N44_00_E009_00"]


def test_a_bbox_crossing_a_latitude_boundary_spans_two_tiles():
    """Latitude straddles must be handled as well as longitude ones.

    Rotterdam is the near miss rather than a casualty: its boundary reaches
    51.9943, so only the 2 km pad crosses 52 deg N and no street node ever lay
    in the uncovered sliver. Its published figures are unaffected. The bounds
    here are that padded box, kept as the latitude-axis case because nothing
    else in the study exercises it.
    """
    assert tiles_for_bounds(4.26, 51.83, 4.67, 52.01) == [
        "N51_00_E004_00",
        "N52_00_E004_00",
    ]


def test_a_bbox_ending_exactly_on_a_tile_edge_does_not_pull_in_the_next_tile():
    """Tile extents are half-open; 9.0 belongs to E008, not E009."""
    assert tiles_for_bounds(8.2, 44.2, 9.0, 45.0) == ["N44_00_E008_00"]


def test_krakow_needs_four_tiles_and_wroclaw_two():
    """Both named extension candidates hit the same bug, Krakow worst of all.

    Krakow crosses 20 deg E *and* 50 deg N, so centre-of-bbox selection fetched
    one tile of the four it needs and modelled three quarters of the city as
    sea level. Running the extension cities on the old code would have produced
    terrain figures that were mostly artefact.
    """
    assert tiles_for_bounds(19.79, 49.97, 20.22, 50.13) == [
        "N49_00_E019_00",
        "N49_00_E020_00",
        "N50_00_E019_00",
        "N50_00_E020_00",
    ]
    assert tiles_for_bounds(16.80, 51.04, 17.18, 51.21) == [
        "N51_00_E016_00",
        "N51_00_E017_00",
    ]


def test_sample_outside_the_dem_is_missing_not_sea_level(tmp_path):
    """The failure that made the clipped DEM invisible.

    GDAL returns 0.0 for a sample outside a raster that declares no nodata, and
    0.0 is a finite, entirely plausible elevation for a port city. Nothing
    downstream could tell it from real ground, so the gap never showed up in
    ``missing_elevation`` -- it showed up as a cliff in the clamp count instead.
    """
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(8.0, 45.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.full((4, 4), 100.0, dtype="float32"), 1)

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    G.add_node(1, x=8.02, y=44.98)   # inside
    G.add_node(2, x=8.50, y=44.98)   # far outside, where GDAL hands back 0.0

    elev = sample_node_elevations(G, path)
    assert elev[1] == pytest.approx(100.0)
    assert np.isnan(elev[2]), "out-of-coverage node was accepted as 0 m"


def test_nodes_outside_the_dem_are_counted_as_missing(tmp_path):
    """And once they are NaN, add_walk_time reports them instead of inventing a slope."""
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(8.0, 45.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.full((4, 4), 100.0, dtype="float32"), 1)

    G = nx.MultiDiGraph()
    G.graph["crs"] = "EPSG:4326"
    G.add_node(1, x=8.02, y=44.98)
    G.add_node(2, x=8.50, y=44.98)
    G.add_edge(1, 2, 0, length=500.0, highway="residential")

    summary = add_walk_time(G, path, FLAT)
    assert summary["missing_elevation"] == 1
    assert G[1][2][0]["slope"] == 0.0
    assert summary["clamped"] == 0, "a coverage gap must not masquerade as a cliff"


def test_dem_nodata_sentinel_is_outside_any_real_elevation():
    assert DEM_NODATA < -1000.0
