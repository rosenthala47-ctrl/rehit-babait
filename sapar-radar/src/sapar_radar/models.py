"""Core data types shared across the pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    """Why a shop is (or is not) a lead. Ordered best-lead-first."""

    NO_WEBSITE = "no_website"            # nothing at all - the ideal lead
    SOCIAL_ONLY = "social_only"          # only an Instagram/Facebook page
    BUILDER_NO_BOOKING = "builder_no_booking"   # Wix-style page, no booking
    WEBSITE_NO_BOOKING = "website_no_booking"   # real site, no booking flow
    HAS_BOOKING = "has_booking"          # already a customer of a competitor
    UNKNOWN = "unknown"                  # could not decide


#: Score attached to each verdict. Higher = better sales lead.
VERDICT_SCORES: dict[Verdict, int] = {
    Verdict.NO_WEBSITE: 100,
    Verdict.SOCIAL_ONLY: 85,
    Verdict.BUILDER_NO_BOOKING: 70,
    Verdict.WEBSITE_NO_BOOKING: 60,
    Verdict.UNKNOWN: 30,
    Verdict.HAS_BOOKING: 0,
}

#: Human-readable reason, shown in reports.
VERDICT_LABELS_HE: dict[Verdict, str] = {
    Verdict.NO_WEBSITE: "אין אתר בכלל",
    Verdict.SOCIAL_ONLY: "רק עמוד סושיאל",
    Verdict.BUILDER_NO_BOOKING: "אתר בונה-אתרים, בלי זימון תורים",
    Verdict.WEBSITE_NO_BOOKING: "יש אתר, אין זימון תורים",
    Verdict.HAS_BOOKING: "כבר יש מערכת תורים",
    Verdict.UNKNOWN: "לא ידוע",
}


def normalize_phone(raw: str | None, default_region: str = "972") -> str | None:
    """Best-effort E.164 normalisation, tuned for Israeli numbers.

    Deliberately dependency-free: we only need a stable key for de-duplication
    and a dialable string, not full libphonenumber validation.

    >>> normalize_phone("03-123-4567")
    '+97231234567'
    >>> normalize_phone("+972 52 555 1234")
    '+972525551234'
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw)
    if not digits:
        return None
    if digits.startswith("+"):
        return "+" + re.sub(r"\D", "", digits[1:])
    digits = re.sub(r"\D", "", digits)
    if digits.startswith("00"):
        return "+" + digits[2:]
    if digits.startswith("0"):
        return f"+{default_region}{digits[1:]}"
    if digits.startswith(default_region):
        return f"+{digits}"
    return f"+{default_region}{digits}"


@dataclass
class Place:
    """A barbershop as returned by the discovery provider."""

    place_id: str
    name: str
    address: str = ""
    phone: str | None = None
    phone_e164: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int = 0
    business_status: str = ""
    maps_url: str = ""
    lat: float | None = None
    lng: float | None = None
    source_query: str = ""
    source_area: str = ""

    def __post_init__(self) -> None:
        if self.phone_e164 is None:
            self.phone_e164 = normalize_phone(self.phone)

    @property
    def is_closed(self) -> bool:
        return self.business_status.upper() in {
            "CLOSED_PERMANENTLY",
            "CLOSED_TEMPORARILY",
        }


@dataclass
class Lead:
    """A classified place, ready to be reported."""

    place: Place
    verdict: Verdict
    score: int
    evidence: list[str] = field(default_factory=list)
    checked_at: str = ""

    def to_row(self) -> dict[str, Any]:
        """Flat dict for CSV/JSON export."""
        p = self.place
        return {
            "name": p.name,
            "phone": p.phone or "",
            "phone_e164": p.phone_e164 or "",
            "tel_link": f"tel:{p.phone_e164}" if p.phone_e164 else "",
            "address": p.address,
            "city": p.source_area,
            "verdict": self.verdict.value,
            "verdict_he": VERDICT_LABELS_HE[self.verdict],
            "score": self.score,
            "website": p.website or "",
            "rating": p.rating if p.rating is not None else "",
            "review_count": p.review_count,
            "maps_url": p.maps_url,
            "place_id": p.place_id,
            "evidence": " | ".join(self.evidence),
            "checked_at": self.checked_at,
        }

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d
