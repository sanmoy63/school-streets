"""Rebuild every city's index on the indicators all of them can support.

Usage
-----
    python scripts/02b_comparative_index.py                 # all cities found
    python scripts/02b_comparative_index.py rotterdam genova
    python scripts/02b_comparative_index.py --keep-suspect  # override the screen

``01_build_city.py`` indexes each city on whatever that city happens to observe.
That is the right thing for a within-city question -- Rotterdam's own streets
are ranked against each other on all the evidence Rotterdam has -- and the wrong
thing for a comparison, because the indicator sets differ. `s_speed` is present
on 79.3% of Rotterdam's roads and 9.4% of Genova's, so it entered one composite
and not the other, and the difference between the two numbers was then read as a
difference between the two cities.

It was not. Restricting both cities to the indicators both observe closes the
residential gap from 0.221 to zero. This script does that restriction, and
writes the intervals that say how much of the remaining difference is real.

Outputs (``outputs/tables/``)
    comparative_index.csv        per city x highway class: point estimate and
                                 identified set, on the shared indicator set
    comparative_claims.csv       every city pair x class, and whether their
                                 intervals are disjoint enough to support a claim

and ``data/processed/<city>_segments_comparative.gpkg`` with the rebuilt scores.
"""

from __future__ import annotations

import argparse
import itertools
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from routes_ssr import config, harmonise, segment_index  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("comparative")

# Classes reported separately. Comparing whole-city means mixes the class
# composition of the two networks into the comparison, which is a different
# question from "is a residential street in Genova worse than one in Rotterdam".
REPORT_CLASSES = ["residential", "unclassified", "tertiary", "secondary", "primary"]

# Intervals must clear each other by more than this to count as disjoint.
# Averaging float scores over tens of thousands of segments leaves the last bits
# unreliable -- Genova's unclassified streets came out at 0.6 and Rotterdam's at
# 0.5999999999999999, identical numbers that a bare `>` reports as a supported
# difference. The cutoff is numerical, not substantive: it removes an artefact,
# it does not decide what counts as a meaningful gap. `margin` reports the size
# of the separation so that judgement stays with the reader.
SEPARATION_EPS = 1e-9

# A separation this small is real but too small to interpret: the underlying
# indicator is a lookup table whose finest step is 0.05, so a gap two orders
# below that cannot reflect a distinction the index is capable of drawing.
NEGLIGIBLE_MARGIN = 0.01

# Below this a within-class difference is floating-point noise rather than a
# measured distinction. The scores it separates come from a lookup whose finest
# step is 0.05, so nothing real lives six orders of magnitude beneath that.
COMPOSITION_EPS = 1e-9

# The row in `summarise` that aggregates over classes. Only this row can carry a
# composition effect: a per-class comparison holds the class fixed, so its whole
# gap is within-class by construction.
AGGREGATE_LABEL = "all (excl. service)"


def summarise(
    segs: gpd.GeoDataFrame, city: str, indicators: set[str]
) -> pd.DataFrame:
    """Point estimate and identified set per highway class, plus city-wide."""
    rows = []
    if "in_analysis_set" not in segs.columns:
        # `.get(col, True)` returns a bare bool here, and `True.astype` is an
        # AttributeError three lines later. Fail with the reason instead.
        raise SystemExit(
            f"{city}: segments carry no `in_analysis_set` column, so service "
            "roads cannot be excluded from the headline figures. The file "
            "predates that column -- re-run scripts/01_build_city.py."
        )
    analysis = segs[segs["in_analysis_set"].astype(bool)]

    groups = [("all (excl. service)", analysis)]
    groups += [
        (cls, analysis[analysis["highway_class"] == cls])
        for cls in REPORT_CLASSES
    ]

    for label, sub in groups:
        # One mask for all three statistics. Taking the point estimate over
        # scored segments and the bounds over every row would let the interval
        # describe a different set of streets than the number inside it -- they
        # coincide today only because a segment's index and its bounds are
        # currently made NaN together, which is not a property anything enforces.
        scored = sub[sub["ssr_index"].notna()]
        if scored.empty:
            continue
        rows.append(
            {
                "city": city,
                "class": label,
                "n": len(scored),
                "index": float(scored["ssr_index"].mean()),
                "lo": float(scored["ssr_index_lo"].mean()),
                "hi": float(scored["ssr_index_hi"].mean()),
                "indicators": " ".join(sorted(indicators)),
            }
        )
    return pd.DataFrame(rows)


