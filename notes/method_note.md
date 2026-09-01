# Measuring school-street readiness across four data regimes

*Method note accompanying the ROUTES pilot-city baseline. Draft — Rotterdam reference implementation.*

---

## 1. The problem this note is about

ROUTES will evaluate schoolyard and school-street transformations in Bratislava,
Espoo, Rotterdam and Tirana. Any such evaluation needs a pre-intervention
baseline that means the same thing in all four cities.

That is harder than it sounds, and the difficulty is not technical. The four
pilots sit in four different data regimes:

| | Rotterdam | Espoo | Bratislava | Tirana |
|---|---|---|---|---|
| National road-crash register | yes | yes | partial | no |
| Fine-grained population grid | 100 m (CBS) | 250 m (Tilastokeskus) | EU grid | GHS-POP only |
| Open GTFS | yes | yes (HSL) | yes (IDS BK) | limited |
| Copernicus Urban Atlas | yes | yes | yes | *pending — non-EU* |
| OSM sidewalk data | **none usable** (§4a) | untested | untested | untested |

*(Table to be finalised against the coverage reports once all four cities have
run. The sidewalk row originally read "near-complete" for Rotterdam on the
strength of its general OSM reputation; measuring it found nothing usable at
all, which is why §4a exists.)*

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
scales. The pipeline produces all four, but the **street segment** is the
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
the problem ROUTES is trying to solve. Severance itself is reported via
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
monitoring framework specified without it risks committing four cities to a
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

**Implication for ROUTES.** If sidewalk provision is required as a monitoring
indicator — and for a school-street project it plainly is — it must come from
street-level imagery or field survey, and that has to be budgeted for at
proposal stage rather than discovered in year two. This is precisely the class of
constraint the monitoring framework exists to surface before four cities commit
to a measurement campaign. It also points at an obvious method: semantic
segmentation of street-level imagery, which is well established for exactly this
task.

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

**A dead indicator still inflates a denominator.** `s_lit` is applicable
everywhere but observed nowhere, so it enlarges `walking_infrastructure`'s
applicable set from 67,444 roads to all 119,882 segments, reporting the domain at
0.107 coverage where the only working indicator gives 0.191. The domain is
excluded either way, so no conclusion changes — but "applicable" should arguably
mean "applicable *and* potentially observable", and it currently does not.

**The index does not discriminate on car-free ways** — see §2a. On 43% of the
network it returns a class constant.

**The reach_ratio outlier is unresolved** — see §2a.

**One city is not a comparison.** Every cross-city claim in §1 is a design
argument, not yet a result. The harmonisation matrix needs the other three
pilots before it means anything.

---

## Status

- [x] Rotterdam reference implementation
- [ ] Espoo, Bratislava, Tirana
- [ ] Harmonisation matrix from coverage reports
- [ ] Greenness and enclosure (environment domain currently unobserved)
- [ ] Weight sensitivity
- [ ] Power / MDE section
