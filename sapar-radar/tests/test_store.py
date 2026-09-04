from datetime import datetime, timezone

import pytest

from sapar_radar.models import Lead, Place, Verdict
from sapar_radar.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


def make_lead(place_id="p1", phone="03-1112222", score=100) -> Lead:
    return Lead(
        place=Place(place_id=place_id, name="מספרה", phone=phone),
        verdict=Verdict.NO_WEBSITE,
        score=score,
        evidence=["אין אתר"],
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def test_unreported_place_is_not_seen_before(store):
    store.upsert(make_lead(), reported=False)
    assert not store.seen_before("p1", "+97231112222")


def test_reported_place_is_seen_before(store):
    store.upsert(make_lead(), reported=True)
    assert store.seen_before("p1", "+97231112222")


def test_dedupe_matches_on_phone_across_place_ids(store):
    """The same shop often has several Google listings; the phone catches it."""
    store.upsert(make_lead(place_id="p1"), reported=True)
    assert store.seen_before("p2_different_listing", "+97231112222")


def test_upsert_is_idempotent_and_keeps_reported_at(store):
    store.upsert(make_lead(), reported=True)
    store.upsert(make_lead(score=85), reported=False)
    rows = store.reported_rows()
    assert len(rows) == 1
    assert rows[0]["score"] == 85          # refreshed
    assert rows[0]["reported_at"] is not None  # but still reported


def test_mark_updates_by_phone_and_rejects_bad_status(store):
    store.upsert(make_lead(), reported=True)
    assert store.mark("+97231112222", "not_interested", "ביקש להסיר") == 1
    with pytest.raises(ValueError):
        store.mark("+97231112222", "bogus_status")


def test_mark_preserves_status_on_later_runs(store):
    store.upsert(make_lead(), reported=True)
    store.mark("p1", "customer")
    store.upsert(make_lead(), reported=True)
    row = store.reported_rows()[0]
    assert row["contact_status"] == "customer"


def test_stats_counts(store):
    store.upsert(make_lead(place_id="p1", phone="03-1112222"), reported=True)
    store.upsert(make_lead(place_id="p2", phone="03-3334444"), reported=False)
    stats = store.stats()
    assert stats["total"] == 2
    assert stats["reported"] == 1
    assert stats["verdict:no_website"] == 1
