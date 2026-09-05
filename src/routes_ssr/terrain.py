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

# The Copernicus COGs declare no nodata value at all, so GDAL returns 0.0 --
# a finite, plausible sea-level elevation -- for any sample outside coverage.
# Nothing downstream could distinguish that from real ground. The cached DEM is
# therefore written with an explicit sentinel, and gaps become NaN rather than
# a fabricated coastline.
DEM_NODATA = -32768.0

# How a merged edge carrying several highway tags is classified for the stair
# rule.
#
# OSMnx is called with simplify=True, so a chain of OSM ways becomes one graph
# edge and its `highway` attribute becomes a list -- 5,826 of Genova's 101,904
# edges, 3,656 of them containing "steps". The previous code read `highway[0]`,
# and Overpass does not guarantee the order ways come back in: refetching the
# same graph returned 5,131 of those 5,826 lists reordered, same contents, so
# `['footway','steps']` and `['steps','footway']` alternate between runs.
#
# The consequence was not jitter. Because the ordering is consistent within a
# response, each run landed near one of two specifications: 3,202 stairways
# raised and reach_ratio 0.3103, or 6,444 and 0.2959. Three runs of unchanged
# code gave 5,295, 6,396 and 3,280. A 0.014 spread in the headline figure, all
# of it specification and none of it data.
#
# True by default, meaning an edge counts as stairs if any of its component
# ways is. It is the conservative direction for this study: the question is
# whether a child can walk the link, an edge containing a flight of stairs
# contains a flight of stairs, and under-counting stairways in the city whose
# defining feature is 290 km of them is the worse error. It does over-penalise
# a long footway carrying a short flight, because simplification does not
# retain the component lengths needed to weight it. Setting this False scores
# only edges whose every component is steps and moves Genova's reach_ratio at
# 10 minutes from 0.296 to 0.310.
STAIRS_IF_ANY_COMPONENT = True


def is_steps(highway) -> bool:
    """Whether an edge should be treated as stairs, deterministically.

    Order-independent by construction, which `highway[0]` was not.
    """
    if highway is None:
        return False
    parts = list(highway) if isinstance(highway, (list, tuple)) else [highway]
    if not parts:
        return False
    return (
        "steps" in parts if STAIRS_IF_ANY_COMPONENT
        else all(p == "steps" for p in parts)
    )


def _dem_path(city: City) -> Path:
    return DATA_RAW / "dem" / f"{city.key}_dem.tif"


def _tile_name(lon: float, lat: float) -> str:
    """Copernicus 1-degree tile identifier containing a point."""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{ns}{abs(int(np.floor(lat))):02d}_00_{ew}{abs(int(np.floor(lon))):03d}_00"


def tiles_for_bounds(w: float, s: float, e: float, n: float) -> list[str]:
    """Every 1-degree tile the bounding box touches.

    Choosing the tile at the box's centre is only correct for a city that fits
    inside one degree square. Genova does not: padded, it spans 8.646 to 9.116,
    so the centre rule picks E008 and drops roughly 9 km of the eastern city --
    Quarto, Quinto and Nervi, which climb steeply off the coast. Krakow (crosses
    20 deg) and Wroclaw (crosses 17 deg) are affected the same way.

    The half-open convention matters: a box ending exactly on 9.0 lies wholly
    within E008 and must not pull in E009, so the eastern and northern edges are
    nudged inward before flooring.
    """
    eps = 1e-9
    lon_lo, lon_hi = int(np.floor(w)), int(np.floor(max(e - eps, w)))
    lat_lo, lat_hi = int(np.floor(s)), int(np.floor(max(n - eps, s)))
    return [
        _tile_name(lon + 0.5, lat + 0.5)
        for lat in range(lat_lo, lat_hi + 1)
        for lon in range(lon_lo, lon_hi + 1)
    ]


def _tile_url(tile: str) -> str:
    return (f"/vsicurl/{_BUCKET}/Copernicus_DSM_COG_10_{tile}_DEM/"
            f"Copernicus_DSM_COG_10_{tile}_DEM.tif")


