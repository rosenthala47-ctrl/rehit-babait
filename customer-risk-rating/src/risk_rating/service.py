"""שירות דירוג הסיכון — הפָּסָאד הראשי של המערכת.

Ties the data layer, the scoring engine and the explanation layer together.
This is the object the credit company's system talks to: give it a customer
(by name or id) and a requested amount, and it pulls every table it has on
that customer and returns a score, a decision and a full breakdown — the
whole "AI sitting inside the system" in one call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import ScoringModel
from .data import DataStore
from .engine import RiskEngine, RiskResult
from .explain import explain as _explain

# project root = .../customer-risk-rating
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "scoring_model.yaml"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "sample"


class RiskRatingService:
    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR,
                 config_path: str | Path = DEFAULT_CONFIG):
        self.store = DataStore.from_directory(data_dir)
        self.model = ScoringModel.load(config_path)
        self.engine = RiskEngine(self.model)

    # ── queries ──────────────────────────────────────────────────────────
    def list_customers(self) -> List[Tuple[str, str]]:
        return self.store.list_customers()

    def assess(self, customer_query: str, requested_amount: float) -> RiskResult:
        """Resolve the customer, pull all their data, and score the request."""
        record = self.store.resolve_record(customer_query)
        return self.engine.score(record, requested_amount)

    def assess_dict(self, customer_query: str, requested_amount: float,
                    use_ai: bool = False,
                    include_explanation: bool = True) -> Dict[str, Any]:
        """Same as :meth:`assess` but returns a JSON-friendly dict + explanation."""
        result = self.assess(customer_query, requested_amount)
        out: Dict[str, Any] = result.to_dict()
        if include_explanation:
            out["explanation"] = _explain(result, use_ai=use_ai)
        return out

    def explanation_for(self, result: RiskResult, use_ai: bool = False,
                        model: Optional[str] = None) -> str:
        return _explain(result, use_ai=use_ai, model=model)
