"""Offline provider with realistic fixtures.

Lets the whole pipeline - CLI, scoring, dedupe, export, notifications - be run
and tested without API keys or billing. Used by `sapar-radar run --mock`.
"""

from __future__ import annotations

from typing import Iterator

from ..models import Place

FIXTURES: list[dict] = [
    {
        "place_id": "mock_001",
        "name": "מספרת דוד",
        "address": "אלנבי 42, תל אביב",
        "phone": "03-5551234",
        "website": None,
        "rating": 4.8,
        "review_count": 214,
    },
    {
        "place_id": "mock_002",
        "name": "Barber King",
        "address": "דיזנגוף 110, תל אביב",
        "phone": "+972 52 555 8877",
        "website": "https://www.instagram.com/barberking.tlv",
        "rating": 4.6,
        "review_count": 89,
    },
    {
        "place_id": "mock_003",
        "name": "מספרת אלגנס",
        "address": "ביאליק 7, רמת גן",
        "phone": "03-6667788",
        "website": "https://booksy.com/en-il/elegance-rg",
        "rating": 4.9,
        "review_count": 402,
    },
    {
        "place_id": "mock_004",
        "name": "הספר של השכונה",
        "address": "הרצל 15, חולון",
        "phone": "050-1112233",
        "website": "https://sapar-shchuna.wixsite.com/home",
        "rating": 4.3,
        "review_count": 27,
    },
    {
        "place_id": "mock_005",
        "name": "מספרה ללא טלפון",
        "address": "סוקולוב 3, גבעתיים",
        "phone": None,
        "website": None,
        "rating": 4.1,
        "review_count": 5,
    },
    {
        "place_id": "mock_006",
        "name": "מספרת יוסי (סגור)",
        "address": "ז'בוטינסקי 90, פתח תקווה",
        "phone": "03-9998877",
        "website": None,
        "business_status": "CLOSED_PERMANENTLY",
        "rating": 4.0,
        "review_count": 61,
    },
    {
        "place_id": "mock_007",
        "name": "Studio Cut",
        "address": "סוקולוב 60, הרצליה",
        "phone": "09-7778899",
        "website": "https://studiocut.co.il",
        "rating": 4.7,
        "review_count": 155,
    },
]


class MockProvider:
    """Returns the fixture set for any query, once."""

    name = "mock"

    def __init__(self) -> None:
        self._served: set[str] = set()

    def search(self, query: str, area: str, max_pages: int = 3) -> Iterator[Place]:
        for item in FIXTURES:
            data = dict(item)
            place_id = data["place_id"]
            if place_id in self._served:
                continue
            self._served.add(place_id)
            yield Place(
                place_id=place_id,
                name=data["name"],
                address=data["address"],
                phone=data.get("phone"),
                website=data.get("website"),
                rating=data.get("rating"),
                review_count=data.get("review_count", 0),
                business_status=data.get("business_status", "OPERATIONAL"),
                maps_url=f"https://maps.google.com/?q={place_id}",
                source_query=query,
                source_area=area,
            )


class MockWebSearch:
    """Web search stub - finds nothing, so classification relies on the URL."""

    name = "mock_web"

    def search(self, query: str, num: int = 10) -> list[str]:
        return []
