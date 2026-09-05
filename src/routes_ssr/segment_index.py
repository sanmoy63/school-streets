"""Street-segment indicators and the composite school-street readiness index.

The unit of analysis is the OSM way segment, because that is the unit at which a
school street intervention is actually implemented -- a municipality closes a
segment, not a grid cell or a neighbourhood.

Three domains are built, then combined:

  traffic_safety        -- what a car can do here
  walking_infrastructure-- what a pedestrian is given here
  environment           -- what the street is like to be in

Each indicator is scored to [0, 1] where 1 is *better for a walking child*.
Missing data is propagated as NaN and reported, never imputed to zero: an
untagged sidewalk is overwhelmingly an unsurveyed sidewalk rather than an
absent one, and scoring absence as zero would manufacture a cross-city
gradient that is really a mapping-effort gradient.
"""

from __future__ import annotations

import logging
import re

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import params
from .sidewalks import ROAD_CLASSES, sidewalk_provision

log = logging.getLogger(__name__)

# A calming feature within this distance of a segment is taken to calm it.
# 15 m is about the width of an urban carriageway plus verge: close enough that
# the bump is on this street rather than the one behind it.
CALMING_SNAP_M = 15.0

# Highway classes ordered by how hostile they are to an unaccompanied child.
# Scores are judgement calls grounded in the school-street literature's usual
# split between "streets children may cross" and "streets they may not".
HIGHWAY_SCORE = {
    # Car-free. These were originally absent from this table, which left
    # `s_highway` NaN and reduced their traffic-safety domain to `s_calming`
    # alone -- scoring steps and bridleways at 0.00-0.07, i.e. as more hostile
    # to a child than a trunk road (0.10). An unlisted key must never degrade
    # silently to a confident low score.
    "pedestrian": 1.00,
    "footway": 1.00,
    "steps": 1.00,
    "corridor": 1.00,
    "elevator": 1.00,
    "path": 0.95,
    "bridleway": 0.95,
    # Motor traffic, ascending hostility.
    "living_street": 1.00,
    "track": 0.85,
    "residential": 0.75,
    "service": 0.70,
    "unclassified": 0.60,
    "tertiary": 0.40,
    "tertiary_link": 0.40,
    "busway": 0.35,        # no general traffic, but large vehicles
    "secondary": 0.20,
    "secondary_link": 0.20,
    "primary": 0.05,
    "primary_link": 0.05,
    "trunk": 0.00,
    "trunk_link": 0.00,
}

# Ways no motor vehicle can enter. Traffic-related indicators are *not
# applicable* here -- which is different from missing. A footway does not "lack
# traffic calming"; the question does not arise. Scoring the absence as 0.0
# capped every car-free way at mean(1.0, 1.0, 0.0) = 0.667 and systematically
# understated the safest infrastructure in the network by a third.
CAR_FREE_CLASSES = {
    "footway", "path", "pedestrian", "steps", "bridleway", "corridor", "elevator",
}

# What a *non-observation* means for each indicator. The distinction decides
# whether "we found nothing here" is evidence of anything.
#
#   two_sided      the source states presence and absence with comparable
#                  reliability. `maxspeed=30` and `maxspeed=50` are both
#                  positive statements; a highway class is always present.
#
#   presence_only  the source records features that exist and is silent
#                  everywhere else. A mapped speed bump is real. An unmapped
#                  street is not a street without speed bumps -- it is a street
#                  nobody has surveyed.
#
# Scoring a presence-only non-detection as 0.0 asserts the layer is complete.
# That assertion is what let `s_calming` report as 100% observed in both cities
# and clear the coverage gate while carrying a 27.6x cross-city gap that is a
# fact about contributors rather than about streets: Rotterdam has 3,806 mapped
# calming features, Genova 73. It is the missing-is-not-zero error this module
# already fights, displaced one level up -- from the segment to the layer.
#
# A presence-only indicator yields a LOWER BOUND on prevalence, never a point
# estimate, unless its detection rate has been established externally. See
# `indicator_completeness` in config/cities.yml.
INDICATOR_KIND = {
    "s_highway": "two_sided",
    "s_speed": "two_sided",
    "s_calming": "presence_only",
    # `sidewalk=no` is a real negative, but where the tag is absent provision is
    # inferred from parallel footway geometry, and an absent footway may simply
    # be unmapped -- so s_sidewalk is two-sided in its tag branch and
    # presence-only in its geometric one. It is left two_sided here because
    # retyping it changes no published number (it is already under the coverage
    # gate in both cities, 0.15 / 0.18) and the split deserves its own evidence
    # rather than being folded in silently.
    "s_sidewalk": "two_sided",
    "s_lit": "two_sided",
    "s_green": "two_sided",
    "s_enclosure": "two_sided",
}

