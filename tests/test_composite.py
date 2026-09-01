"""Composite index: domain gating, coverage accounting, degeneracy guard."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from routes_ssr.segment_index import (
    MIN_DOMAIN_COVERAGE,
    composite_index,
)


def test_traffic_domain_scored_and_environment_excluded(indicator_frame):
    out = composite_index(indicator_frame)
    assert out.attrs["domains_used"] == ["traffic_safety"]
    assert "environment" in out.attrs["domains_dropped"]
    assert out.attrs["domains_dropped"]["environment"] == 0.0


def test_car_free_ways_score_on_road_class_alone(indicator_frame):
    """A footway's only applicable traffic indicator is its class -- and that is
    a complete answer, not a degraded one. It should score 1.0, not be dropped
    for having 'only one indicator'."""
    out = composite_index(indicator_frame)
    car_free = out["highway_class"].isin(["footway", "steps", "path"])
    assert out.loc[car_free, "ssr_index"].notna().all()
    assert out.loc[car_free, "ssr_index"].tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_roads_combine_all_three_traffic_indicators(indicator_frame):
    out = composite_index(indicator_frame)
    roads = out["highway_class"].isin(["residential", "secondary", "trunk"])
    # mean(0.85, 0.5, 0.0) = 0.45
    assert out.loc[roads, "ssr_index"].tolist() == pytest.approx([0.45] * 3)


def test_sparse_domain_is_excluded_not_partially_used(indicator_frame):
    """Regression: a domain present on a minority of segments must not
    contribute where it happens to exist.

    Doing so yields an index that means different things on different segments,
    and if the missingness correlates with street type -- as sidewalk data does
    -- the mixture is biased by street type.
    """
    df = indicator_frame.copy()
    # Sidewalk evidence on exactly one of three applicable roads (33% < 60%).
    df.loc[0, "s_sidewalk"] = 1.0
    out = composite_index(df)

    assert "walking_infrastructure" in out.attrs["domains_dropped"]
    cov = out.attrs["domains_dropped"]["walking_infrastructure"]
    assert cov < MIN_DOMAIN_COVERAGE
    # The segment with sidewalk evidence must not score differently from its peers.
    roads = out["highway_class"].isin(["residential", "secondary", "trunk"])
    assert out.loc[roads, "ssr_index"].nunique() == 1


def test_dense_domain_is_included(indicator_frame):
    df = indicator_frame.copy()
    df.loc[[0, 1, 2], "s_sidewalk"] = 1.0   # all three applicable roads
    out = composite_index(df)
    assert "walking_infrastructure" in out.attrs["domains_used"]


def test_coverage_is_share_of_declared_weight_not_of_used_weight(indicator_frame):
    """`coverage` must report how much of the *intended* construct was measured.

    Renormalising over surviving domains would make it read 1.0 and hide that
    two thirds of the design is missing.
    """
    out = composite_index(indicator_frame)
    # Only traffic_safety (weight 0.40) survives, of 1.00 declared.
    assert out["coverage"].max() == pytest.approx(0.40)


def test_raises_when_no_domain_clears_the_gate(indicator_frame):
    df = indicator_frame.copy()
    for col in ["s_speed", "s_highway", "s_calming", "s_sidewalk", "s_lit"]:
        df[col] = np.nan
    with pytest.raises(ValueError, match="nothing to index"):
        composite_index(df)


# --- degeneracy guard ------------------------------------------------------


def test_degeneracy_guard_fires_on_constant_index(indicator_frame, caplog):
    """Regression for the original failure: an index of mean 0.822, p10 0.822,
    p90 0.822 -- zero variance, and entirely respectable-looking in a table.

    The check that caught it is trivial, so it runs on every build.
    """
    df = indicator_frame.copy()
    df["s_speed"] = 0.85
    df["s_highway"] = 0.85
    df["s_calming"] = 0.85
    with caplog.at_level(logging.ERROR, logger="routes_ssr.segment_index"):
        composite_index(df)
    assert any("DEGENERATE INDEX" in r.message for r in caplog.records)


def test_degeneracy_guard_silent_on_a_varying_index(indicator_frame, caplog):
    """The guard must not cry wolf on an index that genuinely discriminates.

    It fires on fewer than five distinct values, so the fixture is given a
    spread of speed scores rather than the two-value default.
    """
    df = indicator_frame.loc[indicator_frame.index.repeat(3)].reset_index(drop=True)
    roads = df["highway_class"].isin(["residential", "secondary", "trunk"])
    df.loc[roads, "s_speed"] = [0.0, 0.2, 0.5, 0.85, 1.0, 0.2, 0.5, 0.85, 1.0][: roads.sum()]

    with caplog.at_level(logging.ERROR, logger="routes_ssr.segment_index"):
        out = composite_index(df)
    assert out["ssr_index"].nunique() >= 5
    assert not any("DEGENERATE INDEX" in r.message for r in caplog.records)


def test_constant_indicator_is_reported(indicator_frame, caplog):
    """An indicator that never varies adds level, not discrimination."""
    df = indicator_frame.copy()
    df = df.loc[df.index.repeat(40)].reset_index(drop=True)
    df["s_calming"] = 0.0
    with caplog.at_level(logging.WARNING, logger="routes_ssr.segment_index"):
        composite_index(df)
    assert any("is constant at" in r.message for r in caplog.records)
