import pytest

from sapar_radar.models import Place, Verdict, VERDICT_SCORES, normalize_phone


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("03-123-4567", "+97231234567"),
        ("+972 52 555 1234", "+972525551234"),
        ("0525551234", "+972525551234"),
        ("00972501112222", "+972501112222"),
        ("972-3-9999999", "+97239999999"),
        (None, None),
        ("", None),
        ("לא זמין", None),
    ],
)
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


def test_place_normalizes_phone_on_construction():
    assert Place(place_id="x", name="n", phone="03-1112222").phone_e164 == "+97231112222"


@pytest.mark.parametrize("status", ["CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"])
def test_closed_detection(status):
    assert Place(place_id="x", name="n", business_status=status).is_closed


def test_operational_is_not_closed():
    assert not Place(place_id="x", name="n", business_status="OPERATIONAL").is_closed


def test_has_booking_scores_zero():
    """A shop with a booking system must never outrank a real lead."""
    assert VERDICT_SCORES[Verdict.HAS_BOOKING] == 0
    assert VERDICT_SCORES[Verdict.NO_WEBSITE] == max(VERDICT_SCORES.values())
