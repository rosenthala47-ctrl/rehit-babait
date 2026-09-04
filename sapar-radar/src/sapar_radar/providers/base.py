"""Provider interfaces.

Swapping in a different data source (SerpAPI, a scraped CSV, a CRM export)
means implementing one of these two protocols - nothing else changes.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from ..models import Place


class DiscoveryProvider(Protocol):
    """Finds barbershops."""

    name: str

    def search(self, query: str, area: str, max_pages: int = 3) -> Iterable[Place]:
        """Yield places matching '<query> <area>'."""
        ...


class WebSearchProvider(Protocol):
    """Searches the open web (used to catch booking profiles off Maps)."""

    name: str

    def search(self, query: str, num: int = 10) -> list[str]:
        """Return result URLs for a query."""
        ...
