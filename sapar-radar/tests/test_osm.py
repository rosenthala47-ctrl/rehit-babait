"""Tests for the OpenStreetMap discovery provider - no API key needed."""

from __future__ import annotations

import httpx

from sapar_radar.providers.osm import OSMProvider


class FakeOSMClient:
    """Stands in for httpx.Client: returns canned Nominatim + Overpass replies."""

    def __init__(self, geocode_payload, overpass_payload):
        self.geocode_payload = geocode_payload
        self.overpass_payload = overpass_payload
        self.calls: list[str] = []
        self.last_overpass_query: str | None = None

    def get(self, url, params=None):
        self.calls.append("geocode")
        return httpx.Response(
            200, json=self.geocode_payload, request=httpx.Request("GET", url)
        )

    def post(self, url, data=None):
        self.calls.append("overpass")
        self.last_overpass_query = (data or {}).get("data")
        return httpx.Response(
            200, json=self.overpass_payload, request=httpx.Request("POST", url)
        )

    def close(self) -> None:
        pass


GEOCODE_OK = [{"boundingbox": ["32.05", "32.10", "34.75", "34.80"]}]

OVERPASS_HAIRDRESSERS = {
    "elements": [
        {
            "type": "node",
            "id": 111,
            "lat": 32.08,
            "lon": 34.78,
            "tags": {
                "shop": "hairdresser",
                "name": "מספרת אבי",
                "addr:street": "אלנבי",
                "addr:housenumber": "10",
                "addr:city": "תל אביב",
                "phone": "03-1112222",
            },
        },
        {
            "type": "way",
            "id": 222,
            "center": {"lat": 32.09, "lon": 34.79},
            "tags": {
                "shop": "hairdresser",
                "name": "הספר הישן",
                "website": "https://old-barber.co.il",
            },
        },
        {
            # No name tag - useless as a lead, should be skipped.
            "type": "node",
            "id": 333,
            "lat": 32.07,
            "lon": 34.77,
            "tags": {"shop": "hairdresser"},
        },
        {
            "type": "node",
            "id": 444,
            "lat": 32.06,
            "lon": 34.76,
            "tags": {
                "shop": "hairdresser",
                "name": "מספרה סגורה",
                "disused:shop": "hairdresser",
            },
        },
    ]
}


def _provider(geocode=GEOCODE_OK, overpass=OVERPASS_HAIRDRESSERS) -> tuple[OSMProvider, FakeOSMClient]:
    client = FakeOSMClient(geocode, overpass)
    return OSMProvider(client=client, nominatim_pause_seconds=0), client


def test_finds_shops_with_a_name():
    provider, _ = _provider()
    places = list(provider.search("מספרה", "תל אביב"))
    assert {p.name for p in places} == {"מספרת אבי", "הספר הישן", "מספרה סגורה"}


def test_skips_elements_without_a_name():
    provider, _ = _provider()
    places = list(provider.search("מספרה", "תל אביב"))
    assert all(p.place_id != "osm:node:333" for p in places)


def test_disused_shop_is_marked_closed():
    provider, _ = _provider()
    places = {p.name: p for p in provider.search("מספרה", "תל אביב")}
    assert places["מספרה סגורה"].is_closed


def test_phone_and_website_pulled_from_tags():
    provider, _ = _provider()
    places = {p.name: p for p in provider.search("מספרה", "תל אביב")}
    assert places["מספרת אבי"].phone == "03-1112222"
    assert places["מספרת אבי"].phone_e164 == "+97231112222"
    assert places["הספר הישן"].website == "https://old-barber.co.il"


def test_place_id_is_stable_and_namespaced():
    provider, _ = _provider()
    places = {p.name: p for p in provider.search("מספרה", "תל אביב")}
    assert places["מספרת אבי"].place_id == "osm:node:111"
    assert places["הספר הישן"].place_id == "osm:way:222"


def test_second_search_for_the_same_area_does_not_refetch():
    provider, client = _provider()
    list(provider.search("מספרה", "תל אביב"))
    list(provider.search("ברברשופ", "תל אביב"))  # same area, different query
    assert client.calls == ["geocode", "overpass"]


def test_unknown_area_yields_nothing():
    provider, _ = _provider(geocode=[], overpass={"elements": []})
    places = list(provider.search("מספרה", "מקום שלא קיים"))
    assert places == []


def test_query_matches_hairdresser_by_regex_not_exact_equality():
    # A plain `="hairdresser"` filter silently misses compound OSM tags like
    # "hairdresser;beauty", which real-world mappers use often. Overpass's
    # `~` regex operator catches those too - assert we actually use it.
    provider, client = _provider()
    list(provider.search("מספרה", "תל אביב"))
    assert '["shop"~"hairdresser"]' in client.last_overpass_query
    assert '="hairdresser"' not in client.last_overpass_query


def test_compound_shop_tag_is_still_picked_up():
    overpass = {
        "elements": [
            {
                "type": "node",
                "id": 555,
                "lat": 32.08,
                "lon": 34.78,
                "tags": {"shop": "hairdresser;beauty", "name": "סטודיו משולב"},
            }
        ]
    }
    provider, _ = _provider(overpass=overpass)
    places = list(provider.search("מספרה", "תל אביב"))
    assert {p.name for p in places} == {"סטודיו משולב"}
