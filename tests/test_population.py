"""Zonal population extraction.

The tests build a small synthetic raster rather than touching GHS-POP, so the
suite stays offline. What they check is the property that actually matters at
this resolution: partial cells must contribute in proportion to how much of them
lies inside the polygon.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
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
    [(4.47917, 51.9225), (8.9463, 44.4056), (4.4025, 51.2194), (19.9450, 50.0647)],
)
def test_study_and_candidate_cities_resolve_to_a_plausible_tile(lon, lat):
    """Rotterdam and Genova, plus Antwerp and Krakow as extension candidates."""
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
    """Documents *why* the default is what it is, rather than asserting it.

    The ribbon is 10 m, not 20 m. At subsample=5 the sub-cells are exactly
    20 m, so a 20 m ribbon is quantised to precisely one sub-row -- a coverage
    of 1/5, which is the exact answer. That made the coarse result accidentally
    perfect and the assertion depended on which side of the sub-cell boundary
    GDAL happened to round, so it passed or failed with the GDAL build rather
    than with the code. A 10 m ribbon cannot be represented on a 20 m grid at
    all: coverage quantises to 0 or 1/5 against a true 1/10, so the error is
    at least 100% or 67% whatever the alignment.
    """
    path, x0, y0 = uniform_raster
    g = poly(x0, y0 - CELL / 2, 5 * CELL, 10.0)
    exact = zonal_population(g, path, subsample=50).iloc[0]
    coarse = zonal_population(g, path, subsample=5).iloc[0]
    assert abs(coarse - exact) > 0.4 * exact


# --- the free parameter reach_ratio does not have, and this measure does -----
#
# `test_reach_ratio_has_no_free_width_parameter` in test_walksheds.py pins that
# the node-based severance measure takes only a radius. The population-weighted
# measure is not in that position: it is built from corridors buffered at
# CORRIDOR_HALF_WIDTH_M, and although numerator and denominator share the width
# so it partly cancels, measurement says it does not cancel out. Sweeping
# 10-80 m moves the mean by 56.0/24.8/16.5% of its default-width value in Genova
# and 27.9/11.6/6.4% in Rotterdam at 5/10/15 minutes -- most at the short
# thresholds, where the buffer is the largest share of a short corridor.
#
# The first version of this test asserted that `corridors` has a `half_width`
# parameter. That passes on an implementation that accepts the argument and
# never reads it, so it could not catch the regression it named. These exercise
# the behaviour instead.


def _corridors_module():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "pop_script",
        Path(__file__).resolve().parents[1] / "scripts" / "05_population.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _line_graph():
    """Three nodes on a 200 m straight line, with edge geometry."""
    import networkx as nx
    from shapely.geometry import LineString

    G = nx.MultiDiGraph()
    G.graph["crs"] = GHS_CRS
    xs = {1: 300_000.0, 2: 300_100.0, 3: 300_200.0}
    for n, x in xs.items():
        G.add_node(n, x=x, y=6_079_000.0)
    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2)]:
        G.add_edge(u, v, 0, length=100.0,
                   geometry=LineString([(xs[u], 6_079_000.0), (xs[v], 6_079_000.0)]))
    edges = gpd.GeoDataFrame(
        geometry=[LineString([(xs[1], 6_079_000.0), (xs[3], 6_079_000.0)])],
        crs=GHS_CRS,
    )
    schools = gpd.GeoDataFrame(
        {"school_id": ["t_0001"], "name": ["T"]},
        geometry=gpd.points_from_xy([xs[1]], [6_079_000.0]), crs=GHS_CRS,
    )
    nodes = pd.Series([1], index=schools.index, dtype="Int64")
    return G, edges, schools, nodes


def test_corridor_area_actually_responds_to_half_width():
    """The behaviour the signature test only gestured at.

    A wider buffer must produce a larger corridor. If this fails, `half_width`
    is being accepted and ignored -- which is exactly the shape of bug the old
    signature-only assertion would have passed straight through.
    """
    mod = _corridors_module()
    G, edges, schools, nodes = _line_graph()

    reach_20, circle_20 = mod.corridors(G, edges, schools, nodes, 250.0, 20.0)
    reach_40, circle_40 = mod.corridors(G, edges, schools, nodes, 250.0, 40.0)

    assert reach_20[0] is not None and reach_40[0] is not None
    assert reach_40[0].area > reach_20[0].area * 1.5, (
        "doubling the half-width barely moved the corridor; half_width may be "
        "accepted and unused"
    )
    assert circle_40[0].area > circle_20[0].area


def test_the_two_corridors_do_not_scale_identically():
    """Why the width does not simply cancel.

    Numerator and denominator share the half-width, which is why the ratio is
    far more stable than the area-based measure it replaced. It is not exact:
    the reachable corridor and the straight-line corridor are different shapes,
    so they do not scale by the same factor.
    """
    mod = _corridors_module()
    G, edges, schools, nodes = _line_graph()

    r20, c20 = mod.corridors(G, edges, schools, nodes, 120.0, 20.0)
    r40, c40 = mod.corridors(G, edges, schools, nodes, 120.0, 40.0)

    assert r20[0] is not None and c20[0] is not None
    ratio_reach = r40[0].area / r20[0].area
    ratio_circle = c40[0].area / c20[0].area
    assert ratio_reach != pytest.approx(ratio_circle, rel=1e-6), (
        "if both scaled identically the width would cancel exactly, and the "
        "measured 4.6-16.9% drift across a 10-80 m sweep could not occur"
    )


def test_reach_ratio_still_has_no_width_parameter():
    """Guards the contrast rather than the measure -- if this ever fails, the
    node measure has acquired the defect the population one already carries."""
    import inspect

    from routes_ssr.walksheds import reach_ratio

    assert not any(
        k in p.lower()
        for p in inspect.signature(reach_ratio).parameters
        for k in ("width", "buffer", "corridor", "half")
    )
