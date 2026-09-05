"""Persistent lead storage via GitHub's Contents API.

Streamlit Community Cloud's local filesystem is ephemeral - every redeploy
or reboot wipes it, taking the SQLite file used by the CLI's `Store` with
it. Committing the accumulated leads as one JSON file straight into the
GitHub repo the app already lives in survives all of that, and reuses an
account the user already has instead of standing up a new database
service.

`GitHubStoreAdapter` exposes the same methods the CLI's `Store` does
(`seen_before`, `upsert`, `start_run`, `finish_run`, `mark`, `close`) so
`Pipeline` and the web app can use either one interchangeably. Unlike
`Store`, which commits every write to disk immediately (cheap - it's a
local file), this adapter reads the file once at construction and batches
every write from a run into a single commit on `finish_run`/`mark` - a
network round-trip per shop would be slow and would spam the repo with
one commit per shop found.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from .models import Lead
from .store import CONTACT_STATUSES

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"


class GitHubStoreError(RuntimeError):
    """Talking to the GitHub Contents API failed (bad token, 404 repo/branch,
    network error, ...) - distinct from a plain RuntimeError so callers can
    show a specific "couldn't reach GitHub" message."""


class _Conflict(Exception):
    """Internal: the file changed remotely between our read and our write."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class GitHubStoreAdapter:
    """Same interface as `sapar_radar.store.Store`, backed by one JSON file
    in a GitHub repo instead of a local SQLite file."""

    def __init__(
        self,
        token: str,
        repo: str,
        path: str,
        branch: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.repo = repo
        self.path = path
        self.branch = branch
        self._client = client or httpx.Client(
            base_url=API_ROOT,
            timeout=20.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self.records, self._sha = self._get_file()
        # Only the records touched this session - what a conflict retry
        # re-applies on top of the freshest remote copy.
        self._touched: dict[str, dict[str, Any]] = {}

    # -- low-level GitHub Contents API ------------------------------------
    def _get_file(self) -> tuple[dict[str, dict], str | None]:
        try:
            response = self._client.get(
                f"/repos/{self.repo}/contents/{self.path}",
                params={"ref": self.branch},
            )
        except httpx.HTTPError as exc:
            raise GitHubStoreError(f"could not reach GitHub: {exc}") from exc
        if response.status_code == 404:
            return {}, None
        if response.status_code >= 400:
            raise GitHubStoreError(
                f"GitHub API {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        raw = base64.b64decode(data["content"]).decode("utf-8")
        try:
            records = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            records = {}
        return records, data["sha"]

    def _put_file(self, records: dict[str, dict], sha: str | None, message: str) -> str:
        body: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(
                json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
            ).decode("ascii"),
            "branch": self.branch,
        }
        if sha:
            body["sha"] = sha
        try:
            response = self._client.put(
                f"/repos/{self.repo}/contents/{self.path}", json=body
            )
        except httpx.HTTPError as exc:
            raise GitHubStoreError(f"could not reach GitHub: {exc}") from exc
        if response.status_code == 409:
            raise _Conflict()
        if response.status_code >= 400:
            raise GitHubStoreError(
                f"GitHub API {response.status_code}: {response.text[:300]}"
            )
        return response.json()["content"]["sha"]

    def _flush(self, message: str) -> None:
        """Push everything touched this session as a single commit,
        re-merging onto the latest remote copy once if it moved under us."""
        if not self._touched:
            return
        try:
            self._sha = self._put_file(self.records, self._sha, message)
        except _Conflict:
            fresh_records, fresh_sha = self._get_file()
            fresh_records.update(self._touched)
            self.records = fresh_records
            self._sha = self._put_file(fresh_records, fresh_sha, message)
        self._touched.clear()

    # -- Store-compatible interface ---------------------------------------
    def seen_before(self, place_id: str, phone_e164: str | None) -> bool:
        record = self.records.get(place_id)
        if record and record.get("reported_at"):
            return True
        if phone_e164:
            for r in self.records.values():
                if r.get("phone_e164") == phone_e164 and r.get("reported_at"):
                    return True
        return False

    def upsert(self, lead: Lead, reported: bool) -> None:
        p = lead.place
        now = _now()
        existing = self.records.get(p.place_id)
        existing_status = existing.get("contact_status", "new") if existing else "new"
        new_status = "reported" if (existing_status == "new" and reported) else existing_status
        record = {
            "place_id": p.place_id,
            "name": p.name,
            "phone_e164": p.phone_e164,
            "address": p.address,
            "city": p.source_area,
            "website": p.website,
            "verdict": lead.verdict.value,
            "score": lead.score,
            "evidence": " | ".join(lead.evidence),
            "rating": p.rating,
            "review_count": p.review_count,
            "maps_url": p.maps_url,
            "first_seen": existing["first_seen"] if existing else now,
            "last_seen": now,
            "reported_at": (existing.get("reported_at") if existing else None) or (now if reported else None),
            "contact_status": new_status,
            "notes": existing.get("notes") if existing else None,
        }
        self.records[p.place_id] = record
        self._touched[p.place_id] = record

    def start_run(self, provider: str) -> int:
        return 0

    def finish_run(self, run_id: int, discovered: int, new_leads: int) -> None:
        self._flush(f"sapar-radar: search run ({discovered} scanned, {new_leads} new leads)")

    def mark(self, identifier: str, status: str, notes: str | None = None) -> int:
        if status not in CONTACT_STATUSES:
            raise ValueError(
                f"unknown status {status!r}; expected one of {CONTACT_STATUSES}"
            )
        updated = 0
        for key, record in self.records.items():
            if record.get("place_id") == identifier or record.get("phone_e164") == identifier:
                record["contact_status"] = status
                if notes:
                    record["notes"] = notes
                self._touched[key] = record
                updated += 1
        if updated:
            self._flush(f"sapar-radar: mark {identifier} as {status}")
        return updated

    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.records.values():
            status = r.get("contact_status", "new")
            out[status] = out.get(status, 0) + 1
        out["total"] = len(self.records)
        return out

    def all_records(self, limit: int = 1000) -> list[dict]:
        rows = [r for r in self.records.values() if r.get("reported_at")]
        rows.sort(key=lambda r: (r.get("score") or 0, r.get("reported_at") or ""), reverse=True)
        return rows[:limit]

    def close(self) -> None:
        self._client.close()
