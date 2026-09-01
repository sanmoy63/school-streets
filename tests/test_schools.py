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
        "Koninklijk Atheneum Antwerpen",   # Flemish secondary
        "Sint-Jozef Humaniora",            # Flemish secondary
        "III Liceum Ogólnokształcące",     # Polish upper secondary
        "Technikum Nr 5 Kraków",           # Polish technical secondary
        "Liceo Scientifico Cassini",       # Italian upper secondary
        "Istituto Tecnico Nautico",        # Italian technical secondary
        "Scuola Secondaria di Primo Grado",  # Italian lower secondary
        "CPIA CENTRO PONENTE",             # Italian adult education centre
        "UO Formazione aula 1",            # staff training room, not a school
        "Aule didattiche",                 # teaching rooms
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
        "Vrije Basisschool Sint-Lutgardis",   # Flemish primary
        "Szkoła Podstawowa nr 12",            # Polish primary
        "Przedszkole Samorządowe",            # Polish kindergarten
        "Istituto Comprensivo Sampierdarena", # Italian primary unit
        "Scuola Primaria Giovanni Pascoli",   # Italian primary
        "Scuola dell'Infanzia Arcobaleno",    # Italian kindergarten
        "Centro Infanzia Porto Antico",       # real kindergarten -- "centro" trap
        "Istituto Comprensivo Centro Storico",  # real primary -- "centro" is a district
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


def test_centro_is_not_a_blanket_exclusion():
    """The Istituto Comprensivo trap, one word along.

    Excluding a bare "centro" would catch CPIA adult-education centres, but it
    would also drop "Centro Infanzia Porto Antico" (a kindergarten) and
    "Istituto Comprensivo Centro Storico" (a primary school in the historic
    centre district). Only the unambiguous adult-education forms are listed.
    """
    assert _looks_like_non_primary("CPIA Centro Levante", PATTERNS)
    assert not _looks_like_non_primary("Centro Infanzia Porto Antico", PATTERNS)
    assert not _looks_like_non_primary("Istituto Comprensivo Centro Storico", PATTERNS)


def test_yaml_double_quoted_escapes_survive_into_regex():
    r"""Guard a real bug: in a YAML double-quoted scalar "" is a backspace.

    A pattern written "(?i)CPIA" therefore reaches Python as a literal
    backspace and silently never matches -- the filter appears to work and
    quietly passes everything through. Patterns needing a word boundary must
    double the backslash, and this asserts they still function.
    """
    import re

    for p in PATTERNS:
        assert "" not in p, f"pattern contains a literal backspace: {p!r}"
        re.compile(p)

    # The double-escaped word boundaries still behave as boundaries.
    assert _looks_like_non_primary("Rotterdam College", PATTERNS)
    assert not _looks_like_non_primary("Collegewijk Basisschool", PATTERNS)
