"""The atlas keeps its accessibility guarantees.

These check the rendered HTML rather than a browser. The atlas once told its two
street verdicts apart by colour alone, with an amber that failed text contrast
and a red/amber pair that red-green colour-blind readers could not separate, and
no test noticed: automated audits do not see colour-only encoding either. These
pin down the fixes so a template edit cannot quietly undo them.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

# Approximate background colours of the basemaps each palette is drawn on.
LIGHT_BASEMAPS = {"osm land": "#f2efe9", "osm water": "#aad3df", "positron land": "#f2f2f0"}
DARK_BASEMAPS = {"dark matter land": "#262626", "dark matter water": "#0e0e0e"}


def _atlas():
    spec = importlib.util.spec_from_file_location(
        "atlas_06", Path(__file__).resolve().parents[1] / "scripts" / "06_atlas.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def html() -> str:
    cities = {
        "genova": {"name": "Genoa, Italy", "label": "Genova"},
        "rotterdam": {"name": "Rotterdam, Netherlands", "label": "Rotterdam"},
    }
    return _atlas()._render(cities)


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted([_luminance(a), _luminance(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _palettes(html: str) -> dict:
    block = re.search(r"const PALETTES = \{(.*?)\n\};", html, re.S).group(1)
    out = {}
    for theme, body in re.findall(r"(\w+):\s*\{([^}]*)\}", block):
        out[theme] = dict(re.findall(r"(\w+): '(#[0-9a-fA-F]{6})'", body))
    return out


def test_verdicts_differ_by_line_style_not_colour_alone(html):
    assert "candidate: {weight: 2.2, dashArray: '6 4'}" in html
    assert "confirmed: {weight: 3, dashArray: null}" in html
    # The legend shows the same distinction, in words as well as in the swatch.
    assert 'stroke-dasharray="6 4"' in html
    assert "(dashed line)" in html and "(solid line)" in html


@pytest.mark.parametrize("theme,basemaps", [("light", LIGHT_BASEMAPS), ("dark", DARK_BASEMAPS)])
def test_line_colours_clear_non_text_contrast_on_their_basemaps(html, theme, basemaps):
    palette = _palettes(html)[theme]
    for role in ("confirmed", "candidate", "shed", "outline"):
        for name, bg in basemaps.items():
            assert _contrast(palette[role], bg) >= 3.0, (theme, role, name)


def test_withdrawn_low_contrast_amber_is_gone(html):
    # 3.19:1 as text on white; below the 4.5:1 WCAG AA needs.
    assert "#d97706" not in html


def test_page_has_landmarks_and_exposes_button_state(html):
    assert '<main id="side">' in html
    assert '<section id="map-container" aria-label="Interactive map"' in html
    city_buttons = re.findall(r'<button type="button" data-city="\w+" aria-pressed="(true|false)"', html)
    assert sorted(city_buttons) == ["false", "true"]
    assert 'id="flt-group" role="group"' in html and 'id="bm-group" role="group"' in html
    assert html.count('aria-pressed="true"') == 3  # one city, one filter, one basemap


def test_map_has_a_text_alternative(html):
    assert 'id="tbl-streets"' in html and 'id="tbl-schools"' in html
    assert "function renderStreetTable()" in html and "function renderSchoolTable()" in html


def test_map_jumps_are_not_animated(html):
    # Moves triggered from the tables or city buttons land at once, which also
    # honours reduced-motion preferences.
    assert html.count("animate: false") == 3
    assert "animate: true" not in html


def test_place_names_are_escaped_before_insertion(html):
    # OSM names are free text; they reach innerHTML and popups.
    assert "esc(p.name || 'Unnamed School')" in html
    assert "const name = esc(r.p.name" in html


def test_data_payload_is_inserted_once(html):
    payload = re.search(r"const DATA = (\{.*?\});\n", html).group(1)
    assert set(json.loads(payload)) == {"genova", "rotterdam"}
