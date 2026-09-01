"""School filtering -- keeping primary/kindergarten, excluding secondary+."""

from __future__ import annotations

import numpy as np
import pytest

from routes_ssr.config import params
from routes_ssr.osm_extract import _looks_like_non_primary

PATTERNS = params("schools")["exclude_name_patterns"]


@pytest.mark.parametrize(
    "name",
    [
        "Mavo Centraal",              # Dutch secondary
        "De Theaterhavo/vwo",         # Dutch secondary
        "Piet Zwart Institute",       # tertiary
        "Luzac Rotterdam College",    # exam college
        "Erasmus Universiteit",
        "Hogeschool Rotterdam",
        "Espoon lukio",               # Finnish upper secondary
        "Gymnázium Bratislava",       # Slovak secondary
        "Shkolla e mesme Tirana",     # Albanian secondary
    ],
)
def test_excludes_non_primary_institutions(name):
    assert _looks_like_non_primary(name, PATTERNS), f"should have excluded {name!r}"


@pytest.mark.parametrize(
    "name",
    [
        "Basisschool De Wissel",
        "OBS Pluspunt",
        "CBS De Sleutel",
        "Peuterspeelzaal Dikkie Dik",
        "Oranjeschool",
        "Espoon alakoulu",
        "Základná škola",
    ],
)
def test_keeps_primary_and_kindergarten(name):
    assert not _looks_like_non_primary(name, PATTERNS), f"should have kept {name!r}"


@pytest.mark.parametrize("value", [np.nan, None, "", float("nan")])
def test_missing_names_are_handled_not_crashed(value):
    """Regression: unnamed schools arrive from pandas as NaN.

    `bool(nan)` is True, so a `if not name` guard passes the float straight
    through to re.search, which raises. 12% of Rotterdam's kept features are
    unnamed, so this is the common case, not an edge case.
    """
    assert _looks_like_non_primary(value, PATTERNS) is False


def test_all_exclusion_patterns_compile():
    """A malformed regex in config would fail at fetch time, mid-download."""
    import re

    for p in PATTERNS:
        re.compile(p)
