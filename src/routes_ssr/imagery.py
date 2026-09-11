"""Open street-level imagery integration (Mapillary API v4).

Why this module exists
----------------------
A significant share of urban streets in OpenStreetMap lack explicit `maxspeed`
tags (20.7% in Rotterdam, 90.6% in Genova) or presence-only layer verification
for traffic calming and sidewalks.

This module provides tools to:
1. Query Mapillary Graph API v4 for traffic sign detections (e.g. 30 km/h /
   50 km/h speed limit signs).
2. Spatially match detected signs to street segment geometries to infer missing
   `maxspeed_kmh`.
3. Audit imagery coverage across segments, given photo locations.

A fetch that cannot run raises `ImageryUnavailable` rather than returning an
empty frame. An empty frame reads as "no signs on these streets", and that is
how an audit that never ran came to be published as 0% coverage. KartaView is
no longer queried: its sequence endpoint gives one location per photo sequence,
not per photo, so it cannot show whether a given street was photographed.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from pyproj import Transformer

from .config import params

log = logging.getLogger(__name__)

# Mapillary traffic sign value classification mapping to km/h speed limits
MAPILLARY_SPEED_SIGNS = {
    "regulatory--maximum-speed-limit-10--g1": 10.0,
    "regulatory--maximum-speed-limit-20--g1": 20.0,
    "regulatory--maximum-speed-limit-30--g1": 30.0,
    "regulatory--maximum-speed-limit-40--g1": 40.0,
    "regulatory--maximum-speed-limit-50--g1": 50.0,
    "regulatory--maximum-speed-limit-60--g1": 60.0,
    "regulatory--maximum-speed-limit-70--g1": 70.0,
    "regulatory--maximum-speed-limit-80--g1": 80.0,
    "regulatory--zone-30-begin--g1": 30.0,
    "regulatory--living-street-begin--g1": 20.0,
}

_SPEED_PATTERN = re.compile(r"maximum-speed-limit-(\d+)")


def parse_sign_speed(value_key: str) -> float:
    """Extract speed limit km/h from Mapillary feature value key, or NaN if unrecognised."""
    if not value_key or not isinstance(value_key, str):
        return np.nan

    if value_key in MAPILLARY_SPEED_SIGNS:
        return MAPILLARY_SPEED_SIGNS[value_key]

    match = _SPEED_PATTERN.search(value_key)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    return np.nan


class ImageryUnavailable(RuntimeError):
    """An imagery query could not run, so its result would say nothing about the streets."""


def fetch_mapillary_signs(
    bbox: tuple[float, float, float, float],
    client_token: str | None = None,
) -> gpd.GeoDataFrame:
    """Query Mapillary Graph API v4 for traffic sign map features within WGS84 bbox (min_x, min_y, max_x, max_y).

    Requires a valid Mapillary client token (can be supplied or read from `MAPILLARY_CLIENT_TOKEN` env var).
    Returns a GeoDataFrame in EPSG:4326 carrying `value`, `speed_kmh`, and `geometry`.

    Raises `ImageryUnavailable` when there is no token or the request fails. An
    empty frame is returned only when the query ran and found no signs.
    """
    token = client_token or os.environ.get("MAPILLARY_CLIENT_TOKEN")
    if not token:
        raise ImageryUnavailable(
            "no Mapillary client token supplied or found in MAPILLARY_CLIENT_TOKEN"
        )

    base_url = params("imagery")["mapillary_api_base"]
    url = f"{base_url}/map_features"
    minx, miny, maxx, maxy = bbox
    bbox_str = f"{minx},{miny},{maxx},{maxy}"

    limit = 1000
    params_req = {
        "access_token": token,
        "fields": "id,value,geometry",
        "bbox": bbox_str,
        "layers": "traffic_signs",
        "limit": limit,
    }

    try:
        resp = requests.get(url, params=params_req, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        # The exception text can carry the request URL, token included, so only
        # its type and HTTP status are passed on, and the chain is cut.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        raise ImageryUnavailable(
            f"Mapillary API request failed ({type(exc).__name__}, HTTP status {status})"
        ) from None

    features = data.get("data", [])
    if len(features) >= limit:
        log.warning(
            "Mapillary returned %d features, the page limit: results are truncated "
            "and missing signs cannot be read as absent.", len(features),
        )
    if not features:
        return gpd.GeoDataFrame(columns=["value", "speed_kmh", "geometry"], crs="EPSG:4326")

    rows = []
    for feat in features:
        val = feat.get("value")
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue

        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue

        lon, lat = coords[0], coords[1]
        spd = parse_sign_speed(val)

        rows.append({
            "feature_id": feat.get("id"),
            "value": val,
            "speed_kmh": spd,
            "geometry": gpd.points_from_xy([lon], [lat])[0],
        })

    if not rows:
        return gpd.GeoDataFrame(columns=["value", "speed_kmh", "geometry"], crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    log.info("Fetched %d traffic sign features from Mapillary API", len(gdf))
    return gdf


def match_signs_to_segments(
    segments: gpd.GeoDataFrame,
    signs: gpd.GeoDataFrame,
    max_distance_m: float = 25.0,
) -> gpd.GeoDataFrame:
    """Spatially snap detected traffic speed limit signs to street segments.

    `segments` and `signs` must be in the same projected CRS.
    Returns `segments` with `inferred_maxspeed_kmh` and `sign_source` attached.
    """
    out = segments.copy()
    out["inferred_maxspeed_kmh"] = np.nan
    out["sign_source"] = np.nan

    speed_signs = signs.dropna(subset=["speed_kmh"]).copy()
    if speed_signs.empty or out.empty:
        return out

    # Ensure same CRS
    if signs.crs != segments.crs:
        speed_signs = speed_signs.to_crs(segments.crs)

    # Spatial join nearest sign within max_distance_m
    joined = gpd.sjoin_nearest(
        out[["geometry"]],
        speed_signs[["speed_kmh", "value", "geometry"]],
        how="left",
        max_distance=max_distance_m,
        distance_col="_sign_dist",
    )

    joined_unique = (
        joined.sort_values("_sign_dist")
        .groupby(level=0)
        .first()
    )

    out["inferred_maxspeed_kmh"] = joined_unique["speed_kmh"]
    out["sign_source"] = joined_unique["value"]
    return out


def infer_missing_speeds(
    segments: gpd.GeoDataFrame,
    signs: gpd.GeoDataFrame,
    max_distance_m: float = 25.0,
) -> gpd.GeoDataFrame:
    """Fill missing `maxspeed_kmh` on road segments using spatially matched sign detections."""
    out = match_signs_to_segments(segments, signs, max_distance_m=max_distance_m)

    missing_mask = out["maxspeed_kmh"].isna() & out["inferred_maxspeed_kmh"].notna()
    n_inferred = int(missing_mask.sum())

    if n_inferred > 0:
        out.loc[missing_mask, "maxspeed_kmh"] = out.loc[missing_mask, "inferred_maxspeed_kmh"]
        log.info("Inferred maxspeed for %d untagged segments from imagery sign detections", n_inferred)

    return out


def wilson_score_interval(
    successes: int,
    trials: int,
    confidence: float = 0.95,
) -> tuple[float, float, float]:
    """Calculate point estimate and Wilson score confidence interval [p, ci_lo, ci_hi].

    Wilson score interval handles extreme proportions (0 or 1) and small samples
    much better than the standard normal approximation without producing values outside [0, 1].
    """
    if trials <= 0:
        return 0.0, 0.0, 0.0

    if abs(confidence - 0.95) < 1e-3:
        z = 1.959963984540054
    elif abs(confidence - 0.90) < 1e-3:
        z = 1.6448536269514722
    elif abs(confidence - 0.99) < 1e-3:
        z = 2.5758293035489004
    else:
        from scipy.stats import norm
        z = float(norm.ppf(1.0 - (1.0 - confidence) / 2.0))

    p = successes / trials
    z2 = z * z
    denom = 1.0 + z2 / trials
    center = (p + z2 / (2.0 * trials)) / denom
    margin = (z * np.sqrt((p * (1.0 - p) + z2 / (4.0 * trials)) / trials)) / denom

    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return round(p, 4), round(lo, 4), round(hi, 4)


def stratified_sample_segments(
    segments: gpd.GeoDataFrame,
    class_col: str = "highway_class",
    n_per_class: int = 20,
    random_seed: int = 42,
) -> gpd.GeoDataFrame:
    """Sample up to n_per_class segments per highway class to prevent residential over-representation."""
    if segments.empty:
        return segments.copy()

    rng = np.random.default_rng(random_seed)
    sampled_indices = []

    for _, group in segments.groupby(class_col, observed=True):
        idx = group.index.to_numpy()
        size = min(len(idx), n_per_class)
        chosen = rng.choice(idx, size=size, replace=False)
        sampled_indices.extend(chosen)

    return segments.loc[sampled_indices].copy()


def audit_imagery_coverage(
    segments: gpd.GeoDataFrame,
    imagery_points: gpd.GeoDataFrame,
    buffer_m: float = 20.0,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Audit imagery coverage across street segments.

    Computes:
    - total_segments: total segments in analysis
    - covered_segments: segments with at least 1 imagery photo point within buffer_m
    - coverage_share: fraction of segments covered by imagery
    - coverage_ci_lo: lower Wilson confidence bound
    - coverage_ci_hi: upper Wilson confidence bound
    """
    n_total = len(segments)
    if segments.empty or imagery_points.empty:
        return {
            "total_segments": n_total,
            "covered_segments": 0,
            "coverage_share": 0.0,
            "coverage_ci_lo": 0.0,
            "coverage_ci_hi": 0.0,
        }

    points_proj = imagery_points.to_crs(segments.crs) if imagery_points.crs != segments.crs else imagery_points

    buffered = gpd.GeoDataFrame(geometry=segments.geometry.buffer(buffer_m), crs=segments.crs)
    joined = gpd.sjoin(points_proj, buffered, how="inner", predicate="intersects")
    covered_indices = set(joined["index_right"].unique())

    n_cov = len(covered_indices)
    p, lo, hi = wilson_score_interval(n_cov, n_total, confidence=confidence)

    return {
        "total_segments": n_total,
        "covered_segments": n_cov,
        "coverage_share": p,
        "coverage_ci_lo": lo,
        "coverage_ci_hi": hi,
    }
