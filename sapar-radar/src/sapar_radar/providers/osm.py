"""OpenStreetMap discovery via Nominatim (geocoding) + Overpass (data).

No API key, no Google Cloud project, no credit card - unlike Google Places.
The trade-off: OSM's coverage of small Israeli businesses is patchier than
Google Maps, so expect fewer shops and more missing phone/website fields.
Good enough to try the whole pipeline for free before deciding whether the
Google Places quota is worth setting up.
"""

from __future__ import annotations

import logging
import time
from typing import Iterator

import httpx

from ..models import Place

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
#: Nominatim's usage policy requires a descriptive User-Agent identifying the app.
USER_AGENT = "sapar-radar (https://github.com/rosenthala47-ctrl/rehit-babait)"


class OSMProvider:
    """Discovery via OpenStreetMap - free, keyless, no billing account."""

    name = "osm"

    def __init__(
        self,
        client: httpx.Client | None = None,
        nominatim_pause_seconds: float = 1.0,
    ) -> None:
        self.nominatim_pause_seconds = nominatim_pause_seconds
        self._client = client or httpx.Client(
            timeout=30.0, headers={"User-Agent": USER_AGENT}
        )
        # Overpass has no free-text query and no pagination: every query
        # string for the same area returns the same shops. Fetch once per
        # area and replay - the pipeline already de-dupes on place_id.
        self._area_cache: dict[str, list[Place]] = {}

    def search(self, query: str, area: str, max_pages: int = 3) -> Iterator[Place]:
        if area not in self._area_cache:
            self._area_cache[area] = self._fetch_area(query, area)
        yield from self._area_cache[area]

    def _fetch_area(self, query: str, area: str) -> list[Place]:
        bbox = self._geocode(area)
        if bbox is None:
            log.warning("osm: could not geocode area %r, skipping", area)
            return []
        elements = self._overpass(bbox)
        log.info("osm: '%s' -> %d results", area, len(elements))
        places = [self._to_place(el, query, area) for el in elements]
        return [p for p in places if p is not None]

    def _geocode(self, area: str) -> tuple[float, float, float, float] | None:
        params = {"format": "json", "q": area, "countrycodes": "il", "limit": 1}
        response = self._client.get(NOMINATIM_URL, params=params)
        time.sleep(self.nominatim_pause_seconds)  # usage policy: max 1 req/sec
        if response.status_code >= 400:
            log.warning("nominatim %s: %s", response.status_code, response.text[:200])
            return None
        results = response.json()
        if not results:
            return None
        south, north, west, east = (float(x) for x in results[0]["boundingbox"])
        return south, west, north, east

    def _overpass(self, bbox: tuple[float, float, float, float]) -> list[dict]:
        south, west, north, east = bbox
        box = f"{south},{west},{north},{east}"
        # Regex, not "=", because OSM allows compound tags like
        # shop=hairdresser;beauty - an exact match would silently miss those.
        ql = (
            "[out:json][timeout:25];\n"
            "(\n"
            f'  node["shop"~"hairdresser"]({box});\n'
            f'  way["shop"~"hairdresser"]({box});\n'
            ");\n"
            "out center tags;"
        )
        response = self._client.post(OVERPASS_URL, data={"data": ql})
        if response.status_code == 429:
            log.warning("overpass rate limited; backing off 10s")
            time.sleep(10)
            response = self._client.post(OVERPASS_URL, data={"data": ql})
        if response.status_code >= 400:
            raise RuntimeError(
                f"Overpass API {response.status_code}: {response.text[:400]}"
            )
        return response.json().get("elements", [])

    @staticmethod
    def _to_place(element: dict, query: str, area: str) -> Place | None:
        tags = element.get("tags") or {}
        name = tags.get("name") or tags.get("name:he") or tags.get("brand")
        if not name:
            return None

        if element.get("type") == "node":
            lat, lng = element.get("lat"), element.get("lon")
        else:
            center = element.get("center") or {}
            lat, lng = center.get("lat"), center.get("lon")

        street_line = f"{tags.get('addr:street', '')} {tags.get('addr:housenumber', '')}".strip()
        city = tags.get("addr:city", area)
        address = ", ".join(part for part in (street_line, city) if part)

        closed = any(key.startswith("disused:") for key in tags)
        osm_id = f"osm:{element.get('type')}:{element.get('id')}"

        return Place(
            place_id=osm_id,
            name=name,
            address=address,
            phone=tags.get("phone") or tags.get("contact:phone"),
            website=tags.get("website") or tags.get("contact:website"),
            rating=None,
            review_count=0,
            business_status="CLOSED_PERMANENTLY" if closed else "OPERATIONAL",
            maps_url=f"https://www.openstreetmap.org/{element.get('type')}/{element.get('id')}",
            lat=lat,
            lng=lng,
            source_query=query,
            source_area=area,
        )

    def close(self) -> None:
        self._client.close()