PRESENCE_ONLY = frozenset(k for k, v in INDICATOR_KIND.items() if v == "presence_only")

# Why a cross-city difference can separate cleanly and mean nothing.
#
# `s_highway` is `HIGHWAY_SCORE[highway_class]` -- a lookup on the OSM tag that
# already defines the class. It has three properties that let it pass every
# quality gate here while carrying no evidence: present on 100% of segments, so
# no coverage threshold excludes it; never unobserved, so it never widens an
# identified set; identical per class in every city, so it cannot diverge.
#
# When harmonisation drops every genuinely observed indicator -- `s_speed` is
# tagged on 79.98% of Rotterdam's roads and 9.35% of Genova's -- the comparable
# set collapses to this alone, and the cross-city index then compares nothing
# but the two cities' road-class mix.
#
# The test for that is NOT a list of indicator names. Naming them means
# maintaining an allowlist by hand and flagging on what an indicator is called
# rather than on what it does. The property is measurable: decompose a
# between-city difference into the part from differing scores WITHIN a class and
# the part from differing class SHARES. Where the within-class term is zero,
# nothing that was measured differs, and the whole gap is composition -- however
# the surviving indicators happen to be named. See `decompose_gap`.

# A presence-only layer's non-detections may be read as real zeros only once its
# detection rate has been estimated at or above this level. 0.90 is where the
# Rotterdam/Genova traffic-safety intervals first separate under the bounds
# computed in `composite_index`: below it, the distinction the comparison rests
# on is not identified, so treating absence as observed cannot be justified.
COMPLETENESS_FOR_ABSENCE = 0.90

SIDEWALK_SCORE = {
    "both": 1.00,
    "left": 0.55,
    "right": 0.55,
    "yes": 0.80,
    "separate": 0.80,  # mapped as its own way; presence is confirmed
    "no": 0.00,
    "none": 0.00,
}

_MPH = re.compile(r"([\d.]+)\s*mph", re.I)
_KMH = re.compile(r"^\s*([\d.]+)\s*(?:km/?h)?\s*$", re.I)

# OSM implicit speed codes. Only the urban defaults relevant to our four
# countries are listed; anything else falls through to NaN rather than being
# guessed at.
IMPLICIT_SPEED = {
    # Netherlands
    "NL:urban": 50, "NL:zone30": 30, "NL:zone20": 20,
    # Belgium -- Flanders (BE-VLG) is tagged separately from the federal code.
    "BE:urban": 50, "BE-VLG:urban": 50, "BE-BRU:urban": 30, "BE:zone30": 30,
    # Poland
    "PL:urban": 50, "PL:zone30": 30,
    # Italy
    "IT:urban": 50, "IT:rural": 90,
    # Cross-national
    "walk": 7, "living_street": 20,
}


