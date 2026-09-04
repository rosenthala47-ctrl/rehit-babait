"""Google Places API (New) - the discovery backend.

Uses `places:searchText`, which returns phone number and website in the same
response as the listing, so one call per page is enough - no per-place
Place Details call and no extra billing.

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import httpx

from ..models import Place

log = logging.getLogger(__name__)

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"

#: Only these fields are billed/returned. Keep the list tight - the Places API
#: charges by SKU tier and `websiteUri`/phone sit in the Pro tier.
FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.nationalPhoneNumber",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.rating",
        "places.userRatingCount",
        "places.businessStatus",
        "places.googleMapsUri",
        "places.location",
        "nextPageToken",
    ]
)


class GooglePlacesProvider:
    """Discovery via the Google Places API."""

    name = "google_places"

    def __init__(
        self,
        api_key: str,
        language: str = "he",
        region: str = "il",
        client: httpx.Client | None = None,
        pause_seconds: float = 0.4,
    ) -> None:
        self.api_key = api_key
        self.language = language
        self.region = region
        self.pause_seconds = pause_seconds
        self._client = client or httpx.Client(timeout=30.0)

    def search(self, query: str, area: str, max_pages: int = 3) -> Iterator[Place]:
        text = f"{query} {area}".strip()
        page_token: str | None = None

        for page in range(max_pages):
            body: dict[str, object] = {
                "textQuery": text,
                "languageCode": self.language,
                "regionCode": self.region.upper(),
                "maxResultCount": 20,
            }
            if page_token:
                # The API ignores every other field once a pageToken is set.
                body = {"pageToken": page_token}

            data = self._post(body)
            places = data.get("places") or []
            log.info("places: '%s' page %d -> %d results", text, page + 1, len(places))

            for item in places:
                yield self._to_place(item, query, area)

            page_token = data.get("nextPageToken")
            if not page_token:
                break
            # Google needs a moment before a page token becomes valid.
            time.sleep(self.pause_seconds)

    def _post(self, body: dict[str, object]) -> dict:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }
        response = self._client.post(ENDPOINT, json=body, headers=headers)
        if response.status_code == 429:
            log.warning("places API rate limited; backing off 5s")
            time.sleep(5)
            response = self._client.post(ENDPOINT, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Places API {response.status_code}: {response.text[:400]}"
            )
        return response.json()

    @staticmethod
    def _to_place(item: dict, query: str, area: str) -> Place:
        location = item.get("location") or {}
        return Place(
            place_id=item.get("id", ""),
            name=(item.get("displayName") or {}).get("text", ""),
            address=item.get("formattedAddress", ""),
            phone=item.get("internationalPhoneNumber")
            or item.get("nationalPhoneNumber"),
            website=item.get("websiteUri"),
            rating=item.get("rating"),
            review_count=item.get("userRatingCount") or 0,
            business_status=item.get("businessStatus", ""),
            maps_url=item.get("googleMapsUri", ""),
            lat=location.get("latitude"),
            lng=location.get("longitude"),
            source_query=query,
            source_area=area,
        )

    def close(self) -> None:
        self._client.close()
