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


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*", help="city keys; default = all available")
    args = ap.parse_args()
    main(args.cities or None)
