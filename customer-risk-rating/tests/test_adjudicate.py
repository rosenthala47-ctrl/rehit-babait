"""בדיקות לשכבת שיקול הדעת (adjudication) והמעקות (guardrails)."""
import pytest

from risk_rating.adjudicate import _clamp
from risk_rating.service import RiskRatingService


@pytest.fixture(scope="module")
def service():
    return RiskRatingService()


def test_early_career_override_approves(service):
    """A thin-file, fundamentally-sound applicant in the review band gets a
    discretionary override to approve — flagged as an exception."""
    r = service.assess("נועה", 100000, adjudicate=True)
    adj = r.adjudication
    assert r.decision_id == "review"              # by-the-book decision
    assert adj["is_override"] is True             # ...but overridden
    assert adj["final_decision"] == "approve"
    assert adj["early_career_exception"] is True
    assert adj["source"] == "heuristic"
    assert adj["rationale_he"]                    # a reason was given


def test_hard_negative_blocks_override(service):
    """An active default can never be overridden — guardrail wins."""
    r = service.assess("דוד", 100000, adjudicate=True)   # unemployed + default, reject
    adj = r.adjudication
    assert adj["is_override"] is False
    assert adj["source"] == "guardrail"
    assert adj["blocked_reasons"]                 # non-empty


def test_review_band_with_default_is_blocked(service):
    """Being in the review band is not enough — a real negative blocks it."""
    r = service.assess("רון", 100000, adjudicate=True)   # review band, but defaults=2
    adj = r.adjudication
    assert adj["is_override"] is False
    assert adj["source"] == "guardrail"


def test_clearly_approved_customer_no_override(service):
    r = service.assess("C1001", 100000, adjudicate=True)
    assert r.adjudication["is_override"] is False


def test_adjudication_absent_unless_requested(service):
    assert service.assess("C1001", 100000).adjudication is None


def test_override_is_at_most_one_band_and_never_harsher():
    assert _clamp("reject", "approve") == "review"    # clamped to one step
    assert _clamp("review", "approve") == "approve"
    assert _clamp("review", "reject") == "review"     # never harsher via this layer
    assert _clamp("approve", "reject") == "approve"
