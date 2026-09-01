"""Regenerate the interactive map from processed data, without rebuilding.

Usage
-----
    python scripts/03_map.py rotterdam
    python scripts/03_map.py rotterdam --minutes 10 --all-classes

Separated from ``01_build_city.py`` so that map design can be iterated in
seconds rather than re-running the walkshed computation each time.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402

from routes_ssr import config, viz  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("map")


def main(city_key: str, minutes: int, roads_only: bool) -> None:
    config.ensure_dirs()
    city = config.get_city(city_key)

    paths = {
        name: config.DATA_PROCESSED / f"{city.key}_{name}.gpkg"
        for name in ("segments", "schools", "walksheds")
    }
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise SystemExit(
            "Missing processed data:\n  "
            + "\n  ".join(missing)
            + f"\nRun: python scripts/01_build_city.py {city.key}"
        )

    layers = {k: gpd.read_file(p) for k, p in paths.items()}
    out = viz.school_street_map(
        layers["segments"],
        layers["schools"],
        layers["walksheds"],
        city,
        focus_minutes=minutes,
        roads_only=roads_only,
    )
    size_mb = Path(out).stat().st_size / 1e6
    log.info("map: %s (%.1f MB)", out, size_mb)
    if size_mb > 25:
        log.warning(
            "Map is %.0f MB -- browsers struggle past ~25 MB. Narrow the focus "
            "(--minutes 5) or keep roads_only.", size_mb
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("city")
    ap.add_argument("--minutes", type=int, default=5,
                    help="walkshed threshold to focus on (default 5)")
    ap.add_argument("--all-classes", action="store_true",
                    help="include footways, not just roads")
    args = ap.parse_args()
    main(args.city, args.minutes, roads_only=not args.all_classes)
