# Tests

```bash
pip install -r requirements.txt pytest
pytest
```

111 tests, ~2 seconds, **fully offline** — every fixture is synthetic geometry,
nothing touches Overpass or `data/`. A suite that needs a live API is a suite
that fails for reasons unrelated to the code.

## What is covered, and why

The suite is organised around the errors this project actually made, not around
line coverage. Most tests carry a docstring naming the bug they lock down.

| File | Covers |
|---|---|
| `test_maxspeed.py` | Tag parsing (km/h, mph, implicit country codes) and the speed→score curve. Asserts unparseable input yields NaN rather than a guess, and that scoring is monotone. |
| `test_applicability.py` | The applicability masks. Traffic indicators must not apply to car-free ways; the sidewalk question must not apply to footways. Also checks every class in `ROAD_CLASSES` and `CAR_FREE_CLASSES` has a `HIGHWAY_SCORE` entry. |
| `test_composite.py` | Domain gating, coverage accounting, and the degeneracy guard. Asserts a sparse domain is excluded outright rather than used where it happens to exist. |
| `test_sidewalks.py` | Bearing/angle geometry and — the key one — that **no parallel footway means unknown, never zero**. |
| `test_walksheds.py` | Distance conversion at child walking pace, and that `reach_ratio` detects a node that is close by air but unreachable on foot. |
| `test_schools.py` | Name filtering across four languages, including that a NaN name does not crash the regex. |
| `test_config.py` | Every pilot city has a *projected, metre-based* CRS; weights sum to 1; regexes compile. |

## The recurring bug

Six times in this project, "no data" was silently converted into "a confident
zero" — through a tag, a node layer, a free parameter, an inference from
absence, a lookup-table fall-through, and a coverage denominator. The first
instance produced an index with mean 0.822, p10 0.822 and p90 0.822: no
variance whatsoever, and entirely respectable-looking in a summary table.

Several tests here exist solely to make each of those impossible to reintroduce
silently. `test_no_parallel_footway_is_unknown_not_zero` and
`test_traffic_indicators_not_applicable_on_car_free_ways` are the two that
matter most.

Writing this suite found a seventh instance: a dead indicator (`s_lit`, observed
nowhere) was still counted in its domain's applicable denominator, understating
walking-infrastructure coverage as 0.107 when the working indicator gives 0.191.
`test_dense_domain_is_included` locks that down.
