"""The atlas explains itself to a first-time reader.

A structural review found the live page opened with jargon ("data-observability
bounds"), never said what the score meant, buried its findings under a card of
methodological notes, and mixed corrections of claims a new reader never saw
into the notes they had to read. These tests keep the fixes in place. They
check structure and wording, not comprehension: only a test with real readers
can show the page is understood.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _atlas():
    spec = importlib.util.spec_from_file_location(
        "atlas_06_readability", Path(__file__).resolve().parents[1] / "scripts" / "06_atlas.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def atlas():
    return _atlas()


@pytest.fixture(scope="module")
def html(atlas) -> str:
    # Place names as the pipeline receives them, including Nominatim's "Genoa".
    cities = {
        "genova": {"name": "Genoa, Italy"},
        "rotterdam": {"name": "Rotterdam, Netherlands"},
    }
    return atlas._render(cities)


def test_findings_come_before_the_controls_and_details(html):
    found = html.index("What we found")
    assert found < html.index('id="city-buttons"')
    assert found < html.index("How to read the map")
    assert found < html.index("Details and limits")


def test_score_is_defined_before_the_legend_uses_it(html):
    flat = " ".join(html.split())
    assert flat.index("where higher is safer") < flat.index("(solid line)")


def test_template_has_no_jargon_labels(atlas):
    template = atlas._TEMPLATE
    for jargon in ["Data-Deficient", "Confirmed Priorit", "SSR Readiness",
                   "Baseline", "Empirical Synthesis", "Severance", "observability"]:
        assert jargon not in template, jargon
    # Field names are fine inside the script, but not as labels in the page.
    assert "ssr_index_hi" not in template[:template.index("<script>")]


def test_city_is_called_genova_everywhere(atlas, html):
    assert "Genoa" not in atlas._TEMPLATE
    assert '>Genova</button>' in html


def test_withdrawn_claims_appear_only_under_corrections(html):
    corrections = html.index('id="corrections"')
    for withdrawn in ["68,464", "5.8-fold", "0.305", "2.4% in Genova", "about 0.35"]:
        assert withdrawn in html, withdrawn
        # The first mention already sits under corrections, so none is in the reading path.
        assert html.find(withdrawn) > corrections, withdrawn


def test_map_has_a_key_beside_it(html):
    key = html[html.index('class="map-key"'):html.index("</details>", html.index('class="map-key"'))]
    for entry in ["needs work", "cannot judge yet", "10-minute walk", "School:"]:
        assert entry in key, entry


def test_city_summary_is_computed_from_the_counts(html):
    assert "cityTakeaway(label, s)" in html
    assert "c.takeaway" not in html
