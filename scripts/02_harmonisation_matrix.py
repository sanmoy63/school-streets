"""Build the cross-city harmonisation matrix from per-city coverage reports.

Usage
-----
    python scripts/02_harmonisation_matrix.py                # all cities found
    python scripts/02_harmonisation_matrix.py rotterdam genova

Reads every ``outputs/tables/<city>_coverage.csv`` produced by
``01_build_city.py`` and answers the question the comparative analysis depends
on: **which indicators are observable in enough of every city to carry a
cross-city claim?**

An indicator is `comparable` when its length-weighted coverage clears a
threshold in *all* cities under comparison. Anything below that is reported as
city-specific enrichment. This is the deliberate refusal at the centre of the
method: a comparison is only as strong as its weakest site, and picking the
threshold openly is better than letting missingness pick it silently.

This script only *reports* the verdict. ``02b_comparative_index.py`` applies it.
The decision logic itself lives in ``routes_ssr.harmonise`` so that the script
which reports the verdict and the script which obeys it cannot drift apart --
they did, for the whole of the two-city run, and that drift is what produced a
0.221 gap between Rotterdam and Genova out of nothing but missing data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from routes_ssr import config, harmonise  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("harmonise")


def main(city_keys: list[str] | None) -> None:
    config.ensure_dirs()
    cov = harmonise.load_coverage(city_keys)
    matrix = harmonise.coverage_matrix(cov)
    cities_present = [c for c in matrix.columns if c in set(cov["city"])]

    out_path = config.OUT_TABLES / "harmonisation_matrix.csv"
    matrix.round(3).to_csv(out_path)

    # Threshold sensitivity: how many indicators survive at each cut.
    sens = pd.DataFrame({"threshold": [t / 100 for t in range(30, 100, 10)]})
    sens["n_comparable"] = [
        int((matrix["min_coverage"] >= t).sum()) for t in sens["threshold"]
    ]
    sens.to_csv(config.OUT_TABLES / "harmonisation_sensitivity.csv", index=False)

    n_comp = int(matrix["comparable"].sum())
    log.info("--- harmonisation matrix ---")
    log.info("cities: %s", ", ".join(cities_present))
    log.info(
        "%d/%d indicators comparable at threshold %.2f",
        n_comp, len(matrix), harmonise.COMPARABILITY_THRESHOLD,
    )
    if n_comp < len(matrix):
        for ind, row in matrix.loc[~matrix["comparable"]].iterrows():
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

    frames = harmonise.load_segments(city_keys)

    stale = harmonise.stale_presence_only(frames)
    if stale:
        log.error(
            "stored segments for %s predate presence-only typing: a "
            "presence-only indicator is scored 0.0 somewhere, which means "
            "non-detection is still being counted as observed absence. "
            "Re-run 01_build_city.py for those cities before quoting anything.",
            ", ".join(sorted(stale)),
        )

    det = harmonise.detection_rates(frames)
    if det is not None and not det.empty:
        det.round(4).to_csv(config.OUT_TABLES / "harmonisation_detection.csv")
        log.info("--- presence-only detection rates (lower bounds, not prevalence) ---")
        log.info("\n%s", det.round(4).to_string())

    div = harmonise.divergence(frames)
    if div is not None:
        div.round(4).to_csv(config.OUT_TABLES / "harmonisation_divergence.csv")
        log.info("--- value divergence (coverage alone does not prove comparability) ---")
        log.info("\n%s", div.round(4).to_string())
        flagged = div.index[div["suspect"]].tolist()
        if flagged:
            log.warning(
                "%d indicator(s) fully observed in every city but with means "
                "differing by more than %.0fx: %s",
                len(flagged), harmonise.RATIO_ALERT, ", ".join(flagged),
            )
            log.warning(
                "  These pass the coverage gate yet may encode mapping effort "
                "rather than street conditions. Do not report them comparatively "
                "without external validation."
            )

    keep, dropped = harmonise.comparable_indicators(cov, div)
    log.info("--- indicator set available to a comparative index ---")
    log.info("  usable : %s", ", ".join(sorted(keep)) or "(none)")
    for ind, why in sorted(dropped.items()):
        log.info("  dropped: %-14s %s", ind, why)
    log.info("Apply this set with: python scripts/02b_comparative_index.py %s",
             " ".join(cities_present))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*", help="city keys; default = all available")
    args = ap.parse_args()
    main(args.cities or None)
