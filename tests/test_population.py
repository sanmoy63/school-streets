"""Zonal population extraction.

The tests build a small synthetic raster rather than touching GHS-POP, so the
suite stays offline. What they check is the property that actually matters at
this resolution: partial cells must contribute in proportion to how much of them
lies inside the polygon.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from routes_ssr.population import GHS_CRS, ghs_tile_for, zonal_population

CELL = 100.0


@pytest.fixture
def uniform_raster(tmp_path):
    """10x10 grid of 100 m cells, 10 people in every cell. Total 1000.

    Origin is placed in Mollweide coordinates near the Netherlands so the
    polygons in these tests land on it after reprojection.
    """
    x0, y0 = 300_000.0, 6_080_000.0
    data = np.full((10, 10), 10.0, dtype="float32")
    path = tmp_path / "pop.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=10, width=10, count=1,
        dtype="float32", crs=GHS_CRS, transform=from_origin(x0, y0, CELL, CELL),
        nodata=-200.0,
    ) as dst:
        dst.write(data, 1)
    return path, x0, y0


def poly(x0, y0, w, h):
    return gpd.GeoDataFrame(geometry=[box(x0, y0, x0 + w, y0 + h)], crs=GHS_CRS)


def test_whole_raster_sums_to_total(uniform_raster):
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - 1000, 1000, 1000)
    assert zonal_population(g, path).iloc[0] == pytest.approx(1000.0, rel=0.01)


def test_exactly_one_cell(uniform_raster):
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - CELL, CELL, CELL)
    assert zonal_population(g, path).iloc[0] == pytest.approx(10.0, rel=0.02)


def test_half_a_cell_contributes_half_its_people(uniform_raster):
    """The property the whole module exists for.

    Walksheds are ~80 m corridors on a 100 m grid. Centroid-based masking would
    score this 0 or 10; the correct answer is 5. Run at high subsample so this
    tests the method rather than the default grid's quantisation.
    """
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - CELL, CELL / 2, CELL)
    assert zonal_population(g, path, subsample=50).iloc[0] == pytest.approx(5.0, rel=0.03)


def test_narrow_corridor_is_not_lost(uniform_raster):
    """A 20 m ribbon across five cells holds a fifth of one cell-row of people.

    Under centroid masking a corridor this narrow contributes nothing at all,
    which is the systematic undercount this implementation avoids.
    """
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - CELL / 2, 5 * CELL, 20.0)
    got = zonal_population(g, path, subsample=50).iloc[0]
    assert got == pytest.approx(5 * 10.0 * 0.2, rel=0.05)
    assert got > 0


def test_scales_linearly_with_covered_area(uniform_raster):
    path, x0, y0 = uniform_raster
    quarter = zonal_population(poly(x0, y0 - 500, 250, 1000), path, subsample=50).iloc[0]
    half = zonal_population(poly(x0, y0 - 500, 500, 1000), path, subsample=50).iloc[0]
    assert half == pytest.approx(2 * quarter, rel=0.05)


def test_polygon_outside_raster_is_nan_not_zero(uniform_raster):
    """An unavailable tile is not an empty neighbourhood.

    Returning 0 here would silently report "nobody lives near this school" for
    any catchment the raster does not cover.
    """
    path, x0, y0 = uniform_raster
    g = poly(x0 + 500_000, y0 + 500_000, 100, 100)
    assert np.isnan(zonal_population(g, path).iloc[0])


def test_nodata_is_not_counted_as_population(tmp_path):
    x0, y0 = 300_000.0, 6_080_000.0
    data = np.full((4, 4), 10.0, dtype="float32")
    data[0, :] = -200.0  # nodata row
    path = tmp_path / "nd.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=4, width=4, count=1, dtype="float32",
        crs=GHS_CRS, transform=from_origin(x0, y0, CELL, CELL), nodata=-200.0,
    ) as dst:
        dst.write(data, 1)

    g = poly(x0, y0 - 400, 400, 400)
    # 12 valid cells x 10 people; the nodata row must contribute 0, not -800.
    assert zonal_population(g, path).iloc[0] == pytest.approx(120.0, rel=0.02)


def test_result_is_aligned_to_input_index(uniform_raster):
    path, x0, y0 = uniform_raster
    g = gpd.GeoDataFrame(
        geometry=[box(x0, y0 - CELL, x0 + CELL, y0), box(x0 + CELL, y0 - CELL, x0 + 2 * CELL, y0)],
        crs=GHS_CRS,
    )
    g.index = [7, 13]
    assert list(zonal_population(g, path).index) == [7, 13]


# --- tiling ----------------------------------------------------------------


def test_rotterdam_falls_in_tile_r3_c19():
    assert ghs_tile_for(4.47917, 51.9225) == (3, 19)


@pytest.mark.parametrize(
    "lon,lat",
    [(4.47917, 51.9225), (24.6559, 60.2055), (17.1077, 48.1486), (19.8187, 41.3275)],
)
def test_all_four_pilot_cities_resolve_to_a_plausible_tile(lon, lat):
    """Rotterdam, Espoo, Bratislava, Tirana."""
    row, col = ghs_tile_for(lon, lat)
    assert 1 <= row <= 9
    assert 1 <= col <= 36


def test_default_subsample_is_fine_enough_for_corridor_geometry():
    """Guard against lowering the subdivision factor.

    Coverage is quantised to 1/subsample per axis. At 5 the sub-cells are 20 m,
    the same order as the walkshed corridor width, and individual cell fractions
    can be wrong by 100%. This is the parameter that decides whether the
    population figures mean anything at corridor scale.
    """
    from routes_ssr.population import SUBSAMPLE

    assert SUBSAMPLE >= 10


def test_coarse_subsample_visibly_degrades_accuracy(uniform_raster):
    """Documents *why* the default is what it is, rather than asserting it."""
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - CELL / 2, 5 * CELL, 20.0)
    exact = zonal_population(g, path, subsample=50).iloc[0]
    coarse = zonal_population(g, path, subsample=5).iloc[0]
    assert abs(coarse - exact) > 0.4 * exact
