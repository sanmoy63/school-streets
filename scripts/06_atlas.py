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
    ]

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
        "worst": _geojson(worst, ["name", "highway_class", "ssr_index"], tol=SEG_TOLERANCE_M),
        "stats": {
            "schools": int(len(schools)),
            "segments": int(len(segs)),
            "worst_n": int(len(worst)),
            "reach_mean": _safe_mean(sheds.loc[sheds["minutes"] == 10, "reach_ratio"]),
            "pop_median": _safe_median(schools["pop_reachable"]),
            "pop_reach_mean": _safe_mean(schools["pop_reach_ratio"]),
        },
    }
    log.info(
        "%s: %d schools, %d worst streets, reach %.3f",
        key, len(schools), len(worst), payload["stats"]["reach_mean"] or float("nan"),
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
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>School-Street Readiness Atlas</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 *{box-sizing:border-box} html,body{margin:0;height:100%;font:14px/1.5 system-ui,sans-serif;color:#1a1a1a}
 #wrap{display:flex;height:100%}
 #side{width:380px;min-width:380px;overflow-y:auto;padding:20px;background:#fafafa;border-right:1px solid #ddd}
 #map{flex:1}
 h1{font-size:19px;margin:0 0 4px} h2{font-size:14px;margin:22px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:#666}
 .sub{color:#666;font-size:13px;margin-bottom:16px}
 button{font:inherit;padding:6px 12px;margin:0 6px 6px 0;border:1px solid #bbb;background:#fff;border-radius:4px;cursor:pointer}
 button.on{background:#4a1486;color:#fff;border-color:#4a1486}
 table{border-collapse:collapse;width:100%;font-size:13px} td{padding:3px 0;vertical-align:top}
 td.k{color:#666} td.v{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
 .note{font-size:12px;color:#555;background:#fff;border-left:3px solid #4a1486;padding:9px 11px;margin:10px 0}
 .warn{border-left-color:#c44}
 .legend{font-size:12px;margin:8px 0}
 .swatch{display:inline-block;width:13px;height:13px;border-radius:50%;margin-right:6px;vertical-align:-2px;border:1px solid #333}
 .bar{height:9px;background:linear-gradient(90deg,#4a1486,#9e9ac8,#dadaeb);border-radius:2px;margin:4px 0}
 .ends{display:flex;justify-content:space-between;color:#666;font-size:11px}
 a{color:#4a1486}
 @media(max-width:760px){#wrap{flex-direction:column}#side{width:100%;min-width:0;height:46%}#map{height:54%}}
</style></head><body>
<div id="wrap">
 <div id="side">
  <h1>School-Street Readiness Atlas</h1>
  <div class="sub">Two contrasting cities &mdash; <b>Rotterdam</b> is flat,
  <b>Genova</b> is steep. Schools are sized by residents reachable on foot and
  coloured by how much of what looks nearby can actually be walked to, routing on
  slope-adjusted walking time.</div>
  <div>__BUTTONS__</div>

  <h2>This city</h2>
  <table id="stats"></table>

  <h2>Reading the map</h2>
  <div class="legend"><b>School colour</b> &mdash; severance (<code>reach_ratio</code>)
   <div class="bar"></div>
   <div class="ends"><span>0.0 cut off</span><span>1.0 fully reachable</span></div>
  </div>
  <div class="legend"><span class="swatch" style="background:#c44"></span>
   Streets scoring &le; 0.20 &mdash; the plausible intervention set</div>
  <div class="legend"><span class="swatch" style="background:#3182bd;opacity:.35"></span>
   10-minute walkshed &mdash; slope-adjusted walking time, child pace on the level</div>

  <h2>What the data will not support</h2>
  <div class="note warn"><b>Pavements are not measurable from OpenStreetMap.</b>
   In Rotterdam the <code>sidewalk</code> tag appears on 0 of 68,464 roads, and only
   30 km of footway is explicitly tagged as pavement against 3,551 km of road.
   Absence in the map says nothing about absence on the ground.</div>
  <div class="note warn"><b>Traffic calming measures mapping effort, not streets.</b>
   A calming feature is found beside 17.15% of Rotterdam segments and 0.62% of
   Genova's &mdash; a 27.6&times; gap from 3,806 mapped features against 73.
   Italian streets are not 28 times less calmed than Dutch ones; Dutch mappers
   record speed bumps and Italian ones largely do not. The layer reports
   features that exist and is silent everywhere else, so those figures are
   <b>detection rates &mdash; lower bounds on prevalence</b>, not rates. Absence
   of a detection is left unobserved, which is why calming now covers 17.15% and
   0.62% of segments rather than the 100% it once claimed, and why it no longer
   enters the index in either city.</div>
  <div class="note"><b>Terrain changes the comparison, not just the numbers.</b>
   Routing on flat distance made Genova look comparable to Rotterdam
   (reach 0.481 against 0.508). Routing on slope-adjusted walking time over a
   30&nbsp;m elevation model drops Genova to 0.305 and Rotterdam only to 0.462
   &mdash; the gap between the cities was understated <b>5.8&times;</b>, and
   <b>12.6&times;</b> once weighted by residents. Genova is denser but cannot
   convert that density into access on foot.</div>
  <div class="note"><b>One indicator of nine survives two cities.</b>
   Speed limits are tagged on 79.3% of Rotterdam roads and 9.4% of Genova's, and
   traffic calming is a detection rate rather than a rate, so both drop out of
   any cross-city claim. What is left is road classification, and a comparative
   index built on it alone scores every street class identically in the two
   cities &mdash; residential 0.750 against 0.750. The 0.221 gap this project
   previously reported between Rotterdam's and Genova's residential streets was
   produced entirely by indicators one city had and the other did not.</div>

  <h2>Caveats</h2>
  <div class="note">Population is <b>total residents</b>, not children &mdash; GHS-POP
   carries no age structure. Catchments overlap, so per-school figures must not be
   summed to a city total.</div>
  <h2>Full-detail maps</h2>
  <div class="note">This page shows only streets scoring &le; 0.20. The maps below
   draw <b>every</b> street in the 5-minute catchments, coloured by index. They are
   7-8 MB each and take a few seconds to load.<br><br>
   <a href="rotterdam-full.html">Rotterdam, all streets</a> &middot;
   <a href="genova-full.html">Genova, all streets</a></div>

  <div class="sub" style="margin-top:18px">
   Method: <a href="https://github.com/sanmoy63/school-streets/blob/main/notes/method_note.md">method note</a> &middot;
   Code: <a href="https://github.com/sanmoy63/school-streets">GitHub</a>
  </div>

  <h2>Data &amp; attribution</h2>
  <div class="sub" style="font-size:12px">
   Street network, schools and traffic calming: &copy; <a
   href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors,
   <a href="https://opendatacommons.org/licenses/odbl/1-0/">ODbL v1.0</a>. This
   page is a derived database and carries the same share-alike obligation.<br><br>
   Population: GHS-POP R2023A, European Commission Joint Research Centre
   (Schiavina, Freire, Carioli, MacManus 2023),
   <a href="http://data.europa.eu/89h/2ff68a52-5b5b-4a22-8f40-c41da8332cfe">doi:10.2905/2FF68A52-5B5B-4A22-8F40-C41DA8332CFE</a>.<br><br>
   Elevation: produced using Copernicus WorldDEM-30 &copy; DLR e.V. 2010-2014 and
   &copy; Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by
   the European Union and ESA; all rights reserved.
  </div>
 </div>
 <div id="map"></div>
</div>
<script>
const DATA = __DATA__;
const map = L.map('map', {preferCanvas:true});
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom:19, attribution:'&copy; OpenStreetMap contributors'}).addTo(map);

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

let layers = [];
function show(key){
  layers.forEach(l => map.removeLayer(l)); layers = [];
  const c = DATA[key];

  const sheds = L.geoJSON(c.walksheds, {style:{color:'#3182bd',weight:.5,fillOpacity:.05}}).addTo(map);
  const worst = L.geoJSON(c.worst, {style:{color:'#c44',weight:2,opacity:.85},
    onEachFeature:(f,l)=>l.bindTooltip(
      `<b>${f.properties.name||'unnamed street'}</b><br>${f.properties.highway_class}
       &middot; index ${fmt(f.properties.ssr_index,3)}`)}).addTo(map);

  const schools = L.geoJSON(c.schools, {
    pointToLayer:(f,ll)=>L.circleMarker(ll,{
      radius:radius(f.properties.pop_reachable),
      fillColor:colour(f.properties.reach_ratio_10),
      color:'#222', weight:1, fillOpacity:.9}),
    onEachFeature:(f,l)=>{const p=f.properties; l.bindPopup(
      `<b>${p.name||'unnamed school'}</b><br>
       Reachable share of nearby network: <b>${fmt(p.reach_ratio_10,3)}</b><br>
       Residents reachable in 10 min: <b>${p.pop_reachable?Math.round(p.pop_reachable).toLocaleString():'&mdash;'}</b><br>
       Population-weighted reach: <b>${fmt(p.pop_reach_ratio,3)}</b>`);}
  }).addTo(map);

  layers = [sheds, worst, schools];
  map.setView(c.centre, 12);

  const s = c.stats;
  document.getElementById('stats').innerHTML = `
    <tr><td class="k">Schools</td><td class="v">${s.schools}</td></tr>
    <tr><td class="k">Street segments analysed</td><td class="v">${s.segments.toLocaleString()}</td></tr>
    <tr><td class="k">Streets scoring &le; 0.20</td><td class="v">${s.worst_n.toLocaleString()}</td></tr>
    <tr><td class="k">Mean reach ratio (10 min)</td><td class="v">${fmt(s.reach_mean,3)}</td></tr>
    <tr><td class="k">Median residents reachable</td><td class="v">${s.pop_median?s.pop_median.toLocaleString():'&mdash;'}</td></tr>
    <tr><td class="k">Population-weighted reach</td><td class="v">${fmt(s.pop_reach_mean,3)}</td></tr>`;
}

document.querySelectorAll('button[data-city]').forEach(b=>{
  b.onclick = () => {
    document.querySelectorAll('button[data-city]').forEach(x=>x.classList.remove('on'));
    b.classList.add('on'); show(b.dataset.city);
  };
});
show('__FIRST__');
</script></body></html>
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cities", nargs="*")
    args = ap.parse_args()
    main(args.cities or None)
