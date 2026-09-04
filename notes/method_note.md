# Measuring school-street accessibility across contrasting terrain

*Method note accompanying the pilot-city baseline. Draft — Rotterdam and Genova implemented;
Antwerp and Krakow configured but not run.*

---

## 1. The problem this note is about

Consider a measure of school-street accessibility intended to work in more than
one city. It needs a baseline that means the same thing in each of them.

That is harder than it sounds, and the difficulty is not technical. Rotterdam
and Genova already diverge sharply on what can be observed at all:

| | Rotterdam | Genova |
|---|---|---|
| **Mean pedestrian gradient** | **0.048** | **0.113** |
| **Stairways, share of network** | **0.9%** | **4.7%** |
| **OSM sidewalk data** | **none usable** (§4a) | **none usable** (§4d) |
| **`maxspeed` on roads** | **79.3%** | **9.4%** |
| **Mapped traffic calming** | **3,806 features** | **73 features** |
| **Schools with a yard polygon** | **67%** | **30%** |
| National road-crash register | claimed | claimed |
| Fine-grained population grid | claimed | claimed |

**Bold rows are measured. The rest are not, and are labelled as claims.**

That distinction is the entire point of this note, so it would be perverse to
open with a table that blurs it. An earlier version listed national crash
registers and population grids per city from general knowledge. When the pilot
set changed, those values were carried across positionally and became actively
wrong -- asserting that Italy has no crash register and that Genova is outside
the EU. Neither had ever been checked; the row simply looked authoritative.

