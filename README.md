# School-Street Accessibility

How far can a child actually walk to school, and how much of what looks nearby
is genuinely reachable? Built from open data for two deliberately contrasting
cities: **Rotterdam**, which is flat, and **Genova**, which is steep.

### 🗺️ [**Open the interactive atlas →**](https://sanmoy63.github.io/school-streets/)

Rotterdam and Genova, switchable, with every school, its ten-minute walkshed and
the streets a school-street scheme would plausibly act on. Full-detail versions
drawing *every* street:
[Rotterdam](https://sanmoy63.github.io/school-streets/rotterdam-full.html) ·
[Genova](https://sanmoy63.github.io/school-streets/genova-full.html)

> Open the links above, not the `.html` files in this repository. GitHub serves
> raw files as `text/plain` and refuses to display anything over ~1 MB, so
> clicking `docs/index.html` here shows source or an error rather than a map.
> The same files render normally when served by GitHub Pages.

> *An independent methodological study, not affiliated with or endorsed by any
> funded programme.*

## Why choose two cities, and especially these two ?

The pair is chosen for contrast on the variable that turned out to matter most,
and held roughly constant on the rest.

**Rotterdam is flat** — −18 m to +33 m across the whole city, mean pedestrian
gradient 0.044. **Genova is not** — sea level to over 400 m inside the built-up
area, mean gradient 0.103, with 290 km of stairways making up 4.7% of its
walking network against Rotterdam's 0.9%.

Both are EU port cities of comparable order of population, with mature
OpenStreetMap communities and similar open-data regimes. Holding those roughly
constant means the differences that remain are attributable to the cities rather
than to their national statistical systems.

**That contrast is the whole point.** A walkability measure built and tested only
on flat ground can look entirely sound and still be wrong everywhere else — and
this pair demonstrated exactly that. Routing on flat distance put the two cities
within 0.027 of each other. Slope-aware routing separates them by 0.157, a
**5.8× difference**, and **12.6×** once weighted by residents. The flat
assumption was not producing a slightly optimistic Genova figure; it was
concealing almost the entire difference between the two cities.

## Where this is going ?

Two cities establish that terrain matters and roughly how much. They cannot
establish how the measure behaves across the range of European urban form.

The next step is extension to further cities — Antwerp as the closest control on
Rotterdam, Krakow as an intermediate-relief case, then others — to test whether
the indicator set generalises, and on that evidence to converge on a measure
that can be applied as a standard rather than rebuilt for each study. Candidates
are already configured in [`config/cities.yml`](config/cities.yml) and run
through the same pipeline unchanged.

The question this repo answers is not "which city is most walkable". It is the
prior question any multi-city measure has to settle first:

> **What can actually be measured the same way in every city, and what does the
> answer to that cost us?**

Cross-site comparison of this kind is limited by the *weakest* data regime, not
the strongest, and the gap is wider than national reputation suggests. Speed
limits are tagged on 79.3% of Rotterdam's roads and 9.4% of Genova's; Rotterdam
has 3,806 mapped traffic-calming features against Genova's 73. A framework that
uses the Dutch data where it exists and drops it elsewhere is not comparative:
it produces a cross-city gradient that is really a data-availability gradient.
Harmonisation is therefore treated as the first-order result and reported
explicitly, rather than as plumbing.

Sidewalk provision illustrates the problem. The OSM `sidewalk=*` tag is present
on **zero** of Rotterdam's 68,464 road segments, because the Netherlands maps
sidewalks as separate footway geometries. Inferring provision from parallel
footway geometry instead ([`sidewalks.py`](src/routes_ssr/sidewalks.py)) does not
rescue it: Rotterdam carries only 30 km of explicitly tagged sidewalk against
3,551 km of road, so absence in the data is uninformative about absence on the
ground.

**Sidewalk provision is therefore not measurable from OpenStreetMap in
Rotterdam by any available route** (method note §4a). The indicator is recorded
as unknown rather than scored, and the domain containing it falls below the
comparability threshold and is excluded from the index.

---

## What it produces ?

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

## Methodology

**Walksheds are network buffers, not hulls.** The reachable street segments
within a distance threshold are buffered by 40 m and dissolved. Convex hulls and
alpha shapes bridge across rivers, rail cuttings and arterial roads — exactly the
barriers that determine whether a child can walk to school — and so overstate
catchments most in the places such interventions are designed to fix.

**Walking speed is 3.6 km/h on the level, not the usual 4.8.** The relevant
traveller is a 6–12 year old, often accompanied. Adult speeds inflate every
catchment by roughly a third in area.

**Routing is slope-aware** (`--slope`). Edge traversal times come from Tobler's
hiking function over a 30 m elevation model, rescaled so level ground returns
the child pace, and the graph is directed — climbing to school is not the trip
home. This is not a refinement: routing on flat distance made Genova look
comparable to Rotterdam (0.481 against 0.508), while slope-aware routing gives
0.305 against 0.462. **The gap between the two cities was understated 5.8×, and
12.6× once weighted by residents** (method note §4e).

**The composite index is designed around three domains** — traffic safety,
walking infrastructure, environment — each scored on [0, 1] in the direction of
*better for a walking child*, with weights declared in
[`config/cities.yml`](config/cities.yml). A domain observed on under 60% of the
segments where it is *applicable* is excluded from the composite entirely rather
than contributing where it happens to exist. **In Rotterdam only traffic safety
survives that gate**, so the published index is a traffic-safety index and is
named as such.

**Missing data is propagated, not imputed**, and *not applicable* is kept
distinct from *not observed*. An untagged sidewalk is an unsurveyed sidewalk
rather than an absent one; a footway does not "lack traffic calming", since the
question does not arise. An explicit applicability mask
([`segment_index.applicability`](src/routes_ssr/segment_index.py)) separates the
two, and coverage is computed over applicable segments only. Every segment
carries a `coverage` field, and no comparative claim is made without
conditioning on it.

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
python scripts/02b_comparative_index.py       # rebuild both cities on the shared
                                              # indicator set, with identified sets
python scripts/04_schoolyards.py rotterdam    # site-scale yard indicators
python scripts/05_population.py rotterdam     # residents in catchments
python scripts/03_map.py rotterdam            # full-detail map (~8 MB, not in git)
python scripts/06_atlas.py                    # published atlas -> docs/index.html
python scripts/90_diagnostics.py rotterdam    # parameter sensitivity checks
```

### Published Map

`docs/index.html` is a single self-contained page served by GitHub Pages,
carrying schools, 10-minute walksheds and the streets scoring at or below 0.20 —
the plausible intervention set — at 2.3 MB for two cities.

The full-detail maps produced by `03_map.py` draw **every** street in the
5-minute catchments and are published alongside it:

- [Rotterdam, all streets](https://sanmoy63.github.io/school-streets/rotterdam-full.html) (7.6 MB)
- [Genova, all streets](https://sanmoy63.github.io/school-streets/genova-full.html) (7.4 MB)

These are regenerated wholesale on each run rather than diffed, so each rebuild
adds their full size to git history. They are committed once here for viewing;
routine iteration should stay local. The complete network, with all attributes,
is in the GeoPackages under `data/processed`.

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

| Source | Used for | Coverage |
|---|---|---|
| OpenStreetMap (Overpass) | schools, pedestrian network, speed limits, road class, traffic calming, footway geometry, schoolyard footprints | Both cities — the only source with one schema across every national border |
| GHS-POP R2023A 100 m (JRC) | residents within catchments; population-weighted severance | Both cities — identical terms everywhere, unlike national grids |

OSM is the base layer *because* it is the common denominator. National registries
are strictly better where they exist, and the design layers them on top as
clearly labelled enrichment rather than mixing them into cross-city comparisons.

**Planned, not yet implemented.** Listed because the indicator schema already
reserves columns for them (`s_green`, `s_enclosure` are present and NaN), and
because the harmonisation argument depends on them:

| Source | Intended for | Expected coverage |
|---|---|---|
| Copernicus Urban Atlas + Street Tree Layer | land use, greenness | EU members |
| GTFS feeds | transit access, temporal service variation | Rotterdam via NDOV; De Lijn (Antwerp); ZTP (Krakow); AMT (Genova) |
| Street-level imagery | sidewalk provision — **the only viable route**, see method note §4a | to be established |


GHS-POP measures **total residents, not children**. No figure here should be
reported as a child count without age structure, which needs national data.

No number anywhere in this repository is derived from the *planned* sources.

---

## Status

**Rotterdam and Genova are complete**, including slope-aware routing and
population weighting. Candidates for extension are configured but not run.

The cross-city machinery has real content: only **1 of 9 indicators survives both
cities**. `s_calming` is a presence-only layer — it records features that exist
and is silent everywhere else — so its non-detections are unobserved rather than
zero, which drops its coverage from an apparent 100% to 17.15% in Rotterdam and
0.62% in Genova. `s_speed` is tagged on 79.3% of Rotterdam's roads and 9.4% of
Genova's. Both fail the comparability gate, leaving road classification alone.

Built on that shared indicator,
[`02b_comparative_index.py`](scripts/02b_comparative_index.py) scores every
street class **identically** in the two cities — residential 0.750 against 0.750.
The 0.221 gap previously reported between their residential streets was produced
entirely by indicators one city had and the other did not, and is not evidence
about streets. Each index now also carries an identified set: Genova's
residential interval is `[0.494, 0.795]` against Rotterdam's `[0.771, 0.774]`,
and where those overlap there is no claim to make.

Known open items are listed in [`notes/method_note.md`](notes/method_note.md) §5,
including where data could not be obtained — which is itself a result.

## Licence

Code is MIT. **The data is not**, and the three sources carry different
obligations — see [LICENSE](LICENSE) for the full notices.

| Source | Used for | Terms |
|---|---|---|
| OpenStreetMap | network, schools, yards, calming | **ODbL v1.0**, share-alike |
| GHS-POP R2023A (JRC) | residents in catchments | open, attribution required |
| Copernicus DEM GLO-30 | elevation for slope routing | free, specific notice required |

Anything under `data/`, and every table and map this pipeline produces — including
the atlas under `docs/` — derives from all three. Redistributing any of it carries
all three notices, and ODbL's share-alike obligation applies to the derived
database.
