"""Slope-adjusted walking speed from a digital elevation model.

Why this module exists
----------------------
The walkshed model originally applied one flat walking speed to every segment.
That is defensible in Rotterdam, whose elevation range across the whole city is
-17 m to +33 m. It is not defensible in Genova, which runs from sea level to
997 m inside the built-up area and carries 290 km of stairways -- 4.7% of its
pedestrian network, against Rotterdam's 0.9%.

Routing a 600 m path at 3.6 km/h regardless of whether it climbs 200 m
overstates what a child can reach, and overstates it *most* in the steep
neighbourhoods where the question matters.

Source, and why not EU-DEM
--------------------------
Copernicus DEM GLO-30, read from the public AWS mirror. EU-DEM v1.1 is finer
(25 m against 30 m) but its tiles are 4.8 GB each and the two cities fall in
different tiles. Since slope here is rise over *segment* length, and OSM
segments run 20-100 m, 30 m sampling is already finer than the variation that
matters; 25 m does not buy anything worth ~10 GB.

The tiles are Cloud Optimized GeoTIFFs, so only the city's bounding box is
fetched over HTTP range requests -- 1.6 MB for Rotterdam, 2.6 MB for Genova,
rather than 33 MB per full tile.

Two assumptions worth naming
----------------------------
**Tobler's hiking function is calibrated for hikers on open terrain**, not for
children on urban staircases. It is the standard slope-to-speed relation and is
far better than assuming flat ground, but its exact coefficients are not
validated for this population. It is used here rescaled so that flat ground
returns the child walking speed declared in config, which preserves the
*shape* of the relation while anchoring its level to this study.

**GLO-30 is a surface model, not a terrain model.** It includes buildings and
vegetation. A street node beside a tall building can sample a rooftop, which
would fabricate a cliff. Slopes are therefore clamped to a plausible walking
range, and the clamp rate is reported so the reader can see how often it fires.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from .config import DATA_RAW, City

log = logging.getLogger(__name__)

# Copernicus DEM GLO-30, public AWS mirror. No credentials required.
_BUCKET = "https://copernicus-dem-30m.s3.amazonaws.com"

# Tobler's hiking function: W = 6 * exp(-3.5 * |S + 0.05|) km/h, where S is
# rise over run. The peak sits at S = -0.05, i.e. a slight downhill, which is
# the empirical result the function encodes.
_TOBLER_PEAK_KMH = 6.0
_TOBLER_DECAY = 3.5
_TOBLER_OFFSET = 0.05

# Gradients beyond this are treated as surface-model artefacts rather than
# walkable ground. 0.5 is a 1-in-2 slope; a street that steep is exceptional and
# a *segment average* that steep is almost always a building edge in the DSM.
MAX_PLAUSIBLE_SLOPE = 0.5

# Stairs are steep by construction, and a 30 m DEM smooths a single flight into
# near-nothing. Where the sampled gradient for a step way is gentler than this,
# it is raised to it. 0.30 is a shallow-but-real staircase; the true figure for
# Genova's civic stairways is often steeper.
MIN_STAIR_SLOPE = 0.30

# Floor on walking speed, so an extreme segment cannot produce an unbounded
# traversal time and silently disconnect the graph.
MIN_SPEED_KMH = 0.4


def _dem_path(city: City) -> Path:
    return DATA_RAW / "dem" / f"{city.key}_dem.tif"


def _tile_name(lon: float, lat: float) -> str:
    """Copernicus 1-degree tile identifier containing a point."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{ns}{abs(int(np.floor(lat))):02d}_00_{ew}{abs(int(np.floor(lon))):03d}_00"


def fetch_dem(city: City, refresh: bool = False) -> Path:
    """Download the city's bounding box from the remote DEM and cache it.

    Only the window covering the city is read, via HTTP range requests against
    the Cloud Optimized GeoTIFF, so this costs a couple of megabytes rather than
    a full tile.
    """
    import rasterio
    from rasterio.windows import from_bounds

    path = _dem_path(city)
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path

    from . import osm_extract

    bounds = osm_extract.fetch_boundary(city).to_crs(4326).total_bounds
    w, s, e, n = bounds
    pad = 0.02  # ~2 km, so walksheds reaching past the boundary still sample
    w, s, e, n = w - pad, s - pad, e + pad, n + pad

    tile = _tile_name((w + e) / 2, (s + n) / 2)
    url = (f"/vsicurl/{_BUCKET}/Copernicus_DSM_COG_10_{tile}_DEM/"
           f"Copernicus_DSM_COG_10_{tile}_DEM.tif")

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

    log.info("Reading DEM window for %s from tile %s", city.key, tile)
    path.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(url) as src:
        win = from_bounds(w, s, e, n, src.transform)
        arr = src.read(1, window=win)
        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            height=arr.shape[0],
            width=arr.shape[1],
            transform=rasterio.windows.transform(win, src.transform),
            compress="deflate",
        )
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(arr, 1)

    log.info(
        "DEM -> %s (%.1f MB, %.0f-%.0f m)",
        path.name, path.stat().st_size / 1e6, float(np.nanmin(arr)), float(np.nanmax(arr)),
    )
    return path


