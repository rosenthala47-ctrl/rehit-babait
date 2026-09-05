"""Pluggable search backends."""

from .base import DiscoveryProvider, WebSearchProvider
from .google_places import GooglePlacesProvider
from .google_cse import GoogleCSEProvider
from .mock import MockProvider
from .osm import OSMProvider

__all__ = [
    "DiscoveryProvider",
    "WebSearchProvider",
    "GooglePlacesProvider",
    "GoogleCSEProvider",
    "MockProvider",
    "OSMProvider",
]
