"""בדיקות לפנקס ההסכמות וליומן הביקורת."""
import pytest

from risk_rating.consent import ConsentLedger, mask_nid
from risk_rating.service import RiskRatingService


# ── the ledger itself ────────────────────────────────────────────────────────
def test_grant_is_findable_while_valid():
    led = ConsentLedger()
    g = led.grant("034512789", "C1001", "vehicle", 100000, ttl_days=30)
    assert g["consent_id"].startswith("CONSENT-")
    active = led.active_consent("34512789")           # leading zero restored
    assert active is not None
    assert active["consent_id"] == g["consent_id"]


def test_expired_consent_is_not_active():
    led = ConsentLedger()
    led.grant("034512789", "C1001", "vehicle", 100000, ttl_days=-1)  # already expired
    assert led.active_consent("034512789") is None


def test_pull_is_recorded_and_masked():
    led = ConsentLedger()
    g = led.grant("034512789", "C1001", "vehicle", 100000)
    led.record_pull(g["consent_id"], "034512789", "mock", ["missed_payments_12m"], "ok")
    events = led.events("034512789")
    assert [e["type"] for e in events] == ["grant", "pull"]
    # audit view masks the national id
    masked = led.audit_view("034512789")
    assert all("034512789" not in e["national_id"] for e in masked)
    assert masked[0]["national_id"].endswith("2789")


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = ConsentLedger(path)
    g = led.grant("034512789", "C1001", "vehicle", 100000)
    led.record_pull(g["consent_id"], "034512789", "mock", ["x"], "ok")
    # a fresh ledger reads the persisted file
    reloaded = ConsentLedger(path)
    assert len(reloaded.events()) == 2
    assert reloaded.active_consent("034512789")["consent_id"] == g["consent_id"]


def test_mask_nid():
    assert mask_nid("034512789") == "•••••2789"
    assert mask_nid("") == ""


# ── the consent flow through the service ─────────────────────────────────────
@pytest.fixture()
def service():
    # fresh in-memory ledger per test
    return RiskRatingService(consent_ledger=ConsentLedger())


def test_assess_records_consent_and_pull(service):
    r = service.assess("C1001", 100000, purpose="vehicle")
    consent = r.external_report["consent"]
    assert consent["consent_id"]
    assert consent["reused"] is False           # freshly granted
    events = service.audit_trail("C1001")
    assert [e["type"] for e in events] == ["grant", "pull"]


def test_second_pull_reuses_active_consent(service):
    first = service.assess("C1001", 100000).external_report["consent"]
    second = service.assess("C1001", 50000).external_report["consent"]
    assert second["reused"] is True
    assert second["consent_id"] == first["consent_id"]
    # one grant, two pulls
    events = service.audit_trail("C1001")
    assert [e["type"] for e in events].count("grant") == 1
    assert [e["type"] for e in events].count("pull") == 2


def test_no_consent_records_nothing(service):
    r = service.assess("C1001", 100000, consent=False)
    assert r.external_report["status"] == "no_consent"
    assert "consent" not in r.external_report
    assert service.audit_trail("C1001") == []
