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
            "In Rotterdam, tag completeness is high: 100% of candidate streets (560) are confirmed infrastructure priorities."
            if key == "rotterdam"
            else
            "In Genova, 70% of candidate streets (1,679 of 2,406) are data-deficient candidates rather than confirmed failures, "
            "because speed limits (9.4% tagged) and pavement information are missing. Routed with slope, walkable school "
            "reach falls to 0.296; the reach figure in the table below is measured on flat distance."
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
        f'<button type="button" data-city="{k}" aria-pressed="{"true" if k == first else "false"}"'
        f'{" class=on" if k == first else ""}>{k.replace("_", " ").title()}</button>'
        for k, v in cities.items()
    )
    return _TEMPLATE.replace("__DATA__", data).replace("__BUTTONS__", buttons).replace("__FIRST__", first)


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>How far can a child walk to school? | Rotterdam and Genova</title>
<meta name="description" content="How much of the neighbourhood a child can reach on foot, and how dangerous the traffic is, around every primary school and kindergarten in Rotterdam and Genova.">
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
   --confirmed-red: #991b1b;
   --confirmed-bg: #fdf2f2;
   --candidate-amber: #9a3412;
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
 .legend-swatch { flex-shrink: 0; margin-top: 3px; }
 .legend-bar { height: 8px; width: 100%; background: linear-gradient(90deg,#4a1486,#807dba,#dadaeb); border-radius: 2px; margin: 6px 0 2px; }
 .legend-ends { display: flex; justify-content: space-between; font-size: 11px; color: var(--slate-600); font-family: 'JetBrains Mono', monospace; }

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
 .inspector-badge { font-size: 11px; font-weight: 700; padding: 2px 6px; border-radius: 4px; }

 .interval-bar-bg { height: 6px; background: var(--slate-200); border-radius: 3px; position: relative; margin: 8px 0; }
 .interval-bar-fill { position: absolute; height: 100%; border-radius: 3px; }

 a { color: var(--primary); text-decoration: none; font-weight: 600; }
 a:hover { text-decoration: underline; }

 :focus-visible { outline: 3px solid var(--primary); outline-offset: 2px; }
 .leaflet-container:focus-visible { outline-offset: -3px; }
 .skip-link { position: absolute; left: 12px; top: -60px; z-index: 2000; background: var(--slate-900); color: #fff; padding: 8px 12px; border-radius: 6px; }
 .skip-link:focus { top: 12px; }
 .visually-hidden { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
 .fine { font-size: 11px; }
 h2.card-title, h3.card-title { font-size: 12px; }
 table.stats-table th.k { text-align: left; font-weight: 400; color: var(--slate-600); padding: 5px 0; border-bottom: 1px solid var(--slate-100); }
 details { margin-top: 8px; }
 details summary { cursor: pointer; font-size: 13px; font-weight: 600; color: var(--slate-800); padding: 4px 0; }
 .table-scroll { max-height: 320px; overflow: auto; margin-top: 6px; border: 1px solid var(--slate-200); border-radius: 6px; }
 table.data-table { width: 100%; border-collapse: collapse; font-size: 12px; }
 table.data-table th, table.data-table td { padding: 5px 6px; border-bottom: 1px solid var(--slate-100); text-align: left; vertical-align: top; }
 table.data-table thead th { position: sticky; top: 0; background: var(--slate-100); font-weight: 700; }
 table.data-table tbody th { font-weight: 600; }
 .insp-close { border: none; background: transparent; font-size: 18px; line-height: 1; cursor: pointer; color: var(--slate-600); padding: 0 2px; margin-left: 8px; }
 @media (prefers-reduced-motion: reduce) { *, *::before, *::after { transition: none !important; animation: none !important; scroll-behavior: auto !important; } }
 @media (forced-colors: active) { .filter-chip.active, .segmented-control button.on { outline: 2px solid CanvasText; } }

 .findings { border-color: rgba(74,20,134,0.3); }
 .findings-list, .corrections-list { padding-left: 18px; display: flex; flex-direction: column; gap: 8px; line-height: 1.5; }
 .findings-list { font-size: 13px; color: var(--slate-800); }
 .corrections-list { font-size: 12px; color: var(--slate-600); }
 .findings-list b, .corrections-list b { color: var(--slate-900); }
 .map-key { position: absolute; left: 12px; bottom: 24px; z-index: 1000; margin-top: 0; max-width: 270px;
   background: rgba(255,255,255,0.95); border: 1px solid rgba(0,0,0,0.1); border-radius: 10px; padding: 6px 10px;
   box-shadow: 0 4px 14px rgba(0,0,0,0.08); font-size: 12px; }
 .map-key summary { font-size: 12px; }
 .map-key ul { list-style: none; display: flex; flex-direction: column; gap: 5px; margin: 4px 0 2px; }
 .map-key li { display: flex; align-items: center; gap: 8px; line-height: 1.3; }

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
 <a class="skip-link" href="#map-container">Skip to the map</a>
 <main id="side">
  <div>
   <div class="badge">School streets &middot; Rotterdam and Genova</div>
   <h1 style="margin-top: 8px;">How far can a child walk to school?</h1>
   <p class="sub" style="margin-top: 6px;">
    Around every primary school and kindergarten in two cities, flat <b>Rotterdam</b> and
    hilly <b>Genova</b>, we used open map data to measure two things: how much of the
    neighbourhood a child can actually reach on foot in ten minutes, and how dangerous
    the traffic on each nearby street is likely to be.
   </p>
  </div>

  <div class="card findings">
   <h2 class="card-title">What we found</h2>
   <ol class="findings-list">
    <li><b>Hills, not distance, make school harder to reach in Genova.</b> If every street
     were flat, the two cities would look almost the same: a child could reach about half of
     the street junctions within a ten-minute walk as the crow flies (about 0.51 in Rotterdam,
     0.48 in Genova). Counting slopes and stairs, Genova falls to about 0.30, while Rotterdam
     only drops to about 0.46.</li>
    <li><b>We cannot say which city's streets are safer.</b> Only the type of road is recorded
     the same way in both cities. Speed limits, for example, are recorded on about 80% of
     Rotterdam's roads but only about 10% of Genova's, so comparing them would compare the
     mapping, not the streets.</li>
    <li><b>Most streets flagged in Genova cannot be judged yet.</b> About 7 in 10 are flagged
     only because information such as speed limits is missing, so they need checking on the
     ground before any work is planned. In Rotterdam, every flagged street needs work.</li>
   </ol>
  </div>

  <div class="segmented-control" id="city-buttons" role="group" aria-label="Choose a city">
   __BUTTONS__
  </div>

  <div class="card" style="background:var(--primary-subtle); border-color:rgba(74,20,134,0.15);" aria-live="polite">
   <h2 style="font-size:11px; font-weight:800; text-transform:uppercase; color:var(--primary); margin-bottom:4px;" id="city-takeaway-title">City at a glance</h2>
   <p style="font-size:12px; line-height:1.5; color:var(--slate-800);" id="city-takeaway-body">&mdash;</p>
  </div>

  <div class="card">
   <h2 class="card-title">Flagged streets and reach</h2>
   <div class="grid-stats">
    <div class="stat-box confirmed">
     <div class="stat-val" id="stat-confirmed">&mdash;</div>
     <div class="stat-lbl">Need work<br><span class="fine" style="color:#991b1b;">0.20 or below even in the best case</span></div>
    </div>
    <div class="stat-box candidate">
     <div class="stat-val" id="stat-candidate">&mdash;</div>
     <div class="stat-lbl">Cannot judge yet<br><span class="fine" style="color:#92400e;">flagged because data is missing</span></div>
    </div>
   </div>
   <table class="stats-table" id="stats-detail"></table>
  </div>

  <div class="card">
   <h2 class="card-title">How to read the map</h2>
   <p class="sub" style="margin-bottom: 10px;">
    Every street near a school gets a <b>traffic-danger score from 0 to 1, where higher is
    safer</b>. A quiet cul-de-sac with slow traffic scores near 1; a fast multi-lane road scores
    near 0. The map draws the streets that score <b>0.20 or below</b>.
   </p>
   <div class="legend-item">
    <svg class="legend-swatch" width="28" height="14" aria-hidden="true"><line x1="1" y1="7" x2="27" y2="7" stroke="#7f1d1d" stroke-width="3"/></svg>
    <div>
     <b style="color:#7f1d1d;">Needs work</b> <span class="fine">(solid line)</span><br>
     <span class="sub">Scores 0.20 or below even if every missing piece of data turned out to be favourable.</span>
    </div>
   </div>
   <div class="legend-item">
    <svg class="legend-swatch" width="28" height="14" aria-hidden="true"><line x1="1" y1="7" x2="27" y2="7" stroke="#c2410c" stroke-width="2.5" stroke-dasharray="6 4"/></svg>
    <div>
     <b style="color:#c2410c;">Cannot judge yet</b> <span class="fine">(dashed line)</span><br>
     <span class="sub">Scores 0.20 or below on what is recorded, but favourable values for the missing data would clear it. It needs a survey, not construction.</span>
    </div>
   </div>
   <div class="legend-item">
    <svg class="legend-swatch" width="28" height="14" aria-hidden="true"><rect x="1" y="1" width="26" height="12" rx="2" fill="#1d4ed8" fill-opacity="0.15" stroke="#1d4ed8"/></svg>
    <div>
     <b>10-minute walk</b><br>
     <span class="sub">The area a child can reach along streets in ten minutes, measured on flat distance: slope is not applied on this map.</span>
    </div>
   </div>
   <div class="legend-item">
    <svg class="legend-swatch" width="28" height="14" aria-hidden="true"><circle cx="8" cy="7" r="5.5" fill="#6a51a3" stroke="#0f172a"/><circle cx="21" cy="7" r="3" fill="#bcbddc" stroke="#0f172a"/></svg>
    <div>
     <b>Schools</b><br>
     <span class="sub">Colour shows how much of the nearby street network a child can reach in ten minutes; darker means less. Bigger circles have more residents within that walk.</span>
    </div>
   </div>
   <div style="margin-top: 10px;">
    <div style="font-size:11px; font-weight:700; color:var(--slate-600);">School colour: share of nearby street junctions reachable in 10 minutes</div>
    <div class="legend-bar"></div>
    <div class="legend-ends">
     <span>0 = almost none</span>
     <span>1 = all of them</span>
    </div>
   </div>
  </div>

  <div class="card">
   <h2 class="card-title">The map as tables</h2>
   <p class="sub">Everything the map draws, listed so it can be read without the map. The street list follows the street filter on the map. &ldquo;Show&rdquo; moves the map to that place and opens its details.</p>
   <details id="tbl-streets">
    <summary>Flagged streets (<span id="tbl-streets-n">0</span>)</summary>
    <div class="table-scroll" id="tbl-streets-body"></div>
   </details>
   <details id="tbl-schools">
    <summary>Schools (<span id="tbl-schools-n">0</span>)</summary>
    <div class="table-scroll" id="tbl-schools-body"></div>
   </details>
  </div>

  <div class="card" style="display:flex; flex-direction:column; gap:10px;">
   <h2 class="card-title">Details and limits</h2>
   <div class="note">
    <b>What was measured.</b>
    We take the walking network from OpenStreetMap, find every primary school and
    kindergarten, and work outwards from each one at 3.6 km/h. That is a child's pace walking
    with an adult, not the 4.8 km/h usually assumed for grown-ups.
   </div>
   <div class="note">
    <b>Why hills matter so much.</b>
    Walking time on each street comes from its gradient on a 30&nbsp;m elevation model, and
    stairways always count as steep. On flat distance the two cities' ten-minute reach is about
    0.51 and 0.48, and by fifteen minutes Genova is slightly ahead. With slope, Genova's
    ten-minute reach falls to 0.296 and the gap opens to about 0.16, or about 0.13 counted in
    residents, in the same direction at every time limit.
   </div>
   <div class="note warn">
    <b>The score measures traffic danger, and only traffic danger.</b>
    It was designed to combine what a car can do on the street, what a pedestrian is given, and
    what the street is like to be in. Only the first could be measured in both cities, so
    pavements, lighting, greenery and enclosure are left out rather than guessed at. A street
    scoring 1.00 is one no car can threaten; it is not necessarily pleasant to walk down.
   </div>
   <div class="note warn">
    <b>Pavements are not part of the score.</b>
    The step that downloads the map data did not read OpenStreetMap's pavement information, so
    there is nothing to score them on yet. An unrecorded pavement is unknown, not absent.
   </div>
   <div class="note warn">
    <b>Speed bumps count mappers, not bumps.</b>
    They are recorded beside 17.2% of Rotterdam's streets and 0.62% of Genova's (3,806 records
    against 73). Rotterdam does not have 28 times more speed bumps; more people have mapped
    them. These counts set a floor on what exists.
   </div>
   <div class="note">
    <b>Only one kind of information can be compared.</b>
    Of nine, only the road type is recorded the same way in both cities. Any difference between
    the two cities' scores therefore reflects the mix of road types each contains, not
    conditions measured on the streets, so the street-level comparison supports no claim.
   </div>
  </div>

  <div class="card" id="corrections">
   <h2 class="card-title">Corrections to earlier versions</h2>
   <ul class="corrections-list">
    <li><b>Pavement count.</b> We said the <code>sidewalk</code> tag appears on 0 of Rotterdam's
     68,464 road ways because Dutch mappers draw pavements as separate lines. That was wrong: our
     download step never read the tag, in either city.</li>
    <li><b>Size of the hills effect.</b> We described it as a 5.8-fold (12.6-fold
     resident-weighted) disparity, with Genova's reach at 0.305. Both are withdrawn: the ratios
     divide by a flat-distance gap close to zero that changes sign between time limits, and
     0.305 came from an elevation model that left a fifth of Genova at sea level.</li>
    <li><b>Street-imagery audit.</b> We reported that open street-level imagery covered 0% of
     sampled untagged streets (at most 2.1% in Rotterdam and 2.4% in Genova) and blamed
     car-mounted cameras. That result is withdrawn: the check used one location per photo
     sequence rather than one per photo, and the speed-sign search had failed. Street imagery
     has not been tested.</li>
    <li><b>Why the cities cannot be compared.</b> We put this down to an uncertainty range of
     about 0.35 around every score. That was misleading, since most Rotterdam streets have no
     range at all. The real reason is that only road type is recorded the same way in both
     cities.</li>
   </ul>
  </div>

  <div class="card">
   <h2 class="card-title">Every street, not just the flagged ones</h2>
   <p class="sub" style="margin-bottom: 8px;">Larger maps (about 7.5 MB each) showing every scored street in the 5-minute areas around each school:</p>
   <div style="display:flex; gap:10px;">
    <a href="rotterdam-full.html" class="filter-chip" style="display:block; text-align:center; flex:1;">Rotterdam, all streets</a>
    <a href="genova-full.html" class="filter-chip" style="display:block; text-align:center; flex:1;">Genova, all streets</a>
   </div>
  </div>

  <div style="font-size: 11px; color: var(--slate-600); line-height: 1.6; padding-bottom: 12px;">
   How it was done, in full: <a href="https://github.com/sanmoy63/school-streets/blob/main/notes/method_note.md">method note</a> &middot;
   Repository: <a href="https://github.com/sanmoy63/school-streets">GitHub</a><br>
   &copy; OpenStreetMap contributors &middot; GHS-POP R2023A (JRC) &middot; Copernicus WorldDEM-30.
  </div>
 </main>

 <section id="map-container" aria-label="Interactive map" tabindex="-1">
  <div id="map"></div>

  <details class="map-key" open>
   <summary>Map key</summary>
   <ul>
    <li><svg width="28" height="14" aria-hidden="true"><line x1="1" y1="7" x2="27" y2="7" stroke="#7f1d1d" stroke-width="3"/></svg>Street that needs work</li>
    <li><svg width="28" height="14" aria-hidden="true"><line x1="1" y1="7" x2="27" y2="7" stroke="#c2410c" stroke-width="2.5" stroke-dasharray="6 4"/></svg>Street we cannot judge yet</li>
    <li><svg width="28" height="14" aria-hidden="true"><rect x="1" y="1" width="26" height="12" rx="2" fill="#1d4ed8" fill-opacity="0.15" stroke="#1d4ed8"/></svg>10-minute walk (flat distance)</li>
    <li><svg width="28" height="14" aria-hidden="true"><circle cx="8" cy="7" r="5.5" fill="#6a51a3" stroke="#0f172a"/><circle cx="21" cy="7" r="3" fill="#bcbddc" stroke="#0f172a"/></svg>School: darker = less reachable, bigger = more residents</li>
   </ul>
  </details>

  <div class="floating-controls">
   <div class="filter-title" id="flt-title">Show streets</div>
   <div class="filter-btns" id="flt-group" role="group" aria-labelledby="flt-title">
    <button type="button" class="filter-chip active" id="flt-all" aria-pressed="true" onclick="setStreetFilter('all')">All flagged</button>
    <button type="button" class="filter-chip" id="flt-confirmed" aria-pressed="false" onclick="setStreetFilter('confirmed')">Needs work</button>
    <button type="button" class="filter-chip" id="flt-candidate" aria-pressed="false" onclick="setStreetFilter('candidate')">Cannot judge yet</button>
   </div>
   <div class="filter-title" id="bm-title">Background map</div>
   <div class="filter-btns" id="bm-group" role="group" aria-labelledby="bm-title">
    <button type="button" class="filter-chip active" id="bm-standard" aria-pressed="true" onclick="setBasemap('standard')">Standard</button>
    <button type="button" class="filter-chip" id="bm-light" aria-pressed="false" onclick="setBasemap('light')">Plain light</button>
    <button type="button" class="filter-chip" id="bm-dark" aria-pressed="false" onclick="setBasemap('dark')">Plain dark</button>
   </div>
  </div>

  <div class="inspector-card" id="inspector" role="region" aria-label="Street details" aria-live="polite">
   <div class="inspector-header">
    <div class="inspector-name" id="insp-name">Street Name</div>
    <div><span class="inspector-badge" id="insp-badge">Confirmed</span><button type="button" class="insp-close" aria-label="Close street details" onclick="hideInspector()">&times;</button></div>
   </div>
   <div id="insp-class" style="color:var(--slate-600); margin-bottom:6px; font-size:11px;">Road type</div>
   <div style="display:flex; justify-content:space-between; align-items:baseline; margin-top:6px;">
    <span style="font-size:11px; color:var(--slate-600);">Traffic-danger score (higher is safer)</span>
    <span style="font-weight:800; font-family:'JetBrains Mono',monospace; font-size:14px;" id="insp-score">0.18</span>
   </div>
   <div class="interval-bar-bg">
    <div class="interval-bar-fill" id="insp-bar"></div>
   </div>
   <div style="display:flex; justify-content:space-between; font-size:11px; color:var(--slate-600); font-family:'JetBrains Mono',monospace;">
    <span>Lowest possible: <b id="insp-lo">0.05</b></span>
    <span>Highest possible: <b id="insp-hi">0.42</b></span>
   </div>
   <div id="insp-desc" style="font-size:11px; color:var(--slate-600); margin-top:8px; border-top:1px solid var(--slate-100); padding-top:6px;">
    Definitive intervention target.
   </div>
  </div>
 </section>
</div>

<script>
const DATA = __DATA__;
const map = L.map('map', {preferCanvas: true, zoomControl: true});

const BASEMAPS = {
  standard: {url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', theme: 'light',
             options: {maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'}},
  light: {url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', theme: 'light',
          options: {maxZoom: 19, subdomains: 'abcd', attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}},
  dark: {url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', theme: 'dark',
         options: {maxZoom: 19, subdomains: 'abcd', attribution: '&copy; OpenStreetMap contributors &copy; CARTO'}},
};
// Every line colour holds at least 3:1 against the basemaps it is drawn on. The
// two street verdicts also differ by line style, because no red/orange pair
// stays distinguishable under red-green colour blindness while also clearing
// 3:1 against the map.
const PALETTES = {
  light: {confirmed: '#7f1d1d', candidate: '#c2410c', shed: '#1d4ed8', outline: '#0f172a'},
  dark:  {confirmed: '#fca5a5', candidate: '#fdba74', shed: '#93c5fd', outline: '#ffffff'},
};
const LINE = {confirmed: {weight: 3, dashArray: null}, candidate: {weight: 2.2, dashArray: '6 4'}};
const VERDICT = {confirmed: 'Needs work', candidate: 'Cannot judge yet'};

const RAMP = ['#4a1486','#6a51a3','#807dba','#9e9ac8','#bcbddc','#dadaeb'];
function colour(v){
  if(v===null||v===undefined||isNaN(v)) return '#999';
  return RAMP[Math.min(RAMP.length-1, Math.max(0, Math.floor(v*RAMP.length)))];
}
function radius(p){
  if(p===null||p===undefined||isNaN(p)) return 4;
  return Math.max(3, Math.min(15, Math.sqrt(p)/14));
}
function fmt(v,d){ return (v===null||v===undefined||isNaN(v)) ? 'n/a' : Number(v).toFixed(d); }
function esc(s){
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}

let baseLayer = null;
let theme = 'light';
let currentCityKey = null;
let currentFilter = 'all';
let worstGeoJsonLayer = null;
let shedsLayer = null;
let schoolsLayer = null;
let streetLayers = [];
let schoolLayers = [];

function pal(){ return PALETTES[theme]; }
function kind(p){ return p.status === 'confirmed' ? 'confirmed' : 'candidate'; }
function streetStyle(p, hover){
  const k = kind(p), line = LINE[k];
  const shown = currentFilter === 'all' || currentFilter === k;
  return {color: pal()[k], dashArray: line.dashArray, opacity: shown ? 0.95 : 0,
          weight: shown ? line.weight + (hover ? 1.5 : 0) : 0};
}
function pressOnly(groupId, activeId){
  document.querySelectorAll('#' + groupId + ' button').forEach(b => {
    const on = b.id === activeId;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
function restyle(){
  if(worstGeoJsonLayer) worstGeoJsonLayer.eachLayer(l => l.setStyle(streetStyle(l.feature.properties, false)));
  if(shedsLayer) shedsLayer.setStyle({color: pal().shed, fillColor: pal().shed});
  if(schoolsLayer) schoolsLayer.eachLayer(l => l.setStyle({color: pal().outline}));
}

function setStreetFilter(flt){
  currentFilter = flt;
  pressOnly('flt-group', 'flt-' + flt);
  restyle();
  renderStreetTable();
}

function setBasemap(key){
  const b = BASEMAPS[key];
  if(baseLayer) map.removeLayer(baseLayer);
  baseLayer = L.tileLayer(b.url, b.options).addTo(map);
  baseLayer.bringToBack();
  theme = b.theme;
  pressOnly('bm-group', 'bm-' + key);
  restyle();
}

function hideInspector(){ document.getElementById('inspector').style.display = 'none'; }
document.addEventListener('keydown', e => { if(e.key === 'Escape') hideInspector(); });

function showInspector(p){
  const card = document.getElementById('inspector');
  card.style.display = 'block';
  document.getElementById('insp-name').textContent = p.name || 'Unnamed Street';
  document.getElementById('insp-class').textContent = 'Road type: ' + (p.highway_class || 'street');
  document.getElementById('insp-score').textContent = fmt(p.ssr_index, 3);
  document.getElementById('insp-lo').textContent = fmt(p.ssr_index_lo, 3);
  document.getElementById('insp-hi').textContent = fmt(p.ssr_index_hi, 3);

  const badge = document.getElementById('insp-badge');
  const bar = document.getElementById('insp-bar');
  const desc = document.getElementById('insp-desc');

  const loPct = Math.max(0, Math.min(100, (p.ssr_index_lo || 0) * 100));
  const hiPct = Math.max(0, Math.min(100, (p.ssr_index_hi || 0) * 100));
  bar.style.left = loPct + '%';
  bar.style.width = Math.max(2, (hiPct - loPct)) + '%';

  badge.textContent = VERDICT[kind(p)];
  if(kind(p) === 'confirmed'){
    badge.style.background = '#fde8e8'; badge.style.color = '#991b1b';
    bar.style.background = PALETTES.light.confirmed;
    desc.textContent = 'Scores 0.20 or below even in the best case for the missing data, so it needs work.';
  } else {
    badge.style.background = '#fef3c7'; badge.style.color = '#92400e';
    bar.style.background = PALETTES.light.candidate;
    desc.textContent = 'Flagged only because some data is missing. Check it on the ground before planning any work.';
  }
}

// Map jumps are never animated. A move triggered from the tables or the city
// buttons should land at once rather than depend on an animation finishing,
// and it respects reduced-motion preferences without a special case.
function showStreet(i){
  const s = streetLayers[i];
  if(!s) return;
  if(currentFilter !== 'all' && currentFilter !== kind(s.feature.properties)) setStreetFilter('all');
  map.fitBounds(s.layer.getBounds(), {maxZoom: 17, animate: false});
  s.layer.setStyle(streetStyle(s.feature.properties, true));
  showInspector(s.feature.properties);
}

function showSchool(i){
  const s = schoolLayers[i];
  if(!s) return;
  map.setView(s.layer.getLatLng(), 16, {animate: false});
  s.layer.openPopup();
}

function renderStreetTable(){
  const box = document.getElementById('tbl-streets-body');
  const rows = streetLayers.map((s, i) => ({i: i, p: s.feature.properties}))
    .filter(r => currentFilter === 'all' || kind(r.p) === currentFilter)
    .sort((a, b) => (a.p.ssr_index_hi ?? 1) - (b.p.ssr_index_hi ?? 1));
  document.getElementById('tbl-streets-n').textContent = rows.length.toLocaleString();
  if(!document.getElementById('tbl-streets').open){ box.innerHTML = ''; return; }
  box.innerHTML =
    '<table class="data-table"><caption class="visually-hidden">Flagged streets, lowest possible score first</caption>' +
    '<thead><tr><th scope="col">Street</th><th scope="col">Road type</th><th scope="col">Score range</th>' +
    '<th scope="col">Verdict</th><th scope="col"><span class="visually-hidden">On the map</span></th></tr></thead><tbody>' +
    rows.map(r => {
      const name = esc(r.p.name || 'Unnamed street');
      return `<tr><th scope="row">${name}</th><td>${esc(r.p.highway_class || '')}</td>` +
        `<td>${fmt(r.p.ssr_index_lo, 2)}&ndash;${fmt(r.p.ssr_index_hi, 2)}</td><td>${VERDICT[kind(r.p)]}</td>` +
        `<td><button type="button" class="filter-chip" onclick="showStreet(${r.i})">Show<span class="visually-hidden"> ${name} on the map</span></button></td></tr>`;
    }).join('') + '</tbody></table>';
}

function renderSchoolTable(){
  const box = document.getElementById('tbl-schools-body');
  const rows = schoolLayers.map((s, i) => ({i: i, p: s.feature.properties}))
    .sort((a, b) => (a.p.reach_ratio_10 ?? 2) - (b.p.reach_ratio_10 ?? 2));
  document.getElementById('tbl-schools-n').textContent = rows.length.toLocaleString();
  if(!document.getElementById('tbl-schools').open){ box.innerHTML = ''; return; }
  box.innerHTML =
    '<table class="data-table"><caption class="visually-hidden">Schools, lowest 10-minute reach first</caption>' +
    '<thead><tr><th scope="col">School</th><th scope="col">10-min reach (flat distance)</th><th scope="col">Residents within reach</th>' +
    '<th scope="col"><span class="visually-hidden">On the map</span></th></tr></thead><tbody>' +
    rows.map(r => {
      const name = esc(r.p.name || 'Unnamed school');
      const people = r.p.pop_reachable ? Math.round(r.p.pop_reachable).toLocaleString() : 'n/a';
      return `<tr><th scope="row">${name}</th><td>${fmt(r.p.reach_ratio_10, 3)}</td><td>${people}</td>` +
        `<td><button type="button" class="filter-chip" onclick="showSchool(${r.i})">Show<span class="visually-hidden"> ${name} on the map</span></button></td></tr>`;
    }).join('') + '</tbody></table>';
}

// The city summary is written from the city's own counts rather than stored
// prose, so it cannot drift from the numbers beside it.
function cityTakeaway(label, s){
  const worst = s.worst_n || 0, cand = s.candidate_n || 0, conf = s.confirmed_n || 0;
  if(!worst) return `No streets near schools in ${label} score 0.20 or below.`;
  if(!cand) return `All ${worst.toLocaleString()} flagged streets in ${label} need work: each scores 0.20 or below even in the best case for any missing data.`;
  const pct = Math.round(100 * cand / worst);
  return `${cand.toLocaleString()} of the ${worst.toLocaleString()} flagged streets in ${label} (${pct}%) cannot be judged yet: ` +
    `they are flagged because information such as speed limits is missing. The other ${conf.toLocaleString()} need work whatever that data turns out to be.`;
}

function show(key){
  currentCityKey = key;
  [shedsLayer, worstGeoJsonLayer, schoolsLayer].forEach(l => { if(l) map.removeLayer(l); });
  streetLayers = [];
  schoolLayers = [];
  hideInspector();

  const c = DATA[key];
  const label = c.label || (key.charAt(0).toUpperCase() + key.slice(1));

  shedsLayer = L.geoJSON(c.walksheds, {
    style: {color: pal().shed, fillColor: pal().shed, weight: 0.8, fillOpacity: 0.06}
  }).addTo(map);

  worstGeoJsonLayer = L.geoJSON(c.worst, {
    style: f => streetStyle(f.properties, false),
    onEachFeature: (f, l) => {
      streetLayers.push({feature: f, layer: l});
      l.on({
        mouseover: () => { showInspector(f.properties); l.setStyle(streetStyle(f.properties, true)); },
        mouseout: () => l.setStyle(streetStyle(f.properties, false)),
        click: () => showInspector(f.properties),
      });
    }
  }).addTo(map);

  schoolsLayer = L.geoJSON(c.schools, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: radius(f.properties.pop_reachable),
      fillColor: colour(f.properties.reach_ratio_10),
      color: pal().outline, weight: 1, fillOpacity: 0.92
    }),
    onEachFeature: (f, l) => {
      schoolLayers.push({feature: f, layer: l});
      const p = f.properties;
      l.bindPopup(
        `<div style="font-family:'Plus Jakarta Sans',sans-serif; padding:4px;">
          <div style="font-weight:800; font-size:13px; margin-bottom:4px;">${esc(p.name || 'Unnamed School')}</div>
          <div style="font-size:12px; color:#475569;">
            10-minute reach (flat distance): <b>${fmt(p.reach_ratio_10, 3)}</b><br>
            Residents within a 10-minute walk: <b>${p.pop_reachable ? Math.round(p.pop_reachable).toLocaleString() : 'n/a'}</b><br>
            Reach counted in residents (with slope): <b>${fmt(p.pop_reach_ratio, 3)}</b>
          </div>
        </div>`
      );
    }
  }).addTo(map);

  map.setView(c.centre, 12, {animate: false});

  const s = c.stats;
  document.getElementById('stat-confirmed').textContent = (s.confirmed_n || 0).toLocaleString();
  document.getElementById('stat-candidate').textContent = (s.candidate_n || 0).toLocaleString();

  document.getElementById('city-takeaway-title').textContent = label + ' at a glance';
  document.getElementById('city-takeaway-body').textContent = cityTakeaway(label, s);

  document.getElementById('stats-detail').innerHTML = `
    <tr><th scope="row" class="k">Schools</th><td class="v">${s.schools}</td></tr>
    <tr><th scope="row" class="k">Street segments scored</th><td class="v">${s.segments.toLocaleString()}</td></tr>
    <tr><th scope="row" class="k">Flagged streets (score 0.20 or below)</th><td class="v">${s.worst_n.toLocaleString()}</td></tr>
    <tr><th scope="row" class="k">Average 10-minute reach (flat distance)</th><td class="v">${fmt(s.reach_mean, 3)}</td></tr>
    <tr><th scope="row" class="k">Median residents within a 10-minute walk</th><td class="v">${s.pop_median ? s.pop_median.toLocaleString() : 'n/a'}</td></tr>
    <tr><th scope="row" class="k">Reach counted in residents (with slope)</th><td class="v">${fmt(s.pop_reach_mean, 3)}</td></tr>
  `;

  const box = map.getContainer();
  box.setAttribute('role', 'region');
  box.setAttribute('aria-label',
    `Map of ${label}: ${s.worst_n.toLocaleString()} flagged streets and ${s.schools} schools. ` +
    'The same information is listed under The map as tables.');

  renderStreetTable();
  renderSchoolTable();
}

document.querySelectorAll('#city-buttons button').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('#city-buttons button').forEach(x => {
      x.classList.remove('on');
      x.setAttribute('aria-pressed', 'false');
    });
    b.classList.add('on');
    b.setAttribute('aria-pressed', 'true');
    show(b.dataset.city);
  };
});
document.getElementById('tbl-streets').addEventListener('toggle', renderStreetTable);
document.getElementById('tbl-schools').addEventListener('toggle', renderSchoolTable);

setBasemap('standard');
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
