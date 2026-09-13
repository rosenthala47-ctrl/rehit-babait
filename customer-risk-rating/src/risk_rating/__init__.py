"""דירוג סיכון לקוח — Customer Risk-Rating engine for credit providers.

A small, auditable system that pulls a customer's data from many tables,
computes a weighted 1-10 risk score, and returns an approve / review / reject
decision with a full, explainable breakdown.
"""
from __future__ import annotations

from .config import ScoringModel
from .data import AmbiguousCustomer, CustomerNotFound, DataStore
from .engine import CriterionScore, RiskEngine, RiskResult
from .explain import ai_available, explain
from .service import RiskRatingService

__version__ = "0.1.0"

__all__ = [
    "RiskRatingService",
    "RiskEngine",
    "RiskResult",
    "CriterionScore",
    "ScoringModel",
    "DataStore",
    "CustomerNotFound",
    "AmbiguousCustomer",
    "explain",
    "ai_available",
    "__version__",
]