Nothing in this project reads a crash register or a national population grid
(see the README's implemented-versus-planned split), so those rows describe
intentions, not evidence. They stay, unstyled, as a reminder of what the
enrichment layer would need to verify first.

A framework that uses the best available source in each city produces indicators
that are not comparable. A framework that uses only the lowest common
denominator throws away most of what Rotterdam knows. Neither is acceptable, and
the choice between them is the actual methodological content of a multi-site
monitoring design.

**The position taken here:** build the comparable baseline from the common
denominator (OSM + GHS-POP, available everywhere), report observability
explicitly per indicator per city, and layer national sources on top as clearly
labelled *enrichment* that is never mixed into cross-city comparisons.

## 2. Units and scales

The ad specifies indicators across schoolyard, street, neighbourhood and city
scales. The pipeline produces all four scales, but the **street segment** is the
primary unit, because it is the unit at which the intervention exists — a
municipality closes a segment.

| Scale | Unit | Built from |
|---|---|---|
| Site | schoolyard polygon | OSM `amenity=school` ways/relations |
| Street | OSM way segment | walk network edges |
| Neighbourhood | 5/10/15-min network walkshed | ego-graphs on the walk network |
| City | administrative area | aggregation + coverage report |

## 2a. Rotterdam baseline: what came out

284 primary schools and kindergartens (from 364 OSM features, after excluding 80
secondary and tertiary institutions); 42,075 nodes and 119,882 pedestrian
segments; **119,882 segments scored (100%) at mean coverage 0.40** — coverage is
0.40 because only the traffic-safety domain survives the gate (§5).

**Severance is the headline — measured correctly the second time.** The original
metric here was `network_ratio` = walkshed area ÷ circle of equal radius, which
gave 0.44 and a tidy story. It was invalid. Walkshed area is proportional to the
corridor half-width used to dissolve reachable segments into a polygon, and that
width is a free parameter I set to 40 m. Sweeping it over a defensible range
moved the mean ratio from **0.21 (10 m) to 0.61 (80 m)** on the same city and the
same network. Most of the "finding" was the buffer.

The replacement, `reach_ratio`, has no such parameter: the share of network nodes
inside a crow-flies circle that are actually reachable within the same *network*
distance. For Rotterdam at 10 minutes: **mean 0.508, sd 0.136, range 0.02–0.86**.
Roughly half of what looks nearby cannot be walked to in the time it appears to
need.

Crucially the two metrics **rank schools differently**, so this was not a
rescaling — the severance candidates identified by the area ratio were an
artefact of the buffer. The corrected list is led by Doctor A. van
Voorthuysenschool (0.30), OBS Crooswijk (0.34) and Mr. Schats Noord (0.36).

One outlier needs individual verification before use: De Droomplaats Kiekeboe at
0.024 with a 0.05 km² catchment is either genuinely stranded in port land or a
snapping failure, and the pipeline cannot currently tell those apart.

**Index distribution.** Mean 0.697, sd 0.308, 46 distinct values, median 0.867.
Ordered by class (service roads excluded):

| primary_link | trunk | primary | secondary | tertiary | unclassified | residential | living_street | path | footway / steps / pedestrian |
|---|---|---|---|---|---|---|---|---|---|
| 0.078 | 0.104 | 0.118 | 0.155 | 0.305 | 0.355 | 0.607 | 0.747 | 0.950 | 1.000 |

The gradient runs monotonically from motorway link to car-free, as the
school-street literature says it should. Two qualifications matter more than the
ordering does:

*The ordering is partly mechanical.* Road class is itself an input via
`HIGHWAY_SCORE`, so this is a consistency check, not independent validation.

*The index only discriminates on the road network.* Car-free classes have
**sd = 0.000** — `s_highway` is their only applicable indicator, so their score
is exactly the class constant. That covers 51,556 segments (43% of the network).
Genuine variation exists only across the 68,252 road segments, where speed limit
and calming enter (residential sd = 0.168). The catchment means of 0.717 / 0.747
are therefore lifted substantially by 44,514 footways scoring a flat 1.000, and
should not be read as "Rotterdam school catchments score 0.75".

**Sidewalk provision.** Positive evidence found on 9,593 roads (14%) for both
sides and 3,264 (5%) for one side; **54,587 roads (81%) recorded as UNKNOWN**,
not as absent. See §4a for why that distinction is the whole story.

## 3. Four choices that change the numbers

Each of these is a place where a defensible-looking default would have produced
a materially different answer.

**3.1 Walksheds are network buffers, not hulls.** Convex hulls and alpha shapes
bridge rivers, rail cuttings and arterials. In Rotterdam — a city defined by a
river and a ring of infrastructure — hull-based catchments would overstate
reachable area most severely in exactly the neighbourhoods where severance is
the problem such interventions are meant to solve. Severance itself is reported via
`reach_ratio`, not via catchment area — see §2a for why the area-based version
had to be withdrawn.

**3.2 Walking speed is 3.6 km/h.** The standard 4.8 km/h adult figure inflates
a 10-minute catchment's radius by a third, and its area by ~78%. The traveller
here is a 6–12 year old, frequently accompanied.

**3.3 Missing ≠ zero — and the first run got this wrong.** All indicators
propagate NaN and every segment carries a `coverage` field. The first version
still failed, in a way worth recording:

The sidewalk indicator read the OSM `sidewalk=*` tag. That tag is present on
**zero** of Rotterdam's 68,464 road segments, because the Netherlands maps
sidewalks as separate footway ways. Missingness was therefore not random: the
tag only survives on ways that are themselves footways. Requiring it silently
restricted the index to footways — which all score alike — and produced an index
with **mean 0.822, p10 0.822, p90 0.822**. Zero variance, and superficially a
perfectly respectable number.

Two lessons carried into the framework. First, a missingness pattern correlated
with the unit of analysis does not degrade an indicator, it *replaces* it with a
different one. Second, the diagnostic that caught it was trivial — checking
whether the index had any variance at all — and belongs in the pipeline rather
than in someone's judgement.

The same error had a twin: traffic calming, tagged on OSM *nodes*, never appears
in an edge table. Reading it off the edges gave an all-missing column, which a
`fillna(0)` then converted into the confident assertion that Rotterdam has no
traffic calming anywhere. Querying the node layer found **3,806 calming
features** affecting 16,500 segments.

**3.3a Not applicable is not missing.** The distinction that finally closed the
missing-data problem, after it recurred five times.

A footway does not *lack* traffic calming; a speed limit on a flight of steps is
not *unrecorded*. The question does not arise. Conflating "inapplicable" with
"unobserved" produced errors in both directions at once:

- Scoring inapplicable-as-zero made car-free ways look hostile. `steps` scored
  0.074 and `bridleway` 0.000 — below `trunk` at 0.104 — because their traffic
  domain collapsed to `s_calming = 0` alone.
- Scoring inapplicable-as-satisfied made a dead indicator look alive. Footways
  self-scored 1.0 for sidewalk provision ("a footway is its own sidewalk"), true
  but uninformative, inflating that domain's apparent coverage from 0.19 to 0.54
  and nearly carrying it through the comparability gate.
- Counting inapplicable rows in a *denominator* penalised a good indicator.
  `s_speed` fell to 0.52 and dropped out of the comparable set because ~52,000
  car-free segments have no speed limit to observe. On applicable segments it is
  0.79.

The pipeline now carries an explicit applicability mask per indicator
(`segment_index.applicability`), applied at source so that the coverage report
and the cross-city harmonisation matrix inherit it. Correcting this moved the
catchment index from 0.595 to 0.747 and cut walking-infrastructure coverage from
0.544 to 0.107 — neither a small revision.

**3.4 The composite is reported with its own sensitivity.** Domain weights
(0.40 / 0.35 / 0.25) are declared in config. A ranking that flips under
reasonable reweighting is not a finding. *[To add: rank correlation across the
weight simplex.]*

## 4. From baseline to evaluation design

A baseline is only useful if it can detect change. The final section addresses
the question the monitoring framework actually has to answer:

> Given spatial autocorrelation in the outcome and four pilot sites with staggered
> implementation, what effect size is detectable, and how many observation
> sessions per site are needed?

Planned content:

- Moran's I / LISA on the composite index to establish the dependence structure
- Design effect from spatial correlation → variance inflation over an i.i.d. design
- Minimum detectable effect for a pre / during / post design at conventional power
- Implication for on-site observation sampling: sessions per site, per time-of-day stratum

This matters because spatially correlated outcomes make naive power calculations
optimistic — sometimes by a factor of two or more in effective sample size. A
monitoring framework specified without it risks committing a study to a
measurement campaign that cannot detect the effects it was built to find.

## 4a. A negative result: sidewalk provision is not measurable from OSM in Rotterdam

This is the most useful finding in the Rotterdam baseline, and it is a negative
one. Three successive attempts to build a sidewalk indicator failed, each for a
different reason, and the third failure is decisive.

**Attempt 1 — the `sidewalk=*` tag.** Present on **0 of 68,464** road segments.
Because the tag survives only on ways that are themselves footways, requiring it
silently restricted the index to footways and produced a constant index (§3.3).

**Attempt 2 — parallel footway geometry.** Infer provision from a footway
running alongside the carriageway. This gave 69% of roads "no sidewalk" under a
flat 25 m corridor, and 81% under per-class corridors. But a threshold sweep
moved that figure across **31 percentage points** (52%–83%), so it was never
reportable — and, more seriously, it scored absence of evidence as evidence of
absence.

**Attempt 3 — check whether absence is informative at all.** It is not:

| Quantity | Rotterdam |
|---|---|
| Road-class length | 3,550.6 km |
| Foot-class length (routing graph) | 2,176.9 km (0.61× road) |
| `highway=footway` features | 14,679 |
| …tagged `footway=sidewalk` | **306 (2.1%)** |
| Explicit sidewalk length | **30.1 km (0.01× road)** |

Complete two-sided sidewalk mapping would give a ratio near 2.0. Rotterdam is at
0.01 for explicitly typed sidewalks, and the 944 km of *untyped* footway cannot
be distinguished from park path, cycle link or through-block connection.

**Conclusion.** OpenStreetMap cannot support a sidewalk-provision indicator in
Rotterdam — not through tags, not through geometry. The indicator is therefore
recorded as unknown rather than scored, and the `walking_infrastructure` domain
falls below the coverage threshold and is excluded from the composite.

**Implication for programme design.** If sidewalk provision is required as a monitoring
indicator — and for a school-street project it plainly is — it must come from
street-level imagery or field survey, and that has to be budgeted for at
proposal stage rather than discovered in year two. This is precisely the class of
constraint a monitoring framework exists to surface before a study commits
to a measurement campaign. It also points at an obvious method: semantic
segmentation of street-level imagery, which is well established for exactly this
task.

## 4b. Site scale: schoolyards

A programme of this kind is about schoolyards *and* school streets, and the
combination is what makes it distinctive. A site-scale analysis is therefore not
optional.

**What OSM cannot tell us about a Rotterdam schoolyard.** Of 241 school
polygons, the share containing any mapped instance of:

| Feature | Yards | Share |
|---|---|---|
| `landuse=grass` | 57 | 24% |
| barrier on boundary | 60 | 25% |
| `barrier=gate` | 45 | 19% |
| `natural=tree` | 39 | 16% |
| `leisure=pitch` | 16 | 7% |
| `leisure=playground` | 13 | **5%** |

Every interior indicator sits far below the comparability gate, and the
missingness is not random — yards mapped in detail are systematically the newer,
larger, better-surveyed ones. This is §4a again in a different layer. Those
columns are carried as explicit NaN so the coverage report states the gap.

**What it can.** The footprint is complete for every polygon school, and so is
the street network. That supports one genuinely useful site indicator:

**Frontage exposure** — decompose each yard's boundary by the class of road it
fronts. Each stretch is assigned to the *worst* class within 20 m, consumed
worst-first so shares partition the perimeter rather than double-counting
corners. A child at a corner is exposed to the worse road, so the minimum is the
safety-relevant choice.

Rotterdam, 189 yards (67% of 284 schools have a polygon):

- **182/189 (96%) front a mapped way** — the highest coverage of any indicator
  in this project, and the only site-scale one that clears the gate
- mean frontage score **0.769** (p10 0.640, p90 0.914)
- 68.7% of the average perimeter fronts a mapped way
- worst-class on boundary: residential 47%, service 24%, tertiary 8%,
  secondary 7%
- only **4 yards (2%)** score below 0.40 — led by De Stelberg (0.200) and
  Johannes-Martinusschool (0.249), both fronting a secondary road

Median yard area is 2,648 m² (IQR 1,475–4,320); median compactness 0.668 on the
Polsby-Popper scale, where 1.0 is a circle.

**Reading this honestly.** That only 2% of yards are badly exposed is plausibly
a real finding about Rotterdam — Dutch primary schools are typically sited on
quiet residential streets — but the distribution is compressed (p10 to p90 spans
0.64–0.91), so the indicator discriminates weakly at the safe end. It is most
useful as a *screen* for the few genuinely exposed sites, which is what a pilot
city needs it for.

Two limitations. The score includes car-free classes, so a yard bounded by
footpaths scores 1.0 — correct for exposure, but it lifts the mean and
`frontage_share_road` is really "share fronting any mapped way", not just roads.
And 95 schools (33%) are mapped as points only and have no site-scale analysis
at all.

## 4c. Population: severance in people rather than geometry

`reach_ratio` (§2a) counts network nodes. That answers a question about geometry,
not about anyone. Weighting by residents converts it into a statement a city can
act on.

**Source.** GHS-POP R2023A, 100 m, Mollweide — chosen over the CBS grid
precisely because it exists on identical terms in every city. It
measures **total residents, not children**. Converting to child counts needs age
structure GHS-POP does not carry; for Rotterdam the CBS 100 m grid has age bands
and would do it, but that is enrichment and NL-only. Nothing below should be
reported as "children".

**Residents within reach** (median per school, n=284):

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| median residents | 689 | 2,647 | 5,483 |
| IQR | 454–1,060 | 1,757–3,946 | 3,802–7,757 |

**Population-weighted severance.**

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| `pop_reach_ratio` mean | 0.360 | 0.450 | 0.500 |
| sd | 0.132 | 0.127 | 0.134 |
| p10–p90 | 0.20–0.54 | 0.27–0.60 | 0.32–0.66 |

**At 10 minutes, 55% of the residents within straight-line range of a school
cannot walk there within the same distance.**

**The population-weighted figure is worse than the node-based one** (0.450 vs
0.508 at 10 min). Severance therefore falls disproportionately on the *denser*
side of each barrier: what gets cut off is not empty land but populated
neighbourhoods. In a city divided by a river, a port and a motorway ring, that is
plausible, and it is the kind of thing an unweighted geometric measure hides.

Most severed by population at 10 min: De Droomplaats Kiekeboe (60 of 2,447
residents reachable), De Dikkedeur (29 of 346), OBS Passe-Partout (117 of 1,257),
Park16Hoven (131 of 1,014).

This also sharpens the Kiekeboe outlier flagged in §2a: it has 2,447 residents in
straight-line range, so it is not simply stranded in empty port land. Either the
severance is real and severe, or the school snapped to the wrong network node.
That distinction still needs checking by hand.

### Why the denominator is a corridor, not a circle

The obvious denominator — population inside a circle — would have repeated the
exact error that invalidated `network_ratio`. The numerator is a corridor ~80 m
wide; a circle is a solid disc, so their ratio would mostly measure corridor
width.

The denominator is therefore the *same* corridor construction applied to every
street within Euclidean radius, reachable or not, so the buffer appears in both
terms and largely cancels.

It does not cancel exactly, and an earlier version of this section said it did.
The check behind that claim ran two widths — 20 m and 40 m — in Rotterdam only,
found a maximum drift of 0.026, and concluded that "the construction does what
it was designed to do". Its output was never committed, so the figure could not
be re-derived, and `--check-buffer`'s help text hardened the finding into a
claim that the buffer *cancels*.

Two widths in the flatter of the two cities do not support that. 40 → 80 m moves
the measure further than 40 → 20 m does, so a halving understates the range, and
Genova — absent from the original check — is about twice as sensitive as
Rotterdam at every threshold. Swept over 10/20/40/80 m:

| span as % of the 40 m value | 5 min | 10 min | 15 min |
|---|---|---|---|
| Genova | **56.0%** | 24.8% | 16.5% |
| Rotterdam | 27.9% | 11.6% | 6.4% |

**The level is not identified.** The movement is monotone in width and largest at
short thresholds, where the buffer is the biggest share of a short corridor.
`reach_ratio` has no such parameter by construction; `pop_reach_ratio` inherits
one, and this note previously did not distinguish the two measures.

The cross-city gap is the robust quantity, though less so than a two-point check
implies:

| gap (Rotterdam − Genova) | 10 m | 20 m | 40 m | 80 m | span |
|---|---|---|---|---|---|
| 5 min | 0.1309 | 0.1252 | 0.1154 | 0.1021 | 25.0% |
| 10 min | 0.1363 | 0.1356 | 0.1276 | 0.1135 | 17.9% |
| 15 min | 0.1372 | 0.1371 | 0.1293 | 0.1118 | 19.6% |

Rotterdam exceeds Genova at every width and threshold, so the **ordering is safe
and the magnitude carries an interval**. The width enters both cities the same
way, which is why the difference survives what the levels do not.

Population levels are therefore reported as the identified set over the width —
`pop_reach_ratio_lo/hi`, in the same idiom as `ssr_index_lo/hi` — and no figure
in this section deserves three decimal places: the third is a corridor-width
choice. This remains far milder than the area-based metric it replaced, which
moved **0.40** over a comparable sweep. Milder is not cancellation.

### Two caveats

The 100 m grid against an 80 m corridor makes cell-coverage precision the
binding constraint, which is why coverage is computed fractionally on a 10×
subdivided grid rather than by cell centroids — centroid masking would drop most
of a corridor this narrow, and unevenly.

Catchments overlap heavily, so per-school figures must not be summed to a city
total. The "995,399 residents within range but not walkable" figure in the run
log is a sum over 284 overlapping catchments, not a headcount of distinct people;
Rotterdam has roughly 650,000 residents in total.

## 4d. Genova: what a second city revealed

Genova ran through the pipeline unchanged: 304 primary schools and kindergartens
(from 373 features), 38,804 nodes, 101,900 segments, all scored. The Italian name
filter behaved -- it excluded *Scuola Secondaria di Primo Grado*, *Scuola Media*
and *Liceo Classico* while keeping *Istituto Comprensivo*, which is the unit that
actually contains the primary school.

The value of the second city is not the second set of numbers. It is that five
problems became visible which one city could not show — and four of them were
in this pipeline rather than in OSM.

### 1. Coverage is necessary but not sufficient for comparability

`s_calming` was reported as observed on 100% of applicable segments in **both**
cities, and cleared the coverage gate on that basis.

The 100% was wrong, and the error was here rather than in OSM. Traffic calming
is tagged on nodes; a segment scored 1.0 when a calming feature lay within 15 m
of it, and **0.0 otherwise**. That zero is the defect. It converts *we did not
find a speed bump* into *there is no speed bump*.

| | Rotterdam | Genova | ratio |
|---|---|---|---|
| mapped calming features | 3,806 | 73 | 52x |
| segments with a detection | 17.15% | 0.62% | **27.6x** |

Italian streets are not 28 times less calmed than Dutch ones. Dutch OSM
contributors map speed bumps meticulously; Italian ones largely do not.

**The general form of the mistake.** Sources divide into two kinds, and the
pipeline was treating them alike:

* **two-sided** — presence and absence are stated with comparable reliability.
  `maxspeed=30` and `maxspeed=50` are both positive statements; a highway class
  is always present.
* **presence-only** — the source records features that exist and is silent
  everywhere else. Traffic calming, street lighting, crossings, bollards.

A presence-only layer yields a **lower bound on prevalence, never a rate**,
unless its detection rate has been established against an independent source.
Scoring its non-detections as zero asserts the layer is complete. This is the
missing-is-not-zero error the project was built to avoid, displaced one level up
— from the segment to the layer — where the existing guards could not see it,
because a fabricated zero is indistinguishable from an observation.

`s_calming` is now typed presence-only (`INDICATOR_KIND` in `segment_index.py`).
Detections are trusted; non-detections are left unobserved unless
`indicator_completeness` in `config/cities.yml` declares a validated detection
rate of 0.90 or better. Both entries are null, because neither has been
validated. Its coverage therefore falls from an apparent 100% to its true 17.15%
and 0.62%, and it now fails the **coverage** gate in both cities — one gate
earlier than the divergence check written to catch it.

The divergence check remains and is now silent on `s_calming`: where the layer
is observed, its mean is 1.0 in both cities by construction. That is the
intended outcome rather than a regression — the defect is caught by the first
gate it reaches, and the screen stays for the two-sided indicators, where it is
still the only thing asking whether two cities observed the same thing. The
27.6x itself is still reported, in `harmonisation_detection.csv`, labelled as
what it is: a ratio of **detection rates**, which mixes the true difference with
the difference in survey effort and cannot separate them.

### 2. Speed data collapses across the border

| | Rotterdam | Genova |
|---|---|---|
| roads with `maxspeed` | 53,333 / 67,288 (**79.3%**) | 5,394 / 57,498 (**9.4%**) |

`s_speed` was comfortably comparable within Rotterdam. Across two cities it is
not, and it drops out. Adding `IT:urban` to the implicit-speed table was
necessary but nowhere near sufficient — the tags are simply absent.

It is worth recording why the obvious repair fails. Both the Netherlands and
Italy set an urban default of 50 km/h, so filling untagged roads with the legal
default looks defensible. It makes the gap **wider**, not narrower: Rotterdam
0.607 → 0.606, Genova 0.386 → 0.331. Genova's `maxspeed` is missing *not at
random*, and in the informative direction — 56% of its tagged roads are 30
zones, because mappers record the exception and leave the default implicit. The
observed sample is therefore biased high and the legal default biased low. This
is an identification problem, not a gap-filling problem, and no imputation
resolves it.

**One indicator of nine survives two cities**: `s_highway`. The composite in
cross-city use is not "close to" a road-classification index. It is one,
exactly, and should be described that way.

### 3. The available-case mean, and why the gap was never real

The two failures above are about evidence. This one is arithmetic, and it is
what turned them into a finding.

A domain score was the mean of its **observed** indicators. Those indicators do
not share an expectation: in Rotterdam `s_speed` averages 0.80, `s_highway`
0.75, `s_calming` 0.28. So when an indicator goes missing the mean renormalises
over the survivors, and the score moves on its own — in a direction fixed by
which indicator was lost. Missingness was not adding noise. It was a lever.

In Genova 92.8% of residential segments carry no `maxspeed`. The high-valued
indicator vanished and the average fell onto the low-valued one. That, and
nothing about Genovese streets, produced 0.386 against Rotterdam's 0.607.

The decomposition is not an argument, it is arithmetic on the same data:

| residential `d_traffic_safety` | Rotterdam | Genova | gap |
|---|---|---|---|
| available-case mean (as published) | 0.6072 | 0.3858 | **+0.2214** |
| untagged speed filled with the legal default | 0.6065 | 0.3309 | +0.2756 |
| `s_speed` dropped in both cities | 0.5135 | 0.3776 | +0.1359 |
| `s_highway` only | 0.7500 | 0.7500 | **0.0000** |

The last row is the result. Every point of the gap came from indicators one city
had and the other did not.

### 4. The comparability verdict was computed and never applied

`02_harmonisation_matrix.py` determined that `s_speed` was not comparable, wrote
that verdict to a CSV, and nothing read it. Each city was then indexed on
whatever it happened to observe, so `s_speed` entered Rotterdam's composite and
not Genova's — and the difference between the two numbers was reported as a
difference between the two cities.

The comparability test was running *downstream* of the index it should have been
gating. The decision now lives in `routes_ssr.harmonise`, as one definition used
both by the script that reports it and by `02b_comparative_index.py`, which
rebuilds every city on the intersection of what all of them observe.

### 5. The flat-walking assumption breaks, and by how much

The topographic hypothesis was that Genova would strain a model built on
constant-speed flat walking. It does, measurably:

| | Rotterdam | Genova |
|---|---|---|
| stairway segments | 1,605 | **6,454** |
| stairway length | 52.2 km (0.9% of network) | **290.1 km (4.7%)** |

Genova has 5.6x the stairway length. The pipeline scores `steps` at **1.000** --
correct for *traffic* safety, since no car can reach them -- and then routes
through them at a flat 3.6 km/h. A 600 m route containing 200 m of stairs is not
a ten-minute walk for a child, and the walkshed treats it as one.

So Genova's catchments are overstated, and overstated *most* in the steep
neighbourhoods where the question matters. Fixing it means a slope-adjusted
speed (Tobler's hiking function) over a digital elevation model, which is now
listed as required work rather than assumed away. Until then, Genovese walkshed
and `reach_ratio` figures should be read as upper bounds.

### What the framework asks now

Three questions, independent of each other, all three of which an indicator must
survive before it can carry a cross-city claim:

| gate | question | where |
|---|---|---|
| coverage | did we observe it, in every city? | `harmonise.coverage_matrix` |
| divergence | did we observe the *same thing*? | `harmonise.divergence` |
| variance | does it discriminate between streets? | `segment_index.modal_share` |

The third is new. An indicator whose observed values sit on a single value for
more than 95% of segments contributes level and no information, and — unlike an
all-missing indicator — it moves the domain's level while doing it. `s_calming`
sits on one value for 99.4% of observed segments in Genova against 82.9% in
Rotterdam: the indicator carrying almost no within-city information in Genova
was the same one carrying the 27.6x between-city gap. The gate has a
100-observation floor, because a modal share computed on a handful of segments
is noise.

Independence is the point. `s_calming` cleared coverage at an apparent 100% and
failed the other two.

### Reporting what is not identified

A point estimate invites a comparison the evidence may not support, so every
index now carries an **identified set** alongside it: the range the index could
take across every value the unobserved indicators could have held. Observed
values are pinned; unobserved ones contribute the widest bound that cannot be
argued away — the full support [0, 1], except for `s_speed` on minor roads,
where the law narrows it to a 50 km/h default (0.20) and a signed 30 zone
(0.85).

Where two cities' intervals overlap, there is no claim to make, however far
apart the point estimates sit: the ordering can be reversed by values the data
never ruled out.

| residential streets | index | identified set | width |
|---|---|---|---|
| Rotterdam | 0.773 | [0.771, 0.774] | 0.003 |
| Genova | 0.749 | [0.494, 0.795] | **0.302** |

The widths are the honest part of the answer. The same index, backed by two very
different quantities of evidence, and Genova's interval is a hundred times the
wider. The intervals overlap, so the residual 0.024 between the point estimates
is not reportable.

Separation would require knowing how complete Genova's calming layer is.
Sweeping that completeness over the residential traffic-safety domain, the two
cities' intervals stay overlapping up to a detection rate of 0.75 and separate
somewhere between 0.75 and 0.85 — that is, one would have to establish that
Genovese OSM captures around four fifths of real calming before the comparison
became possible at all, which is precisely what is in doubt. (The threshold at
which the code will accept a non-detection as an observed absence,
`COMPLETENESS_FOR_ABSENCE`, is set at 0.90: deliberately above where separation
first appears, so that a layer must be better than merely good enough to
separate the cities before its silences are read as evidence.)

The machinery refuses, and the refusal is correct.

### The name filter is language-specific, and Genova proved it

The exclusion list was built against Dutch naming and extended to Italian by
adding the obvious secondary forms. Genova's population run surfaced what that
missed: `UO Formazione aula 1` and `UO Formazione aule 5-6` (health-authority
staff training rooms), `CPIA Centro Ponente` and `CPIA Centro Levante` (Centro
Provinciale per l'Istruzione degli Adulti -- adult education), and
`Aule didattiche` (teaching rooms). Five features of 304, or 1.6%.

They surfaced because they appeared in the *most severed by population* list --
`UO Formazione aula 1` showed 858 of 10,091 residents reachable -- which is a
reminder that an outlier list is a data-quality instrument as much as a finding.

**The fix had to avoid a trap.** The obvious pattern is a bare `centro`. It
would have caught both CPIA entries, and also dropped `Centro Infanzia Porto
Antico` (a real kindergarten) and `Istituto Comprensivo Centro Storico` (a real
primary school, where *centro* is the district name). That is precisely the
`Istituto Comprensivo` problem one word along: the discriminating token in one
language is a common noun in the same language. Only the unambiguous forms are
listed.

**A second bug lurked in the fix.** The first attempt wrote the pattern as
`"(?i)CPIA"` in YAML. In a double-quoted YAML scalar `` is a *backspace*,
not a regex word boundary, so the pattern reached Python containing a control
character and silently matched nothing -- the filter would have looked correct
and passed everything through. Patterns needing a boundary must double the
backslash, as the Dutch entries do. A test now asserts no pattern contains a
literal backspace.

Genova was re-run with the corrected filter: 304 schools became 299. The
headline figures barely moved -- `reach_ratio` 0.478 to 0.481,
`pop_reach_ratio` 0.437 to 0.440 -- which is itself informative, since five
misclassified features out of 304 change a distributional statistic very little.

**And the re-run found more.** The corrected outlier list surfaced `Istituto
Nautico San Giorgio`, a nautical *secondary* institute that "istituto tecnico"
does not match, and `New Western Men Cesino` (1 of 29 residents reachable),
which is not a school at all. Italian secondary institutes come as *nautico*,
*magistrale*, *d'arte*, *alberghiero* and more; each is a separate string.

### The irreducible error rate

Two rounds of correction each surfaced new cases, so the honest conclusion is
not that the filter is now right. It is that **name-based classification has an
error rate that cannot be driven to zero and cannot be measured without manual
validation of every feature.**

The principled alternative is OSM's `isced:level`, which encodes the education
stage directly. It carried on only 97 of 289 Rotterdam features (34%), so it
cannot carry the filter alone -- and the two cannot simply be combined, because
where the tag is absent you are back to names.

What this bounds is precision. Roughly 1-2% of any city's school set is likely
misclassified, in both directions, and every figure conditioned on the school
set inherits that. It is not large enough to overturn any finding here, and it
is large enough that no school-level number should be quoted to three decimal
places without checking that school by hand.

**Published Genova figures use the 299-school set.** The `nautico`/`magistrale`/
`d'arte`/`alberghiero` patterns were added afterwards and are *not* reflected in
the current outputs; re-running would drop one or two further features and shift
`reach_ratio` by well under 0.005. The config is ahead of the data by design, so
that the next full run is correct.

The lesson generalises: **every new language needs its own audit against real
names from that city**, not a translation of the previous city's list — and the
audit will still be incomplete.

### What is comparable anyway

Reported for completeness, with the caveats above:

| | Rotterdam | Genova |
|---|---|---|
| schools kept | 284 | 304 |
| with a yard polygon | 189 (67%) | 91 (**30%**) |
| foot / road length ratio | 0.61 | 0.87 |
| sidewalk evidence (both / one / unknown) | 14 / 5 / 81% | 23 / 6 / 72% |
| `reach_ratio` at 10 min | 0.508 (sd 0.136) | 0.481 (sd 0.146) |
| index in catchments, excl. service | 0.822 [0.821, 0.823] | 0.798 [0.706, 0.861] |
| residential streets | 0.773 [0.771, 0.774] | 0.749 [0.494, 0.795] |
| residential, shared indicator only | **0.750** | **0.750** |

The last row is the one that matters. Restricted to the single indicator both
cities observe, every street class scores identically in the two cities —
residential 0.750 against 0.750, unclassified 0.600 against 0.600, primary 0.050
against 0.050. The previous edition of this note reported a residential gap of
0.607 against 0.386 and argued that it measured OSM rather than Genova. That
reading was right, and it is now a measurement rather than an interpretation.

One comparison survives: the whole network excluding service roads, 0.790 in
Genova against 0.813 in Rotterdam, intervals disjoint by 0.023. It is a
statement about the **composition** of the two networks — Genova carries 3,078
primary-road segments to Rotterdam's 722 — and not about the quality of
comparable streets. All five class-level comparisons are unsupported.

What this leaves is a road-classification index with a cross-city gradient of
zero, which is a thin result and an honest one. Improving it does not mean a
better composite; it means either establishing detection rates for the
presence-only layers against an independent source (municipal open data for
both cities; the Comune di Genova's *grafo stradale* and Zone 30 layers, the
Dutch NWB and its speed-regime dataset), or adding indicators that do not depend
on tagging effort at all — greenness and enclosure from pan-European raster and
building-footprint products, and through-traffic exposure from network
centrality, which is computed identically wherever there is a graph.

## 4e. Terrain: the flat-network assumption was hiding the comparison

§4d flagged that the walkshed model applied one flat walking speed everywhere,
and that Genova's 290 km of stairways made this indefensible there. Implementing
slope showed the problem was larger than "Genova is overstated".

**Source.** Copernicus DEM GLO-30, read from the public AWS mirror. EU-DEM v1.1
is finer (25 m against 30 m) but its tiles are 4.8 GB and the two cities fall in
different ones. Since slope is rise over *segment* length and OSM segments run
20-100 m, 30 m sampling is already finer than the variation that matters. The
tiles are Cloud Optimized GeoTIFFs, so only each city's bounding box is fetched
over range requests: **1.3 MB for Rotterdam, 3.0 MB for Genova**, mosaicked
across every one-degree tile the padded box intersects — two for each of these
cities, and four for Krakow.

**Method.** Elevation is sampled at every network node; per-edge gradient is
rise over length; Tobler's hiking function converts gradient to speed, rescaled
so level ground returns the 3.6 km/h child pace declared in config. The graph is
directed, so climbing to a school and walking home are different costs.

### A fifth of Genova was missing from the DEM

The original implementation chose one Copernicus tile from the centre of each
city's bounding box. The tiles are one degree square; Genova's padded box spans
8.646 to 9.116 E and needs two. Everything east of 9 degrees — **50.4 km²,
20.9% of the municipality, median true elevation 355 m** — lay outside the tile
that was fetched.

Nothing failed. `rasterio` clips a window that overruns its dataset rather than
raising, so the cached raster was silently truncated. Worse, the Copernicus
GeoTIFFs declare no nodata value, so GDAL returns `0.0` for any sample outside
coverage — and `0.0` is a wholly plausible elevation for a port city. The gap
was therefore invisible to the diagnostic built to catch it: elevations came
back finite, so `missing_elevation` read zero on every run. Eastern Genova was
not recorded as unknown. It was recorded as sea level.

Two consequences, in opposite directions. Inside the truncated zone genuinely
steep ground was routed flat, overstating reach. At the tile edge, nodes at
several hundred metres met fabricated neighbours at 0 m, and the resulting
gradients were absorbed by the 1-in-2 clamp — so the artefact surfaced in the
clamp rate rather than as an error. **21 of 295 Genova schools (7.1%)** stood in
the affected zone, at true elevations up to 222 m, 11 of them above 50 m.

The fix mosaics every tile a padded box intersects, writes an explicit nodata
sentinel so a gap can never again read as sea level, and marks samples outside
coverage as missing rather than as ground.

**Rotterdam is unaffected.** Its boundary reaches 51.9943 N, just short of the
tile edge, so only the 2 km analysis pad crossed and no street node lay in the
uncovered sliver; its published DEM range of −18 to 33 m reproduces exactly.
Among the extension candidates, **Krakow would have been worst hit** — it
crosses both 20 E and 50 N, so one tile of the four it needs would have covered
a quarter of the city.

### The terrain itself

| | Rotterdam | Genova (published) | Genova (revised) |
|---|---|---|---|
| DEM range | −18 to 33 m | 0 to 1,181 m | 0 to 1,181 m |
| mean \|gradient\| | 0.048 | 0.103 | **0.113** |
| p90 \|gradient\| | 0.126 | 0.300 | 0.300 |
| stairways given the minimum gradient | 1,509 | 5,295 | 6,396 |
| gradients clamped as surface-model artefacts | 894 (0.7%) | 3,816 (3.7%) | 3,982 (3.9%) |
| nodes with no elevation | 0 | 0 (reported) | **0 (verified)** |

Genova's mean pedestrian gradient rises about 10%: the restored eastern
hinterland is steep, and it had been averaged in as level ground. The clamp rate
rose rather than fell, which was not expected — removing the fabricated cliff
removes clamped edges, but the 50 km² it exposes holds far more genuinely steep
ground than that thin line of artefact ever contributed. The clamp now measures
surface-model noise on real terrain instead of partly measuring a data gap.

### The result

`reach_ratio` at 10 minutes:

| | flat model | slope-aware (published) | slope-aware (revised) |
|---|---|---|---|
| Rotterdam | 0.507 | 0.462 | 0.459 |
| Genova | 0.482 | 0.305 | **0.296** |

Across thresholds, revised against published:

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| Rotterdam | 0.445 (was 0.447) | 0.459 (was 0.462) | 0.478 (was 0.480) |
| Genova | 0.250 (was 0.257) | **0.296** (was 0.305) | 0.339 (was 0.350) |
| **gap** | **0.196** (was 0.190) | **0.163** (was 0.157) | **0.139** (was 0.130) |

Rotterdam moves −0.003 at 10 minutes with a DEM that was already correct, so
that is the drift in OSM and library versions since the original run. Genova
moves −0.009 over the same interval: roughly one third drift, two thirds the DEM
correction. **The gap widens at every threshold.** Genova is harder to walk than
previously reported, not easier — the direction the defect implied, since a
fifth of the city had been flattened.

Genova's disadvantage still *compounds at short range*: 0.250 at 5 minutes
rising to 0.339 at 15, against Rotterdam's much flatter 0.445 / 0.459 / 0.478.
The walk that matters most for a young child is the one terrain penalises
hardest.

### The multiplier does not survive being recomputed

The published figure — that the gap was understated **5.8-fold** — divides the
slope-aware gap by the flat one. Recomputing both on the same run, at all three
thresholds rather than only at ten minutes:

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| Rotterdam, flat | 0.4881 | 0.5068 | 0.5236 |
| Genova, flat | 0.4008 | 0.4818 | 0.5317 |
| **flat gap** | 0.0873 | 0.0250 | **−0.0081** |
| slope gap | 0.1955 | 0.1628 | 0.1389 |
| **ratio** | **2.24×** | **6.51×** | **−17.22×** |

The same data and the same code give 2.2, 6.5 or −17 depending only on which
threshold is read. At fifteen minutes the flat gap changes sign: routed on flat
distance Genova scores *above* Rotterdam, which is credible — the centro storico
has a very dense network — but it leaves the ratio undefined in the
neighbourhood of the reporting range.

This is not a consequence of the DEM correction. The flat model does not read
the DEM and reproduces almost exactly: 0.5068 and 0.4818 here against 0.508 and
0.481 as published. The instability is structural. **The ratio divides by a
quantity close to zero that crosses zero between the ten- and fifteen-minute
thresholds**, and ten minutes is simply where the denominator was small but
still positive. Because the note reported flat figures at ten minutes only, the
sign change was never visible.

**The multiplier is therefore withdrawn rather than corrected.** Replacing 5.8×
with 6.5× would preserve the defect. The gap is the stable statistic and carries
the same argument:

> Routed on flat distance, the two cities sit 0.025 apart at ten minutes. Routed
> on walking time over real terrain, they sit 0.163 apart. The flat assumption
> was not producing a slightly optimistic Genova figure; it was concealing
> almost the entire difference between the two cities.

That holds at every threshold — the gap is 0.196, 0.163 and 0.139 at five, ten
and fifteen minutes, consistently signed and varying smoothly. Any ratio built
on the flat gap must carry its threshold and show its denominator, or not be
reported.

### Weighted by residents, the effect is larger still

Population was recomputed on the same terrain-adjusted routing, since reporting
residents for catchments a child cannot reach would defeat the exercise. At 10
minutes:

| | Rotterdam | Genova (published) | Genova (revised) |
|---|---|---|---|
| median residents reachable, slope | 2,417 (was 2,470) | 3,240 | 3,162 |
| `pop_reach_ratio`, flat | 0.450 | 0.440 | 0.440 |
| `pop_reach_ratio`, slope | 0.413 (was 0.418) | 0.292 | **0.285** |

Across thresholds, Genova revised against published:

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| `pop_reach_ratio` | 0.217 (was 0.221) | **0.285** (was 0.292) | 0.331 (was 0.340) |
| median residents reachable | 881 (was 905) | 3,162 (was 3,240) | 7,122 (was 7,420) |

**The population-weighted gap is essentially unchanged**: 0.126 as published,
0.1276 recomputed. The DEM correction moves both cities slightly and the
difference between them barely shifts. Weighting by where people live still
amplifies the terrain effect, because Genova's hillside neighbourhoods are
densely populated: the steep ground is not empty.

**The 12.6× multiplier is withdrawn on the same grounds as the 5.8×.**
Recomputing the flat and slope-aware population gaps together:

| | 5 min | 10 min | 15 min |
|---|---|---|---|
| Rotterdam, flat | 0.3599 | 0.4498 | 0.4982 |
| Genova, flat | 0.3332 | 0.4404 | 0.5035 |
| **flat gap** | +0.0268 | +0.0094 | **−0.0053** |
| slope gap | +0.1155 | +0.1276 | +0.1293 |
| **ratio** | **4.31×** | **13.60×** | **−24.31×** |

The flat figures reproduce closely — 0.4498 and 0.4404 against 0.450 and 0.440
as published — so this is not an artefact of the re-run. The population
denominator at ten minutes is 0.0094, smaller still than the 0.0250 on the node
measure, so the ratio is correspondingly more sensitive; and it changes sign
between ten and fifteen minutes in the same way.

Both multipliers behave this way for one reason, and it is a finding rather than
a nuisance. **Routed on flat distance, the two cities' curves cross.** Genova
starts below Rotterdam at five minutes and ends above it at fifteen, on both the
node and the population measure. Ignore terrain and Genova's dense network lets
it overtake as the radius grows. The flat gap is not a stable baseline that
terrain widens; it is a quantity passing through zero inside the reporting
range, and any ratio against it inherits that. What terrain changes is not the
size of a known gap but whether the gap exists at all.

The defensible statement is the one the gaps support at every threshold:

> Routed on flat distance the two cities are indistinguishable at ten minutes —
> 0.0094 apart on residents reached, and crossing over by fifteen. Routed on
> walking time over real terrain they are 0.128 apart, in the same direction at
> every threshold. Terrain does not widen a known gap; it is what makes the gap
> visible at all.

The starkest reading is the raw count. Under the flat model each Genovese school
appeared to serve roughly twice as many residents within a ten-minute walk as
each Rotterdam school (5,365 against 2,647), which looked like a straightforward
density advantage. With terrain that advantage largely disappears (3,240 against
2,470). Genova is denser, but its residents cannot convert that density into
access on foot.

### A prediction that was wrong, and what it revealed

The stated expectation was that Rotterdam, being flat, would barely move, and
that a large Rotterdam shift would indicate a broken implementation. Rotterdam
moved 9.0%. Decomposing it on a 40-school sample:

| configuration | mean reach_ratio |
|---|---|
| flat distance | 0.4804 |
| slope, no stair rule | 0.4429 |
| slope + stair rule | 0.4334 |

**78% of the Rotterdam change is genuine terrain**, and only 22% comes from the
stair-gradient assumption. Rotterdam is flat as a *city* and not flat as a
*pedestrian network*: dikes, Maas bridge ramps and underpasses give it a mean
gradient of 0.044, and Tobler's curve is concave, so beyond about ±0.05 both
directions are slower than level ground. A child walking a bridge ramp really is
slower.

So the control worked, but as a diagnostic rather than as the null result
predicted. "Flat city" is a claim about topography, not about the gradient a
pedestrian network actually carries.

### What this rests on

Two assumptions carry the result and neither is validated for this population.

**Tobler's function is calibrated for hikers on open terrain**, not children on
urban staircases. Rescaling anchors its level to this study's walking pace but
leaves its shape untested here. A validation against observed school-journey
times would be the proper check.

**GLO-30 is a surface model**, including buildings and vegetation. A street node
beside a tall building can sample a rooftop and fabricate a cliff, which is why
gradients are clamped at 1-in-2 — but the clamp fires on 3.9% of Genova's edges,
which is high enough to matter. A terrain model would be preferable where one is
available at comparable cost.

**Coverage is now checked rather than assumed.** The DEM is assembled from
every tile the analysis window intersects, gaps carry an explicit nodata value,
and nodes outside coverage are counted as missing rather than silently given an
elevation. The count is reported per run. The defect this replaces was
undetectable from the outputs: it produced plausible elevations, plausible
gradients and no warning.

**Stairs are assigned a minimum 30% gradient** where the DEM smooths them
flatter, because a 30 m cell cannot resolve a single flight. That figure is a
judgement, not a measurement, and it moves Rotterdam by 0.010 and Genova
correspondingly more.

Slope routing is opt-in (`--slope`) so the distance-routed results remain
reproducible for comparison.

## 5. What is not yet trustworthy

Stated explicitly because the numbers above will otherwise be read as firmer
than they are.

**The composite currently rests on one domain.** After the coverage gate,
`walking_infrastructure` (sidewalks, §4a) and `environment` are both excluded,
leaving `traffic_safety` alone at 0.40 of the declared weight. The index is
internally consistent and comparable across segments, but it is a *traffic
safety* index, not the three-domain construct the design specifies, and it
should be named as such wherever it is reported.

**The remaining traffic-safety indicators have their own gaps.** 20.5% of roads
carry no `maxspeed`, and that missingness is overwhelmingly concentrated in
service roads (85.1% of them), which is why service roads are excluded from
headline statistics. `lit` has 0% coverage and contributes nothing.

**34 unnamed school features (12%) bypass name filtering entirely.** They are
flagged via `name_unknown` but not verified; some fraction are likely secondary
or tertiary institutions that the filter would otherwise have caught.

**~~A dead indicator still inflates a denominator.~~ Fixed.** `s_lit` is
applicable everywhere but observed nowhere, and it was enlarging
`walking_infrastructure`'s applicable set from 67,444 roads to all 119,882
segments — reporting that domain at 0.107 coverage where its only working
indicator gives 0.191. "Applicable" now means applicable *and* potentially
observable: an indicator with no observations anywhere is dropped from its
domain before coverage is computed. The domain is excluded either way, so no
conclusion changed, but the reported number is now the meaningful one.

This was found by writing the test suite, not by inspection — see §6.

**The index does not discriminate on car-free ways** — see §2a. On 43% of the
network it returns a class constant.

**The reach_ratio outlier is unresolved** — see §2a.

**The one supported cross-city claim compares classification, not streets.**
`02b_comparative_index.py` restricts both cities to the indicators they share
and refuses any claim whose identified sets overlap. Exactly one comparison
survives — Genova 0.789 against Rotterdam 0.811, margin 0.0225, clearing both
the separation test and the 0.01 negligible-margin gate — and it compares
nothing that was measured.

Harmonisation drops every genuinely observed indicator: `s_speed` is tagged on
79.98% of Rotterdam's roads and 9.35% of Genova's, `s_sidewalk` 18.8% against
28.2%, `s_lit` and `s_green` nowhere. The shared set collapses to `s_highway`,
which is `HIGHWAY_SCORE[highway_class]` — a lookup on the tag that already
defines the class. Every class therefore scores identically in both cities and
only the class proportions differ. Decomposing the aggregate difference gives a
within-class term of **0.000000** against a composition term of **−0.062635**:
**100% of the gap is road-class mix.**

No existing gate could catch this, and the reason generalises. The surviving
indicator is built to pass them: present on 100% of segments, so no coverage
threshold excludes it; never unobserved, so the identified set is degenerate
(`lo == hi`) and separation is automatic; identical per class everywhere, so it
cannot be flagged as divergent. **A quantity that is never missing cannot
express doubt**, and its clean separation is a property of that rather than
evidence about streets.

The detector added is a decomposition rather than a list of indicator names: a
name list must be maintained by hand and flags on what an indicator is called,
where the within-class term flags on what it does. This does not repair the
estimation. The repair is an independent speed source for Genova — the Comune's
grafo stradale — or accepting that the street-scale cross-city index does not
yet exist and declining to report one.

**Cross-city claims at the street scale are therefore currently unsupported.**
The neighbourhood-scale results are not affected: `reach_ratio`,
`pop_reach_ratio` and the terrain finding depend on the network and the DEM, not
on the sparse OSM indicators.

**Single-run figures have no reproducibility check.** The DEM defect survived
because no result had ever been recomputed from scratch and compared. It was
found by re-running the pipeline on a different machine, not by any test or
diagnostic here. A periodic full re-run, diffed against the committed tables,
would have caught it in one pass and is the cheapest safeguard against the next
defect of this kind.

**One city is not a comparison.** Every cross-city claim in §1 is a design
argument, not yet a result. The harmonisation matrix needs the other three
pilots before it means anything.

## 6. Test suite

111 tests, ~2 seconds, entirely offline — every fixture is synthetic geometry,
nothing touches Overpass or `data/`. See [`tests/README.md`](../tests/README.md).

The suite is organised around the errors this project actually made rather than
around line coverage. Most tests carry a docstring naming the bug they lock
down; the two that matter most are
`test_no_parallel_footway_is_unknown_not_zero` and
`test_traffic_indicators_not_applicable_on_car_free_ways`.

Writing it surfaced a seventh instance of the recurring missing-is-not-zero
error — the dead-indicator denominator described in §5 — which is the argument
for the suite in miniature: six instances were found by reading output, and the
seventh only by stating an expectation formally enough for a machine to check.

---

## Status

Complete:

- [x] Rotterdam and Genova, both scales, both with population weighting
- [x] Slope-adjusted walking speed over a 30 m elevation model (§4e)
- [x] Harmonisation matrix and cross-city divergence check, on two real cities
- [x] Test suite (173 tests) and CI

Open, in rough order of value:

- [ ] Extension to further cities — Antwerp as the closest control on Rotterdam,
      Krakow as an intermediate-relief case — to test whether the measure
      generalises
- [ ] Crossings: a child's route is gated by where they can cross, not only by
      network connectivity
- [ ] Multiple access points per school; currently one routing origin each, and
      70% of Genova's schools are point-mapped
- [ ] Greenness and enclosure (environment domain still unobserved)
- [ ] Weight sensitivity, spatial autocorrelation, power / MDE
- [ ] Validation against ground truth — nothing here has been checked against
      street imagery or field survey
