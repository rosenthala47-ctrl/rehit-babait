"""מנוע דירוג הסיכון — Customer risk-rating engine.

Given a merged customer record and a requested loan amount, computes a
weighted 1-10 risk score and a decision (approve / review / reject).

The engine is fully deterministic and explainable: every criterion's raw
value, sub-score and contribution to the total is returned, so a human
underwriter (or a regulator) can audit exactly how the score was produced.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import Criterion, ScoringModel
from .features import get_feature


def round_half_up(x: float) -> int:
    """Round to nearest integer, halves up (1-3 / 4-6 / 7-10 buckets)."""
    return int(math.floor(x + 0.5))


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


@dataclass
class CriterionScore:
    """The scoring of a single criterion for a single customer."""

    id: str
    label_he: str
    weight: float
    raw_value: Any
    risk: float
    contribution: float
    missing: bool = False

    def to_dict(self) -> Dict[str, Any]:
        raw = self.raw_value
        if isinstance(raw, float) and not (raw != raw):  # not NaN
            raw = round(raw, 4)
        return {
            "id": self.id,
            "label_he": self.label_he,
            "weight": self.weight,
            "raw_value": None if self.missing else raw,
            "risk": round(self.risk, 2),
            "contribution": round(self.contribution, 3),
            "missing": self.missing,
        }


@dataclass
class RiskResult:
    """The full result of scoring one customer for one loan request."""

    customer_id: str
    full_name: str
    requested_amount: float
    score: float                 # continuous 1-10
    score_rounded: int           # rounded to nearest integer
    decision_id: str
    decision_label_he: str
    decision_action_he: str
    breakdown: List[CriterionScore] = field(default_factory=list)
    currency: str = "ILS"

    def top_risk_drivers(self, n: int = 3) -> List[CriterionScore]:
        """The criteria contributing the most risk to the final score."""
        return sorted(self.breakdown, key=lambda c: c.contribution, reverse=True)[:n]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "customer_id": self.customer_id,
            "full_name": self.full_name,
            "requested_amount": self.requested_amount,
            "currency": self.currency,
            "score": round(self.score, 2),
            "score_rounded": self.score_rounded,
            "decision": {
                "id": self.decision_id,
                "label_he": self.decision_label_he,
                "action_he": self.decision_action_he,
            },
            "breakdown": [c.to_dict() for c in self.breakdown],
        }


class RiskEngine:
    """Scores customers against a :class:`ScoringModel`."""

    def __init__(self, model: ScoringModel):
        self.model = model

    # ── per-criterion scoring ────────────────────────────────────────────
    def _risk_for_criterion(self, crit: Criterion, record: Dict[str, Any],
                            ctx: Dict[str, Any]) -> (Any, float, bool):
        """Return (raw_value, risk_sub_score, missing_flag) for one criterion."""
        # Resolve the raw value (either a plain field or a derived feature).
        if crit.is_derived:
            fn = get_feature(crit.feature_name)
            if fn is None:
                raise ValueError(
                    f"criterion '{crit.id}' references unknown derived "
                    f"feature '{crit.feature_name}'")
            raw = fn(record, ctx)
        else:
            raw = record.get(crit.source)

        if _is_missing(raw):
            return raw, self._clamp(crit.missing_risk), True

        if crit.type == "categorical":
            key = str(raw).strip().lower()
            risk = crit.mapping.get(key, crit.unknown_risk)
            return raw, self._clamp(risk), False

        # numeric_bands
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return raw, self._clamp(crit.missing_risk), True
        for band in crit.bands:
            if value <= band.max:
                return raw, self._clamp(band.risk), False
        # Value exceeded every band (last band should be .inf, but be safe).
        return raw, self._clamp(crit.bands[-1].risk), False

    def _clamp(self, risk: float) -> float:
        return max(self.model.scale_min, min(self.model.scale_max, float(risk)))

    # ── public API ───────────────────────────────────────────────────────
    def score(self, record: Dict[str, Any], requested_amount: float) -> RiskResult:
        ctx = {
            "requested_amount": float(requested_amount),
            "assumptions": self.model.assumptions,
        }

        breakdown: List[CriterionScore] = []
        total = 0.0
        for crit in self.model.criteria:
            raw, risk, missing = self._risk_for_criterion(crit, record, ctx)
            contribution = crit.weight * risk
            total += contribution
            breakdown.append(CriterionScore(
                id=crit.id,
                label_he=crit.label_he,
                weight=crit.weight,
                raw_value=raw,
                risk=risk,
                contribution=contribution,
                missing=missing,
            ))

        total = max(self.model.scale_min, min(self.model.scale_max, total))
        score_rounded = max(self.model.scale_min,
                            min(self.model.scale_max, round_half_up(total)))
        band = self.model.band_for(score_rounded)

        return RiskResult(
            customer_id=str(record.get("customer_id", "")),
            full_name=str(record.get("full_name", "")),
            requested_amount=float(requested_amount),
            score=total,
            score_rounded=score_rounded,
            decision_id=band.id,
            decision_label_he=band.label_he,
            decision_action_he=band.action_he,
            breakdown=breakdown,
            currency=str(self.model.assumptions.get("currency", "ILS")),
        )
