"""Google Custom Search JSON API - "search the whole web" verification.

Google's Terms of Service forbid scraping google.com/search results, and any
scraper is blocked within a few hundred queries anyway. The Custom Search JSON
API is the supported way to query Google programmatically: create a
Programmable Search Engine set to "Search the entire web" and use its ID as
GOOGLE_CSE_ID.

Free tier: 100 queries/day. Paid: $5 per 1000, capped at 10k/day.
"""

from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)

ENDPOINT = "https://www.googleapis.com/customsearch/v1"


class GoogleCSEProvider:
    """Web search over the official Custom Search JSON API."""

    name = "google_cse"

    def __init__(
        self,
        api_key: str,
        cse_id: str,
        language: str = "he",
        region: str = "il",
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.cse_id = cse_id
        self.language = language
        self.region = region
        self._client = client or httpx.Client(timeout=30.0)
        self.queries_used = 0

    def search(self, query: str, num: int = 10) -> list[str]:
        params = {
            "key": self.api_key,
            "cx": self.cse_id,
            "q": query,
            "num": max(1, min(num, 10)),
            "hl": self.language,
            "gl": self.region,
        }
        try:
            response = self._client.get(ENDPOINT, params=params)
        except httpx.HTTPError as exc:
            log.warning("CSE request failed for %r: %s", query, exc)
            return []

        if response.status_code == 429:
            log.warning("CSE daily quota hit - skipping remaining web checks")
            raise QuotaExceeded("Custom Search quota exceeded")
        if response.status_code >= 400:
            log.warning("CSE %s for %r: %s", response.status_code, query,
                        response.text[:200])
            return []

        self.queries_used += 1
        items = response.json().get("items") or []
        time.sleep(0.2)
        return [item.get("link", "") for item in items if item.get("link")]

    def close(self) -> None:
        self._client.close()


class QuotaExceeded(RuntimeError):
    """Raised when the Custom Search daily quota is exhausted."""