def decompose_gap(summary: pd.DataFrame, cls_a: str, cls_b: str,
                  aggregate: str) -> tuple[float, float]:
    """Split a between-city difference into within-class and composition parts.

    Returns ``(within, composition)``. The first is what the indicators actually
    measured differently on comparable streets; the second is what follows from
    the two cities holding different proportions of each road class.

    This is the test for a vacuous comparison, and it is computed rather than
    declared. A surviving indicator that is a lookup on `highway_class` scores
    every class identically in every city, so every within-class difference is
    exactly zero and the entire gap is composition -- no matter what the
    indicator is called. An indicator that genuinely varies within a class
    produces a non-zero within term and the comparison is about streets again.

    Both terms use the two-city mean as the reference weight and score, so the
    split does not depend on which city is called `a`.
    """
    per_class = summary[summary["class"] != aggregate]
    if aggregate != AGGREGATE_LABEL:
        raise ValueError(
            f"decompose_gap is only meaningful for {AGGREGATE_LABEL!r}; a "
            f"per-class comparison holds the class fixed and has no "
            f"composition component. Got {aggregate!r}."
        )
    a = per_class[per_class["city"] == cls_a].set_index("class")
    b = per_class[per_class["city"] == cls_b].set_index("class")
    shared = a.index.intersection(b.index)
    if shared.empty:
        return float("nan"), float("nan")

    wa = a.loc[shared, "n"] / a.loc[shared, "n"].sum()
    wb = b.loc[shared, "n"] / b.loc[shared, "n"].sum()
    sa, sb = a.loc[shared, "index_"], b.loc[shared, "index_"]

    within = float((((wa + wb) / 2) * (sa - sb)).sum())
    composition = float(((wa - wb) * ((sa + sb) / 2)).sum())

    # `summarise` emits rows only for REPORT_CLASSES, while the aggregate covers
    # every non-service class. The decomposition therefore describes a subset,
    # and its two terms do not sum to the reported aggregate gap. Say so rather
    # than letting a reader assume they should reconcile.
    covered = (
        a.loc[shared, "n"].sum() + b.loc[shared, "n"].sum()
    ) / (
        summary.loc[(summary["city"] == cls_a) & (summary["class"] == aggregate), "n"].sum()
        + summary.loc[(summary["city"] == cls_b) & (summary["class"] == aggregate), "n"].sum()
    )
    log.debug("decomposition covers %.1f%% of the aggregate's segments", 100 * covered)
    return within, composition


def claims(summary: pd.DataFrame) -> pd.DataFrame:
    """For each city pair and class: does the evidence support a difference?

    Two point estimates differing is not a finding while the intervals around
    them overlap -- the ordering can be reversed by values the data never ruled
    out. This is the table that decides what may be written down.
    """
    rows = []
    for cls, grp in summary.groupby("class", sort=False):
        for a, b in itertools.combinations(grp.itertuples(), 2):
            gap = a.index_ - b.index_
            # How far the intervals clear each other. Negative means they
            # overlap, and an overlap is the end of the matter: the ordering of
            # the two point estimates can be reversed by values the data never
            # ruled out, so there is no difference to report.
            margin = max(a.lo - b.hi, b.lo - a.hi)
            # What the comparison actually rests on. A pair can separate
            # cleanly, clear the negligible-margin test, and still be comparing
            # nothing but road-class composition. Measured, not declared: the
            # within-class term is what the indicators found to differ on
            # comparable streets, and where it is zero the gap is entirely a
            # difference in road-class mix. Recording the split beside the
            # verdict makes that readable in this table rather than only to
            # someone who cross-reads two files.
            if cls == AGGREGATE_LABEL:
                within, comp = decompose_gap(summary, a.city, b.city, cls)
            else:
                # Same class in both cities: nothing can be attributed to a
                # difference in class shares, so the gap is within-class by
                # definition. Decomposing here would silently split it over the
                # OTHER classes and report a number about the wrong streets.
                within, comp = gap, 0.0
            total = abs(within) + abs(comp)
            comp_share = (abs(comp) / total) if total > COMPOSITION_EPS else float("nan")
            rows.append(
                {
                    "class": cls,
                    "city_a": a.city,
                    "city_b": b.city,
                    "index_a": round(a.index_, 4),
                    "index_b": round(b.index_, 4),
                    "point_gap": round(gap, 4),
                    "interval_a": f"[{a.lo:.3f}, {a.hi:.3f}]",
                    "interval_b": f"[{b.lo:.3f}, {b.hi:.3f}]",
                    "margin": round(margin, 6),
                    "supported": bool(margin > SEPARATION_EPS),
                    # Only meaningful for a pair that actually separated: it
                    # qualifies a supported claim, it does not resurrect an
                    # unsupported one.
                    "negligible": bool(
                        SEPARATION_EPS < margin <= NEGLIGIBLE_MARGIN
                    ),
                    "indicators": a.indicators,
                    "within_class": round(within, 6),
                    "composition": round(comp, 6),
                    "composition_share": (
                        round(comp_share, 4) if comp_share == comp_share else None
                    ),
                    # Qualifies a supported claim the same way `negligible`
                    # does: the separation is real, and it is a separation in
                    # how the two cities' roads are classified, not in what was
                    # measured on them.
                    "composition_only": bool(
                        total > COMPOSITION_EPS and abs(within) <= COMPOSITION_EPS
                    ),
                }
            )
    return pd.DataFrame(rows)


