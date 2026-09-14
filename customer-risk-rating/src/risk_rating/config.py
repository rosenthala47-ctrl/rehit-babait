"""טעינת מודל הניקוד (scoring_model.yaml) לאובייקטים, כולל ולידציה.

Loads the risk-scoring model from YAML into typed objects. The model is
data, not code: credit analysts tune weights, bands and decision cut-offs
here without touching the engine.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def _to_float(value: Any) -> float:
    """Parse a number that may be written as .inf / -.inf in YAML."""
    if value is None:
        return math.inf
    if isinstance(value, str):
        token = value.strip().lower()
        if token in (".inf", "inf", "infinity", "+.inf"):
            return math.inf
        if token in ("-.inf", "-inf", "-infinity"):
            return -math.inf
    return float(value)


@dataclass
class Band:
    """A single risk band for a numeric criterion: value <= max -> risk."""

    max: float
    risk: float


@dataclass
class Criterion:
    """A single scoring criterion and how its raw value maps to a 1-10 risk."""

    id: str
    label_he: str
    weight: float
    source: str
    type: str  # "categorical" | "numeric_bands"
    label_en: str = ""
    mapping: Dict[str, float] = field(default_factory=dict)
    bands: List[Band] = field(default_factory=list)
    unknown_risk: float = 5.0
    missing_risk: float = 5.0

    @property
    def is_derived(self) -> bool:
        return self.source.startswith("derived:")

    @property
    def feature_name(self) -> Optional[str]:
        return self.source.split(":", 1)[1] if self.is_derived else None


@dataclass
class DecisionBand:
    """Maps a final (rounded) score to a decision: approve / review / reject."""

    id: str
    min: int
    max: int
    label_he: str
    label_en: str = ""
    action_he: str = ""


def _rule_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


@dataclass
class KnockoutRule:
    """A hard auto-reject condition, independent of the weighted score.

    When a rule fires the request is rejected outright ("נדחה עקב כלל
    נוק-אאוט") and the discretionary (AI) layer may not overturn it. A rule
    only fires when the field is actually present — a missing value never
    triggers a knockout.
    """

    id: str
    label_he: str
    field: str
    label_en: str = ""
    gte: Optional[float] = None
    lte: Optional[float] = None
    equals: List[str] = field(default_factory=list)

    def triggered(self, record: Dict[str, Any]) -> bool:
        raw = record.get(self.field)
        if _rule_missing(raw):
            return False
        if self.gte is not None or self.lte is not None:
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return False
            if self.gte is not None and value < self.gte:
                return False
            if self.lte is not None and value > self.lte:
                return False
        if self.equals and str(raw).strip().lower() not in self.equals:
            return False
        # at least one condition must be defined for the rule to mean anything
        return self.gte is not None or self.lte is not None or bool(self.equals)


@dataclass
class ScoringModel:
    version: int
    scale_min: int
    scale_max: int
    assumptions: Dict[str, Any]
    decision_bands: List[DecisionBand]
    criteria: List[Criterion]
    knockout_rules: List[KnockoutRule] = field(default_factory=list)

    # ── loading ──────────────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path) -> "ScoringModel":
        path = Path(path)
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ScoringModel":
        scale = raw.get("scale", {}) or {}
        criteria: List[Criterion] = []
        for c in raw.get("criteria", []):
            bands = [Band(max=_to_float(b.get("max")), risk=float(b["risk"]))
                     for b in c.get("bands", [])]
            mapping = {str(k).lower(): float(v) for k, v in (c.get("mapping") or {}).items()}
            criteria.append(Criterion(
                id=c["id"],
                label_he=c.get("label_he", c["id"]),
                label_en=c.get("label_en", ""),
                weight=float(c["weight"]),
                source=c["source"],
                type=c["type"],
                mapping=mapping,
                bands=bands,
                unknown_risk=float(c.get("unknown_risk", 5.0)),
                missing_risk=float(c.get("missing_risk", 5.0)),
            ))
        decision_bands = [
            DecisionBand(
                id=d["id"],
                min=int(d["min"]),
                max=int(d["max"]),
                label_he=d.get("label_he", d["id"]),
                label_en=d.get("label_en", ""),
                action_he=d.get("action_he", ""),
            )
            for d in raw.get("decision_bands", [])
        ]
        knockout_rules = [
            KnockoutRule(
                id=k["id"],
                label_he=k.get("label_he", k["id"]),
                label_en=k.get("label_en", ""),
                field=k["field"],
                gte=_to_float(k["gte"]) if k.get("gte") is not None else None,
                lte=_to_float(k["lte"]) if k.get("lte") is not None else None,
                equals=[str(v).strip().lower() for v in (k.get("equals") or [])],
            )
            for k in raw.get("knockout_rules", [])
        ]
        model = cls(
            version=int(raw.get("version", 1)),
            scale_min=int(scale.get("min", 1)),
            scale_max=int(scale.get("max", 10)),
            assumptions=raw.get("assumptions", {}) or {},
            decision_bands=decision_bands,
            criteria=criteria,
            knockout_rules=knockout_rules,
        )
        model.validate(raise_on_error=True)
        return model

    # ── validation ───────────────────────────────────────────────────────
    def validate(self, raise_on_error: bool = False) -> List[str]:
        """Return a list of warnings; raise ValueError on hard errors."""
        warnings: List[str] = []
        if not self.criteria:
            raise ValueError("scoring model has no criteria")

        total_weight = sum(c.weight for c in self.criteria)
        if abs(total_weight - 1.0) > 1e-6:
            msg = (f"סכום המשקלים הוא {total_weight:.4f} במקום 1.0 "
                   f"(weights sum to {total_weight:.4f}, expected 1.0)")
            if raise_on_error:
                raise ValueError(msg)
            warnings.append(msg)

        for c in self.criteria:
            if c.type == "numeric_bands" and not c.bands:
                raise ValueError(f"criterion '{c.id}' is numeric_bands but has no bands")
            if c.type == "categorical" and not c.mapping:
                raise ValueError(f"criterion '{c.id}' is categorical but has no mapping")
            if c.type not in ("numeric_bands", "categorical"):
                raise ValueError(f"criterion '{c.id}' has unknown type '{c.type}'")

        if not self.decision_bands:
            raise ValueError("scoring model has no decision_bands")

        for r in self.knockout_rules:
            if r.gte is None and r.lte is None and not r.equals:
                raise ValueError(
                    f"knockout rule '{r.id}' has no condition (gte/lte/equals)")

        return warnings

    # ── helpers ──────────────────────────────────────────────────────────
    def check_knockouts(self, record: Dict[str, Any]) -> List[KnockoutRule]:
        """Return every knockout rule that fires for this (merged) record."""
        return [r for r in self.knockout_rules if r.triggered(record)]

    def reject_band(self) -> Optional[DecisionBand]:
        """The 'reject' decision band, if the model defines one."""
        for b in self.decision_bands:
            if b.id == "reject":
                return b
        return self.decision_bands[-1] if self.decision_bands else None

    def band_for(self, score_int: int) -> DecisionBand:
        """Return the decision band that contains the given (rounded) score."""
        for band in self.decision_bands:
            if band.min <= score_int <= band.max:
                return band
        # Fall back to the closest band if the score is out of range.
        return min(
            self.decision_bands,
            key=lambda b: min(abs(score_int - b.min), abs(score_int - b.max)),
        )
