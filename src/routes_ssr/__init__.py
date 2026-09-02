"""Slope-aware school-street accessibility, built from open data.

Two contrasting cities: Rotterdam, which is flat, and Genova, which is steep.
The pair exists to test whether a walkability measure built on level ground
survives real relief -- it does not, without correction (see the method note).

Indicators run at site, street, neighbourhood and city scale, and every one
reports what share of the network it could actually observe.
"""

from .config import City, cities, ensure_dirs, get_city, params

__version__ = "0.1.0"

__all__ = ["City", "cities", "ensure_dirs", "get_city", "params"]
