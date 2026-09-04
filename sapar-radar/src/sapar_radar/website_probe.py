"""Fetch a shop's homepage so the classifier can look for a booking widget."""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "sapar-radar/1.0 (+https://github.com/rosenthala47-ctrl/sapar-radar) "
    "lead-research bot"
)

#: Only HTML is useful, and only the first chunk of it - booking widgets and
#: their CTA text live in the header/hero, never 2 MB down the page.
MAX_BYTES = 400_000


class WebsiteProbe:
    """Best-effort homepage fetcher. Never raises: a failed probe is just
    'no evidence', which the classifier handles."""

    def __init__(self, timeout: float = 10.0, client: httpx.Client | None = None):
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "he,en;q=0.8"},
        )

    def fetch(self, url: str | None) -> str | None:
        """Return page HTML, or None if it could not be fetched."""
        if not url:
            return None
        if "://" not in url:
            url = "https://" + url
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            log.debug("probe failed for %s: %s", url, exc)
            return None

        if response.status_code >= 400:
            log.debug("probe %s -> HTTP %s", url, response.status_code)
            return None
        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return None
        return response.text[:MAX_BYTES]

    def close(self) -> None:
        self._client.close()