def fetch_dem(city: City, refresh: bool = False) -> Path:
    """Download the city's bounding box from the remote DEM and cache it.

    Only the window covering the city is read, via HTTP range requests against
    the Cloud Optimized GeoTIFF, so this costs a couple of megabytes rather than
    a full tile.
    """
    import rasterio
    from rasterio.merge import merge

    path = _dem_path(city)
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path

    from . import osm_extract

    bounds = osm_extract.fetch_boundary(city).to_crs(4326).total_bounds
    w, s, e, n = bounds
    pad = 0.02  # ~2 km, so walksheds reaching past the boundary still sample
    w, s, e, n = w - pad, s - pad, e + pad, n + pad

    tiles = tiles_for_bounds(w, s, e, n)

    os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")

    log.info("Reading DEM window for %s from %d tile(s): %s",
             city.key, len(tiles), ", ".join(tiles))
    path.parent.mkdir(parents=True, exist_ok=True)

    # A tile that is entirely ocean is not published at all, so a missing tile
    # is expected rather than an error for a coastal city. It is logged, and the
    # gap it leaves is filled with the nodata sentinel -- not with zero, which
    # would read downstream as land at sea level.
    srcs, missing = [], []
    for tile in tiles:
        try:
            srcs.append(rasterio.open(_tile_url(tile)))
        except rasterio.errors.RasterioIOError:
            missing.append(tile)
    if missing:
        log.warning("DEM tile(s) unavailable (ocean-only, or not published): %s",
                    ", ".join(missing))
    if not srcs:
        raise RuntimeError(
            f"No Copernicus DEM tile available for {city.key} over bounds "
            f"{(w, s, e, n)}; tried {tiles}"
        )

    try:
        # `bounds` clips the mosaic to the city window, so this still costs a
        # couple of megabytes of range requests rather than a full tile each.
        arr, transform = merge(srcs, bounds=(w, s, e, n), nodata=DEM_NODATA)
        profile = srcs[0].profile.copy()
    finally:
        for src in srcs:
            src.close()

    band = arr[0]
    profile.update(
        driver="GTiff",
        height=band.shape[0],
        width=band.shape[1],
        count=1,
        transform=transform,
        nodata=DEM_NODATA,
        compress="deflate",
    )
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(band, 1)

    real = band[band != DEM_NODATA]
    gaps = int((band == DEM_NODATA).sum())
    if gaps:
        # The outermost row/column of the mosaic is a partial cell of the
        # requested window and can land just outside every source tile, which
        # costs one pixel at the edge of the 2 km pad -- well outside the city
        # and harmless. Reporting that as a warning on every run would train the
        # reader to ignore the message, which is how the missing half of the
        # Genova DEM survived unnoticed in the first place. A real gap -- a
        # tile that failed to open, or a city extending past published
        # coverage -- is orders of magnitude larger than a single edge pixel.
        share = 100 * gaps / band.size
        log.log(
            logging.WARNING if share > 0.5 else logging.INFO,
            "DEM for %s has %d cell(s) with no coverage (%.2f%%)",
            city.key, gaps, share,
        )
    log.info(
        "DEM -> %s (%.1f MB, %.0f-%.0f m, %d tile(s))",
        path.name, path.stat().st_size / 1e6,
        float(real.min()) if real.size else float("nan"),
        float(real.max()) if real.size else float("nan"),
        len(srcs),
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
        pts = np.column_stack([lon, lat])
        vals = np.fromiter(
            (v[0] for v in src.sample(pts)),
            dtype=float, count=len(nodes),
        )
        nodata = src.nodata
        b = src.bounds

    if nodata is not None:
        vals[vals == nodata] = np.nan

    # Belt and braces. GDAL returns 0.0 -- finite, and indistinguishable from
    # real sea-level ground -- for a sample outside the raster when no nodata
    # value is declared, and the Copernicus COGs declare none. Masking on the
    # sentinel alone would still let a point beyond the edge through as 0 m, so
    # anything outside the bounds is marked missing explicitly.
    outside = (
        (pts[:, 0] < b.left) | (pts[:, 0] > b.right)
        | (pts[:, 1] < b.bottom) | (pts[:, 1] > b.top)
    )
    vals[outside] = np.nan
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

        if is_steps(data.get("highway")) and abs(slope) < MIN_STAIR_SLOPE:
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
