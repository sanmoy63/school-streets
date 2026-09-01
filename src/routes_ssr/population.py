"""Residential population within school catchments, from GHS-POP.

Source
------
JRC Global Human Settlement Layer, GHS-POP R2023A, 100 m, Mollweide
(ESRI:54009). Chosen over national grids because it is the *comparable* layer:
it exists on identical terms for Rotterdam, Antwerp, Krakow and Genova, which
the CBS (NL), Statbel (BE), GUS (PL) and ISTAT (IT) products do not -- they
differ in geometry, vintage and age banding. National grids are strictly better
where they exist and belong in the enrichment layer, not here.

What this measures, and what it does not
----------------------------------------
GHS-POP is **total residents**, not children. Every number this module produces
is "people living within the catchment", and converting that to a child count
requires age structure GHS-POP does not carry. For Rotterdam the CBS 100 m grid
has age bands and would give real child counts; that is enrichment, and NL-only.
Nothing here should be reported as "children" without that step.

Resolution matters here more than usual
---------------------------------------
The walksheds are network corridors roughly 80 m wide, against a 100 m grid. A
naive zonal sum that counts cells whose *centroid* falls inside the polygon
would miss most of a corridor that narrow, and would do so unevenly -- worse for
sparse suburban networks than for dense grids, which is exactly the sort of
systematic bias this project keeps having to guard against.

So coverage is computed **fractionally**: the polygon is rasterised on a
subdivided grid aligned to the population raster, and each cell contributes its
population multiplied by the fraction of its area inside the polygon. Mollweide
is equal-area, so those fractions are true area shares.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.features import rasterize

from .config import DATA_RAW, City

log = logging.getLogger(__name__)

GHS_CRS = "ESRI:54009"

# GHSL global tiling: 1000 km tiles from this origin, in Mollweide metres.
_TILE_X0, _TILE_Y0, _TILE_SIZE = -18_041_000.0, 9_000_000.0, 1_000_000.0

_BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A"

# Subdivision factor when measuring what share of each population cell falls
# inside a catchment.
#
# The coverage fraction is quantised to 1/subsample per axis, so this directly
# bounds accuracy. At subsample=5 the sub-cells are 20 m -- the same order as
# the corridor width -- and an 80 m corridor's fraction could only land on
# multiples of 0.2, giving errors up to 100% on individual cells. That is not a
# rounding detail at this scale; it is the difference between a corridor
# counting as one cell-row of residents or two.
#
# 10 gives 10 m sub-cells and quantisation of 0.1 per axis, which sits below
# GHS-POP's own modelled-allocation uncertainty. Raising it further costs
# quadratically for accuracy the source data does not have.
SUBSAMPLE = 10


def ghs_tile_for(lon: float, lat: float) -> tuple[int, int]:
    """GHSL tile (row, col) containing a WGS84 point."""
    x, y = Transformer.from_crs("EPSG:4326", GHS_CRS, always_xy=True).transform(lon, lat)
    col = int((x - _TILE_X0) // _TILE_SIZE) + 1
    row = int((_TILE_Y0 - y) // _TILE_SIZE) + 1
    return row, col


def fetch_ghs_pop(city: City, epoch: str = "E2020", refresh: bool = False) -> Path:
    """Download and cache the GHS-POP tile covering a city. Returns the GeoTIFF path.

    One 1000 km tile is ~40 MB zipped and covers the whole of the Low Countries,
    so a single download serves several pilot cities.
    """
    # Tile is chosen from the city's own boundary centroid, in WGS84.
    from . import osm_extract

    b = osm_extract.fetch_boundary(city).to_crs(4326)
    pt = b.geometry.iloc[0].centroid
    row, col = ghs_tile_for(pt.x, pt.y)

    product = f"GHS_POP_{epoch}_GLOBE_R2023A_54009_100"
    name = f"{product}_V1_0_R{row}_C{col}"
    tif = DATA_RAW / "ghs_pop" / f"{name}.tif"

    if tif.exists() and tif.stat().st_size > 0 and not refresh:
        return tif

    tif.parent.mkdir(parents=True, exist_ok=True)
    url = f"{_BASE}/{product}/V1-0/tiles/{name}.zip"
    log.info("Downloading GHS-POP tile R%d_C%d (~40 MB) ...", row, col)

    resp = requests.get(url, timeout=600)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        members = [m for m in z.namelist() if m.lower().endswith(".tif")]
        if not members:
            raise RuntimeError(f"No GeoTIFF inside {url}")
        with z.open(members[0]) as src, tif.open("wb") as dst:
            dst.write(src.read())

    log.info("GHS-POP tile -> %s (%.0f MB)", tif.name, tif.stat().st_size / 1e6)
    return tif


def zonal_population(
    polygons: gpd.GeoDataFrame,
    raster_path: Path,
    subsample: int = SUBSAMPLE,
) -> pd.Series:
    """Population inside each polygon, area-weighted across partial cells.

    Returns a Series aligned to ``polygons.index``. A polygon falling entirely
    outside the raster yields NaN, not 0 -- an unavailable tile is not an empty
    neighbourhood.
    """
    polys = polygons.to_crs(GHS_CRS)
    out = pd.Series(np.nan, index=polys.index, dtype=float)

    with rasterio.open(raster_path) as src:
        nodata = src.nodata
        rb = src.bounds

        for idx, geom in polys.geometry.items():
            if geom is None or geom.is_empty:
                continue
            minx, miny, maxx, maxy = geom.bounds
            if maxx < rb.left or minx > rb.right or maxy < rb.bottom or miny > rb.top:
                continue  # outside the tile -> stays NaN

            # Snap OUTWARD to whole cells containing the polygon, rather than
            # rounding the float window. Rounding lengths floors a corridor
            # thinner than one cell to zero height -- which is precisely the
            # geometry this module exists for, and it raised WindowError. It
            # also shifted the window transform off the cell grid, so the
            # sub-cell fractions below were computed against a misaligned
            # raster and came out wrong even when the window was non-empty.
            r0, c0 = src.index(minx, maxy)
            r1, c1 = src.index(maxx, miny)
            row_start = max(0, min(r0, r1))
            col_start = max(0, min(c0, c1))
            row_stop = min(src.height, max(r0, r1) + 1)
            col_stop = min(src.width, max(c0, c1) + 1)
            if row_stop <= row_start or col_stop <= col_start:
                continue
            win = rasterio.windows.Window(
                col_start, row_start, col_stop - col_start, row_stop - row_start
            )

            pop = src.read(1, window=win).astype("float64")
            if nodata is not None:
                pop[pop == nodata] = 0.0
            pop[pop < 0] = 0.0  # GHS-POP uses negative fill in places

            # Rasterise the polygon `subsample` times finer, then average each
            # block back down: that block mean is the fraction of the parent
            # cell covered. Mollweide is equal-area, so it is a true area share.
            wt = rasterio.windows.transform(win, src.transform)
            fine = rasterize(
                [(geom, 1)],
                out_shape=(int(win.height) * subsample, int(win.width) * subsample),
                transform=wt @ rasterio.Affine.scale(1 / subsample, 1 / subsample),
                fill=0,
                dtype="uint8",
                all_touched=False,
            )
            frac = fine.reshape(
                int(win.height), subsample, int(win.width), subsample
            ).mean(axis=(1, 3))

            out[idx] = float((pop * frac).sum())

    return out
