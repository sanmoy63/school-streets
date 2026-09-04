"""Regressions for the three defects that made Rotterdam and Genova look
different when they were not.

The failure being guarded against, in one line: Genova's residential streets
scored 0.386 against Rotterdam's 0.607, and every point of that 0.221 gap came
from indicators one city had and the other did not. Restricting both cities to
the indicators they share closes it to exactly zero.

Three independent mistakes produced it, and each has its own section here:

  1. a presence-only layer's non-detections were scored as observed zeros,
     which asserted the layer was complete;
  2. an indicator that never varies was still averaged into its domain, where
     it moved the level without discriminating between streets;
  3. the domain score was an available-case mean, so losing an indicator
     renormalised over the survivors and shifted the score by itself.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString

from routes_ssr import harmonise
from routes_ssr.segment_index import (
    COMPLETENESS_FOR_ABSENCE,
    MAX_MODAL_SHARE,
    MIN_N_FOR_MODAL_GATE,
    PRESENCE_ONLY,
    composite_index,
    indicator_bounds,
    modal_share,
    score_presence_only,
)

CRS = "EPSG:28992"


# A real network is a mix of classes, and `s_highway` earns its place by varying
# across them. Building a test frame from one class alone makes s_highway
# constant, which the informativeness gate then removes -- correctly, but it
# tests the gate rather than the thing under test.
CLASS_CYCLE = ["residential", "unclassified", "tertiary", "secondary"]
CLASS_SCORE = {"residential": 0.75, "unclassified": 0.60, "tertiary": 0.40,
               "secondary": 0.20}


def roads(n: int, one_class: bool = False, **cols) -> gpd.GeoDataFrame:
    """`n` motor-traffic segments with the given indicator columns.

    `s_highway` defaults to the class score, so it varies unless the caller
    overrides it. Every class used carries motor traffic, so the traffic
    indicators are applicable on all of them.
    """
    classes = (
        ["residential"] * n
        if one_class
        else [CLASS_CYCLE[i % len(CLASS_CYCLE)] for i in range(n)]
    )
    data = {"highway_class": classes}
    for key, value in cols.items():
        data[key] = value if isinstance(value, (list, np.ndarray)) else [value] * n
    data.setdefault("s_highway", [CLASS_SCORE[c] for c in classes])
    geoms = [LineString([(i, 0), (i + 1, 0)]) for i in range(n)]
    return gpd.GeoDataFrame(data, geometry=geoms, crs=CRS)


# --- 1. presence-only layers ------------------------------------------------


def test_non_detection_is_unobserved_not_zero():
    """The core type error. A traffic-calming layer reports features that exist
    and is silent everywhere else, so "no bump within 15 m" and "nobody has
    surveyed this street" are the same observation. Scoring the first as 0.0
    asserts the layer is complete -- which is what let `s_calming` report 100%
    coverage in both cities while carrying a 27.6x gap between them.
    """
    detected = pd.Series([True, False, False, False])
    out = score_presence_only(detected, completeness=None, name="s_calming")

    assert out.tolist()[0] == 1.0
    assert out.isna().sum() == 3, "non-detections must be missing, never zero"
    assert not (out == 0.0).any()


def test_validated_layer_may_score_absence():
    """A non-detection becomes evidence once the layer's detection rate is
    known. That is the only thing that licenses it, and it has to be asserted
    deliberately rather than assumed by default."""
    detected = pd.Series([True, False, False, False])
    out = score_presence_only(
        detected, completeness=COMPLETENESS_FOR_ABSENCE, name="s_calming"
    )
    assert out.tolist() == [1.0, 0.0, 0.0, 0.0]


def test_completeness_below_threshold_is_not_enough():
    out = score_presence_only(
        pd.Series([True, False]),
        completeness=COMPLETENESS_FOR_ABSENCE - 0.01,
        name="s_calming",
    )
    assert out.isna().sum() == 1


def test_calming_is_typed_presence_only():
    """If this ever flips back to two-sided, the 27.6x artefact returns."""
    assert "s_calming" in PRESENCE_ONLY


def test_stale_files_are_detected():
    """A presence-only indicator can only be 1.0 or missing. An observed 0.0
    means the file predates this fix, and every number from it is the artefact.
    """
    good = roads(3, s_calming=[1.0, np.nan, np.nan])
    bad = roads(3, s_calming=[1.0, 0.0, np.nan])
    assert harmonise.stale_presence_only({"a": good}) == []
    assert harmonise.stale_presence_only({"a": good, "b": bad}) == ["b"]


# --- 2. the informativeness gate --------------------------------------------


def test_modal_share_measures_pile_up():
    assert modal_share(pd.Series([1.0] * 99 + [0.0])) == pytest.approx(0.99)
    assert modal_share(pd.Series([np.nan, np.nan])) == 1.0, "no data, no discrimination"


def test_near_constant_indicator_is_excluded(caplog):
    """`s_calming` sits on one value for 99.4% of observed segments in Genova
    against 82.9% in Rotterdam. An indicator that cannot tell two streets apart
    within a city has no business setting the level of a comparison between
    cities."""
    n = 1000
    calming = [1.0] * 996 + [0.0] * 4          # 99.6% modal -- above the cap
    speed = [0.85, 0.5] * (n // 2)             # varies -- must survive
    df = roads(n, s_speed=speed, s_calming=calming)

    with caplog.at_level(logging.WARNING, logger="routes_ssr.segment_index"):
        out = composite_index(df)

    assert any(
        "indicator s_calming excluded from traffic_safety" in r.message
        for r in caplog.records
    )
    # The domain is the row-wise mean of the two survivors; s_calming, which
    # would have pulled the level up on 99.6% of segments, is gone entirely.
    expected = np.mean([(sp + hw) / 2 for sp, hw in zip(speed, df["s_highway"])])
    assert out["d_traffic_safety"].mean() == pytest.approx(expected)


def test_gate_does_not_fire_on_a_small_sample():
    """A modal share over a handful of observations is noise. Gating on noise
    would drop sound indicators in small study areas, so the gate has a floor.
    """
    n = MIN_N_FOR_MODAL_GATE - 1
    df = roads(n, one_class=True, s_speed=0.85, s_highway=0.75, s_calming=1.0)
    out = composite_index(df)
    # All three constant, but too few to judge: all three still contribute.
    assert out["d_traffic_safety"].mean() == pytest.approx(np.mean([0.85, 0.75, 1.0]))


def test_varying_indicator_survives_the_gate():
    """The gate must not cry wolf on an indicator that does its job."""
    n = 400
    speed = ([0.85] * 3 + [0.2]) * (n // 4)     # 75% modal -- under the cap
    assert modal_share(pd.Series(speed)) < MAX_MODAL_SHARE
    df = roads(n, s_speed=speed)
    out = composite_index(df)
    assert out["d_traffic_safety"].nunique() > 1


# --- 3. the available-case mean, and the identified set ---------------------


def test_point_estimate_lies_inside_the_identified_set():
    n = 200
    df = roads(n, s_speed=[0.85] * 20 + [np.nan] * (n - 20))
    out = composite_index(df)
    inside = (out["ssr_index"] >= out["ssr_index_lo"] - 1e-9) & (
        out["ssr_index"] <= out["ssr_index_hi"] + 1e-9
    )
    assert inside.all()


def test_interval_collapses_when_everything_is_observed():
    df = roads(200, s_speed=[0.85, 0.2] * 100)
    out = composite_index(df)
    width = (out["ssr_index_hi"] - out["ssr_index_lo"]).mean()
    assert width == pytest.approx(0.0, abs=1e-9)


def test_interval_widens_with_missing_evidence():
    """Genova's residential index sits inside an interval 100x wider than
    Rotterdam's, because 92.8% of its roads carry no speed limit. The width is
    the honest part of the answer."""
    def width(df):
        out = composite_index(df)
        return float((out["ssr_index_hi"] - out["ssr_index_lo"]).mean())

    # Rotterdam: maxspeed on ~79% of roads. Genova: ~9%. The indicator is live
    # in both -- partially observed, not absent -- so in both the unobserved
    # segments contribute a bound rather than dropping out.
    seen = roads(200, s_speed=[0.85, 0.2] * 79 + [np.nan] * 42)
    scarce = roads(200, s_speed=[0.85, 0.2] * 9 + [np.nan] * 182)

    assert width(scarce) > width(seen)
    assert width(scarce) > 0.2

    # An indicator observed *nowhere* is a different case: it leaves the
    # construct entirely rather than widening it, and `coverage` is what records
    # that loss. Bounds describe the index that was built, not the one intended.
    absent = roads(200, s_speed=[np.nan] * 200)
    assert width(absent) == pytest.approx(0.0, abs=1e-9)
    assert composite_index(absent)["coverage"].max() < 1.0


def test_speed_bounds_use_the_legal_band_on_small_streets():
    """An untagged Italian or Dutch residential street is legally 50 km/h and
    may be a signed 30 zone, so its score is bounded, not unknown. A trunk road
    may be posted well above 50 and gets no such comfort."""
    df = gpd.GeoDataFrame(
        {"highway_class": ["residential", "trunk"], "s_speed": [np.nan, np.nan]},
        geometry=[LineString([(0, 0), (1, 0)]), LineString([(2, 0), (3, 0)])],
        crs=CRS,
    )
    lo, hi = indicator_bounds(df, "s_speed")
    assert (lo.iloc[0], hi.iloc[0]) == (0.20, 0.85)
    assert (lo.iloc[1], hi.iloc[1]) == (0.0, 1.0)


def test_shared_indicator_set_removes_the_artefact_gap():
    """The regression that names this file.

    Two cities with identical streets -- every segment residential, every
    highway score 0.75, every observed speed the same 0.85. The only difference
    is that one city tagged `maxspeed` and the other did not.

    Indexed on what each city happens to have, the available-case mean
    renormalises over the survivors and invents a gap. Indexed on the shared
    set, there is none, because there never was one.
    """
    n = 400
    speed = [0.85, 0.5] * (n // 2)
    # The one difference: Rotterdam tags maxspeed on ~79% of roads, Genova on
    # ~9%. Same streets, same scores where either city looked.
    rich = roads(n, s_speed=speed[:316] + [np.nan] * 84)
    poor = roads(n, s_speed=speed[:36] + [np.nan] * 364)

    free_gap = (
        composite_index(rich)["ssr_index"].mean()
        - composite_index(poor)["ssr_index"].mean()
    )
    assert free_gap > 0.05, "the artefact this project exists to catch"

    shared = {"s_highway"}
    tied_gap = (
        composite_index(rich, indicators=shared)["ssr_index"].mean()
        - composite_index(poor, indicators=shared)["ssr_index"].mean()
    )
    assert tied_gap == pytest.approx(0.0)


def test_restriction_excludes_named_indicators():
    df = roads(200, s_speed=[0.85, 0.2] * 100)
    out = composite_index(df, indicators={"s_highway"})
    assert sorted(out["d_traffic_safety"].unique()) == sorted(CLASS_SCORE.values())


# --- the comparable set -----------------------------------------------------


def coverage_rows(**cities) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"city": city, "indicator": ind, "share_length": share}
            for city, inds in cities.items()
            for ind, share in inds.items()
        ]
    )


def test_comparable_set_is_bound_by_the_weakest_city():
    cov = coverage_rows(
        rotterdam={"s_highway": 1.0, "s_speed": 0.82, "s_sidewalk": 0.15},
        genova={"s_highway": 1.0, "s_speed": 0.11, "s_sidewalk": 0.18},
    )
    keep, dropped = harmonise.comparable_indicators(cov)

    assert keep == {"s_highway"}
    assert "genova" in dropped["s_speed"], "must name where the constraint binds"
    assert "rotterdam" in dropped["s_sidewalk"]


def test_divergence_flag_bars_an_indicator_from_the_comparison():
    """Full coverage in both cities is not enough. An indicator that is observed
    everywhere and still disagrees by 27x is measuring survey effort."""
    cov = coverage_rows(
        rotterdam={"s_highway": 1.0, "s_calming": 1.0},
        genova={"s_highway": 1.0, "s_calming": 1.0},
    )
    div = pd.DataFrame(
        {"ratio": [27.6, 1.03], "suspect": [True, False]},
        index=pd.Index(["s_calming", "s_highway"], name="indicator"),
    )

    keep, dropped = harmonise.comparable_indicators(cov, div)
    assert keep == {"s_highway"}
    assert "divergence" in dropped["s_calming"]

    # A flagged indicator is barred from the comparison, not deleted from the
    # project: within one city it still compares like with like.
    kept, _ = harmonise.comparable_indicators(cov, div, exclude_suspect=False)
    assert kept == {"s_highway", "s_calming"}


def test_domain_columns_are_not_treated_as_inputs():
    cov = coverage_rows(
        rotterdam={"s_highway": 1.0, "d_traffic_safety": 1.0},
        genova={"s_highway": 1.0, "d_traffic_safety": 1.0},
    )
    keep, _ = harmonise.comparable_indicators(cov)
    assert keep == {"s_highway"}


# --- loader consistency -----------------------------------------------------


def test_city_set_mismatch_is_detected():
    """The comparable set is derived from the coverage reports and then applied
    to the segment files. Those are two separate globs, and nothing made them
    agree.

    A city with segments but no coverage report never gets a vote on what is
    comparable, so the set is computed as though it were absent and applied to
    it anyway. In this repo, losing Genova's coverage CSV widens the set from
    {s_highway} to {s_highway, s_speed} -- an indicator observed on 9.4% of
    Genova's roads -- which is the artefact walking back in through a missing
    file.
    """
    cov = coverage_rows(
        rotterdam={"s_highway": 1.0, "s_speed": 0.82},
        genova={"s_highway": 1.0, "s_speed": 0.11},
    )
    frames = {"rotterdam": roads(4), "genova": roads(4)}

    assert harmonise.city_set_mismatch(cov, frames) == {
        "missing_coverage": set(),
        "missing_segments": set(),
    }

    # Genova's coverage report has gone missing.
    thin = cov[cov["city"] != "genova"]
    assert harmonise.city_set_mismatch(thin, frames)["missing_coverage"] == {"genova"}

    # ...and this is why that matters.
    assert harmonise.comparable_indicators(cov)[0] == {"s_highway"}
    assert harmonise.comparable_indicators(thin)[0] == {"s_highway", "s_speed"}


def test_coverage_without_segments_is_detected():
    cov = coverage_rows(rotterdam={"s_highway": 1.0}, genova={"s_highway": 1.0})
    frames = {"rotterdam": roads(4)}
    assert harmonise.city_set_mismatch(cov, frames)["missing_segments"] == {"genova"}


# ---------------------------------------------------------------------------
# 4. A comparison can separate cleanly and still compare nothing
#
# When harmonisation drops every genuinely observed indicator -- s_speed is
# tagged on 79.98% of Rotterdam's roads and 9.35% of Genova's -- the comparable
# set collapses to s_highway alone. That indicator is a lookup on the OSM
# highway tag: present everywhere so no coverage gate excludes it, never
# unobserved so the identified set is degenerate, identical per class in every
# city so it cannot diverge.
#
# The committed comparative_claims.csv shows the consequence: Genova 0.789
# against Rotterdam 0.811, margin 0.0224, clearing both SEPARATION_EPS and the
# 0.01 negligible-margin gate, recorded as supported. Measured on real data the
# within-class term is exactly 0.00000 and composition is -0.06263: the whole
# difference is road-class mix.
#
# The detector is a decomposition, not a list of indicator names. A name list
# has to be maintained by hand and flags on what an indicator is called; the
# decomposition flags on what it does, and catches a future proxy nobody
# thought to add.
# ---------------------------------------------------------------------------


def _script(name):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _summary(score_g, score_r, n_g, n_r, aggregate_gap=0.0225):
    """Two cities over two classes, with per-class scores and shares given."""
    rows = []
    for city, sc, n in (("genova", score_g, n_g), ("rotterdam", score_r, n_r)):
        agg = sum(s * c for s, c in zip(sc, n)) / sum(n)
        rows.append({"city": city, "class": "all (excl. service)",
                     "n": sum(n), "index_": agg, "lo": agg, "hi": agg,
                     "indicators": "x"})
        for cls, s, c in zip(("residential", "primary"), sc, n):
            rows.append({"city": city, "class": cls, "n": c, "index_": s,
                         "lo": s, "hi": s, "indicators": "x"})
    return pd.DataFrame(rows)


def test_identical_per_class_scores_decompose_to_pure_composition():
    """The regression: same scores, different mix -> gap is entirely composition."""
    mod = _script("02b_comparative_index")
    summary = _summary(score_g=(0.75, 0.05), score_r=(0.75, 0.05),
                       n_g=(4893, 806), n_r=(6617, 141))
    within, comp = mod.decompose_gap(summary, "genova", "rotterdam",
                                     "all (excl. service)")
    assert within == pytest.approx(0.0, abs=1e-12), "nothing measured differs"
    assert abs(comp) > 0.01, "and yet the aggregate gap is real"


def test_a_real_within_class_difference_is_not_composition():
    """A measured difference on comparable streets must not be flagged."""
    mod = _script("02b_comparative_index")
    summary = _summary(score_g=(0.60, 0.05), score_r=(0.75, 0.05),
                       n_g=(5000, 500), n_r=(5000, 500))
    within, comp = mod.decompose_gap(summary, "genova", "rotterdam",
                                     "all (excl. service)")
    assert within < -0.01, "the within-class term must carry the difference"
    assert comp == pytest.approx(0.0, abs=1e-12), "shares are identical here"


def test_claims_flags_a_composition_only_comparison_that_separates():
    mod = _script("02b_comparative_index")
    summary = _summary(score_g=(0.75, 0.05), score_r=(0.75, 0.05),
                       n_g=(4893, 806), n_r=(6617, 141))
    row = mod.claims(summary).query("`class` == 'all (excl. service)'").iloc[0]
    assert row["supported"], "degenerate intervals do separate"
    assert not row["negligible"], "the margin clears 0.01"
    assert row["composition_only"], "and yet it compares only road-class mix"
    assert row["composition_share"] == pytest.approx(1.0)


def test_claims_does_not_flag_a_measured_difference():
    mod = _script("02b_comparative_index")
    summary = _summary(score_g=(0.60, 0.05), score_r=(0.75, 0.05),
                       n_g=(5000, 500), n_r=(5000, 500))
    row = mod.claims(summary).query("`class` == 'all (excl. service)'").iloc[0]
    assert not row["composition_only"]
    assert row["composition_share"] == pytest.approx(0.0)


def test_decomposition_is_symmetric_in_city_order():
    """The split must not depend on which city is called `a`."""
    mod = _script("02b_comparative_index")
    summary = _summary(score_g=(0.60, 0.05), score_r=(0.75, 0.20),
                       n_g=(4000, 900), n_r=(6000, 200))
    w1, c1 = mod.decompose_gap(summary, "genova", "rotterdam", "all (excl. service)")
    w2, c2 = mod.decompose_gap(summary, "rotterdam", "genova", "all (excl. service)")
    assert w1 == pytest.approx(-w2)
    assert c1 == pytest.approx(-c2)
