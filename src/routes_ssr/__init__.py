"""School-street readiness analysis for four European pilot cities.

A harmonised, open-data baseline of walkability and school-street readiness at
site, street, neighbourhood and city scale.
"""

from .config import City, cities, ensure_dirs, get_city, params

__version__ = "0.1.0"

__all__ = ["City", "cities", "ensure_dirs", "get_city", "params"]
