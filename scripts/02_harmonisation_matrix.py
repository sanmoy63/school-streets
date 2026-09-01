"""Build the cross-city harmonisation matrix from per-city coverage reports.

Usage
-----
    python scripts/02_harmonisation_matrix.py                # all cities found
    python scripts/02_harmonisation_matrix.py rotterdam espoo

Reads every ``outputs/tables/<city>_coverage.csv`` produced by
``01_build_city.py`` and answers the question the comparative analysis depends
on: **which indicators are observable in enough of every city to carry a
cross-city claim?**

An indicator is `comparable` when its length-weighted coverage clears a
threshold in *all* cities under comparison. Anything below that is reported as
city-specific enrichment. This is the deliberate refusal at the centre of the
method: a comparison is only as strong as its weakest site, and picking the
threshold openly is better than letting missingness pick it silently.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from routes_ssr import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("harmonise")

# An indicator observed on less than this share of street length cannot support
# a cross-city comparison. 0.60 is a judgement call and is stated as one; the
# sensitivity of the comparable set to this threshold is reported below.
COMPARABILITY_THRESHOLD = 0.60


def load_coverage(city_keys: list[str] | None) -> pd.DataFrame:
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


def main(city_keys: list[str] | None) -> None:
    config.ensure_dirs()
    cov = load_coverage(city_keys)

    # Indicator x city matrix of length-weighted coverage.
    matrix = cov.pivot_table(
        index="indicator", columns="city", values="share_length", aggfunc="first"
    ).sort_index()

    cities_present = list(matrix.columns)
    matrix["min_coverage"] = matrix[cities_present].min(axis=1)
    matrix["comparable"] = matrix["min_coverage"] >= COMPARABILITY_THRESHOLD

    # Which city is the binding constraint for each indicator? This is the
    # actionable column: it says where fieldwork or a national source would buy
    # the most comparability.
    matrix["binding_city"] = matrix[cities_present].idxmin(axis=1)

    out_path = config.OUT_TABLES / "harmonisation_matrix.csv"
    matrix.round(3).to_csv(out_path)

    # Threshold sensitivity: how many indicators survive at each cut.
    sens = pd.DataFrame(
        {
            "threshold": [t / 100 for t in range(30, 100, 10)],
        }
    )
    sens["n_comparable"] = [
        int((matrix["min_coverage"] >= t).sum()) for t in sens["threshold"]
    ]
    sens.to_csv(config.OUT_TABLES / "harmonisation_sensitivity.csv", index=False)

    n_comp = int(matrix["comparable"].sum())
    log.info("--- harmonisation matrix ---")
    log.info("cities: %s", ", ".join(cities_present))
    log.info(
        "%d/%d indicators comparable at threshold %.2f",
        n_comp, len(matrix), COMPARABILITY_THRESHOLD,
    )
    if n_comp < len(matrix):
        dropped = matrix.loc[~matrix["comparable"]]
        for ind, row in dropped.iterrows():
            log.info(
                "  dropped: %-18s min coverage %.2f (binding: %s)",
                ind, row["min_coverage"], row["binding_city"],
            )
    log.info("-> %s", out_path)

    if len(cities_present) == 1:
        log.warning(
            "Only one city present -- the matrix is not yet a comparison. "
            "Run the remaining pilot cities before quoting these numbers."
        )

    div = divergence_check(city_keys)
    if div is not None:
        div.round(4).to_csv(config.OUT_TABLES / "harmonisation_divergence.csv")
        log.info("--- value divergence (coverage alone does not prove comparability) ---")
        log.info("\n%s", div.round(4).to_string())
        flagged = div.index[div["suspect"]].tolist()
        if flagged:
            log.warning(
                "%d indicator(s) fully observed in every city but with means "
                "differing by more than %.0fx: %s",
                len(flagged), RATIO_ALERT, ", ".join(flagged),
            )
            log.warning(
                "  These pass the coverage gate yet may encode mapping effort "
                "rather than street conditions. Do not report them comparatively "
                "without external validation."
            )


def divergence_check(city_keys: list[str] | None) -> pd.DataFrame | None:
    """Flag indicators that are fully observed everywhere yet disagree wildly.

    Coverage answers "did we observe it?". It does not answer "did we observe
    the same thing?", and the two came apart immediately once a second city
    existed.

    `s_calming` is observed on 100% of applicable segments in both Rotterdam and
    Genova, so the coverage gate passes it as comparable. But 17.2% of Rotterdam
    segments are calmed against 0.6% of Genova's -- a 28x gap. Rotterdam has
    3,806 mapped calming features; Genova has 73. Italian streets are not 28x
    less calmed than Dutch ones; Dutch OSM contributors map speed bumps and
    Italian ones largely do not.

    Left unchecked, that mapping-effort gradient enters the composite as a
    substantive finding, which is precisely the error this project exists to
    avoid -- arriving here one level up, in the comparability test itself.

    So: for each indicator observed in every city, compare the mean where
    observed. A ratio beyond `RATIO_ALERT` is reported as suspect. This is a
    screen, not a verdict; a genuine cross-city difference can be large. It puts
    the burden of proof on the analyst rather than letting the number pass
    silently.
    """
    frames = {}
    for path in sorted(config.DATA_PROCESSED.glob("*_segments.gpkg")):
        key = path.stem.replace("_segments", "")
        if city_keys and key not in city_keys:
            continue
        frames[key] = gpd.read_file(path)

    if len(frames) < 2:
        return None

    cols = sorted({c for d in frames.values() for c in d.columns if c.startswith("s_")})
    rows = []
    for col in cols:
        rec = {"indicator": col}
        for key, d in frames.items():
            rec[key] = float(d[col].mean()) if col in d.columns and d[col].notna().any() else float("nan")
        rows.append(rec)

    df = pd.DataFrame(rows).set_index("indicator")
    vals = df.dropna(how="any")
    if vals.empty:
        return None

    lo = vals.min(axis=1)
    hi = vals.max(axis=1)
    # Guard the zero case: a zero mean makes the ratio undefined, and that is
    # itself the signal worth reporting -- handled by the `lo.eq(0.0)` term below.
    ratio = hi / lo.replace(0.0, float("nan"))

    out = vals.copy()
    out["ratio"] = ratio
    out["suspect"] = (ratio > RATIO_ALERT) | lo.eq(0.0)
    return out


RATIO_ALERT = 5.0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*", help="city keys; default = all available")
    args = ap.parse_args()
    main(args.cities or None)
