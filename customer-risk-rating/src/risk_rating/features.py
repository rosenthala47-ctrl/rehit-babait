"""ערכים מחושבים (derived features) — כאלה שאינם שדה גולמי בטבלה.

A small registry of feature functions referenced from the model as
`source: "derived:<name>"`. Each takes the merged customer record plus a
context dict (requested amount, model assumptions) and returns a number,
or None when it can't be computed (the engine then uses missing_risk).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

FeatureFn = Callable[[Dict[str, Any], Dict[str, Any]], Optional[float]]

_REGISTRY: Dict[str, FeatureFn] = {}


def feature(name: str) -> Callable[[FeatureFn], FeatureFn]:
    def decorator(fn: FeatureFn) -> FeatureFn:
        _REGISTRY[name] = fn
        return fn
    return decorator


def get_feature(name: str) -> Optional[FeatureFn]:
    return _REGISTRY.get(name)


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        # guard against pandas NaN
        return None if f != f else f
    except (TypeError, ValueError):
        return None


@feature("dti")
def debt_to_income(record: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[float]:
    """יחס ההחזר החודשי הכולל להכנסה, כולל ההלוואה המבוקשת.

    DTI = (existing monthly obligations + estimated payment on the new loan)
          / monthly net income

    The new loan's monthly payment is estimated as amount / term_months
    (a simple annuity-free proxy, transparent and easy to audit).
    """
    income = _num(record.get("monthly_income"))
    if not income or income <= 0:
        return None

    requested = _num(ctx.get("requested_amount")) or 0.0
    term = _num((ctx.get("assumptions") or {}).get("default_term_months")) or 60.0
    if term <= 0:
        term = 60.0

    existing_monthly = _num(record.get("monthly_debt_payments")) or 0.0
    new_monthly = requested / term
    return (existing_monthly + new_monthly) / income


@feature("existing_leverage")
def existing_leverage(record: Dict[str, Any], ctx: Dict[str, Any]) -> Optional[float]:
    """מינוף קיים — יחס סך החוב הקיים להכנסה השנתית.

    total existing debt / annual net income. A "amounts owed" / capacity
    signal distinct from utilization (which is only revolving credit) and
    from the new loan's DTI. Returns None if income or debt is unknown.
    """
    income = _num(record.get("monthly_income"))
    if not income or income <= 0:
        return None
    total_debt = _num(record.get("total_debt"))
    if total_debt is None:
        return None
    return total_debt / (income * 12.0)
