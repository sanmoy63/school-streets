"""Street-segment indicators and the composite school-street readiness index.

The unit of analysis is the OSM way segment, because that is the unit at which a
school street intervention is actually implemented -- a municipality closes a
segment, not a grid cell or a neighbourhood.

Three domains are built, then combined:

  traffic_safety        -- what a car can do here
  walking_infrastructure-- what a pedestrian is given here
  environment           -- what the street is like to be in

Each indicator is scored to [0, 1] where 1 is *better for a walking child*.
Missing data is propagated as NaN and reported, never imputed to zero: in
Bratislava and Tirana an untagged sidewalk is overwhelmingly an unsurveyed
sidewalk, not an absent one, and scoring absence as zero would manufacture a
cross-city gradient that is really a mapping-effort gradient.
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
    "NL:urban": 50, "NL:zone30": 30, "NL:zone20": 20,
    "FI:urban": 50, "FI:zone30": 30,
    "SK:urban": 50, "SK:zone30": 30,
    "AL:urban": 40,
    "walk": 7, "living_street": 20,
}


def _first(value):
    """OSM tags arrive as scalars or lists when a segment merges several ways."""
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def parse_maxspeed(value) -> float:
    """Parse an OSM ``maxspeed`` tag to km/h, or NaN if it cannot be read."""
    value = _first(value)
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
    vals = series.map(_first)
    out = pd.Series(np.nan, index=series.index, dtype=float)
    known = vals.notna()
    out[known] = vals[known].astype(str).str.lower().isin(truthy).astype(float)
    return out


def build_segment_indicators(
    edges: gpd.GeoDataFrame,
    calming: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Compute per-segment indicator scores from OSM edge attributes.

    ``edges`` must be the projected edge frame from ``osm_extract.graph_to_edges``.
    ``calming`` is the traffic-calming point layer from
    ``osm_extract.fetch_traffic_calming``; pass ``None`` only when the query was
    genuinely not run, which leaves the indicator missing rather than zero.
    """
    net = params("network")
    hostile = float(net["hostile_speed_kmh"])

    df = edges.copy()

    # --- traffic safety -------------------------------------------------
    df["maxspeed_kmh"] = df.get("maxspeed", pd.Series(index=df.index, dtype=object)).map(
        parse_maxspeed
    )

    highway = df.get("highway", pd.Series(index=df.index, dtype=object)).map(_first)
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
    elif calming.empty:
        # Queried and genuinely empty: absence is now an observation.
        df["s_calming"] = 0.0
    else:
        near = gpd.sjoin_nearest(
            df[["geometry"]],
            calming[["geometry"]],
            how="left",
            max_distance=CALMING_SNAP_M,
            distance_col="_calm_dist",
        )
        # sjoin_nearest can emit several rows per segment on ties.
        hit = near.groupby(level=0)["_calm_dist"].min().notna()
        df["s_calming"] = hit.reindex(df.index).fillna(False).astype(float)

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
    sidewalk = df.get("sidewalk", pd.Series(index=df.index, dtype=object)).map(_first)
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


def composite_index(df: gpd.GeoDataFrame, min_domains: int = 1) -> gpd.GeoDataFrame:
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
    """
    weights = params("segment_index")["domain_weights"]
    out = df.copy()
    applies = applicability(out)

    # Domain score = mean over indicators that are BOTH applicable and observed.
    # A domain is scored only where at least half of its applicable indicators
    # were observed, so a three-indicator construct is never silently replaced
    # by a one-indicator proxy.
    for domain, cols in DOMAIN_INDICATORS.items():
        present = [c for c in cols if c in out.columns]
        if not present:
            out[f"d_{domain}"] = np.nan
            out[f"applicable_{domain}"] = False
            continue

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
        score[(n_app == 0) | (n_obs == 0) | (n_obs < 0.5 * n_app)] = np.nan

        out[f"d_{domain}"] = score
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

    out["coverage"] = weight_present / total_declared
    out["ssr_index"] = index

    # Refuse to publish an index built on a single domain.
    n_domains = mask.sum(axis=1)
    out.loc[n_domains < min_domains, "ssr_index"] = np.nan

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
