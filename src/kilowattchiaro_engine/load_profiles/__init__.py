"""Italian residential load profile archetypes package.

Provides 7 representative household load profiles based on RSE/ARERA research
for inferring hourly consumption from bill-level F1/F2/F3 data.
"""

from .archetypes import ARCHETYPES, LoadArchetype, get_archetype, list_archetypes
from .infer import band_split_known, infer_hourly_load

__all__ = [
    "ARCHETYPES",
    "LoadArchetype",
    "band_split_known",
    "get_archetype",
    "infer_hourly_load",
    "list_archetypes",
]
