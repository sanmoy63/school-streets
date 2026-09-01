"""Configuration loading and shared paths.

Every threshold that affects a published number lives in ``config/cities.yml``,
not in code. This module is the single place that reads it.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# Repo root, resolved from this file's location so that scripts work regardless
# of the directory they are invoked from.
ROOT = Path(__file__).resolve().parents[2]

CONFIG_PATH = ROOT / "config" / "cities.yml"

DATA_RAW = ROOT / "data" / "raw"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"

OUT_FIGURES = ROOT / "outputs" / "figures"
OUT_MAPS = ROOT / "outputs" / "maps"
OUT_TABLES = ROOT / "outputs" / "tables"

_ALL_DIRS = (
    DATA_RAW,
    DATA_INTERIM,
    DATA_PROCESSED,
    OUT_FIGURES,
    OUT_MAPS,
    OUT_TABLES,
)


def ensure_dirs() -> None:
    """Create the data and output tree if it is missing."""
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class City:
    """A single study area.

    Attributes
    ----------
    key:
        Short lowercase identifier, used in every filename.
    place:
        Nominatim-resolvable place string.
    epsg:
        Projected CRS for metric work. All lengths, buffers and densities are
        computed in this CRS; nothing metric happens in EPSG:4326.
    status:
        ``"pilot"`` for the four living-lab cities, ``"observer"`` otherwise.
    """

    key: str
    place: str
    country: str
    epsg: int
    status: str
    notes: str = ""

    @property
    def crs(self) -> str:
        return f"EPSG:{self.epsg}"


@functools.lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Read ``config/cities.yml``. Cached: the file is read once per process."""
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.lru_cache(maxsize=1)
def cities(include_observers: bool = False) -> dict[str, City]:
    """Return the study areas keyed by short name.

    Observer cities are excluded by default -- they are an out-of-sample check
    run at the end, not part of the main pipeline.
    """
    cfg = load_config()
    out: dict[str, City] = {}
    groups = ["pilots"] + (["observers"] if include_observers else [])
    for group in groups:
        for key, spec in (cfg.get(group) or {}).items():
            out[key] = City(
                key=key,
                place=spec["place"],
                country=spec["country"],
                epsg=int(spec["epsg"]),
                status=spec.get("status", group.rstrip("s")),
                notes=spec.get("notes", "") or "",
            )
    return out


def get_city(key: str) -> City:
    """Look up one city by key, searching observers too."""
    all_cities = {**cities(include_observers=True)}
    try:
        return all_cities[key]
    except KeyError:
        known = ", ".join(sorted(all_cities))
        raise KeyError(f"Unknown city {key!r}. Known cities: {known}") from None


def params(section: str) -> dict[str, Any]:
    """Return one analysis-parameter block from the config."""
    cfg = load_config()
    if section not in cfg:
        raise KeyError(f"No {section!r} section in {CONFIG_PATH}")
    return cfg[section]
