"""The classifier decides who gets called. These are the load-bearing tests."""

import pytest

from sapar_radar.classify import classify, domain_of
from sapar_radar.models import Place, Verdict


def shop(**kwargs) -> Place:
    base = dict(place_id="p1", name="מספרת בדיקה", phone="03-1234567")
    base.update(kwargs)
    return Place(**base)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.Booksy.com/en-us/x", "booksy.com"),
        ("http://example.co.il/path", "example.co.il"),
        ("example.com", "example.com"),
        (None, ""),
        ("", ""),
    ],
)
def test_domain_of(url, expected):
    assert domain_of(url) == expected


def test_no_website_is_the_best_lead(platforms):
    verdict, score, evidence = classify(shop(website=None), platforms)
    assert verdict is Verdict.NO_WEBSITE
    assert score == 100
    assert evidence


def test_instagram_only_is_a_lead(platforms):
    verdict, score, _ = classify(
        shop(website="https://instagram.com/barber.tlv"), platforms
    )
    assert verdict is Verdict.SOCIAL_ONLY
    assert score == 85


@pytest.mark.parametrize(
    "url",
    [
        "https://booksy.com/en-il/12345_shop",
        "https://www.fresha.com/a/shop",
        "https://nello.co.il/shop",          # Israeli platform
        "https://app.arbox.co.il/booking",   # subdomain must still match
        "https://book.squareup.com/appointments/x",  # host+path entry
    ],
)
def test_booking_platform_website_is_not_a_lead(platforms, url):
    verdict, score, _ = classify(shop(website=url), platforms)
    assert verdict is Verdict.HAS_BOOKING
    assert score == 0


def test_homepage_linking_to_a_platform_disqualifies(platforms):
    html = '<a href="https://booksy.com/en-il/999">קבע תור</a>'
    verdict, _, evidence = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.HAS_BOOKING
    assert "booksy.com" in evidence[0]


def test_hebrew_booking_cta_disqualifies(platforms):
    html = "<html><body><button>קביעת תור אונליין</button></body></html>"
    verdict, _, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.HAS_BOOKING


def test_real_site_without_booking_is_a_lead(platforms):
    html = "<html><body><h1>מספרה</h1><p>שעות פתיחה 9-19. התקשרו אלינו.</p></body></html>"
    verdict, score, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.WEBSITE_NO_BOOKING
    assert score == 60


def test_unprobed_website_is_unknown_not_a_lead(platforms):
    """With probing off we must not claim a site has no booking system."""
    verdict, score, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=None
    )
    assert verdict is Verdict.UNKNOWN
    assert score < 60


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("demote", Verdict.BUILDER_NO_BOOKING),
        ("lead", Verdict.WEBSITE_NO_BOOKING),
        ("skip", Verdict.HAS_BOOKING),
    ],
)
def test_site_builder_policy(platforms, mode, expected):
    verdict, _, _ = classify(
        shop(website="https://shop.wixsite.com/home"),
        platforms,
        treat_builders_as=mode,
    )
    assert verdict is expected


def test_web_hit_overrides_missing_website(platforms):
    """A Booksy profile found by web search beats 'no website on Maps'."""
    verdict, _, evidence = classify(
        shop(website=None), platforms, web_hits=["booksy.com"]
    )
    assert verdict is Verdict.HAS_BOOKING
    assert "booksy.com" in evidence[0]


def test_unreachable_site_is_reported_as_unreachable(platforms):
    """A probe that failed must not be described as 'probing was off'."""
    verdict, score, evidence = classify(
        shop(website="https://down.example.co.il"),
        platforms,
        page_html=None,
        probed=True,
    )
    assert verdict is Verdict.UNKNOWN
    assert score < 60
    assert "לא נגיש" in evidence[-1]
    assert "כבוי" not in evidence[-1]


def test_unprobed_site_says_probing_was_off(platforms):
    _, _, evidence = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=None, probed=False
    )
    assert "כבוי" in evidence[-1]


def test_domain_match_requires_a_host_boundary(platforms):
    """'nello.co.il' must not match inside 'antonello.co.il'."""
    html = '<a href="https://antonello.co.il">האתר שלנו</a>'
    verdict, _, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.WEBSITE_NO_BOOKING


def test_real_platform_link_still_matches_with_boundary(platforms):
    html = '<iframe src="https://www.booksy.com/widget/1"></iframe>'
    verdict, _, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.HAS_BOOKING


def test_subdomain_of_a_platform_still_matches(platforms):
    html = '<script src="https://cdn.fresha.com/widget.js"></script>'
    verdict, _, _ = classify(
        shop(website="https://mybarber.co.il"), platforms, page_html=html
    )
    assert verdict is Verdict.HAS_BOOKING
