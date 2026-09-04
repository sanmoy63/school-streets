"""Map and figure output.

The interactive map is a prototype of an open urban atlas: one self-contained
HTML file, no server, no API keys, openable by a municipal officer who will not
install anything. That constraint drives most of the choices here.
"""

from __future__ import annotations

import logging

import branca.colormap as cm
import folium
import geopandas as gpd
import numpy as np

from .config import OUT_MAPS, City

log = logging.getLogger(__name__)

# Diverging is wrong for this index -- there is no meaningful midpoint, only
# "worse" and "better". A single-hue sequential ramp keeps the reading ordinal
# and stays legible to the ~8% of men with red-green colour vision deficiency,
# which a red-to-green ramp would not.
INDEX_COLORS = ["#4a1486", "#6a51a3", "#807dba", "#9e9ac8", "#bcbddc", "#dadaeb"]


# Fields carried into the HTML. Everything else is dropped before serialising:
# the first version of this map embedded all 30 columns for 83k segments and
# produced a 57 MB file, which defeats the point of a single-file artefact.
TOOLTIP_FIELDS = [
    "name",
    "highway_class",
    "maxspeed_kmh",
    "ssr_index",
    "ssr_index_lo",
    "ssr_index_hi",
    "sidewalk_source",
]


def _simplify_for_web(
    gdf: gpd.GeoDataFrame,
    tolerance_m: float = 5.0,
    keep: list[str] | None = None,
    precision: int = 5,
) -> gpd.GeoDataFrame:
    """Reduce vertex count, columns and coordinate precision before writing HTML.

    Simplification happens in the *projected* CRS so the tolerance means metres;
    simplifying in degrees does not. Coordinates are then rounded to ~1 m, which
    is well inside the positional accuracy of OSM and removes roughly a third of
    the file size on its own.
    """
    # dict.fromkeys de-duplicates while preserving order: a repeated name here
    # yields a duplicated column, after which gdf["col"] returns a DataFrame and
    # every downstream float() call fails.
    requested = list(dict.fromkeys(keep or list(gdf.columns)))
    cols = [c for c in requested if c in gdf.columns]
    out = gdf[cols + ["geometry"]].copy() if "geometry" not in cols else gdf[cols].copy()
    out["geometry"] = out.geometry.simplify(tolerance_m, preserve_topology=True)
    out = out.to_crs(4326)
    out["geometry"] = out.geometry.set_precision(10 ** (-precision))
    return out.loc[~out.geometry.is_empty & out.geometry.notna()]


def school_street_map(
    segments: gpd.GeoDataFrame,
    schools: gpd.GeoDataFrame,
    walksheds: gpd.GeoDataFrame,
    city: City,
    focus_minutes: int = 5,
    roads_only: bool = True,
) -> str:
    """Write a self-contained interactive map. Returns the output path.

    Scope is deliberately narrower than the analysis. The full catchment set is
    83k segments; at that size the single-file map stops opening in a browser,
    which is the one property that makes it useful to a municipal officer. The
    map therefore shows **roads within the 5-minute catchments** -- the streets a
    school-street scheme would actually act on -- while the GeoPackages carry the
    complete network for anyone doing analysis.

    Segments are coloured by the composite index; unscored segments are drawn in
    grey rather than omitted, so gaps in the data read as gaps rather than as
    absent streets.
    """
    from .sidewalks import ROAD_CLASSES

    focus = walksheds.loc[walksheds["minutes"] == focus_minutes]
    if focus.empty:
        raise ValueError(f"No {focus_minutes}-minute walksheds for {city.key}")

    segs = segments.copy()
    if roads_only and "highway_class" in segs.columns:
        segs = segs.loc[segs["highway_class"].isin(ROAD_CLASSES)]

    # Clip to the focus catchment rather than the 10-minute flag used upstream.
    focus_union = focus.dissolve().geometry.iloc[0]
    segs = segs.loc[segs.geometry.intersects(focus_union)]

    if segs.empty:
        raise ValueError(f"No segments to map for {city.key}")

    segs = _simplify_for_web(segs, keep=TOOLTIP_FIELDS + ["ssr_index"])
    sheds10 = _simplify_for_web(
        walksheds.loc[walksheds["minutes"] == focus_minutes], tolerance_m=15.0, keep=["minutes"]
    )
    pts = schools.to_crs(4326)

    centre = [pts.geometry.y.mean(), pts.geometry.x.mean()]
    # Plain OSM tiles, not CartoDB Positron: Positron now requires an API key and
    # renders the basemap as "API KEY REQUIRED" watermarks for anyone opening the
    # file without one, which defeats a single-file artefact meant to be sent to
    # a municipal officer and simply opened.
    m = folium.Map(location=centre, zoom_start=13, tiles="OpenStreetMap",
                   prefer_canvas=True)

    scored = segs["ssr_index"].dropna()
    vmin, vmax = (float(scored.min()), float(scored.max())) if len(scored) else (0.0, 1.0)
    # Dark = low index. The ramp runs dark-to-pale as the score improves so that
    # the streets needing attention are the ones that stand out; on an
    # intervention-targeting map the problem cases should carry the visual weight.
    ramp = cm.LinearColormap(
        INDEX_COLORS, vmin=vmin, vmax=vmax,
        caption="School-street readiness (dark = lower readiness, needs attention)",
    )

    # 10-minute catchments, drawn underneath everything else.
    folium.GeoJson(
        sheds10.to_json(),
        name=f"{focus_minutes}-minute school walksheds",
        style_function=lambda _: {
            "fillColor": "#3182bd", "color": "#3182bd",
            "weight": 0.5, "fillOpacity": 0.06,
        },
    ).add_to(m)

    def style(feature):
        props = feature["properties"]
        v = props.get("ssr_index")
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return {"color": "#bdbdbd", "weight": 1.2, "opacity": 0.5}

        # Intrinsic uncertainty modulation: segments with wide identified intervals (>0.20)
        # receive softer opacity to prevent false certainty
        hi = props.get("ssr_index_hi")
        lo = props.get("ssr_index_lo")
        width = (hi - lo) if (hi is not None and lo is not None and not np.isnan(hi) and not np.isnan(lo)) else 0.0
        opacity = 0.65 if width > 0.20 else 0.90
        weight = 2.0 if width > 0.20 else 2.6
        return {"color": ramp(v), "weight": weight, "opacity": opacity}

    alias_map = {
        "name": "Street",
        "highway_class": "Class",
        "maxspeed_kmh": "Speed (km/h)",
        "ssr_index": "Index",
        "ssr_index_lo": "Lower Bound",
        "ssr_index_hi": "Upper Bound",
        "sidewalk_source": "Sidewalk evidence",
    }
    present_fields = [f for f in TOOLTIP_FIELDS if f in segs.columns]

    folium.GeoJson(
        segs.to_json(),
        name="Street segments",
        style_function=style,
        tooltip=folium.GeoJsonTooltip(
            fields=present_fields,
            aliases=[alias_map.get(f, f) for f in present_fields],
            localize=True,
        ),
    ).add_to(m)

    schools_layer = folium.FeatureGroup(name="Schools")
    for _, row in pts.iterrows():
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=3.5, color="#252525", weight=1,
            fill=True, fillColor="#feb24c", fillOpacity=0.95,
            tooltip=str(row.get("name") or row["school_id"]),
        ).add_to(schools_layer)
    schools_layer.add_to(m)

    ramp.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    path = OUT_MAPS / f"{city.key}_school_streets.html"
    m.save(str(path))
    log.info("map -> %s (%d segments)", path, len(segs))
    return str(path)
