# School-Street Readiness: a harmonised open-data baseline for four European pilot cities

A reproducible, multi-scale baseline of walkability and school-street readiness for
**Rotterdam, Antwerp, Krakow and Genova**, built entirely from openly available data.

> *An independent methodological study, not affiliated with or endorsed by any
> funded programme. The four cities vary along two axes the indicators are
> sensitive to: national data regime, and topography. All four are EU members,
> so the data spread is deliberately narrow — if a harmonised indicator set
> fails across four comparably-resourced European cities, it fails everywhere.
> Rotterdam, Antwerp and Krakow are flat; Genova climbs from sea level to over
> 400 m, and is included because it is expected to break the flat constant-speed
> walkshed assumption the other three tolerate.*

The question this repo answers is not "which city is most walkable". It is the
prior question that any four-city evaluation has to settle first:

> **What can actually be measured the same way in all four pilot cities, and what
> does the answer to that cost us?**

Cross-site comparison of this kind is limited by the *weakest* data
regime, not the strongest. Rotterdam has a national road-crash register and a
100 m census grid; the others have coarser equivalents. A comparative framework
that quietly uses the Dutch data where it exists and drops it elsewhere is not
comparative — it
produces a cross-city gradient that is really a data-availability gradient. So
harmonisation is treated here as the first-order result, reported explicitly,
rather than as plumbing.

The first run of this pipeline demonstrated the point on itself. The sidewalk
indicator was built from the OSM `sidewalk=*` tag, which is present on **zero**
of Rotterdam's 68,464 road segments — the Netherlands maps sidewalks as separate
footway geometries instead. Because the tag only survives on ways that are
themselves footways, requiring it silently narrowed the index to footways, which
all score alike, yielding a near-constant index that looked like a result. The
first fix ([`sidewalks.py`](src/routes_ssr/sidewalks.py)) inferred provision
from parallel footway geometry instead — and that failed too, for a deeper
reason: Rotterdam maps only 30 km of explicit sidewalk against 3,551 km of road,
so absence in the data says nothing about absence on the ground. The conclusion
is a negative result (method note §4a): **sidewalk provision is not measurable
from OSM in Rotterdam by any route.** The episode is kept because it is exactly
the failure mode this design exists to catch.

---

## What it produces

| Scale | Unit | Output |
|---|---|---|
| Site | schoolyard footprint | yard area, compactness, **frontage exposure** by road class |
| **Street** | **OSM way segment** | **indicator scores + composite SSR index** |
| Neighbourhood | 5 / 10 / 15-min network walkshed | catchment area, `reach_ratio` and population-weighted `pop_reach_ratio` |
| City | administrative area | coverage report, index distribution |

Three artefacts per city, in `data/processed`:

- `<city>_schools.gpkg` — schools with walkshed summary attributes
- `<city>_walksheds.gpkg` — one polygon per (school, time threshold)
- `<city>_segments.gpkg` — street segments with indicator and composite scores

plus `outputs/tables/<city>_coverage.csv`, the per-indicator observability report
that feeds the cross-city harmonisation matrix.

---

## Method, in short

**Walksheds are network buffers, not hulls.** The reachable street segments
within a distance threshold are buffered by 40 m and dissolved. Convex hulls and
alpha shapes bridge across rivers, rail cuttings and arterial roads — exactly the
barriers that determine whether a child can walk to school — and so overstate
catchments most in the places such interventions are designed to fix.

**Walking speed is 3.6 km/h, not the usual 4.8.** The relevant traveller is a
6–12 year old, often accompanied. Adult speeds inflate every catchment by
roughly a third in area.

**The composite index is designed around three domains** — traffic safety,
walking infrastructure, environment — each scored on [0, 1] in the direction of
*better for a walking child*, with weights declared in
[`config/cities.yml`](config/cities.yml). A domain observed on under 60% of the
segments where it is *applicable* is excluded from the composite entirely rather
than contributing where it happens to exist. **In Rotterdam only traffic safety
survives that gate**, so the published index is a traffic-safety index and is
named as such.

**Missing data is propagated, not imputed** — and *not applicable* is kept
distinct from *not observed*. An untagged sidewalk is an unsurveyed sidewalk, not
an absent one; equally, a footway does not "lack traffic calming" — the question
does not arise. Conflating the two produced errors in both directions, and an
explicit applicability mask ([`segment_index.applicability`](src/routes_ssr/segment_index.py))
is what finally resolved it. Every segment carries a `coverage` field, and no
comparative claim is made without conditioning on it.