def main(city_keys: list[str] | None, keep_suspect: bool = False) -> None:
    config.ensure_dirs()

    cov = harmonise.load_coverage(city_keys)
    frames = harmonise.load_segments(city_keys)
    if len(frames) < 2:
        raise SystemExit(
            "A comparative index needs at least two cities. Run "
            "scripts/01_build_city.py for each before this."
        )

    mismatch = harmonise.city_set_mismatch(cov, frames)
    if mismatch["missing_coverage"]:
        raise SystemExit(
            "No coverage report for "
            f"{', '.join(sorted(mismatch['missing_coverage']))}. Those cities "
            "have segment files and would be indexed, but they never gated the "
            "comparable set -- so the set is computed as if they did not exist "
            "and then applied to them anyway, which is how a sparse indicator "
            "gets back into a comparison. Run scripts/01_build_city.py for them."
        )
    if mismatch["missing_segments"]:
        raise SystemExit(
            "Coverage reports exist for "
            f"{', '.join(sorted(mismatch['missing_segments']))} but their "
            "segment files do not. Those cities constrain the comparable set "
            "without being indexed on it; delete the stale reports or rebuild."
        )

    stale = harmonise.stale_presence_only(frames)
    if stale:
        raise SystemExit(
            f"Stored segments for {', '.join(sorted(stale))} predate "
            "presence-only typing -- a presence-only indicator is scored 0.0, "
            "meaning non-detection is still counted as observed absence. "
            "Re-run scripts/01_build_city.py for those cities first; building a "
            "comparison on them would reproduce the artefact this script exists "
            "to remove."
        )

    div = harmonise.divergence(frames)
    keep, dropped = harmonise.comparable_indicators(
        cov, div, exclude_suspect=not keep_suspect
    )

    log.info("--- shared indicator set ---")
    log.info("  usable : %s", ", ".join(sorted(keep)) or "(none)")
    for ind, why in sorted(dropped.items()):
        log.info("  dropped: %-14s %s", ind, why)
    if keep_suspect:
        log.warning(
            "--keep-suspect: divergence-flagged indicators are being kept. "
            "Say so in print, with the external validation that justifies it."
        )
    if not keep:
        raise SystemExit(
            "No indicator is observable in every city at the comparability "
            "threshold. There is no cross-city index to build, and that is the "
            "result -- report it as one rather than lowering the threshold."
        )

    summaries = []
    for city, segs in frames.items():
        rebuilt = segment_index.composite_index(segs, indicators=keep)
        out_path = config.DATA_PROCESSED / f"{city}_segments_comparative.gpkg"
        rebuilt.to_file(out_path, driver="GPKG")
        log.info("%s -> %s", city, out_path.name)
        summaries.append(summarise(rebuilt, city, keep))

    summary = pd.concat(summaries, ignore_index=True)
    # `index` collides with the DataFrame attribute in itertuples; rename for the
    # claims pass and restore on the way out.
    summary = summary.rename(columns={"index": "index_"})

    cl = claims(summary)
    summary = summary.rename(columns={"index_": "index"})

    summary.round(4).to_csv(config.OUT_TABLES / "comparative_index.csv", index=False)
    cl.to_csv(config.OUT_TABLES / "comparative_claims.csv", index=False)

    log.info("--- comparative index (shared indicators only) ---")
    log.info("\n%s", summary.round(4).to_string(index=False))
    log.info("--- what the evidence supports ---")
    log.info("\n%s", cl.to_string(index=False))

    # A class only one city has produces no pair, so it drops out of the claims
    # table with nothing said. That is a real asymmetry between the networks and
    # a reader should hear about it rather than infer it from a missing row.
    lonely = [
        cls
        for cls, grp in summary.groupby("class", sort=False)
        if grp["city"].nunique() < 2
    ]
    if lonely:
        log.warning(
            "no comparison possible for %s: present in fewer than two cities. "
            "Absent from the claims table entirely -- check whether the class is "
            "genuinely missing or merely unscored there.",
            ", ".join(lonely),
        )

    n_ok = int(cl["supported"].sum())
    log.info(
        "%d/%d city-pair comparisons have disjoint identified sets. "
        "The rest are differences the data cannot distinguish from missingness.",
        n_ok, len(cl),
    )
    n_comp = int((cl["supported"] & cl["composition_only"]).sum())
    if n_comp:
        log.warning(
            "%d supported comparison(s) have a within-class difference of zero: "
            "100%% of the gap is road-class MIX, not anything measured on the "
            "streets. Every surviving indicator scores each class identically "
            "in both cities, so the identified set is degenerate (lo == hi) as "
            "well -- a quantity that is never missing cannot express doubt. "
            "Report these as a comparison of classification, or not at all.",
            n_comp,
        )
    n_negl = int(cl["negligible"].sum())
    if n_negl:
        log.warning(
            "%d of those clear each other by %.2f or less. That is a real "
            "separation and an uninterpretable one: the surviving indicator is a "
            "lookup table whose finest step is 0.05, so a gap this small is not "
            "a distinction the index can draw. Do not report them as findings.",
            n_negl, NEGLIGIBLE_MARGIN,
        )
    log.info("-> outputs/tables/comparative_index.csv, comparative_claims.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*", help="city keys; default = all available")
    ap.add_argument(
        "--keep-suspect", action="store_true",
        help="keep divergence-flagged indicators (requires external validation)",
    )
    args = ap.parse_args()
    main(args.cities or None, keep_suspect=args.keep_suspect)