def sample_node_elevations(G, dem_path: Path) -> dict:
    """Elevation in metres for every graph node, keyed by node id."""
    import rasterio

    nodes = list(G.nodes)
    with rasterio.open(dem_path) as src:
        # Graph is in the city's projected CRS; the DEM is WGS84.
        from pyproj import Transformer

        tf = Transformer.from_crs(G.graph["crs"], src.crs, always_xy=True)
        xs = np.fromiter((G.nodes[n]["x"] for n in nodes), dtype=float, count=len(nodes))
        ys = np.fromiter((G.nodes[n]["y"] for n in nodes), dtype=float, count=len(nodes))
        lon, lat = tf.transform(xs, ys)
        vals = np.fromiter(
            (v[0] for v in src.sample(np.column_stack([lon, lat]))),
            dtype=float, count=len(nodes),
        )
        nodata = src.nodata
    if nodata is not None:
        vals[vals == nodata] = np.nan
    return dict(zip(nodes, vals))


def tobler_speed_kmh(slope: np.ndarray, flat_speed_kmh: float) -> np.ndarray:
    """Walking speed for a signed gradient, anchored to a flat-ground speed.

    Tobler's function is rescaled so that ``slope == 0`` returns
    ``flat_speed_kmh``. This keeps the empirical shape -- including its
    asymmetry, since the peak is at a slight *downhill* -- while setting the
    level from this study's own child walking pace rather than a hiker's.
    """
    raw = _TOBLER_PEAK_KMH * np.exp(-_TOBLER_DECAY * np.abs(slope + _TOBLER_OFFSET))
    flat_raw = _TOBLER_PEAK_KMH * np.exp(-_TOBLER_DECAY * _TOBLER_OFFSET)
    return np.maximum(raw * (flat_speed_kmh / flat_raw), MIN_SPEED_KMH)


def add_walk_time(G, dem_path: Path, flat_speed_kmh: float) -> dict:
    """Attach a ``walk_time`` (seconds) attribute to every directed edge.

    Returns a summary dict for reporting. Because the graph is directed, the
    same physical street carries a different time in each direction -- which is
    the point: climbing to a school is not the same trip as walking home.
    """
    elev = sample_node_elevations(G, dem_path)

    n_clamped = n_stairs = n_missing = 0
    slopes = []

    for u, v, k, data in G.edges(keys=True, data=True):
        length = float(data.get("length", 0.0) or 0.0)
        zu, zv = elev.get(u, np.nan), elev.get(v, np.nan)

        if length <= 0 or not np.isfinite(zu) or not np.isfinite(zv):
            # No usable geometry or elevation: fall back to flat, and count it.
            n_missing += 1
            slope = 0.0
        else:
            slope = (zv - zu) / length
            if abs(slope) > MAX_PLAUSIBLE_SLOPE:
                slope = float(np.sign(slope) * MAX_PLAUSIBLE_SLOPE)
                n_clamped += 1

        highway = data.get("highway")
        if isinstance(highway, (list, tuple)):
            highway = highway[0] if highway else None
        if highway == "steps" and abs(slope) < MIN_STAIR_SLOPE:
            slope = float(np.sign(slope) if slope else 1.0) * MIN_STAIR_SLOPE
            n_stairs += 1

        speed = float(tobler_speed_kmh(np.array([slope]), flat_speed_kmh)[0])
        data["slope"] = slope
        data["walk_time"] = length / (speed * 1000.0 / 3600.0) if length > 0 else 0.0
        slopes.append(slope)

    arr = np.asarray(slopes)
    summary = {
        "edges": len(arr),
        "mean_abs_slope": float(np.mean(np.abs(arr))),
        "p90_abs_slope": float(np.percentile(np.abs(arr), 90)),
        "clamped": n_clamped,
        "stairs_raised": n_stairs,
        "missing_elevation": n_missing,
    }
    log.info(
        "slope: mean |S| %.3f, p90 %.3f | clamped %d (%.1f%%), stairs raised %d, "
        "no elevation %d",
        summary["mean_abs_slope"], summary["p90_abs_slope"],
        n_clamped, 100 * n_clamped / max(len(arr), 1), n_stairs, n_missing,
    )
    return summary
