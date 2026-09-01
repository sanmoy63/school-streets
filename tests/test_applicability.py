"""Applicability masks -- 'not applicable' must stay distinct from 'not observed'.

Conflating the two produced errors in both directions at once, and these are the
regression tests for each of them.
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString

from routes_ssr.segment_index import (
    CAR_FREE_CLASSES,
    HIGHWAY_SCORE,
    applicability,
)
from routes_ssr.sidewalks import ROAD_CLASSES

CRS = "EPSG:28992"


def frame(classes):
    return gpd.GeoDataFrame(
        {"highway_class": classes},
        geometry=[LineString([(0, 0), (10, 0)])] * len(classes),
        crs=CRS,
    )


def test_traffic_indicators_not_applicable_on_car_free_ways():
    """Regression: steps scored 0.074 and bridleway 0.000 -- below trunk at 0.104.

    Their traffic domain had collapsed to `s_calming = 0` alone, because a
    footway was treated as *lacking* traffic calming rather than as a place
    where the question does not arise.
    """
    df = frame(["footway", "steps", "bridleway", "residential", "trunk"])
    app = applicability(df)

    assert app["s_calming"].tolist() == [False, False, False, True, True]
    assert app["s_speed"].tolist() == [False, False, False, True, True]


def test_road_class_applies_everywhere():
    df = frame(["footway", "residential", "trunk", "steps"])
    assert applicability(df)["s_highway"].all()


def test_sidewalk_question_only_applies_to_roads():
    """Regression: footways self-scored 1.0 for sidewalk provision.

    True but tautological -- it inflated the domain's apparent coverage from
    0.19 to 0.54 and very nearly carried a useless domain through the gate.
    """
    df = frame(["footway", "path", "residential", "secondary"])
    assert applicability(df)["s_sidewalk"].tolist() == [False, False, True, True]


def test_every_indicator_has_a_mask():
    from routes_ssr.segment_index import DOMAIN_INDICATORS

    df = frame(["residential"])
    app = applicability(df)
    for cols in DOMAIN_INDICATORS.values():
        for col in cols:
            assert col in app, f"{col} has no applicability mask"


def test_masks_align_to_frame_index():
    """Regression: masks inherited the name 'highway_class' from .isin().

    A bare pd.concat of them produced unlabelled columns, and DataFrame.where()
    aligns by label -- silently blanking every value. The guard is that each
    mask is indexed like the frame it came from.
    """
    df = frame(["residential", "footway"])
    df.index = [17, 42]
    for name, mask in applicability(df).items():
        assert list(mask.index) == [17, 42], f"{name} lost the frame index"


def test_car_free_classes_are_all_scored():
    """Every car-free class must have a HIGHWAY_SCORE entry.

    An unlisted key leaves s_highway NaN, and since traffic indicators are
    inapplicable on these ways, that removes their only remaining evidence.
    """
    missing = CAR_FREE_CLASSES - set(HIGHWAY_SCORE)
    assert not missing, f"car-free classes with no score: {sorted(missing)}"


def test_road_classes_are_all_scored():
    missing = ROAD_CLASSES - set(HIGHWAY_SCORE)
    assert not missing, f"road classes with no score: {sorted(missing)}"


def test_car_free_and_road_classes_are_disjoint():
    overlap = CAR_FREE_CLASSES & ROAD_CLASSES
    assert not overlap, f"a class cannot be both car-free and a road: {overlap}"


@pytest.mark.parametrize("cls", sorted(CAR_FREE_CLASSES))
def test_car_free_ways_score_at_least_as_well_as_any_road(cls):
    """No car-free way may score below the safest motor road.

    This is the invariant the fall-through bug violated.
    """
    worst_car_free = HIGHWAY_SCORE[cls]
    best_road = max(HIGHWAY_SCORE[c] for c in ROAD_CLASSES)
    assert worst_car_free >= best_road * 0.9, (
        f"{cls} scores {worst_car_free}, implausible against best road {best_road}"
    )
