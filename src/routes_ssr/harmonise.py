"""Which indicators can carry a cross-city claim, and why the others cannot.

This module exists because the answer used to be computed and then thrown away.
``02_harmonisation_matrix.py`` decided that `s_speed` was not comparable --
79.3% coverage in Rotterdam against 9.4% in Genova -- wrote that verdict to a
CSV, and nothing ever read it. Each city was meanwhile indexed on whatever it
happened to have, so `s_speed` entered Rotterdam's composite and not Genova's.

The resulting difference was then reported as a difference between the cities.
It was a difference in evidence. Genova's residential streets scored 0.386
against Rotterdam's 0.607, and restricting both cities to the one indicator they
both observe closes that gap to exactly zero -- every point of it came from
indicators one city had and the other did not.

So the verdict lives here, one definition, used by the matrix that reports it
and by the comparative index that must obey it.

Three independent questions have to be answered before an indicator may carry a
comparison:

    coverage      did we observe it, in every city?          (this module)
    divergence    did we observe the *same thing*?           (this module)
    variance      does it discriminate between streets?      (segment_index)

An indicator has to survive all three. They are independent: `s_calming` cleared
coverage at 100% in both cities while failing the other two.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from . import config
from .segment_index import PRESENCE_ONLY, applicability

log = logging.getLogger(__name__)

# An indicator observed on less than this share of street length cannot support
# a cross-city comparison. 0.60 is a judgement call and is stated as one; the
# sensitivity of the comparable set to it is reported by the matrix script.
COMPARABILITY_THRESHOLD = 0.60

# Means differing by more than this factor, where both cities fully observed the
# indicator, are reported as suspect.
RATIO_ALERT = 5.0


def load_coverage(city_keys: list[str] | None = None) -> pd.DataFrame:
    """Concatenate the per-city coverage reports written by 01_build_city.py."""
    paths = sorted(config.OUT_TABLES.glob("*_coverage.csv"))
    if city_keys:
        wanted = {f"{k}_coverage.csv" for k in city_keys}
        paths = [p for p in paths if p.name in wanted]
    if not paths:
        raise SystemExit(
            "No coverage reports found. Run scripts/01_build_city.py <city> first."
        )
    log.info("Reading %d coverage report(s): %s", len(paths), [p.stem for p in paths])
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


def load_segments(city_keys: list[str] | None = None) -> dict[str, gpd.GeoDataFrame]:
    """Load each city's scored segment table from data/processed."""
    frames = {}
    for path in sorted(config.DATA_PROCESSED.glob("*_segments.gpkg")):
        key = path.stem.replace("_segments", "")
        if city_keys and key not in city_keys:
            continue
        frames[key] = gpd.read_file(path)
    return frames


def city_set_mismatch(
    cov: pd.DataFrame, frames: dict[str, gpd.GeoDataFrame]
) -> dict[str, set[str]]:
    """Cities present in one input but not the other.

    The comparable set is derived from the coverage reports; the index is then
    rebuilt over the segment files. Those are two separate globs over two
    directories, and nothing made them agree.

    The dangerous direction is a city with segments but no coverage report: it
    never gets a vote on what is comparable, so the set is computed as though it
    did not exist and then applied to it anyway. Dropping Genova's coverage CSV
    widens the set from {s_highway} to {s_highway, s_speed} -- and s_speed is
    observed on 9.4% of Genova's roads. That is precisely the artefact this
    module exists to prevent, walking back in through a missing file.

    Returned rather than raised: the matrix script reports it, the comparative
    index refuses on it.
    """
    cov_cities = set(cov["city"].unique())
    seg_cities = set(frames)
    return {
        "missing_coverage": seg_cities - cov_cities,
        "missing_segments": cov_cities - seg_cities,
    }


def coverage_matrix(cov: pd.DataFrame) -> pd.DataFrame:
    """Indicator x city matrix of length-weighted coverage, with the verdict.

    ``binding_city`` is the actionable column: it names where fieldwork or a
    national source would buy the most comparability.
    """
    matrix = cov.pivot_table(
        index="indicator", columns="city", values="share_length", aggfunc="first"
    ).sort_index()

    cities = list(matrix.columns)
    matrix["min_coverage"] = matrix[cities].min(axis=1)
    matrix["comparable"] = matrix["min_coverage"] >= COMPARABILITY_THRESHOLD
    matrix["binding_city"] = matrix[cities].idxmin(axis=1)
    return matrix