**The index is not the finding.** A composite whose ranking is not robust to its
own weights is not evidence. Weight sensitivity is **not yet run** — it is listed
as open work in the method note, and no ranking claim is made in the meantime.

---

## Reproducing

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt
python scripts/01_build_city.py rotterdam
```

Then, optionally:

```bash
python scripts/02_harmonisation_matrix.py     # cross-city comparability table
python scripts/04_schoolyards.py rotterdam    # site-scale yard indicators
python scripts/05_population.py rotterdam     # residents in catchments
python scripts/03_map.py rotterdam            # full-detail map (~8 MB, not in git)
python scripts/06_atlas.py                    # published atlas -> docs/index.html
python scripts/90_diagnostics.py rotterdam    # parameter sensitivity checks
```

### The published atlas

[**View the atlas →**](https://sanmoy63.github.io/school-streets/)

`docs/index.html` is a single self-contained page served by GitHub Pages. It is
deliberately *not* the same artefact as `03_map.py`: that one draws every street
in the catchments and runs to ~8 MB per city, which is fine on disk and
unacceptable in git history. The atlas carries schools, 10-minute walksheds and
only the streets scoring at or below 0.20 — the plausible intervention set —
which brings two cities to 2.3 MB. The complete network stays in the
GeoPackages.

Requires Python 3.11+ (developed on 3.13). `requirements.txt` gives lower bounds;
`requirements.lock.txt` pins the exact versions the Rotterdam baseline was built
with.

First run per city takes a few minutes against the Overpass API — occasionally
longer, and Overpass does drop connections under load, so a retry is normal.
Everything is cached under `data/raw` afterwards and later runs take ~3 minutes.
No data is versioned: every input is open and re-fetched, which is what makes the
reproducibility claim checkable rather than decorative.

---

## Data sources

**Currently implemented — the pipeline reads only this:**

| Source | Used for | Coverage across the four pilots |
|---|---|---|
| OpenStreetMap (Overpass) | schools, pedestrian network, speed limits, road class, traffic calming, footway geometry, schoolyard footprints | All four — the only source with one schema across every national border |
| GHS-POP R2023A 100 m (JRC) | residents within catchments; population-weighted severance | All four — same terms everywhere, which is why it is preferred to national grids |

OSM is the base layer *because* it is the common denominator. National registries
are strictly better where they exist, and the design layers them on top as
clearly labelled enrichment rather than mixing them into cross-city comparisons.

**Planned, not yet implemented.** Listed because the indicator schema already
reserves columns for them (`s_green`, `s_enclosure` are present and NaN), and
because the harmonisation argument depends on them:

| Source | Intended for | Expected coverage |
|---|---|---|
| Copernicus Urban Atlas + Street Tree Layer | land use, greenness | All four (EU members) |
| GTFS feeds | transit access, temporal service variation | Rotterdam via NDOV; De Lijn (Antwerp); ZTP (Krakow); AMT (Genova) |
| Street-level imagery | sidewalk provision — **the only viable route**, see method note §4a | to be established |
| Digital elevation model | slope-adjusted walking speed — **required for Genova**, see `config/cities.yml` | Copernicus EU-DEM, all four |

GHS-POP measures **total residents, not children**. No figure here should be
reported as a child count without age structure, which needs national data.

No number anywhere in this repository is derived from the *planned* sources.

---

## Status

**Rotterdam and Genova are complete. Antwerp and Krakow have not yet been run**, so despite the four-city framing this is currently a
two-city comparison plus a harmonisation framework designed for four. The
cross-city machinery now has real content -- only **3 of 9 indicators survive
two cities**, and one of the survivors (`s_calming`) is flagged as encoding
mapping effort rather than street conditions (method note §4d) -- and
[`02_harmonisation_matrix.py`](scripts/02_harmonisation_matrix.py) says so on
every run rather than implying a comparison exists.

Known open items are listed in [`notes/method_note.md`](notes/method_note.md) §5,
including where data could not be obtained — which is itself a result.

## Licence

Code is MIT — see [LICENSE](LICENSE).

Data derived from OpenStreetMap (everything under `data/`, and any table or map
this pipeline produces) is a Derivative Database under **ODbL v1.0** and is *not*
covered by the MIT licence. © OpenStreetMap contributors. Redistributing derived
data requires attribution and ODbL licensing of the derived database.
