"""Site-scale schoolyard analysis for one city.

Usage
-----
    python scripts/04_schoolyards.py rotterdam

Writes:
    data/processed/<city>_yards.gpkg      yard polygons with site indicators
    outputs/tables/<city>_yard_coverage.csv

Depends on ``01_build_city.py`` having run (it needs the walk network, which is
cached from that run).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from routes_ssr import config, osm_extract, schoolyards  # noqa: E402
from routes_ssr.segment_index import HIGHWAY_SCORE  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("yards")


def main(city_key: str, refresh: bool = False) -> None:
    config.ensure_dirs()
    city = config.get_city(city_key)
    log.info("=== schoolyards: %s ===", city.place)

    schools = osm_extract.fetch_schools(city, refresh=refresh)
    yards = osm_extract.fetch_yards(city, refresh=refresh)
    log.info("schools: %d, of which %d have a yard polygon (%.0f%%)",
             len(schools), len(yards), 100 * len(yards) / len(schools))

    G = osm_extract.fetch_walk_graph(city)
    edges = osm_extract.graph_to_edges(G)
    edges["highway_class"] = edges.get("highway").map(
        lambda v: v[0] if isinstance(v, (list, tuple)) and v else v
    )

    out = schoolyards.build_yard_indicators(yards, edges, HIGHWAY_SCORE)
    out.to_file(config.DATA_PROCESSED / f"{city.key}_yards.gpkg", driver="GPKG")

    # --- coverage ---------------------------------------------------------
    rows = []
    measured = ["yard_area_m2", "yard_perimeter_m", "yard_compactness",
                "frontage_score", "frontage_share_road"]
    for col in measured:
        obs = out[col].notna()
        rows.append({"city": city.key, "indicator": col, "kind": "measured",
                     "share": round(float(obs.mean()), 4),
                     "n_observed": int(obs.sum()), "n_yards": len(out)})
    for col, tag in schoolyards.UNMEASURABLE_INTERIOR.items():
        rows.append({"city": city.key, "indicator": col, "kind": f"unmeasurable ({tag})",
                     "share": 0.0, "n_observed": 0, "n_yards": len(out)})
    cov = pd.DataFrame(rows)
    cov.to_csv(config.OUT_TABLES / f"{city.key}_yard_coverage.csv", index=False)

    # --- report -----------------------------------------------------------
    log.info("--- yard form ---")
    log.info("  area m2      median %8.0f   IQR %.0f-%.0f",
             out["yard_area_m2"].median(),
             out["yard_area_m2"].quantile(.25), out["yard_area_m2"].quantile(.75))
    log.info("  compactness  median %8.3f   (1.0 = circle)", out["yard_compactness"].median())

    log.info("--- frontage exposure ---")
    fs = out["frontage_score"].dropna()
    if not fs.empty:
        log.info("  scored %d/%d yards; mean %.3f  p10 %.3f  p90 %.3f",
                 len(fs), len(out), fs.mean(), fs.quantile(.10), fs.quantile(.90))
        log.info("  perimeter fronting a mapped road: mean %.1f%%",
                 100 * out["frontage_share_road"].mean())
        worst = out["frontage_worst"].value_counts()
        log.info("  worst-class on boundary (top 6):")
        for cls, n in worst.head(6).items():
            log.info("    %-16s %4d yards (%.0f%%)", cls, n, 100 * n / len(out))

        hostile = out.loc[out["frontage_score"] < 0.4]
        log.info("  yards with frontage score < 0.40: %d (%.0f%%)",
                 len(hostile), 100 * len(hostile) / len(out))
        if not hostile.empty:
            log.info("  most exposed:")
            for _, r in hostile.nsmallest(5, "frontage_score").iterrows():
                log.info("    %-42s score %.3f  worst %s",
                         str(r.get("name"))[:42], r["frontage_score"], r["frontage_worst"])

    log.info("coverage -> outputs/tables/%s_yard_coverage.csv", city.key)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    main(args.city, refresh=args.refresh)
