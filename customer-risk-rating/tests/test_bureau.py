"""בדיקות למחבר מרשם נתוני האשראי (בנק ישראל) ולזרימת ההסכמה."""
import pytest

from risk_rating.bureau import (BureauNotConfigured, ConsentRequired,
                                CreditReport, HttpBankOfIsraelClient,
                                MockBankOfIsraelClient)
from risk_rating.service import RiskRatingService


@pytest.fixture(scope="module")
def bureau():
    return MockBankOfIsraelClient()


# ── the mock register ────────────────────────────────────────────────────────
def test_fetch_known_customer(bureau):
    # Yossi's national id (leading zero dropped on the way in is restored)
    report = bureau.fetch("34512789")
    assert report is not None
    assert report.national_id == "034512789"
    assert report.bureau_score == 720
    assert report.missed_payments_12m == 0
    assert report.credit_utilization == 0.35
    assert report.source.startswith("mock")
    assert report.retrieved_at  # timestamped


def test_fetch_unknown_returns_none(bureau):
    assert bureau.fetch("999999999") is None


def test_as_record_maps_fields():
    report = CreditReport(national_id="034512789", bureau_score=720,
                          missed_payments_12m=0, credit_utilization=0.35,
                          monthly_debt_payments=1800, defaults=0)
    rec = report.as_record()
    assert rec["external_bureau_score"] == 720
    assert rec["missed_payments_12m"] == 0
    assert rec["credit_utilization"] == 0.35
    # None fields are dropped
    assert "num_open_loans" not in rec


def test_summary_masks_national_id():
    report = CreditReport(national_id="034512789", bureau_score=720)
    s = report.summary()
    assert s["national_id_masked"].endswith("2789")
    assert "034512789" not in s["national_id_masked"]


def test_require_consent_raises_without_ref():
    strict = MockBankOfIsraelClient(require_consent=True)
    with pytest.raises(ConsentRequired):
        strict.fetch("034512789")
    # with a consent reference it works
    assert strict.fetch("034512789", consent_ref="CONSENT-123") is not None


# ── the http skeleton ────────────────────────────────────────────────────────
def test_http_client_needs_url(monkeypatch):
    monkeypatch.delenv("RISK_BUREAU_URL", raising=False)
    with pytest.raises(BureauNotConfigured):
        HttpBankOfIsraelClient().fetch("034512789", consent_ref="X")


def test_http_client_needs_consent():
    client = HttpBankOfIsraelClient(base_url="https://example.invalid/credit")
    with pytest.raises(ConsentRequired):
        client.fetch("034512789")  # no consent_ref


# ── end-to-end through the service ───────────────────────────────────────────
@pytest.fixture(scope="module")
def service():
    return RiskRatingService()


def test_assess_pulls_credit_report(service):
    result = service.assess("יוסי", 100000, consent=True)
    rep = result.external_report
    assert rep["status"] == "ok"
    assert rep["bureau_score"] == 720
    assert "missed_payments_12m" in rep["fields_pulled"]
    # score is unchanged vs. the original (bureau supplies the same numbers)
    assert result.score == pytest.approx(1.57, abs=0.01)


def test_no_consent_skips_bureau_and_raises_risk(service):
    with_consent = service.assess("יוסי", 100000, consent=True)
    without = service.assess("יוסי", 100000, consent=False)
    assert without.external_report["status"] == "no_consent"
    # credit criteria now fall back to missing-data risk -> higher score
    assert without.score > with_consent.score
    payment = next(c for c in without.breakdown if c.id == "payment_history")
    assert payment.missing is True