def divergence(frames: dict[str, gpd.GeoDataFrame]) -> pd.DataFrame | None:
    """Flag indicators observed everywhere that nonetheless disagree wildly.

    Coverage answers "did we observe it?". It does not answer "did we observe
    the same thing?", and the two came apart the moment a second city existed.

    A note on what this check no longer has to catch. It was written because
    `s_calming` was observed on 100% of applicable segments in both cities --
    17.2% of Rotterdam's calmed against 0.6% of Genova's, a 28x gap driven by
    3,806 mapped features against 73. Now that `s_calming` is typed
    presence-only, its non-detections are unobserved rather than zero, so that
    gap surfaces one gate earlier, as a coverage collapse to 17.2% / 0.6%. The
    mean-where-observed is 1.0 in both cities and this screen is silent on it.

    That is the intended outcome, not a regression: the defect is caught by the
    first gate it reaches. The screen stays for the two-sided indicators, where
    it is still the only thing asking the question.
    """
    if len(frames) < 2:
        return None

    cols = sorted({c for d in frames.values() for c in d.columns if c.startswith("s_")})
    rows = []
    for col in cols:
        rec = {"indicator": col}
        for key, d in frames.items():
            has = col in d.columns and d[col].notna().any()
            rec[key] = float(d[col].mean()) if has else float("nan")
        rows.append(rec)

    df = pd.DataFrame(rows).set_index("indicator")
    vals = df.dropna(how="any")
    if vals.empty:
        return None

    lo = vals.min(axis=1)
    hi = vals.max(axis=1)
    # Guard the zero case: a zero mean makes the ratio undefined, and that is
    # itself the signal worth reporting -- handled by the `lo.eq(0.0)` term.
    ratio = hi / lo.replace(0.0, float("nan"))

    out = vals.copy()
    out["ratio"] = ratio
    out["suspect"] = (ratio > RATIO_ALERT) | lo.eq(0.0)
    return out


def detection_rates(frames: dict[str, gpd.GeoDataFrame]) -> pd.DataFrame | None:
    """Cross-city detection rates for presence-only indicators.

    For a presence-only layer this is the honest version of the number that used
    to be reported as prevalence: the share of applicable segments where a
    feature was *found*. It is a lower bound on the real rate, and the gap
    between two cities' rates is a lower bound on nothing at all -- it mixes the
    true difference with the difference in survey effort, and cannot separate
    them without an external estimate of either.

    It is reported rather than dropped because it is the quantity a reader will
    otherwise reconstruct wrongly from the coverage table, and because the size
    of the gap is what justifies refusing the comparison.
    """
    if not frames:
        return None

    rows = []
    for col in sorted(PRESENCE_ONLY):
        rec = {"indicator": col}
        for key, d in frames.items():
            if col not in d.columns:
                rec[key] = float("nan")
                continue
            app = applicability(d).get(col, pd.Series(True, index=d.index))
            n_app = int(app.sum())
            rec[key] = float((d.loc[app, col] == 1.0).sum() / n_app) if n_app else float("nan")
        rows.append(rec)

    out = pd.DataFrame(rows).set_index("indicator")
    cities = [c for c in out.columns]
    if len(cities) >= 2:
        lo, hi = out[cities].min(axis=1), out[cities].max(axis=1)
        out["ratio"] = hi / lo.replace(0.0, float("nan"))
    return out


def comparable_indicators(
    cov: pd.DataFrame,
    div: pd.DataFrame | None = None,
    exclude_suspect: bool = True,
) -> tuple[set[str], dict[str, str]]:
    """The indicator set a cross-city comparison may be built on.

    Returns the surviving indicator names and, for everything excluded, the
    reason -- because "which indicators were dropped and why" is the result
    here, not a diagnostic on the way to one.

    ``exclude_suspect`` implements the split that keeps the divergence screen
    honest. A flagged indicator is not deleted from the project: it stays in
    each city's own descriptive index, where a within-city comparison of like
    with like is unaffected by how another country tags its streets. It is
    barred only from the *comparative* index, where the burden of proof sits.
    A genuine cross-city difference can be large, so an analyst who has the
    external validation to defend one can pass ``False`` and say so in print.
    """
    matrix = coverage_matrix(cov)
    keep, dropped = set(), {}

    for ind, row in matrix.iterrows():
        if not str(ind).startswith("s_"):
            continue  # domain rows are outputs of the indicators, not inputs
        if not bool(row["comparable"]):
            dropped[ind] = (
                f"coverage {row['min_coverage']:.3f} < {COMPARABILITY_THRESHOLD:.2f} "
                f"(binding: {row['binding_city']})"
            )
            continue
        keep.add(ind)

    if exclude_suspect and div is not None:
        for ind in div.index[div["suspect"]]:
            if ind in keep:
                keep.discard(ind)
                dropped[ind] = (
                    f"divergence ratio {div.at[ind, 'ratio']:.1f}x exceeds "
                    f"{RATIO_ALERT:.0f}x -- fully observed in every city yet "
                    f"disagreeing; may encode mapping effort"
                )

    return keep, dropped


def stale_presence_only(frames: dict[str, gpd.GeoDataFrame]) -> list[str]:
    """Cities whose stored segments predate presence-only typing.

    A presence-only indicator can only ever be 1.0 or missing. An observed 0.0
    means the file was written when non-detection was still being scored as
    observed absence, and every number derived from it is the artefact this
    change exists to remove.
    """
    stale = []
    for key, d in frames.items():
        for col in PRESENCE_ONLY:
            if col in d.columns and bool((d[col] == 0.0).any()):
                stale.append(key)
                break
    return stale
