"""בדיקות לכללי הנוק-אאוט (auto-reject) — דחייה מוחלטת שאינה תלויה בציון."""
import pytest

from risk_rating.config import KnockoutRule, ScoringModel
from risk_rating.service import DEFAULT_CONFIG, RiskRatingService


@pytest.fixture(scope="module")
def service():
    return RiskRatingService()


# ── the rule primitive ───────────────────────────────────────────────────────
def test_gte_rule_triggers_only_when_present_and_at_or_above():
    rule = KnockoutRule(id="d", label_he="כשל", field="defaults", gte=1)
    assert rule.triggered({"defaults": 1}) is True
    assert rule.triggered({"defaults": 2}) is True
    assert rule.triggered({"defaults": 0}) is False
    assert rule.triggered({}) is False              # missing → never fires
    assert rule.triggered({"defaults": None}) is False


def test_equals_rule():
    rule = KnockoutRule(id="s", label_he="מוגבל", field="status", equals=["restricted"])
    assert rule.triggered({"status": "Restricted"}) is True   # case-insensitive
    assert rule.triggered({"status": "ok"}) is False


def test_rule_with_no_condition_is_rejected_by_validation():
    with pytest.raises(ValueError):
        ScoringModel.from_dict({
            "criteria": [
                {"id": "a", "weight": 1.0, "source": "a", "type": "categorical",
                 "mapping": {"x": 1}},
            ],
            "decision_bands": [{"id": "approve", "min": 1, "max": 10}],
            "knockout_rules": [{"id": "bad", "field": "x"}],
        })


# ── the model loads the real rules ───────────────────────────────────────────
def test_default_model_has_knockout_rules():
    model = ScoringModel.load(DEFAULT_CONFIG)
    ids = {r.id for r in model.knockout_rules}
    assert {"active_default", "restricted_account", "bankruptcy"} <= ids


# ── end-to-end through the service ───────────────────────────────────────────
def test_bankrupt_customer_is_knocked_out(service):
    """Yosef (ת\"ז 358024917): bankruptcy + restricted account + active default."""
    r = service.assess("358024917", 100000)
    assert r.decision_id == "reject"
    fired = {k["id"] for k in r.knockouts}
    assert {"active_default", "restricted_account", "bankruptcy"} <= fired
    assert "כלל נוק-אאוט" in r.decision_action_he


def test_knockout_cannot_be_overridden_by_adjudication(service):
    r = service.assess("358024917", 100000, adjudicate=True)
    adj = r.adjudication
    assert adj["is_override"] is False
    assert adj["source"] == "guardrail"
    assert "נוק-אאוט" in adj["rationale_he"]
    # the knockout reasons appear among the blocked reasons
    assert any("פשיטת רגל" in b for b in adj["blocked_reasons"])


def test_review_band_customer_knocked_out_to_reject(service):
    """רון (C1008) scores in the review band, but defaults=2 → auto-reject."""
    r = service.assess("רון", 100000)
    assert r.knockouts                       # a rule fired
    assert r.decision_id == "reject"


def test_clean_customer_has_no_knockout(service):
    r = service.assess("C1001", 100000)
    assert r.knockouts == []
    assert r.decision_id == "approve"


def test_no_consent_means_no_knockout_data(service):
    """Without consent the register isn't pulled, so knockout fields are absent."""
    r = service.assess("358024917", 100000, consent=False)
    assert r.knockouts == []                 # can't knock out on data we never pulled


def test_knockout_serializes_to_dict(service):
    import json
    r = service.assess("358024917", 100000)
    d = r.to_dict()
    assert d["knockout"] is True
    assert d["knockouts"]
    json.dumps(d)  # must not raise
