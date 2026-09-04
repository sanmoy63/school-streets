"""Tests for open street-level imagery integration (imagery.py)."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from routes_ssr.imagery import (
    audit_imagery_coverage,
    infer_missing_speeds,
    match_signs_to_segments,
    parse_sign_speed,
)


def test_parse_sign_speed():
    assert parse_sign_speed("regulatory--maximum-speed-limit-30--g1") == 30.0
    assert parse_sign_speed("regulatory--maximum-speed-limit-50--g1") == 50.0
    assert parse_sign_speed("regulatory--zone-30-begin--g1") == 30.0
    assert parse_sign_speed("regulatory--living-street-begin--g1") == 20.0
    assert parse_sign_speed("regulatory--maximum-speed-limit-15") == 15.0
    assert np.isnan(parse_sign_speed("warning--pedestrians--g1"))
    assert np.isnan(parse_sign_speed(None))


def test_match_signs_to_segments():
    # Simple line segment in EPSG:28992
    line = LineString([(0, 0), (100, 0)])
    segments = gpd.GeoDataFrame(
        {"osmid": [1], "maxspeed_kmh": [np.nan]},
        geometry=[line],
        crs="EPSG:28992",
    )

    # Sign located 5 m away from segment
    sign_pt = Point(50, 5)
    signs = gpd.GeoDataFrame(
        {"value": ["regulatory--maximum-speed-limit-30--g1"], "speed_kmh": [30.0]},
        geometry=[sign_pt],
        crs="EPSG:28992",
    )

    matched = match_signs_to_segments(segments, signs, max_distance_m=10.0)
    assert matched.at[0, "inferred_maxspeed_kmh"] == 30.0
    assert matched.at[0, "sign_source"] == "regulatory--maximum-speed-limit-30--g1"


def test_infer_missing_speeds():
    line1 = LineString([(0, 0), (100, 0)])
    line2 = LineString([(0, 100), (100, 100)])

    segments = gpd.GeoDataFrame(
        {"osmid": [1, 2], "maxspeed_kmh": [np.nan, 50.0]},
        geometry=[line1, line2],
        crs="EPSG:28992",
    )

    # Sign near line1 only
    signs = gpd.GeoDataFrame(
        {"value": ["regulatory--maximum-speed-limit-30--g1"], "speed_kmh": [30.0]},
        geometry=[Point(50, 5)],
        crs="EPSG:28992",
    )

    inferred = infer_missing_speeds(segments, signs, max_distance_m=10.0)
    assert inferred.at[0, "maxspeed_kmh"] == 30.0
    assert inferred.at[1, "maxspeed_kmh"] == 50.0  # Kept existing tag value


def test_audit_imagery_coverage():
    line1 = LineString([(0, 0), (100, 0)])
    line2 = LineString([(0, 1000), (100, 1000)])

    segments = gpd.GeoDataFrame(
        geometry=[line1, line2],
        crs="EPSG:28992",
    )

    # Photo point near line1 only
    photos = gpd.GeoDataFrame(
        {"photo_id": ["p1"]},
        geometry=[Point(50, 2)],
        crs="EPSG:28992",
    )

    audit = audit_imagery_coverage(segments, photos, buffer_m=10.0)
    assert audit["total_segments"] == 2
    assert audit["covered_segments"] == 1
    assert audit["coverage_share"] == 0.5
    assert 0.0 < audit["coverage_ci_lo"] <= 0.5
    assert 0.5 <= audit["coverage_ci_hi"] < 1.0


def test_wilson_score_interval():
    from routes_ssr.imagery import wilson_score_interval

    # Edge case: zero trials
    p, lo, hi = wilson_score_interval(0, 0)
    assert (p, lo, hi) == (0.0, 0.0, 0.0)

    # 0 successes out of 100
    p, lo, hi = wilson_score_interval(0, 100)
    assert p == 0.0
    assert lo == 0.0
    assert hi > 0.0

    # 100 successes out of 100
    p, lo, hi = wilson_score_interval(100, 100)
    assert p == 1.0
    assert lo < 1.0
    assert hi == 1.0

    # 50 successes out of 100 -> p=0.5, symmetric interval
    p, lo, hi = wilson_score_interval(50, 100)
    assert p == 0.5
    assert 0.40 <= lo <= 0.45
    assert 0.55 <= hi <= 0.60


def test_stratified_sample_segments():
    from routes_ssr.imagery import stratified_sample_segments

    # Create dummy segments across 3 classes
    df = gpd.GeoDataFrame({
        "highway_class": ["residential"] * 50 + ["tertiary"] * 10 + ["primary"] * 3,
        "geometry": [Point(0, 0)] * 63,
    })

    sampled = stratified_sample_segments(df, class_col="highway_class", n_per_class=10, random_seed=42)
    counts = sampled["highway_class"].value_counts()
    assert counts["residential"] == 10
    assert counts["tertiary"] == 10
    assert counts["primary"] == 3  # All available when less than n_per_class

