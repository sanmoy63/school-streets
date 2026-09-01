"""Extraction of schools, boundaries and the pedestrian network from OSM.

Everything here is cached to ``data/raw``. Re-running is cheap; the first run
for a city takes a few minutes against the Overpass API.

A note on why OSM is the base layer rather than national registries: the four
pilot cities sit in four different data regimes (NL, FI, SK, AL), and only OSM
covers all four with the same schema. National sources are strictly better where
they exist, and are intended to be layered on top as clearly labelled
enrichment (not yet implemented) -- but the *comparable*
baseline has to come from a source that does not vanish at the Albanian border.
"""

from __future__ import annotations

import logging
import re
import warnings
from pathlib import Path

import geopandas as gpd
import osmnx as ox
import pandas as pd

from .config import DATA_RAW, City, params

log = logging.getLogger(__name__)

# OSMnx caches Overpass responses itself; point it at our data tree so the whole
# cache is removable with one `rm -rf data/`.
ox.settings.use_cache = True
ox.settings.cache_folder = str(DATA_RAW / "osmnx_cache")
ox.settings.log_console = False


def _boundary_path(city: City) -> Path:
    return DATA_RAW / f"{city.key}_boundary.gpkg"


def _schools_path(city: City) -> Path:
    return DATA_RAW / f"{city.key}_schools.gpkg"


def _graph_path(city: City) -> Path:
    return DATA_RAW / f"{city.key}_walk.graphml"


def _use_cache(path: Path, refresh: bool) -> bool:
    """Whether a cached artefact should be read.

    Existence is checked explicitly rather than caught as an exception: pyogrio
    raises ``DataSourceError``, which does not inherit from ``OSError``, so a
    try/except around the read silently fails to catch a missing file.
    """
    return not refresh and path.exists() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Boundary
# ---------------------------------------------------------------------------


def fetch_boundary(city: City, refresh: bool = False) -> gpd.GeoDataFrame:
    """Administrative boundary polygon for the city, in the city's metric CRS."""
    path = _boundary_path(city)
    if _use_cache(path, refresh):
        return gpd.read_file(path)

    log.info("Geocoding boundary for %s", city.place)
    gdf = ox.geocode_to_gdf(city.place)
    gdf = gdf.to_crs(city.crs)
    gdf.to_file(path, driver="GPKG")
    return gdf


# ---------------------------------------------------------------------------
# Schools
# ---------------------------------------------------------------------------


def _looks_like_non_primary(name: object, patterns: list[str]) -> bool:
    """True if a school name matches one of the exclusion patterns.

    OSM's ``isced:level`` tag would be the principled filter, but its coverage
    across our four cities ranges from decent (NL) to near-absent (AL), so name
    matching is used as the fallback. Both are applied; this one only fires when
    the tag is missing.
    """
    # An explicit isinstance check, not a truthiness test: an unnamed school
    # arrives from pandas as NaN, and `bool(nan)` is True -- so a `if not name`
    # guard passes the float straight through to re.search.
    if not isinstance(name, str) or not name:
        return False
    return any(re.search(p, name) for p in patterns)


