"""שירות דירוג הסיכון — הפָּסָאד הראשי של המערכת.

Ties the data layer, the credit-bureau connector, the scoring engine and the
explanation layer together. This is the object the credit company's system
talks to: give it a customer (by name or id) and a requested amount, and it

  1. pulls the customer's own data from the company tables,
  2. **requests the customer's credit report from the Bank of Israel register**
     (subject to consent), merging it in,
  3. scores the request and returns a decision + full, explainable breakdown.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adjudicate import adjudicate as _adjudicate
from .bureau import BureauError, CreditBureauClient, get_bureau_client
from .config import ScoringModel
from .consent import ConsentLedger
from .data import DataStore
from .engine import RiskEngine, RiskResult
from .explain import explain as _explain

# project root = .../customer-risk-rating
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "scoring_model.yaml"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "sample"


class RiskRatingService:
    def __init__(self, data_dir: str | Path = DEFAULT_DATA_DIR,
                 config_path: str | Path = DEFAULT_CONFIG,
                 bureau: Optional[CreditBureauClient] = None,
                 consent_ledger: Optional[ConsentLedger] = None):
        self.store = DataStore.from_directory(data_dir)
        self.model = ScoringModel.load(config_path)
        self.engine = RiskEngine(self.model)
        # the connector to the Bank of Israel credit register (mock by default)
        self.bureau = bureau if bureau is not None else get_bureau_client()
        # consent + audit trail (persists to RISK_CONSENT_LOG if set, else in-memory)
        self.consent_ledger = consent_ledger if consent_ledger is not None \
            else ConsentLedger(os.environ.get("RISK_CONSENT_LOG"))

    # ── queries ──────────────────────────────────────────────────────────
    def list_customers(self) -> List[Tuple[str, str]]:
        return self.store.list_customers()

    def assess(self, customer_query: str, requested_amount: float,
               consent: bool = True,
               consent_ref: Optional[str] = None,
               purpose: Optional[str] = None,
               collateral: Optional[str] = None,
               adjudicate: bool = False,
               use_ai: bool = False) -> RiskResult:
        """Resolve the customer, pull their credit report, and score the request.

        Parameters
        ----------
        consent:
            Whether the customer consented to a credit-data pull. Without
            consent the register is **not** contacted (as the law requires);
            the credit criteria then fall back to their missing-data risk.
        consent_ref:
            An optional reference to the recorded consent. One is synthesized
            for the demo when consent is given and none is supplied.
        purpose:
            The loan purpose (Conditions), e.g. ``debt_consolidation``,
            ``vehicle``, ``business``, ``investment`` — a request-level input.
        collateral:
            The security backing the loan (Collateral), e.g. ``unsecured``,
            ``full_secured``, ``guarantor`` — a request-level input.
        """
        record = self.store.resolve_record(customer_query)
        record, report = self._pull_credit_report(
            record, consent, consent_ref, requested_amount, purpose)
        # request-level fields (Conditions / Collateral) live on the request,
        # not on the customer — inject them so criteria can reference them.
        request_fields = {"requested_amount": requested_amount}
        if purpose is not None:
            request_fields["loan_purpose"] = purpose
        if collateral is not None:
            request_fields["collateral"] = collateral
        record = {**record, **request_fields}

        result = self.engine.score(record, requested_amount)
        result.external_report = report
        if adjudicate:
            result.adjudication = _adjudicate(
                result, record, self.model, use_ai=use_ai).to_dict()
        return result

    # ── credit register integration + consent flow ───────────────────────
    def _pull_credit_report(self, record: Dict[str, Any], consent: bool,
                            consent_ref: Optional[str], requested_amount: float,
                            purpose: Optional[str]
                            ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Record/verify consent, fetch the credit report, and merge it in.

        Returns ``(merged_record, report_summary)``. Every pull (and the
        consent behind it) is written to the audit-trail ledger. The record is
        never mutated in place.
        """
        national_id = record.get("national_id")

        if not consent:
            return record, {"status": "no_consent",
                            "message": "הלקוח לא נתן הסכמה למשיכת נתוני אשראי — "
                                       "המערכת לא פנתה לבנק ישראל."}
        if not national_id:
            return record, {"status": "no_national_id",
                            "message": "אין ת\"ז ללקוח — לא ניתן לפנות למרשם האשראי."}

        # consent flow: reuse an active consent, or record a fresh grant now.
        if consent_ref:
            grant = {"consent_id": consent_ref, "granted_at": None, "expires_at": None}
            reused = True
        else:
            grant = self.consent_ledger.active_consent(national_id)
            reused = grant is not None
            if grant is None:
                grant = self.consent_ledger.grant(
                    national_id, record.get("customer_id"), purpose,
                    requested_amount, via="loan-request")
        consent_id = grant["consent_id"]

        def _consent_block() -> Dict[str, Any]:
            return {"consent_id": consent_id, "granted_at": grant.get("granted_at"),
                    "expires_at": grant.get("expires_at"), "reused": reused}

        try:
            report = self.bureau.fetch(national_id, consent_ref=consent_id)
        except BureauError as exc:
            self.consent_ledger.record_pull(consent_id, national_id,
                                            self.bureau.source, [], status="error")
            return record, {"status": "error", "message": str(exc),
                            "consent": _consent_block()}

        if report is None:
            self.consent_ledger.record_pull(consent_id, national_id,
                                            self.bureau.source, [], status="not_found")
            return record, {"status": "not_found",
                            "message": "הלקוח אינו רשום במרשם נתוני האשראי.",
                            "consent": _consent_block()}

        fields = sorted(report.as_record().keys())
        self.consent_ledger.record_pull(consent_id, national_id, report.source,
                                        fields, status="ok")
        merged = {**record, **report.as_record()}
        summary = report.summary()
        summary["status"] = "ok"
        summary["fields_pulled"] = fields
        summary["consent"] = _consent_block()
        return merged, summary

    # ── audit trail ──────────────────────────────────────────────────────
    def audit_trail(self, customer_query: Optional[str] = None,
                    limit: int = 200) -> List[Dict[str, Any]]:
        """Consent/pull events (national id masked). Optionally for one customer."""
        nid = None
        if customer_query:
            rec = self.store.resolve_record(customer_query)
            nid = rec.get("national_id")
        return self.consent_ledger.audit_view(nid, limit)

    # ── convenience wrappers ─────────────────────────────────────────────
    def assess_dict(self, customer_query: str, requested_amount: float,
                    consent: bool = True, use_ai: bool = False,
                    purpose: Optional[str] = None, collateral: Optional[str] = None,
                    adjudicate: bool = False,
                    include_explanation: bool = True) -> Dict[str, Any]:
        """Same as :meth:`assess` but returns a JSON-friendly dict + explanation."""
        result = self.assess(customer_query, requested_amount, consent=consent,
                             purpose=purpose, collateral=collateral,
                             adjudicate=adjudicate, use_ai=use_ai)
        out: Dict[str, Any] = result.to_dict()
        if include_explanation:
            out["explanation"] = _explain(result, use_ai=use_ai)
        return out

    def explanation_for(self, result: RiskResult, use_ai: bool = False,
                        model: Optional[str] = None) -> str:
        return _explain(result, use_ai=use_ai, model=model)
