"""Decide whether a barbershop is a lead.

The whole product is this file: given what Google knows about a shop, does it
already have a way for customers to book online? If not, it is a lead.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .config import Platforms
from .models import Place, Verdict, VERDICT_SCORES


def domain_of(url: str | None) -> str:
    """Registrable-ish host of a URL, lowercased, without 'www.'.

    >>> domain_of("https://www.Booksy.com/en-us/x")
    'booksy.com'
    """
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _matches(url: str | None, domains: set[str]) -> str | None:
    """Return the domain entry that `url` belongs to, if any.

    Entries may be bare hosts ('booksy.com') or host+path prefixes
    ('squareup.com/appointments'). Host matching covers subdomains.
    """
    if not url:
        return None
    host = domain_of(url)
    if not host:
        return None
    parsed = urlparse(url if "://" in url else "https://" + url)
    full = f"{host}{parsed.path}".rstrip("/").lower()
    for entry in domains:
        if "/" in entry:
            if full.startswith(entry.rstrip("/")):
                return entry
        elif host == entry or host.endswith("." + entry):
            return entry
    return None


def find_booking_links(html: str, platforms: Platforms) -> list[str]:
    """Booking-platform domains referenced anywhere in a page's HTML.

    A plain substring test is not enough: 'tor.co.il' is a substring of
    'creator.co.il', which would disqualify a perfectly good lead. The domain
    must start at a host boundary: the preceding character may not be a letter,
    digit or hyphen. A dot is allowed, so subdomains ('cdn.fresha.com') and
    'www.' prefixes still match.
    """
    lowered = html.lower()
    return sorted(
        {
            d
            for d in platforms.booking
            if re.search(r"(?<![\w-])" + re.escape(d), lowered)
        }
    )


def find_booking_keywords(html: str, platforms: Platforms) -> list[str]:
    """Booking call-to-action phrases present in a page."""
    lowered = html.lower()
    return [k for k in platforms.keywords if k in lowered]


def classify(
    place: Place,
    platforms: Platforms,
    page_html: str | None = None,
    web_hits: list[str] | None = None,
    treat_builders_as: str = "demote",
    probed: bool = False,
) -> tuple[Verdict, int, list[str]]:
    """Classify one shop.

    Args:
        place: the shop, as discovered.
        platforms: domain/keyword lists from config.
        page_html: homepage HTML, if it was fetched.
        web_hits: booking-platform domains found by a web search for this shop.
        treat_builders_as: 'lead' | 'demote' | 'skip' for DIY site builders.
        probed: whether a fetch was attempted. Distinguishes "we did not look"
            from "we looked and the site was unreachable" - an unreachable site
            may well have a booking widget, so neither may be called a lead.

    Returns:
        (verdict, score, evidence lines)
    """
    evidence: list[str] = []

    # A profile on a booking platform found anywhere on the web disqualifies
    # the shop even if Google Maps shows no website at all.
    if web_hits:
        evidence.append(f"נמצא בחיפוש אינטרנט על: {', '.join(web_hits)}")
        return Verdict.HAS_BOOKING, VERDICT_SCORES[Verdict.HAS_BOOKING], evidence

    website = (place.website or "").strip()

    if not website:
        evidence.append("אין אתר ברישום גוגל")
        return Verdict.NO_WEBSITE, VERDICT_SCORES[Verdict.NO_WEBSITE], evidence

    # The listed "website" is itself a booking platform.
    booking_domain = _matches(website, platforms.booking)
    if booking_domain:
        evidence.append(f"האתר הרשום הוא פלטפורמת תורים: {booking_domain}")
        return Verdict.HAS_BOOKING, VERDICT_SCORES[Verdict.HAS_BOOKING], evidence

    social_domain = _matches(website, platforms.social)
    builder_domain = _matches(website, platforms.builders)

    # Homepage evidence overrides URL-shape guessing.
    if page_html:
        links = find_booking_links(page_html, platforms)
        if links:
            evidence.append(f"האתר מקשר לפלטפורמת תורים: {', '.join(links)}")
            return Verdict.HAS_BOOKING, VERDICT_SCORES[Verdict.HAS_BOOKING], evidence
        keywords = find_booking_keywords(page_html, platforms)
        if keywords:
            evidence.append(f"נמצאו ביטויי זימון תור באתר: {', '.join(keywords[:3])}")
            return Verdict.HAS_BOOKING, VERDICT_SCORES[Verdict.HAS_BOOKING], evidence
        evidence.append("נסרק דף הבית - לא נמצא מנגנון זימון תורים")

    if social_domain:
        evidence.append(f"ה'אתר' הוא עמוד סושיאל בלבד: {social_domain}")
        return Verdict.SOCIAL_ONLY, VERDICT_SCORES[Verdict.SOCIAL_ONLY], evidence

    if builder_domain:
        evidence.append(f"אתר על פלטפורמת בונה-אתרים: {builder_domain}")
        if treat_builders_as == "skip":
            return Verdict.HAS_BOOKING, 0, evidence  # filtered out downstream
        verdict = (
            Verdict.WEBSITE_NO_BOOKING
            if treat_builders_as == "lead"
            else Verdict.BUILDER_NO_BOOKING
        )
        return verdict, VERDICT_SCORES[verdict], evidence

    if page_html is None:
        evidence.append(
            f"האתר {domain_of(website)} לא נגיש לסריקה - לא ניתן לקבוע"
            if probed
            else "יש אתר, לא נסרק (probe_websites כבוי)"
        )
        return Verdict.UNKNOWN, VERDICT_SCORES[Verdict.UNKNOWN], evidence

    evidence.append(f"יש אתר ({domain_of(website)}) אך ללא זימון תורים")
    return (
        Verdict.WEBSITE_NO_BOOKING,
        VERDICT_SCORES[Verdict.WEBSITE_NO_BOOKING],
        evidence,
    )