def _components(value) -> list:
    """The component tag values of a segment, as a list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None]
    return [value]


def _governing(value, favourability):
    """The least favourable component of a merged segment.

    OSM tags arrive as scalars, or as lists when simplification merges several
    ways into one segment -- 3,982 of Rotterdam's segments and 5,826 of
    Genova's, every one of them carrying more than one distinct value.

    This used to read ``value[0]``, and Overpass does not fix the order ways
    come back in: refetching Genova's graph returned 5,131 of 5,826 lists
    reordered with identical contents. That silently reclassified 1,265 Genova
    segments and 552 Rotterdam segments from one run to the next, on individual
    edges by as much as the full 0..1 range of the score.

    The rule is that the least favourable component governs, because a child
    walks the whole segment: a stretch that is part residential and part
    pedestrian exposes them to the residential part. It is the same principle
    as the stair floor in terrain.py, and it is a modelling choice rather than
    a bug fix -- what is not a choice is that the answer must not depend on the
    order Overpass replied in.

    ``favourability`` maps a component to a sortable value, higher being better
    for a walking child.
    """
    parts = _components(value)
    if not parts:
        return None
    ranked = [(favourability(p), p) for p in parts]
    ranked = [(f, p) for f, p in ranked if f is not None and f == f]
    if not ranked:
        return sorted(parts, key=str)[0]
    # Tie-break on the value itself. Scores are not unique -- footway, steps,
    # pedestrian and living_street all score 1.00 -- so ranking on score alone
    # leaves min() picking whichever tied component came first, which is the
    # ordering dependence this function exists to remove. It matters
    # downstream: `footway` triggers the implicit 20 km/h fill and `steps` does
    # not, so a ['footway','steps'] segment silently changed its speed score
    # with the order Overpass replied in.
    return min(ranked, key=lambda t: (t[0], str(t[1])))[1]


def _first(value):
    """Deprecated: order-dependent. Retained only so that a caller that has no
    favourability ordering still behaves deterministically."""
    parts = _components(value)
    return sorted(parts, key=str)[0] if parts else None


def parse_maxspeed(value) -> float:
    """Parse an OSM ``maxspeed`` tag to km/h, or NaN if it cannot be read.

    A merged segment carries several limits (714 segments in Rotterdam, 40 in
    Genova). The highest governs: it is the fastest traffic the child is
    exposed to anywhere along the segment, and it does not depend on tag order.
    """
    parts = _components(value)
    if len(parts) > 1:
        speeds = [_parse_one_maxspeed(v) for v in parts]
        speeds = [x for x in speeds if x == x]
        return max(speeds) if speeds else np.nan
    return _parse_one_maxspeed(parts[0] if parts else None)


def _parse_one_maxspeed(value) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    text = str(value).strip()

    if text in IMPLICIT_SPEED:
        return float(IMPLICIT_SPEED[text])
    if (m := _MPH.search(text)):
        return float(m.group(1)) * 1.609344
    if (m := _KMH.match(text)):
        return float(m.group(1))
    return np.nan


def score_speed(kmh: float, hostile_kmh: float) -> float:
    """Map a speed limit to [0, 1].

    The curve is deliberately steep between 30 and 50 km/h. Pedestrian fatality
    risk in a collision rises sharply across exactly that band, which is why
    30 km/h zones are the standard school-street measure; a linear score would
    flatten the one distinction that matters most.
    """
    if np.isnan(kmh):
        return np.nan
    if kmh <= 20:
        return 1.00
    if kmh <= 30:
        return 0.85
    if kmh <= 40:
        return 0.50
    if kmh <= hostile_kmh:
        return 0.20
    return 0.00


def _has_tag(series: pd.Series, truthy: set[str] | None = None) -> pd.Series:
    """Binary presence score for a tag, preserving NaN for untagged rows."""
    truthy = truthy or {"yes", "true", "1"}
    # Presence is a detection: if any component way carries the tag, the
    # feature is present on the segment. Order-independent, unlike reading the
    # first component.
    out = pd.Series(np.nan, index=series.index, dtype=float)
    for i, v in series.items():
        parts = _components(v)
        parts = [p for p in parts if p is not None and p == p]
        if not parts:
            continue
        out[i] = float(any(str(p).lower() in truthy for p in parts))
    return out


def score_presence_only(
    detected: pd.Series, completeness: float | None, name: str
) -> pd.Series:
    """Score a detection layer that reports presence but never absence.

    A detection is trusted: a mapped speed bump is a speed bump. A
    non-detection is trusted **only** when the layer's detection rate has been
    estimated at ``COMPLETENESS_FOR_ABSENCE`` or above; otherwise it is left
    unobserved, because "no feature within 15 m" and "nobody has surveyed this
    street" are the same observation from here.

    Leaving it unobserved is not a shrug. It propagates into the coverage
    report, and through that into the harmonisation matrix, so the indicator is
    judged on detections it actually made rather than on rows it filled with an
    assumption.
    """
    out = pd.Series(np.nan, index=detected.index, dtype=float)
    out[detected] = 1.0
    n_det = int(detected.sum())

    if completeness is not None and completeness >= COMPLETENESS_FOR_ABSENCE:
        out[~detected] = 0.0
        log.info(
            "%s: layer completeness %.2f >= %.2f, so non-detections are scored "
            "as observed absence (%d detected / %d segments)",
            name, completeness, COMPLETENESS_FOR_ABSENCE, n_det, len(detected),
        )
    else:
        log.warning(
            "%s is presence-only and its completeness is %s: %d detections on "
            "%d segments are a LOWER BOUND on prevalence, not a rate. "
            "Non-detections are left unobserved. Establish a detection rate "
            "against an independent source to score absence.",
            name,
            "unvalidated" if completeness is None else f"{completeness:.2f}",
            n_det, len(detected),
        )
    return out


def build_segment_indicators(
    edges: gpd.GeoDataFrame,
    calming: gpd.GeoDataFrame | None = None,
    calming_completeness: float | None = None,
) -> gpd.GeoDataFrame:
    """Compute per-segment indicator scores from OSM edge attributes.

    ``edges`` must be the projected edge frame from ``osm_extract.graph_to_edges``.
    ``calming`` is the traffic-calming point layer from
    ``osm_extract.fetch_traffic_calming``; pass ``None`` only when the query was
    genuinely not run, which leaves the indicator missing rather than zero.

    ``calming_completeness`` is the estimated share of real calming features the
    OSM layer captures in this city. Leave it ``None`` unless that share has
    been measured against an independent source -- see ``score_presence_only``.
    """
    net = params("network")
    hostile = float(net["hostile_speed_kmh"])

    df = edges.copy()

    # --- traffic safety -------------------------------------------------
    df["maxspeed_kmh"] = df.get("maxspeed", pd.Series(index=df.index, dtype=object)).map(
        parse_maxspeed
    )

    highway = df.get("highway", pd.Series(index=df.index, dtype=object)).map(
        lambda v: _governing(v, lambda p: HIGHWAY_SCORE.get(str(p)))
    )
    df["highway_class"] = highway
    s_highway = highway.astype(str).map(HIGHWAY_SCORE)

    # A living_street or pedestrian way has an implicit low speed even when
    # maxspeed is untagged. Filling here is defensible: the tag itself carries
    # the legal speed, so this is decoding, not imputation.
    implicit_low = highway.astype(str).isin(["living_street", "pedestrian", "footway", "path"])
    df.loc[implicit_low & df["maxspeed_kmh"].isna(), "maxspeed_kmh"] = 20.0

    s_speed = df["maxspeed_kmh"].map(lambda v: score_speed(v, hostile))
    # Traffic calming comes from the node layer, not the edge tags: OSM tags
    # bumps and tables on nodes, so `edges["traffic_calming"]` is empty in every
    # city. A segment counts as calmed when a calming feature sits within
    # CALMING_SNAP_M of it.
    if calming is None:
        # Never queried -- leave missing. Filling zero here would assert that no
        # street in the study area is calmed, which is a claim, not a default.
        log.warning("no traffic-calming layer supplied; s_calming left missing")
        df["s_calming"] = np.nan
    else:
        if calming.empty:
            # Queried and empty. This is NOT the same as "nothing is calmed" --
            # an empty Overpass result and an unsurveyed city look identical
            # from here, which is exactly why the completeness argument below is
            # required rather than assumed.
            hit = pd.Series(False, index=df.index)
        else:
            near = gpd.sjoin_nearest(
                df[["geometry"]],
                calming[["geometry"]],
                how="left",
                max_distance=CALMING_SNAP_M,
                distance_col="_calm_dist",
            )
            # sjoin_nearest can emit several rows per segment on ties.
            hit = (
                near.groupby(level=0)["_calm_dist"].min().notna()
                .reindex(df.index).fillna(False)
            )
        df["s_calming"] = score_presence_only(hit, calming_completeness, "s_calming")

    df["s_speed"] = s_speed
    df["s_highway"] = s_highway

    # --- walking infrastructure -----------------------------------------
    # Two sources, in priority order. The `sidewalk=*` tag is authoritative
    # where a mapper set it; where it is absent -- which is *every* road in
    # Rotterdam, because NL maps sidewalks as separate ways -- provision is
    # inferred from parallel footway geometry instead.
    #
    # Using the tag alone is not merely low-coverage: it is non-random. The tag
    # only survives on ways that are themselves footways, so an index requiring
    # it silently narrows to footways, which all score alike. That produced a
    # near-constant index in the first run of this pipeline.
    sidewalk = df.get("sidewalk", pd.Series(index=df.index, dtype=object)).map(
        lambda v: _governing(v, lambda p: SIDEWALK_SCORE.get(str(p).lower()))
    )
    s_tagged = sidewalk.astype(str).str.lower().map(SIDEWALK_SCORE)

    df["highway_class"] = highway  # sidewalks.py needs this present
    s_geometric = sidewalk_provision(df)

    df["s_sidewalk"] = s_tagged.where(s_tagged.notna(), s_geometric)
    df["sidewalk_source"] = np.where(
        s_tagged.notna(), "osm_tag", np.where(s_geometric.notna(), "geometry", "none")
    )

    df["s_lit"] = _has_tag(df.get("lit", pd.Series(index=df.index, dtype=object)))

    # --- analysis subset --------------------------------------------------
    # Service roads are 23% of the Rotterdam network and 85% of them carry no
    # maxspeed, so `d_traffic_safety` rests on different evidence there than on
    # ordinary streets -- it is not the same indicator. They are also mostly
    # parking aisles, driveways and rear access, not streets a child walks along.
    #
    # They stay in the graph, because they carry real pedestrian access and
    # removing them would truncate walksheds. They are excluded from headline
    # index statistics, which are reported over `in_analysis_set`.
    df["is_service"] = highway.astype(str) == "service"
    df["in_analysis_set"] = ~df["is_service"]

    # --- environment -----------------------------------------------------
    # Greenness and enclosure need raster / building inputs and are attached in
    # a future enrichment step (not yet implemented). The columns are created
    # here so the schema is stable across
    # cities whether or not those inputs were available.
    df["s_green"] = np.nan
    df["s_enclosure"] = np.nan

    # --- enforce applicability -------------------------------------------
    # Blank out indicators where the question does not arise, at source rather
    # than only inside the composite. Everything downstream -- the coverage
    # report, and through it the cross-city harmonisation matrix -- then counts
    # observations against the segments that could have been observed, instead
    # of against every row in the table.
    for col, mask in applicability(df).items():
        if col in df.columns:
            df.loc[~mask, col] = np.nan

    return df


DOMAIN_INDICATORS = {
    "traffic_safety": ["s_speed", "s_highway", "s_calming"],
    "walking_infrastructure": ["s_sidewalk", "s_lit"],
    "environment": ["s_green", "s_enclosure"],
}


def applicability(df: gpd.GeoDataFrame) -> dict[str, pd.Series]:
    """Boolean mask per indicator: is this question meaningful on this segment?

    Distinguishing "not applicable" from "unobserved" is the single correction
    that fixes both of the residual defects in this pipeline:

    * Traffic indicators on car-free ways were scored 0.0 rather than skipped,
      dragging footways and steps down toward trunk-road scores.
    * Sidewalk provision was scored 1.0 on footways themselves ("a footway is
      its own sidewalk"). True but tautological -- it inflated the domain's
      apparent coverage from 0.19 to 0.54 and very nearly carried a domain
      through the gate on the strength of segments that told us nothing.

    Coverage is computed over applicable segments only, so a domain is judged on
    what it could have known, not on how many rows it happened to fill.
    """
    car_free = df["highway_class"].isin(CAR_FREE_CLASSES)
    is_road = df["highway_class"].isin(ROAD_CLASSES)
    everywhere = pd.Series(True, index=df.index)

    return {
        # Motor-traffic questions: meaningless where motor traffic cannot go.
        "s_speed": ~car_free,
        "s_calming": ~car_free,
        "s_highway": everywhere,
        # Sidewalk provision is a question about roads. A footway is walking
        # infrastructure; asking whether it "has a sidewalk" is not informative.
        "s_sidewalk": is_road,
        "s_lit": everywhere,
        "s_green": everywhere,
        "s_enclosure": everywhere,
    }


# A domain observed on less than this share of segments cannot enter the
# composite. Matching the harmonisation threshold is deliberate: an indicator
# too sparse to support a cross-city comparison is also too sparse to support a
# within-city index, and applying one rule in both places keeps the index
# meaning the same thing everywhere it is computed.
MIN_DOMAIN_COVERAGE = 0.60

# An indicator whose observed values pile up this heavily on one value is not
# distinguishing between streets; it contributes level and nothing else.
#
# `_warn_if_degenerate` has always reported the perfectly constant case. This
# promotes that warning to a gate and widens it slightly, because the damaging
# case is not exact constancy but near-constancy: `s_calming` sits on one value
# for 99.4% of observed segments in Genova against 82.9% in Rotterdam, so the
# indicator that carries almost no within-city information in Genova is the same
# one carrying a 27.6x between-city gap. A cut at 0.95 separates those two
# cleanly and leaves every other live indicator untouched (`s_highway` 0.38/0.42,
# `s_speed` 0.56/0.67, `s_sidewalk` 0.75/0.80).
#
# Coverage asks "did we observe it?"; the divergence check asks "did we observe
# the same thing?"; this asks "does it vary?". Three independent questions, and
# an indicator has to survive all three.
MAX_MODAL_SHARE = 0.95

# ...but only once there are enough observations for a modal share to mean
# anything. On a handful of segments it is noise, and gating on noise would drop
# sound indicators in small study areas. The floor matches the one
# `_warn_if_degenerate` has always used. It costs nothing on real data: an
# indicator with fewer than 100 observations in a city of ~100,000 segments is
# already far under the coverage gate.
MIN_N_FOR_MODAL_GATE = 100


def modal_share(values: pd.Series) -> float:
    """Share of observed values sitting on the single most common value.

    Returns 1.0 for an indicator observed nowhere -- no observations means no
    discrimination, which is the same verdict by a different route.
    """
    seen = values.dropna()
    if seen.empty:
        return 1.0
    return float(seen.value_counts(normalize=True).iloc[0])


# Where an indicator is applicable but unobserved, these are the tightest bounds
# its value can be held to without assuming anything about it. The default is
# the full support [0, 1] -- a refusal, and the correct one.
#
# `s_speed` is the single case where law narrows it. NL and IT both set an urban
# default of 50 km/h (score 0.20), and the realistic signed alternative on a
# minor street is a 30 zone (0.85). The band is applied only to small-street
# classes: a primary or trunk road may legally be posted well above 50, so its
# lower bound is genuinely 0.
#
# The band matters because Genova's `maxspeed` is missing-not-at-random in the
# informative direction -- 56% of its tagged roads are 30 zones, because mappers
# record the exception and leave the default implicit. Filling the gap with
# either end of the band is therefore a guess; carrying both ends is not.
SPEED_BAND_CLASSES = frozenset(
    {"residential", "living_street", "unclassified", "service"}
)
SPEED_BAND = (0.20, 0.85)


def indicator_bounds(
    df: gpd.GeoDataFrame, col: str
) -> tuple[pd.Series, pd.Series]:
    """Per-segment ``(lo, hi)`` for one indicator.

    Observed values are pinned to themselves. Unobserved-but-applicable values
    get the widest range that cannot be argued away. The result is an identified
    set, in the partial-identification sense: the range of values the index
    could take across every assignment consistent with what was actually
    observed.
    """
    vals = df[col] if col in df.columns else pd.Series(np.nan, index=df.index)
    lo = pd.Series(0.0, index=df.index, dtype=float)
    hi = pd.Series(1.0, index=df.index, dtype=float)

    if col == "s_speed" and "highway_class" in df.columns:
        small = df["highway_class"].isin(SPEED_BAND_CLASSES)
        lo[small], hi[small] = SPEED_BAND

    seen = vals.notna()
    lo[seen] = vals[seen]
    hi[seen] = vals[seen]
    return lo, hi


def composite_index(
    df: gpd.GeoDataFrame,
    min_domains: int = 1,
    indicators: set[str] | None = None,
) -> gpd.GeoDataFrame:
    """Combine indicator scores into domain scores and a composite index.

    Domain scores are the mean of their available indicators. A domain observed
    on fewer than ``MIN_DOMAIN_COVERAGE`` of segments is **excluded from the
    composite entirely**, rather than contributing where it happens to exist.

    That exclusion is the important part. Letting a sparse domain contribute
    where available produces an index that means different things on different
    segments -- traffic safety alone here, traffic safety plus sidewalks there
    -- and those values are not comparable even within one city. Worse, if the
    sparse domain's missingness correlates with street type (as sidewalk data
    does), the mixture is systematically biased by street type.

    ``coverage`` records the share of total declared weight that survived, so a
    reader can see how much of the intended construct was actually measured.

    ``indicators`` restricts the build to a named indicator set. This is how the
    cross-city comparable set is enforced: without it each city is indexed on
    whatever it happens to have, so `s_speed` enters Rotterdam's number and not
    Genova's, and the resulting difference is a difference in evidence rather
    than in streets. Passing the same set to both cities is the whole point.

    Alongside the point estimate, ``ssr_index_lo`` / ``ssr_index_hi`` give the
    identified set: the range the index could take over every value the
    unobserved indicators could have held. Where those intervals overlap between
    two cities, the comparison does not support a claim, however far apart the
    point estimates sit.
    """
    weights = params("segment_index")["domain_weights"]
    out = df.copy()
    applies = applicability(out)
    bounds: dict[str, tuple[pd.Series, pd.Series]] = {}

    # Domain score = mean over indicators that are BOTH applicable and observed.
    # A domain is scored only where at least half of its applicable indicators
    # were observed, so a three-indicator construct is never silently replaced
    # by a one-indicator proxy.
    for domain, cols in DOMAIN_INDICATORS.items():
        present = [c for c in cols if c in out.columns]
        if indicators is not None:
            present = [c for c in present if c in indicators]

        # An indicator observed *nowhere* carries no information, and counting
        # it as applicable inflates the domain's denominator. `s_lit` has 0%
        # coverage in Rotterdam, yet applies everywhere, so it stretched
        # walking_infrastructure's applicable set from 67,444 roads to all
        # 119,882 segments and reported that domain at 0.107 when the only
        # working indicator gives 0.191. "Applicable" has to mean applicable
        # *and* potentially observable.
        #
        # The same argument extends past zero coverage to near-zero variance:
        # an indicator observed everywhere but constant is equally uninformative
        # and, unlike the all-missing case, it silently moves the domain's level
        # while doing it.
        live = []
        for c in present:
            if not out[c].notna().any():
                continue
            share = modal_share(out[c])
            if out[c].notna().sum() >= MIN_N_FOR_MODAL_GATE and share > MAX_MODAL_SHARE:
                log.warning(
                    "indicator %s excluded from %s: %.1f%% of observed values "
                    "sit on a single value (cap %.0f%%). It cannot discriminate "
                    "between streets here, and averaging it in would shift the "
                    "domain's level on no evidence.",
                    c, domain, 100 * share, 100 * MAX_MODAL_SHARE,
                )
                continue
            live.append(c)

        if not live:
            out[f"d_{domain}"] = np.nan
            out[f"d_{domain}_lo"] = np.nan
            out[f"d_{domain}_hi"] = np.nan
            out[f"applicable_{domain}"] = False
            continue
        present = live

        # Build these with explicit column labels. `applies[c]` inherits the
        # name "highway_class" from the .isin() that produced it, so a bare
        # pd.concat yields unlabelled columns -- and DataFrame.where() aligns by
        # column label, silently blanking every value it cannot match.
        app = pd.DataFrame({c: applies[c] for c in present}, index=out.index)
        obs = pd.DataFrame(
            {c: out[c].notna() & applies[c] for c in present}, index=out.index
        )

        n_app = app.sum(axis=1)
        n_obs = obs.sum(axis=1)

        vals = out[present].where(obs)
        score = vals.mean(axis=1, skipna=True)
        unscored = (n_app == 0) | (n_obs == 0) | (n_obs < 0.5 * n_app)
        score[unscored] = np.nan

        # The identified set for this domain, over the SAME indicators as the
        # point estimate. An unobserved-but-applicable indicator contributes its
        # bound rather than being dropped from the average, which is the fix for
        # the mechanism at the root of this: an available-case mean renormalises
        # over the survivors, so dropping a high-valued indicator lowers the
        # score by itself. Genova's residential streets scored 0.386 against
        # Rotterdam's 0.607 on exactly that -- 92.8% of them have no maxspeed,
        # so the high indicator vanished and the average fell onto the low one.
        lo_cols, hi_cols = {}, {}
        for c in present:
            # Not `setdefault`: Python evaluates the default eagerly, so the
            # bounds would be recomputed on every hit and the cache would save
            # nothing.
            if c not in bounds:
                bounds[c] = indicator_bounds(out, c)
            b_lo, b_hi = bounds[c]
            lo_cols[c] = b_lo.where(app[c])
            hi_cols[c] = b_hi.where(app[c])
        d_lo = pd.DataFrame(lo_cols, index=out.index).mean(axis=1, skipna=True)
        d_hi = pd.DataFrame(hi_cols, index=out.index).mean(axis=1, skipna=True)
        d_lo[n_app == 0] = np.nan
        d_hi[n_app == 0] = np.nan

        out[f"d_{domain}"] = score
        out[f"d_{domain}_lo"] = d_lo
        out[f"d_{domain}_hi"] = d_hi
        out[f"applicable_{domain}"] = n_app > 0

    # Coverage is judged over applicable segments only: a domain should be
    # gated on what it could have known, not on how many rows it filled.
    usable, dropped = [], []
    for domain in DOMAIN_INDICATORS:
        app_mask = out[f"applicable_{domain}"]
        n_app = int(app_mask.sum())
        cov = float(out.loc[app_mask, f"d_{domain}"].notna().mean()) if n_app else 0.0
        (usable if cov >= MIN_DOMAIN_COVERAGE else dropped).append((domain, cov))

    for domain, cov in dropped:
        log.warning(
            "domain %s excluded from composite: observed on %.1f%% of "
            "APPLICABLE segments (threshold %.0f%%). It remains in the output "
            "for inspection but does not enter the index.",
            domain, 100 * cov, 100 * MIN_DOMAIN_COVERAGE,
        )
    if not usable:
        raise ValueError(
            "No domain clears the coverage threshold; there is nothing to index. "
            "Check the indicator inputs before proceeding."
        )
    log.info(
        "composite built from %d/%d domains: %s",
        len(usable), len(DOMAIN_INDICATORS),
        ", ".join(f"{d} ({100 * c:.0f}%)" for d, c in usable),
    )
    out.attrs["domains_used"] = [d for d, _ in usable]
    out.attrs["domains_dropped"] = dict(dropped)

    domain_cols = [f"d_{d}" for d, _ in usable]
    w = np.array([weights[d] for d, _ in usable], dtype=float)
    # Denominator stays the FULL declared weight, so `coverage` reports how much
    # of the intended construct is present rather than silently reading 1.0.
    total_declared = float(sum(weights.values()))

    values = out[domain_cols].to_numpy(dtype=float)
    mask = ~np.isnan(values)

    weight_present = (mask * w).sum(axis=1)
    weighted_sum = np.nansum(np.where(mask, values, 0.0) * w, axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        index = np.where(weight_present > 0, weighted_sum / weight_present, np.nan)

    # Bounds ride the same weights and the same surviving domains as the point
    # estimate, so `ssr_index` always lies inside [lo, hi] and the three columns
    # describe one quantity rather than three.
    def _weighted(cols: list[str]) -> np.ndarray:
        vals = out[cols].to_numpy(dtype=float)
        summed = np.nansum(np.where(mask, vals, 0.0) * w, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(weight_present > 0, summed / weight_present, np.nan)

    index_lo = _weighted([f"d_{d}_lo" for d, _ in usable])
    index_hi = _weighted([f"d_{d}_hi" for d, _ in usable])

    out["coverage"] = weight_present / total_declared
    out["ssr_index"] = index
    out["ssr_index_lo"] = index_lo
    out["ssr_index_hi"] = index_hi

    # Refuse to publish an index built on a single domain.
    n_domains = mask.sum(axis=1)
    out.loc[n_domains < min_domains, ["ssr_index", "ssr_index_lo", "ssr_index_hi"]] = np.nan

    width = float(np.nanmean(index_hi - index_lo)) if np.isfinite(index_lo).any() else float("nan")
    log.info(
        "identified set: mean interval width %.3f (0 = every contributing "
        "indicator observed everywhere it applies)", width,
    )

    log.info(
        "Composite index: %d/%d segments scored, mean coverage %.2f",
        int(out["ssr_index"].notna().sum()),
        len(out),
        float(out["coverage"].mean()),
    )
    _warn_if_degenerate(out)
    return out


# An index whose interquartile range is below this is not discriminating between
# streets, whatever its mean looks like.
MIN_PLAUSIBLE_IQR = 0.02


def _warn_if_degenerate(out: gpd.GeoDataFrame) -> None:
    """Flag an index that has collapsed to (near) a single value.

    This exists because the first version of this pipeline silently produced an
    index with mean 0.822, p10 0.822 and p90 0.822 -- a number that looks
    entirely reasonable in a summary table and is in fact an artefact of
    non-random missingness selecting the sample down to a single street type.
    A degenerate index is far more likely than not to be a bug, and it is cheap
    to check, so the check runs on every build rather than relying on someone
    thinking to look.
    """
    idx = out["ssr_index"].dropna()
    if idx.empty:
        log.error("DEGENERATE INDEX: no segment received a score at all.")
        return

    iqr = float(idx.quantile(0.75) - idx.quantile(0.25))
    n_unique = int(idx.nunique())
    if iqr < MIN_PLAUSIBLE_IQR or n_unique < 5:
        log.error(
            "DEGENERATE INDEX: IQR %.4f over %d distinct values (n=%d). "
            "The index is not discriminating between streets -- check whether an "
            "indicator's missingness is correlated with street type before using "
            "these results.",
            iqr, n_unique, len(idx),
        )

    # An indicator that is constant wherever it is observed carries no
    # information and silently shifts the composite's level.
    for col in [c for c in out.columns if c.startswith("s_")]:
        vals = out[col].dropna()
        if len(vals) > 100 and float(vals.std()) == 0.0:
            log.warning(
                "indicator %s is constant at %.3f across %d observed segments "
                "-- it adds no discrimination, only level",
                col, float(vals.iloc[0]), len(vals),
            )
