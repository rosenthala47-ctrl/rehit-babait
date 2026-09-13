"""בדיקות למנוע הניקוד ולמודל הניקוד."""
import pathlib

import pytest

from risk_rating.config import Band, Criterion, DecisionBand, ScoringModel
from risk_rating.engine import RiskEngine, round_half_up
from risk_rating.service import DEFAULT_CONFIG, DEFAULT_DATA_DIR, RiskRatingService

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def service():
    return RiskRatingService(data_dir=DEFAULT_DATA_DIR, config_path=DEFAULT_CONFIG)


# ── end-to-end anchors on the sample data ────────────────────────────────────
def test_yossi_is_low_risk_approve(service):
    result = service.assess("יוסי", 100000)
    assert result.customer_id == "C1001"
    assert result.score == pytest.approx(1.63, abs=0.01)
    assert result.score_rounded == 2
    assert result.decision_id == "approve"


def test_unemployed_high_risk_reject(service):
    result = service.assess("דוד", 100000)
    assert result.customer_id == "C1003"
    assert result.score == pytest.approx(8.45, abs=0.01)
    assert result.score_rounded == 8
    assert result.decision_id == "reject"


def test_borderline_goes_to_review(service):
    result = service.assess("שרה", 50000)
    assert result.decision_id == "review"
    assert 4 <= result.score_rounded <= 6


def test_larger_loan_raises_risk(service):
    """A bigger requested amount can only push DTI (and the score) up."""
    small = service.assess("יוסי", 50000).score
    big = service.assess("יוסי", 900000).score
    assert big > small


def test_missing_table_data_uses_missing_risk(service):
    # Noa (C1006) has no row in banking.csv.
    result = service.assess("נועה", 40000)
    banking = next(c for c in result.breakdown if c.id == "banking_conduct")
    assert banking.missing is True
    assert banking.raw_value is None
    assert banking.risk == 5.0  # missing_risk from the model


def test_score_dict_is_json_serializable(service):
    import json
    result = service.assess("יוסי", 100000)
    json.dumps(result.to_dict())  # must not raise


# ── unit tests on the scoring primitives ─────────────────────────────────────
@pytest.mark.parametrize("x,expected", [
    (2.4, 2), (2.5, 3), (3.5, 4), (6.4, 6), (6.5, 7), (1.0, 1), (9.99, 10),
])
def test_round_half_up(x, expected):
    assert round_half_up(x) == expected


def _tiny_model():
    """A minimal 2-criterion model for isolated engine tests."""
    return ScoringModel(
        version=1, scale_min=1, scale_max=10,
        assumptions={"default_term_months": 60},
        decision_bands=[
            DecisionBand("approve", 1, 3, "אישור"),
            DecisionBand("review", 4, 6, "בדיקה"),
            DecisionBand("reject", 7, 10, "דחייה"),
        ],
        criteria=[
            Criterion(id="status", label_he="סטטוס", weight=0.5, source="status",
                      type="categorical",
                      mapping={"good": 1, "bad": 10}, unknown_risk=5, missing_risk=8),
            Criterion(id="num", label_he="מספר", weight=0.5, source="num",
                      type="numeric_bands",
                      bands=[Band(10, 1), Band(20, 5), Band(float("inf"), 10)],
                      missing_risk=7),
        ],
    )


def test_numeric_band_selection_is_first_match():
    engine = RiskEngine(_tiny_model())
    # num=5 -> first band (<=10) risk 1 ; status good -> risk 1 ; total 1.0
    assert engine.score({"status": "good", "num": 5}, 0).score == pytest.approx(1.0)
    # num=15 -> risk 5 ; status bad -> risk 10 ; total 0.5*5 + 0.5*10 = 7.5
    r = engine.score({"status": "bad", "num": 15}, 0)
    assert r.score == pytest.approx(7.5)
    assert r.decision_id == "reject"


def test_missing_and_unknown_values():
    engine = RiskEngine(_tiny_model())
    # status missing -> missing_risk 8 ; num missing -> missing_risk 7 ; total 7.5
    r = engine.score({}, 0)
    assert r.score == pytest.approx(7.5)
    status = next(c for c in r.breakdown if c.id == "status")
    assert status.missing is True
    # unknown categorical value -> unknown_risk 5
    r2 = engine.score({"status": "weird", "num": 5}, 0)
    status2 = next(c for c in r2.breakdown if c.id == "status")
    assert status2.risk == 5
    assert status2.missing is False


def test_band_for_boundaries():
    model = _tiny_model()
    assert model.band_for(1).id == "approve"
    assert model.band_for(3).id == "approve"
    assert model.band_for(4).id == "review"
    assert model.band_for(6).id == "review"
    assert model.band_for(7).id == "reject"
    assert model.band_for(10).id == "reject"


# ── model validation ─────────────────────────────────────────────────────────
def test_default_model_weights_sum_to_one():
    model = ScoringModel.load(DEFAULT_CONFIG)
    assert sum(c.weight for c in model.criteria) == pytest.approx(1.0)
    assert model.validate() == []  # no warnings


def test_bad_weights_raise():
    with pytest.raises(ValueError):
        ScoringModel.from_dict({
            "criteria": [
                {"id": "a", "weight": 0.3, "source": "a", "type": "categorical",
                 "mapping": {"x": 1}},
            ],
            "decision_bands": [{"id": "approve", "min": 1, "max": 10}],
        })
