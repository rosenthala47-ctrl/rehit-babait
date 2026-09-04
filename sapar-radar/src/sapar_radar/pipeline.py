"""The run loop: discover -> classify -> filter -> report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .classify import classify, domain_of
from .config import Config
from .models import Lead, Place, Verdict
from .providers.google_cse import QuotaExceeded
from .store import Store
from .website_probe import WebsiteProbe

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    discovered: int = 0
    unique: int = 0
    leads: int = 0
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class Pipeline:
    """Wires the providers, the classifier and the store together."""

    def __init__(
        self,
        config: Config,
        discovery,
        web_search=None,
        store: Store | None = None,
        probe: WebsiteProbe | None = None,
    ) -> None:
        self.config = config
        self.discovery = discovery
        self.web_search = web_search
        self.store = store
        self.probe = probe
        self.stats = RunStats()
        self._web_checks_left = int(
            config.get("classification.web_verify_max_per_run", 100)
        )

    def run(self, limit: int | None = None) -> list[Lead]:
        cfg = self.config
        leads: list[Lead] = []
        seen_ids: set[str] = set()
        seen_phones: set[str] = set()

        for area in cfg.areas:
            for query in cfg.queries:
                for place in self.discovery.search(
                    query, area, max_pages=int(cfg.get("search.max_pages_per_query", 3))
                ):
                    self.stats.discovered += 1

                    if place.place_id in seen_ids:
                        self.stats.skip("duplicate_place_id")
                        continue
                    seen_ids.add(place.place_id)

                    if place.phone_e164 and place.phone_e164 in seen_phones:
                        self.stats.skip("duplicate_phone")
                        continue

                    self.stats.unique += 1
                    lead = self._evaluate(place)
                    if lead is None:
                        continue

                    if place.phone_e164:
                        seen_phones.add(place.phone_e164)
                    leads.append(lead)
                    self.stats.leads += 1

                    if limit and len(leads) >= limit:
                        log.info("hit --limit %d, stopping discovery", limit)
                        return self._sorted(leads)

        return self._sorted(leads)

    # -- per-place decision ------------------------------------------------
    def _evaluate(self, place: Place) -> Lead | None:
        cfg = self.config

        if cfg.get("filters.skip_closed", True) and place.is_closed:
            self.stats.skip("closed")
            return None

        if cfg.get("filters.require_phone", True) and not place.phone_e164:
            self.stats.skip("no_phone")
            return None

        if self._is_blocked(place):
            self.stats.skip("do_not_contact")
            return None

        min_reviews = int(cfg.get("filters.min_review_count", 0))
        if min_reviews and place.review_count < min_reviews:
            self.stats.skip("too_few_reviews")
            return None

        if (
            cfg.get("filters.skip_already_reported", True)
            and self.store
            and self.store.seen_before(place.place_id, place.phone_e164)
        ):
            self.stats.skip("already_reported")
            return None

        page_html = None
        probed = bool(
            cfg.get("classification.probe_websites", True)
            and place.website
            and self.probe
        )
        if probed:
            page_html = self.probe.fetch(place.website)

        web_hits = self._web_verify(place)

        verdict, score, evidence = classify(
            place,
            cfg.platforms,
            page_html=page_html,
            web_hits=web_hits,
            treat_builders_as=str(cfg.get("classification.treat_builders_as", "demote")),
            probed=probed,
        )

        lead = Lead(
            place=place,
            verdict=verdict,
            score=score,
            evidence=evidence,
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

        if verdict is Verdict.HAS_BOOKING or score < cfg.min_score:
            self.stats.skip(
                "has_booking" if verdict is Verdict.HAS_BOOKING else "below_min_score"
            )
            if self.store:
                self.store.upsert(lead, reported=False)
            return None

        if self.store:
            self.store.upsert(lead, reported=True)
        return lead

    def _is_blocked(self, place: Place) -> bool:
        block = self.config.do_not_contact
        return bool(block) and (
            place.place_id in block or (place.phone_e164 or "") in block
        )

    def _web_verify(self, place: Place) -> list[str] | None:
        """Ask Google whether this shop has a profile on a booking platform."""
        if not self.config.get("classification.web_verify", False):
            return None
        if not self.web_search or self._web_checks_left <= 0:
            return None

        terms = [f'"{place.name}"']
        if place.source_area:
            terms.append(place.source_area)
        terms.append("תור OR booking OR appointment")
        try:
            urls = self.web_search.search(" ".join(terms), num=10)
        except QuotaExceeded:
            log.warning("web verification disabled for the rest of this run (quota)")
            self._web_checks_left = 0
            return None

        self._web_checks_left -= 1
        hits = sorted(
            {
                d
                for url in urls
                for d in self.config.platforms.booking
                if domain_of(url) == d or domain_of(url).endswith("." + d)
            }
        )
        return hits or None

    @staticmethod
    def _sorted(leads: list[Lead]) -> list[Lead]:
        return sorted(
            leads, key=lambda l: (-l.score, -l.place.review_count, l.place.name)
        )