def fetch_schools(city: City, refresh: bool = False) -> gpd.GeoDataFrame:
    """Schools and kindergartens as points, in the city's metric CRS.

    Polygonal school features are reduced to a representative point *inside* the
    polygon (not the centroid, which can fall outside a concave campus). This
    point is the origin for walkshed routing, so it needs to be snappable to the
    network -- a centroid in the middle of a sports field is not.

    The returned frame carries ``school_id``, ``name``, ``amenity`` and the
    original geometry type, so that the site-scale (schoolyard) analysis can
    recover the polygon where one exists.
    """
    path = _schools_path(city)
    if _use_cache(path, refresh):
        return gpd.read_file(path)

    spec = params("schools")
    log.info("Fetching schools for %s", city.place)

    with warnings.catch_warnings():
        # OSMnx warns about mixed geometry types in features_from_place; that is
        # exactly what we want here (nodes, ways and relations all tagged school).
        warnings.simplefilter("ignore")
        feats = ox.features_from_place(city.place, tags=spec["tags"])

    if feats.empty:
        raise RuntimeError(f"No school features returned for {city.place}")

    feats = feats.to_crs(city.crs)

    # Keep the polygon where we have one -- it is the schoolyard footprint and
    # is needed for the site-scale indicators.
    feats["geom_type"] = feats.geometry.geom_type
    feats["yard_area_m2"] = feats.geometry.area.where(
        feats.geom_type.isin(["Polygon", "MultiPolygon"])
    )

    points = feats.geometry.representative_point()

    name = feats["name"] if "name" in feats.columns else pd.Series(index=feats.index, dtype=object)
    amenity = feats["amenity"] if "amenity" in feats.columns else pd.Series(index=feats.index, dtype=object)
    isced = feats["isced:level"] if "isced:level" in feats.columns else pd.Series(index=feats.index, dtype=object)

    schools = gpd.GeoDataFrame(
        {
            "name": name.values,
            "amenity": amenity.values,
            "isced_level": isced.values,
            "geom_type": feats["geom_type"].values,
            "yard_area_m2": feats["yard_area_m2"].values,
        },
        geometry=points.values,
        crs=city.crs,
    )

    n_raw = len(schools)

    # Drop anything that is clearly tertiary or adult education. Where isced:level
    # exists and excludes primary (level 1) we trust it; otherwise fall back to
    # the name patterns.
    has_isced = schools["isced_level"].notna()
    isced_is_primary = schools["isced_level"].fillna("").astype(str).str.contains(r"\b[01]\b", regex=True)
    name_is_bad = schools["name"].apply(
        lambda n: _looks_like_non_primary(n, spec["exclude_name_patterns"])
    )

    # Name exclusion applies even when ISCED says primary: OSM ISCED tags are
    # frequently copied from a parent relation covering a whole campus, so a
    # secondary school inside a mixed site can inherit isced:level=1.
    keep = (has_isced & isced_is_primary & ~name_is_bad) | (~has_isced & ~name_is_bad)
    excluded = schools.loc[~keep]
    if not excluded.empty:
        log.info(
            "%s: excluded %d non-primary features (e.g. %s)",
            city.key,
            len(excluded),
            ", ".join(excluded["name"].dropna().head(5).astype(str)),
        )
    schools = schools.loc[keep].reset_index(drop=True)

    # Unnamed features cannot be name-filtered, so whatever they are, they
    # survive. Flag rather than drop: some are genuine primary schools, and the
    # count needs to be reportable both ways.
    schools["name_unknown"] = schools["name"].isna()
    n_unnamed = int(schools["name_unknown"].sum())
    if n_unnamed:
        log.info(
            "%s: %d/%d kept features are unnamed (%.0f%%) and bypassed name filtering",
            city.key, n_unnamed, len(schools), 100 * n_unnamed / len(schools),
        )

    schools.insert(0, "school_id", [f"{city.key}_{i:04d}" for i in range(len(schools))])
    schools.to_file(path, driver="GPKG")

    log.info(
        "%s: %d school features -> %d primary/kindergarten after filtering",
        city.key,
        n_raw,
        len(schools),
    )
    return schools


# ---------------------------------------------------------------------------
# Pedestrian network
# ---------------------------------------------------------------------------


def fetch_walk_graph(city: City, refresh: bool = False):
    """Pedestrian network as a projected OSMnx graph.

    Uses OSMnx's ``walk`` filter, which already drops motorways and honours
    ``foot=no``. Service roads are retained deliberately: school access in all
    four cities frequently runs over them, and dropping them silently truncates
    walksheds.
    """
    path = _graph_path(city)
    if _use_cache(path, refresh):
        log.info("Loaded cached walk graph for %s", city.key)
        G = ox.load_graphml(path)
        return ox.project_graph(G, to_crs=city.crs)

    net = params("network")
    log.info("Downloading %s walk network (this takes a few minutes)", city.place)
    G = ox.graph_from_place(city.place, network_type=net["type"], simplify=True)
    ox.save_graphml(G, path)
    return ox.project_graph(G, to_crs=city.crs)


def fetch_traffic_calming(city: City, refresh: bool = False) -> gpd.GeoDataFrame:
    """Traffic-calming features as points, in the city's metric CRS.

    Speed bumps, humps, tables and cushions are tagged on OSM *nodes*, not on
    ways, so they never appear in the edge table returned by ``graph_to_gdfs``.
    Reading calming off the edges yields an all-missing column -- which, if
    filled with zeros, silently asserts that no city in the study has any
    traffic calming at all.

    Returns an empty frame (not an error) where a city genuinely has none
    mapped; the caller distinguishes "queried and absent" from "never queried".
    """
    path = DATA_RAW / f"{city.key}_calming.gpkg"
    if _use_cache(path, refresh):
        return gpd.read_file(path)

    log.info("Fetching traffic calming for %s", city.place)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            feats = ox.features_from_place(city.place, tags={"traffic_calming": True})
    except Exception as exc:  # noqa: BLE001 -- Overpass returns varied errors on empty result sets
        log.warning("traffic calming query failed for %s (%s)", city.key, exc)
        feats = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    if feats.empty:
        log.warning("%s: no traffic calming features found", city.key)
        out = gpd.GeoDataFrame({"calming_type": []}, geometry=[], crs=city.crs)
    else:
        feats = feats.to_crs(city.crs)
        out = gpd.GeoDataFrame(
            {"calming_type": feats.get("traffic_calming", pd.Series(index=feats.index)).astype(str).values},
            geometry=feats.geometry.representative_point().values,
            crs=city.crs,
        )
        log.info("%s: %d traffic calming features", city.key, len(out))

    out.to_file(path, driver="GPKG")
    return out


def graph_to_edges(G) -> gpd.GeoDataFrame:
    """Edges of a projected graph as a GeoDataFrame, one row per segment."""
    edges = ox.graph_to_gdfs(G, nodes=False, edges=True)
    return edges.reset_index()
