"""Build the published atlas: one self-contained page for GitHub Pages.

Usage
-----
    python scripts/06_atlas.py                 # every city with processed data
    python scripts/06_atlas.py rotterdam genova

Writes ``docs/index.html``, which GitHub Pages serves from the `main` branch.

Why this is not the same map as `03_map.py`
-------------------------------------------
`03_map.py` renders every street in the 5-minute catchments. That is the right
artefact for someone doing analysis, and it is ~8 MB per city -- fine on disk,
unacceptable in git history, where it would be rewritten in full on every run.

The published atlas makes a different trade. Measured GeoJSON sizes:

    schools                     ~60 KB per city
    10-min walksheds (t=50 m)  ~200 KB per city
    streets with index < 0.20  ~740 KB per city
    ALL catchment streets      ~11 MB per city   <- excluded

Restricting the street layer to the worst-scoring segments is not only a size
decision. Those are the streets a school-street scheme would actually act on, so
the published map answers "where would you intervene?" rather than "what does
every street score?". The complete network stays in the GeoPackages.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from routes_ssr import config  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
log = logging.getLogger("atlas")

WORST_INDEX = 0.20      # streets at or below this are drawn
SHED_TOLERANCE_M = 50   # walkshed simplification
SEG_TOLERANCE_M = 15


def _geojson(gdf: gpd.GeoDataFrame, cols: list[str], tol: float, prec: int = 5) -> dict:
    """Trim, simplify and reproject a layer for the web."""
    keep = [c for c in cols if c in gdf.columns]
    g = gdf[keep + ["geometry"]].copy()
    if tol:
        g["geometry"] = g.geometry.simplify(tol, preserve_topology=True)
    g = g.to_crs(4326)
    g["geometry"] = g.geometry.set_precision(10 ** (-prec))
    # `.notna()` alone no longer excludes empty geometries in GeoPandas 1.x.
    g = g.loc[~g.geometry.is_empty & g.geometry.notna()]
    for c in keep:
        if g[c].dtype == object:
            g[c] = g[c].astype(str).replace({"nan": None, "None": None})
    return json.loads(g.to_json())


def build_city(key: str) -> dict | None:
    proc = config.DATA_PROCESSED
    need = proc / f"{key}_schools.gpkg"
    if not need.exists():
        log.warning("%s: no processed data, skipping", key)
        return None

    city = config.get_city(key)
    schools = gpd.read_file(proc / f"{key}_schools.gpkg")
    sheds = gpd.read_file(proc / f"{key}_walksheds.gpkg")
    segs = gpd.read_file(proc / f"{key}_segments.gpkg")

    # Population, where it has been computed. Absent is left absent rather than
    # filled with zero -- the recurring rule in this project.
    pop_path = config.OUT_TABLES / f"{key}_population.csv"
    if pop_path.exists():
        pop = pd.read_csv(pop_path)
        pop = pop[(pop["minutes"] == 10) & (pop["half_width_m"] == 40.0)]
        schools = schools.merge(
            pop[["school_id", "pop_reachable", "pop_reach_ratio"]],
            on="school_id", how="left",
        )
    else:
        log.warning("%s: no population table; schools will size uniformly", key)
        schools["pop_reachable"] = None
        schools["pop_reach_ratio"] = None

    shed10 = sheds[sheds["minutes"] == 10]
    worst = segs[
        segs["in_school_catchment"]
        & segs["in_analysis_set"]
        & (segs["ssr_index"] <= WORST_INDEX)
    ].copy()
    import numpy as np
    worst["status"] = np.where(worst["ssr_index_hi"] <= WORST_INDEX, "confirmed", "candidate")
    worst["ci_width"] = (worst["ssr_index_hi"] - worst["ssr_index_lo"]).round(3)

    centre = schools.to_crs(4326)
    payload = {
        "name": city.place,
        "centre": [float(centre.geometry.y.mean()), float(centre.geometry.x.mean())],
        "schools": _geojson(
            schools,
            ["school_id", "name", "reach_ratio_10", "pop_reachable", "pop_reach_ratio"],
            tol=0,
        ),
        "walksheds": _geojson(shed10, ["school_id"], tol=SHED_TOLERANCE_M, prec=4),
        "worst": _geojson(
            worst,
            ["name", "highway_class", "ssr_index", "ssr_index_lo", "ssr_index_hi", "status", "ci_width"],
            tol=SEG_TOLERANCE_M,
        ),
        "stats": {
            "schools": int(len(schools)),
            "segments": int(len(segs)),
            "worst_n": int(len(worst)),
            "confirmed_n": int((worst["status"] == "confirmed").sum()),
            "candidate_n": int((worst["status"] == "candidate").sum()),
            "reach_mean": _safe_mean(sheds.loc[sheds["minutes"] == 10, "reach_ratio"]),
            "pop_median": _safe_median(schools["pop_reachable"]),
            "pop_reach_mean": _safe_mean(schools["pop_reach_ratio"]),
        },
        "takeaway": (
            "In Rotterdam, tag completeness is high: 100% of candidate streets (560) are confirmed infrastructure priorities. "
            "Sidewalks exist primarily as separate footway geometries (3,551 km of roads vs 0 km inline tags)."
            if key == "rotterdam"
            else
            "In Genova, 70% of candidate streets (1,679 of 2,406) are data-deficient candidates rather than confirmed failures, "
            "due to missing speed limits (9.4% tagged) and sidewalk tags. Vertical topography drops walkable school reach to 0.296."
        ),
        "audit_insight": (
            "Rotterdam imagery audit shows 0% open coverage on sampled untagged links (Wilson 95% bound ≤ 2.1%). "
            "Dense municipal cycleway and separate footway mapping prevents conflation errors."
            if key == "rotterdam"
            else
            "Genova imagery audit shows 0% open coverage on sampled untagged links (Wilson 95% bound ≤ 2.4%). "
            "Car cameras cannot access historic pedestrian creuse, alleys, and stairways where children actually walk."
        ),
    }
    log.info(
        "%s: %d schools, %d worst streets (%d confirmed, %d candidate), reach %.3f",
        key, len(schools), len(worst),
        payload["stats"]["confirmed_n"], payload["stats"]["candidate_n"],
        payload["stats"]["reach_mean"] or float("nan"),
    )
    return payload


def _safe_mean(s) -> float | None:
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    return round(float(s.mean()), 3) if len(s) else None


def _safe_median(s) -> float | None:
    s = pd.to_numeric(pd.Series(s), errors="coerce").dropna()
    return round(float(s.median())) if len(s) else None


def main(keys: list[str] | None) -> None:
    config.ensure_dirs()
    available = sorted(p.stem.replace("_schools", "")
                       for p in config.DATA_PROCESSED.glob("*_schools.gpkg"))
    keys = keys or available
    if not keys:
        raise SystemExit("No processed cities. Run scripts/01_build_city.py <city> first.")

    cities = {}
    for k in keys:
        payload = build_city(k)
        if payload:
            cities[k] = payload
    if not cities:
        raise SystemExit("Nothing to publish.")

    docs = Path(__file__).resolve().parents[1] / "docs"
    docs.mkdir(exist_ok=True)
    out = docs / "index.html"
    out.write_text(_render(cities), encoding="utf-8")

    mb = out.stat().st_size / 1e6
    log.info("atlas -> %s (%.1f MB, %d cities)", out, mb, len(cities))
    if mb > 8:
        log.warning("Atlas is %.1f MB. GitHub serves it, but consider raising "
                    "WORST_INDEX or SHED_TOLERANCE_M.", mb)


def _render(cities: dict) -> str:
    data = json.dumps(cities, separators=(",", ":"))
    first = next(iter(cities))
    buttons = "".join(
        f'<button data-city="{k}"{" class=on" if k == first else ""}>{v["name"].split(",")[0]}</button>'
        for k, v in cities.items()
    )
    return _TEMPLATE.replace("__DATA__", data).replace("__BUTTONS__", buttons).replace("__FIRST__", first)


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>School-Street Readiness Atlas | Comparative Multi-City Study</title>
<meta name="description" content="Open urban atlas evaluating school-street intervention readiness, spatial data observability, and topological severance in Rotterdam and Genoa.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 :root {
   --primary: #4a1486;
   --primary-light: #7048a6;
   --primary-subtle: #f3effa;
   --confirmed-red: #b30000;
   --confirmed-bg: #fdf2f2;
   --candidate-amber: #d97706;
   --candidate-bg: #fffbeb;
   --shed-blue: #2563eb;
   --slate-50: #f8fafc;
   --slate-100: #f1f5f9;
   --slate-200: #e2e8f0;
   --slate-600: #475569;
   --slate-800: #1e293b;
   --slate-900: #0f172a;
 }
 * { box-sizing: border-box; margin: 0; padding: 0; }
 html, body { height: 100%; font-family: 'Plus Jakarta Sans', system-ui, sans-serif; color: var(--slate-900); background: #fff; }
 #wrap { display: flex; height: 100%; position: relative; overflow: hidden; }
 #side {
   width: 420px; min-width: 420px; height: 100%; overflow-y: auto; padding: 24px;
   background: var(--slate-50); border-right: 1px solid var(--slate-200);
   display: flex; flex-direction: column; gap: 20px; z-index: 10;
   box-shadow: 2px 0 12px rgba(0,0,0,0.03);
 }
 #map-container { flex: 1; position: relative; height: 100%; }
 #map { width: 100%; height: 100%; }

 .badge {
   display: inline-flex; align-items: center; gap: 5px;
   font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em;
   padding: 4px 8px; border-radius: 9999px; background: var(--primary-subtle); color: var(--primary);
   width: fit-content;
 }
 h1 { font-size: 22px; font-weight: 800; color: var(--slate-900); line-height: 1.25; }
 .sub { font-size: 13px; color: var(--slate-600); line-height: 1.55; }

 .segmented-control {
   display: flex; background: var(--slate-200); padding: 3px; border-radius: 8px; gap: 3px;
 }
 .segmented-control button {
   flex: 1; border: none; padding: 8px 14px; font-family: inherit; font-size: 13px; font-weight: 600;
   border-radius: 6px; cursor: pointer; background: transparent; color: var(--slate-600);
   transition: all 0.2s ease;
 }
 .segmented-control button.on {
   background: #fff; color: var(--primary); box-shadow: 0 1px 3px rgba(0,0,0,0.1);
 }

 .card {
   background: #fff; border: 1px solid var(--slate-200); border-radius: 10px; padding: 16px;
   box-shadow: 0 1px 3px rgba(0,0,0,0.02);
 }
 .card-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--slate-600); margin-bottom: 12px; }

 .grid-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
 .stat-box { padding: 10px 12px; border-radius: 8px; background: var(--slate-50); border: 1px solid var(--slate-200); }
 .stat-val { font-size: 18px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }
 .stat-lbl { font-size: 11px; color: var(--slate-600); margin-top: 2px; }

 .stat-box.confirmed { background: var(--confirmed-bg); border-color: rgba(179,0,0,0.2); }
 .stat-box.confirmed .stat-val { color: var(--confirmed-red); }
 .stat-box.candidate { background: var(--candidate-bg); border-color: rgba(217,119,6,0.2); }
 .stat-box.candidate .stat-val { color: var(--candidate-amber); }

 table.stats-table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
 table.stats-table td { padding: 5px 0; border-bottom: 1px solid var(--slate-100); }
 table.stats-table td.k { color: var(--slate-600); }
 table.stats-table td.v { text-align: right; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

 .legend-item { display: flex; align-items: flex-start; gap: 10px; font-size: 12px; line-height: 1.4; margin-bottom: 10px; }
 .legend-icon { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; margin-top: 2px; }
 .legend-bar { height: 8px; width: 100%; background: linear-gradient(90deg,#4a1486,#807dba,#dadaeb); border-radius: 2px; margin: 6px 0 2px; }
 .legend-ends { display: flex; justify-content: space-between; font-size: 10px; color: var(--slate-600); font-family: 'JetBrains Mono', monospace; }

 .note {
   font-size: 12px; line-height: 1.5; color: var(--slate-600);
   background: #fff; border-left: 3px solid var(--primary); padding: 10px 12px; border-radius: 0 6px 6px 0;
   border-top: 1px solid var(--slate-200); border-right: 1px solid var(--slate-200); border-bottom: 1px solid var(--slate-200);
 }
 .note.warn { border-left-color: #e11d48; }
 .note b { color: var(--slate-900); }

 .floating-controls {
   position: absolute; top: 16px; right: 16px; z-index: 1000;
   background: rgba(255, 255, 255, 0.92); backdrop-filter: blur(8px);
   border: 1px solid rgba(0,0,0,0.1); border-radius: 10px; padding: 8px 12px;
   box-shadow: 0 4px 14px rgba(0,0,0,0.08); display: flex; flex-direction: column; gap: 6px;
 }
 .filter-title { font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--slate-600); }
 .filter-btns { display: flex; gap: 4px; }
 .filter-chip {
   padding: 4px 8px; font-size: 11px; font-weight: 600; border-radius: 6px; border: 1px solid var(--slate-200);
   background: #fff; cursor: pointer; transition: all 0.15s ease;
 }
 .filter-chip.active { background: var(--slate-800); color: #fff; border-color: var(--slate-800); }

 .inspector-card {
   position: absolute; bottom: 20px; right: 20px; z-index: 1000; width: 310px;
   background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(8px);
   border: 1px solid rgba(0,0,0,0.1); border-radius: 10px; padding: 14px;
   box-shadow: 0 10px 25px rgba(0,0,0,0.1); display: none; font-size: 12px;
 }
 .inspector-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
 .inspector-name { font-weight: 700; font-size: 13px; color: var(--slate-900); }
 .inspector-badge { font-size: 10px; font-weight: 700; padding: 2px 6px; border-radius: 4px; }

 .interval-bar-bg { height: 6px; background: var(--slate-200); border-radius: 3px; position: relative; margin: 8px 0; }
 .interval-bar-fill { position: absolute; height: 100%; border-radius: 3px; }

 a { color: var(--primary); text-decoration: none; font-weight: 600; }
 a:hover { text-decoration: underline; }

 @media(max-width: 860px) {
   #wrap { flex-direction: column; }
   #side { width: 100%; min-width: 0; height: 48%; border-right: none; border-bottom: 1px solid var(--slate-200); }
   #map-container { height: 52%; }
   .floating-controls { top: 10px; right: 10px; }
 }
</style>
</head>
<body>
<div id="wrap">
 <div id="side">
  <div>
   <div class="badge">Open Urban Atlas &middot; Spatial Data Observability</div>
   <h1 style="margin-top: 8px;">School-Street Readiness Atlas</h1>
   <p class="sub" style="margin-top: 6px;">
    Evaluating school access in contrasting topographies: flat <b>Rotterdam</b> versus vertical <b>Genova</b>.
    Comparing local priorities against data-observability bounds to prevent missing-data bias.
   </p>
  </div>

  <div class="segmented-control" id="city-buttons">
   __BUTTONS__
  </div>

  <div class="card" style="background:var(--primary-subtle); border-color:rgba(74,20,134,0.15);">
   <div style="font-size:11px; font-weight:800; text-transform:uppercase; color:var(--primary); margin-bottom:4px;" id="city-takeaway-title">City Synthesis</div>
   <p style="font-size:12px; line-height:1.5; color:var(--slate-800);" id="city-takeaway-body">&mdash;</p>
   <div style="font-size:11px; line-height:1.45; color:var(--slate-600); margin-top:8px; border-top:1px dashed rgba(74,20,134,0.2); padding-top:6px;" id="city-audit-body">&mdash;</div>
  </div>

  <div class="card">
   <div class="card-title">City Network Overview</div>
   <div class="grid-stats">
    <div class="stat-box confirmed">
     <div class="stat-val" id="stat-confirmed">&mdash;</div>
     <div class="stat-lbl">Confirmed Priorities<br><span style="font-size:9px;color:#991b1b;">Upper bound &le; 0.20</span></div>
    </div>
    <div class="stat-box candidate">
     <div class="stat-val" id="stat-candidate">&mdash;</div>
     <div class="stat-lbl">Data-Deficient Candidates<br><span style="font-size:9px;color:#92400e;">Missing tags (audit needed)</span></div>
    </div>
   </div>
   <table class="stats-table" id="stats-detail"></table>
  </div>

  <div class="card">
   <div class="card-title">Reading the Map</div>
   <div class="legend-item">
    <div class="legend-icon" style="background:#b30000;"></div>
    <div>
     <b style="color:#b30000;">Confirmed Intervention Priorities</b><br>
     <span class="sub">ssr_index_hi &le; 0.20 &mdash; definitive infrastructure deficit regardless of unobserved tags.</span>
    </div>
   </div>
   <div class="legend-item">
    <div class="legend-icon" style="background:#d97706;"></div>
    <div>
     <b style="color:#d97706;">Data-Deficient Candidates</b><br>
     <span class="sub">index &le; 0.20 but hi &gt; 0.20 &mdash; flagged by default penalties; target for audit, not civil works.</span>
    </div>
   </div>
   <div class="legend-item">
    <div class="legend-icon" style="background:#2563eb; opacity:0.6;"></div>
    <div>
     <b>10-minute Network Walkshed</b><br>
     <span class="sub">Reachable catchment based on slope-adjusted walking pace (Tobler hiking function on Copernicus 30m DEM).</span>
    </div>
   </div>
   <div style="margin-top: 10px;">
    <div style="font-size:11px; font-weight:700; color:var(--slate-600);">School Reach Ratio (Network Severance)</div>
    <div class="legend-bar"></div>
    <div class="legend-ends">
     <span>0.0 Severed</span>
     <span>0.5 Baseline</span>
     <span>1.0 Full Reach</span>
    </div>
   </div>
  </div>

  <div class="card" style="display:flex; flex-direction:column; gap:10px;">
   <div class="card-title">Methodological Insights &amp; Audit Results</div>
   <div class="note warn">
    <b>Pavements are unobservable in standard OSM tags.</b>
    In Rotterdam, <code>sidewalk</code> appears on 0 of 68,464 road ways because sidewalks are drawn as separate footway geometries. In Genova, inline sidewalk tags are sparsely recorded. Absence of tags indicates mapping style, not absence of physical pavements.
   </div>
   <div class="note warn">
    <b>Traffic calming measures mapper density, not street calming.</b>
    Calming features exist beside 17.2% of Rotterdam segments vs 0.62% in Genova (a $28\times$ disparity from 3,806 vs 73 tags). These are detection rates, not prevalence rates.
   </div>
   <div class="note">
    <b>Topography penalizes access far beyond distance.</b>
    Planar distance suggested Rotterdam and Genova had comparable school reach (0.507 vs 0.482, a gap of just <b>0.025</b> &mdash; and by 15 minutes Genova scores <i>above</i> Rotterdam). Factoring elevation over a 30m DEM (Tobler's hiking function) separates them by <b>0.163</b> (reach drops to 0.296 in Genova), and by <b>0.128</b> when resident-weighted &mdash; in the same direction at every threshold. Terrain does not widen a known gap; it is what makes the gap exist at all.
   </div>
   <div class="note">
    <b>Street-level imagery cannot fill the gaps (Audit Findings).</b>
    A stratified audit of open imagery (KartaView) across road classes yielded 0% coverage on untagged networks (Wilson 95% bound &le; 2.4%). Car-mounted cameras cannot navigate Genova's historic pedestrian <em>creuse</em>, alleys, and stairways—imputing attributes from imagery would create severe car-centric selection bias.
   </div>
   <div class="note">
    <b>Identified sets prevent false cross-city claims.</b>
    Because missing speed and sidewalk data create an uncertainty interval width of &sim;0.35, inter-city differences smaller than this width cannot be distinguished from missing-data bias. Only 1 of 9 indicators (<code>highway_class</code>) survives cross-city harmonisation.
   </div>
  </div>

  <div class="card">
   <div class="card-title">Full Catchment Explorer</div>
   <p class="sub" style="margin-bottom: 8px;">Explore 100% of street segments in 5-minute school catchments:</p>
   <div style="display:flex; gap:10px;">
    <a href="rotterdam-full.html" class="filter-chip" style="display:block; text-align:center; flex:1;">Rotterdam Full Network</a>
    <a href="genova-full.html" class="filter-chip" style="display:block; text-align:center; flex:1;">Genova Full Network</a>
   </div>
  </div>

  <div style="font-size: 11px; color: var(--slate-600); line-height: 1.6; padding-bottom: 12px;">
   Method note: <a href="https://github.com/sanmoy63/school-streets/blob/main/notes/method_note.md">Methodological Reference</a> &middot;
   Repository: <a href="https://github.com/sanmoy63/school-streets">GitHub</a><br>
   &copy; OpenStreetMap contributors &middot; GHS-POP R2023A (JRC) &middot; Copernicus WorldDEM-30.
  </div>
 </div>

 <div id="map-container">
  <div id="map"></div>

  <div class="floating-controls">
   <div class="filter-title">Street Display Filter</div>
   <div class="filter-btns">
    <button class="filter-chip active" id="flt-all" onclick="setStreetFilter('all')">All (&le;0.20)</button>
    <button class="filter-chip" id="flt-confirmed" onclick="setStreetFilter('confirmed')">Confirmed Only</button>
    <button class="filter-chip" id="flt-candidate" onclick="setStreetFilter('candidate')">Data-Deficient</button>
   </div>
  </div>

  <div class="inspector-card" id="inspector">
   <div class="inspector-header">
    <div class="inspector-name" id="insp-name">Street Name</div>
    <span class="inspector-badge" id="insp-badge">Confirmed</span>
   </div>
   <div id="insp-class" style="color:var(--slate-600); margin-bottom:6px; font-size:11px;">highway_class</div>
   <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:6px;">
    <span style="font-size:11px; color:var(--slate-600);">SSR Readiness Index</span>
    <span style="font-weight:800; font-family:'JetBrains Mono',monospace; font-size:14px;" id="insp-score">0.18</span>
   </div>
   <div class="interval-bar-bg">
    <div class="interval-bar-fill" id="insp-bar"></div>
   </div>
   <div style="display:flex; justify-content:space-between; font-size:10px; color:var(--slate-600); font-family:'JetBrains Mono',monospace;">
    <span>Lo: <b id="insp-lo">0.05</b></span>
    <span>Width: &plusmn;<b id="insp-width">0.18</b></span>
    <span>Hi: <b id="insp-hi">0.42</b></span>
   </div>
   <div id="insp-desc" style="font-size:11px; color:var(--slate-600); margin-top:8px; border-top:1px solid var(--slate-100); padding-top:6px;">
    Definitive intervention target.
   </div>
  </div>
 </div>
</div>

<script>
const DATA = __DATA__;
const map = L.map('map', {preferCanvas: true, zoomControl: true});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
}).addTo(map);

const RAMP = ['#4a1486','#6a51a3','#807dba','#9e9ac8','#bcbddc','#dadaeb'];
function colour(v){
  if(v===null||v===undefined||isNaN(v)) return '#999';
  return RAMP[Math.min(RAMP.length-1, Math.max(0, Math.floor(v*RAMP.length)))];
}
function radius(p){
  if(p===null||p===undefined||isNaN(p)) return 4;
  return Math.max(3, Math.min(15, Math.sqrt(p)/14));
}
function fmt(v,d){ return (v===null||v===undefined||isNaN(v)) ? '&mdash;' : Number(v).toFixed(d); }

let currentCityKey = null;
let currentFilter = 'all';
let worstGeoJsonLayer = null;
let shedsLayer = null;
let schoolsLayer = null;

function setStreetFilter(flt){
  currentFilter = flt;
  document.querySelectorAll('.filter-btns .filter-chip').forEach(b => b.classList.remove('active'));
  document.getElementById('flt-' + flt).classList.add('active');
  if(worstGeoJsonLayer){
    worstGeoJsonLayer.eachLayer(l => {
      const status = l.feature.properties.status;
      if(flt === 'all'){
        l.setStyle({opacity: status === 'confirmed' ? 0.9 : 0.75, weight: status === 'confirmed' ? 2.5 : 1.8});
      } else if(flt === 'confirmed'){
        l.setStyle({opacity: status === 'confirmed' ? 0.95 : 0, weight: status === 'confirmed' ? 2.6 : 0});
      } else if(flt === 'candidate'){
        l.setStyle({opacity: status === 'candidate' ? 0.9 : 0, weight: status === 'candidate' ? 2.4 : 0});
      }
    });
  }
}

function showInspector(p){
  const card = document.getElementById('inspector');
  card.style.display = 'block';
  document.getElementById('insp-name').textContent = p.name || 'Unnamed Street';
  document.getElementById('insp-class').textContent = 'Class: ' + (p.highway_class || 'street');
  document.getElementById('insp-score').textContent = fmt(p.ssr_index, 3);
  document.getElementById('insp-lo').textContent = fmt(p.ssr_index_lo, 3);
  document.getElementById('insp-hi').textContent = fmt(p.ssr_index_hi, 3);
  document.getElementById('insp-width').textContent = fmt(p.ci_width ? p.ci_width/2 : 0, 3);

  const badge = document.getElementById('insp-badge');
  const bar = document.getElementById('insp-bar');
  const desc = document.getElementById('insp-desc');

  const loPct = Math.max(0, Math.min(100, (p.ssr_index_lo || 0) * 100));
  const hiPct = Math.max(0, Math.min(100, (p.ssr_index_hi || 0) * 100));
  bar.style.left = loPct + '%';
  bar.style.width = Math.max(2, (hiPct - loPct)) + '%';

  if(p.status === 'confirmed'){
    badge.textContent = 'Confirmed Priority';
    badge.style.background = '#fde8e8'; badge.style.color = '#991b1b';
    bar.style.background = '#b30000';
    desc.textContent = 'Upper bound ≤ 0.20: Structural infrastructure deficit regardless of missing speed/sidewalk data.';
  } else {
    badge.textContent = 'Data-Deficient';
    badge.style.background = '#fef3c7'; badge.style.color = '#92400e';
    bar.style.background = '#d97706';
    desc.textContent = 'Flagged by default penalties (missing tags). Requires field survey before civil works.';
  }
}

function show(key){
  currentCityKey = key;
  if(shedsLayer) map.removeLayer(shedsLayer);
  if(worstGeoJsonLayer) map.removeLayer(worstGeoJsonLayer);
  if(schoolsLayer) map.removeLayer(schoolsLayer);

  const c = DATA[key];

  shedsLayer = L.geoJSON(c.walksheds, {
    style: {color: '#2563eb', weight: 0.6, fillOpacity: 0.05}
  }).addTo(map);

  worstGeoJsonLayer = L.geoJSON(c.worst, {
    style: f => {
      const isConf = f.properties.status === 'confirmed';
      return {
        color: isConf ? '#b30000' : '#d97706',
        weight: isConf ? 2.5 : 1.8,
        opacity: isConf ? 0.9 : 0.75,
      };
    },
    onEachFeature: (f, l) => {
      l.on({
        mouseover: e => {
          showInspector(f.properties);
          l.setStyle({weight: 4});
        },
        mouseout: e => {
          const isConf = f.properties.status === 'confirmed';
          l.setStyle({weight: isConf ? 2.5 : 1.8});
        },
        click: e => {
          showInspector(f.properties);
        }
      });
    }
  }).addTo(map);

  schoolsLayer = L.geoJSON(c.schools, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: radius(f.properties.pop_reachable),
      fillColor: colour(f.properties.reach_ratio_10),
      color: '#0f172a', weight: 1, fillOpacity: 0.92
    }),
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindPopup(
        `<div style="font-family:'Plus Jakarta Sans',sans-serif; padding:4px;">
          <div style="font-weight:800; font-size:13px; margin-bottom:4px;">${p.name || 'Unnamed School'}</div>
          <div style="font-size:12px; color:#475569;">
            Reachable Network Share: <b>${fmt(p.reach_ratio_10, 3)}</b><br>
            10-min Walkable Population: <b>${p.pop_reachable ? Math.round(p.pop_reachable).toLocaleString() : '&mdash;'}</b><br>
            Population-Weighted Reach: <b>${fmt(p.pop_reach_ratio, 3)}</b>
          </div>
        </div>`
      );
    }
  }).addTo(map);

  map.setView(c.centre, 12);

  const s = c.stats;
  document.getElementById('stat-confirmed').textContent = (s.confirmed_n || 0).toLocaleString();
  document.getElementById('stat-candidate').textContent = (s.candidate_n || 0).toLocaleString();

  document.getElementById('city-takeaway-title').innerHTML = c.name.split(',')[0] + ' &middot; Empirical Synthesis';
  document.getElementById('city-takeaway-body').textContent = c.takeaway || '';
  document.getElementById('city-audit-body').innerHTML = '<b>📷 Street Imagery Audit:</b> ' + (c.audit_insight || '');

  document.getElementById('stats-detail').innerHTML = `
    <tr><td class="k">Evaluated Schools</td><td class="v">${s.schools}</td></tr>
    <tr><td class="k">Street Segments Analyzed</td><td class="v">${s.segments.toLocaleString()}</td></tr>
    <tr><td class="k">Total Deficient (Score &le; 0.20)</td><td class="v">${s.worst_n.toLocaleString()}</td></tr>
    <tr><td class="k">Mean 10-min Reach Ratio</td><td class="v">${fmt(s.reach_mean, 3)}</td></tr>
    <tr><td class="k">Median Reachable Residents</td><td class="v">${s.pop_median ? s.pop_median.toLocaleString() : '&mdash;'}</td></tr>
    <tr><td class="k">Population-Weighted Reach</td><td class="v">${fmt(s.pop_reach_mean, 3)}</td></tr>
  `;

  setStreetFilter(currentFilter);
}

document.querySelectorAll('#city-buttons button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('#city-buttons button').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    show(b.dataset.city);
  };
});

show('__FIRST__');
</script>
</body>
</html>
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*")
    args = ap.parse_args()
    main(args.cities or None)
