"""Tests for the GitHub-backed persistent store used by the web app."""

from __future__ import annotations

import base64
import json as jsonlib

import httpx
import pytest

from sapar_radar.github_store import GitHubStoreAdapter, GitHubStoreError
from sapar_radar.models import Lead, Place, Verdict


class FakeGitHubClient:
    """Simulates just enough of the GitHub Contents API for one file."""

    def __init__(self, initial_records: dict | None = None, exists: bool = True):
        self._remote = dict(initial_records or {})
        self._sha = "sha-0" if exists else None
        self._version = 0
        self.get_calls = 0
        self.put_calls = 0
        self.force_conflict_once = False

    def get(self, url, params=None):
        self.get_calls += 1
        if self._sha is None:
            return httpx.Response(
                404, json={"message": "Not Found"}, request=httpx.Request("GET", url)
            )
        content = base64.b64encode(
            jsonlib.dumps(self._remote, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        return httpx.Response(
            200,
            json={"content": content, "sha": self._sha},
            request=httpx.Request("GET", url),
        )

    def put(self, url, json=None):
        self.put_calls += 1
        body = json
        if self.force_conflict_once:
            self.force_conflict_once = False
            return httpx.Response(
                409, json={"message": "conflict"}, request=httpx.Request("PUT", url)
            )
        if body.get("sha") != self._sha:
            return httpx.Response(
                409, json={"message": "sha mismatch"}, request=httpx.Request("PUT", url)
            )
        decoded = jsonlib.loads(base64.b64decode(body["content"]).decode("utf-8"))
        self._remote = decoded
        self._version += 1
        self._sha = f"sha-{self._version}"
        return httpx.Response(
            200,
            json={"content": {"sha": self._sha}},
            request=httpx.Request("PUT", url),
        )

    def close(self) -> None:
        pass


def _lead(place_id="p1", name="מספרת בדיקה", phone="03-1234567", score=100) -> Lead:
    return Lead(
        place=Place(place_id=place_id, name=name, phone=phone),
        verdict=Verdict.NO_WEBSITE,
        score=score,
        evidence=["no website field"],
        checked_at="2026-09-05T00:00:00+00:00",
    )


def _adapter(client=None, initial_records=None, exists=True) -> GitHubStoreAdapter:
    fake = client or FakeGitHubClient(initial_records, exists=exists)
    return GitHubStoreAdapter(
        token="fake-token", repo="me/repo", path="data/leads.json",
        branch="main", client=fake,
    ), fake


def test_seen_before_is_false_when_file_does_not_exist_yet():
    adapter, _ = _adapter(exists=False)
    assert adapter.seen_before("p1", "+97231234567") is False


def test_upsert_then_seen_before_true_only_after_reported():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(), reported=False)
    assert adapter.seen_before("p1", "+97231234567") is False

    adapter.upsert(_lead(), reported=True)
    assert adapter.seen_before("p1", "+97231234567") is True


def test_seen_before_matches_by_phone_across_different_place_ids():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(place_id="p1", phone="03-1234567"), reported=True)
    assert adapter.seen_before("some-other-place-id", "+97231234567") is True


def test_first_seen_is_preserved_across_repeated_upserts():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(), reported=True)
    first_seen = adapter.records["p1"]["first_seen"]

    adapter.upsert(_lead(name="שם עודכן"), reported=True)
    assert adapter.records["p1"]["first_seen"] == first_seen
    assert adapter.records["p1"]["name"] == "שם עודכן"


def test_contact_status_moves_to_reported_only_from_new():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(), reported=True)
    assert adapter.records["p1"]["contact_status"] == "reported"

    adapter.mark("p1", "interested")
    assert adapter.records["p1"]["contact_status"] == "interested"

    # A later upsert (e.g. re-discovered in a new search) must not reset a
    # status the user already set by hand.
    adapter.upsert(_lead(), reported=True)
    assert adapter.records["p1"]["contact_status"] == "interested"


def test_finish_run_batches_every_upsert_into_a_single_commit():
    adapter, fake = _adapter(exists=False)
    adapter.upsert(_lead(place_id="p1"), reported=True)
    adapter.upsert(_lead(place_id="p2"), reported=True)
    adapter.upsert(_lead(place_id="p3"), reported=False)
    assert fake.put_calls == 0  # nothing pushed yet

    adapter.finish_run(run_id=0, discovered=3, new_leads=2)
    assert fake.put_calls == 1
    assert set(fake._remote.keys()) == {"p1", "p2", "p3"}


def test_mark_updates_by_phone_and_rejects_unknown_status():
    adapter, fake = _adapter(exists=False)
    adapter.upsert(_lead(place_id="p1", phone="03-1234567"), reported=True)
    adapter.finish_run(0, 1, 1)

    updated = adapter.mark("+97231234567", "contacted")
    assert updated == 1
    assert adapter.records["p1"]["contact_status"] == "contacted"

    with pytest.raises(ValueError):
        adapter.mark("+97231234567", "not-a-real-status")


def test_conflict_on_push_retries_by_merging_onto_latest_remote():
    adapter, fake = _adapter(exists=False)
    adapter.upsert(_lead(place_id="p1"), reported=True)

    # Simulate someone else committing a change between our read and write.
    fake._remote["p2"] = {"place_id": "p2", "name": "אחר", "contact_status": "new"}
    fake._version += 1
    fake._sha = f"sha-{fake._version}"
    adapter.finish_run(0, 1, 1)

    assert fake.put_calls == 2  # first attempt conflicts, second succeeds
    assert "p1" in fake._remote  # our change survived the retry
    assert "p2" in fake._remote  # the concurrent change was not clobbered


def test_all_records_only_includes_reported_leads_sorted_best_first():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(place_id="low", score=60), reported=True)
    adapter.upsert(_lead(place_id="high", score=100), reported=True)
    adapter.upsert(_lead(place_id="unreported", score=90), reported=False)

    rows = adapter.all_records()
    assert [r["place_id"] for r in rows] == ["high", "low"]


def test_stats_counts_by_contact_status():
    adapter, _ = _adapter(exists=False)
    adapter.upsert(_lead(place_id="p1"), reported=True)
    adapter.upsert(_lead(place_id="p2"), reported=True)
    adapter.mark("p2", "customer")

    stats = adapter.stats()
    assert stats["total"] == 2
    assert stats["reported"] == 1
    assert stats["customer"] == 1


def test_unreachable_github_raises_a_clear_error():
    class BoomingClient:
        def get(self, url, params=None):
            raise httpx.ConnectError("boom")

        def close(self) -> None:
            pass

    with pytest.raises(GitHubStoreError):
        GitHubStoreAdapter(
            token="t", repo="me/repo", path="data/leads.json",
            branch="main", client=BoomingClient(),
        )
