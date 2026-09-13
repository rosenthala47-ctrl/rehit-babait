"""מחבר ללשכת נתוני אשראי / מרשם נתוני האשראי של בנק ישראל.

Credit-bureau connector. When a loan is requested, the system asks the credit
register (in Israel: the Bank of Israel Credit Data Register, מרשם נתוני
אשראי) for the customer's credit data, keyed by national id (ת"ז).

Reality check
-------------
There is no public/open Bank of Israel API. Under the Credit Data Law (חוק
נתוני אשראי, 2016), access requires:
  * being (or working through) a licensed credit bureau (לשכת נתוני אשראי),
  * the customer's explicit **consent** to pull their data, and
  * a secured, authenticated integration (API key / mutual-TLS certificates).

So this module ships:
  * ``CreditBureauClient``       — the interface the system depends on.
  * ``MockBankOfIsraelClient``   — a simulator (reads data/bureau/credit_register.csv)
                                   so the whole flow works end-to-end offline.
  * ``HttpBankOfIsraelClient``   — a real-integration skeleton: fill in the URL,
                                   credentials and response mapping once licensed.
  * a consent gate on every fetch.

Select the client with the ``RISK_BUREAU`` env var: ``mock`` (default) or ``http``.
"""
from __future__ import annotations

import abc
import csv
import datetime
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTER = PROJECT_ROOT / "data" / "bureau" / "credit_register.csv"

# The fields the register supplies, and the record keys they map to.
_REGISTER_FIELDS = (
    "bureau_score", "num_open_loans", "total_debt", "monthly_debt_payments",
    "missed_payments_12m", "defaults", "credit_utilization", "oldest_account_years",
)


class BureauError(Exception):
    """Base class for credit-bureau errors."""


class BureauNotConfigured(BureauError):
    """The real bureau client is missing required configuration."""


class ConsentRequired(BureauError):
    """A credit pull was attempted without the customer's consent."""


def _canonical_id(national_id: Any) -> str:
    """Normalize an Israeli national id to 9 digits (restores lost leading zeros)."""
    return str(national_id).strip().zfill(9)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class CreditReport:
    """A credit report returned by the register for one person."""

    national_id: str
    bureau_score: Optional[float] = None
    num_open_loans: Optional[float] = None
    total_debt: Optional[float] = None
    monthly_debt_payments: Optional[float] = None
    missed_payments_12m: Optional[float] = None
    defaults: Optional[float] = None
    credit_utilization: Optional[float] = None
    oldest_account_years: Optional[float] = None
    source: str = "mock"
    retrieved_at: str = ""
    consent_ref: Optional[str] = None

    def as_record(self) -> Dict[str, Any]:
        """The credit fields to merge into the customer record (non-null only).

        ``bureau_score`` is exposed as ``external_bureau_score`` to match the
        naming a scoring criterion would reference.
        """
        mapping = {
            "external_bureau_score": self.bureau_score,
            "num_open_loans": self.num_open_loans,
            "total_debt": self.total_debt,
            "monthly_debt_payments": self.monthly_debt_payments,
            "missed_payments_12m": self.missed_payments_12m,
            "defaults": self.defaults,
            "credit_utilization": self.credit_utilization,
            "oldest_account_years": self.oldest_account_years,
        }
        return {k: v for k, v in mapping.items() if v is not None}

    def summary(self) -> Dict[str, Any]:
        """A JSON-friendly, privacy-preserving summary for display/audit."""
        nid = self.national_id
        masked = ("•" * max(0, len(nid) - 4)) + nid[-4:] if nid else ""
        return {
            "source": self.source,
            "national_id_masked": masked,
            "bureau_score": self.bureau_score,
            "retrieved_at": self.retrieved_at,
            "consent_ref": self.consent_ref,
        }


class CreditBureauClient(abc.ABC):
    """Interface the system depends on to pull external credit data."""

    source = "credit-bureau"

    @abc.abstractmethod
    def fetch(self, national_id: Any,
              consent_ref: Optional[str] = None) -> Optional[CreditReport]:
        """Return a :class:`CreditReport`, or None if the person is not on file."""


class MockBankOfIsraelClient(CreditBureauClient):
    """Simulated Bank of Israel register — reads a local CSV keyed by national id.

    Lets the entire request-to-score flow run offline with realistic data.
    """

    source = "mock:bank-of-israel-register"

    def __init__(self, register_path: str | Path = DEFAULT_REGISTER,
                 require_consent: bool = False):
        self.register_path = Path(register_path)
        self.require_consent = require_consent
        self._register = self._load(self.register_path)

    @staticmethod
    def _load(path: Path) -> Dict[str, Dict[str, Any]]:
        register: Dict[str, Dict[str, Any]] = {}
        if not path.exists():
            return register
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                nid = _canonical_id(row.get("national_id", ""))
                if not nid.strip("0"):
                    continue
                register[nid] = {f: _to_float(row.get(f)) for f in _REGISTER_FIELDS}
        return register

    def fetch(self, national_id: Any,
              consent_ref: Optional[str] = None) -> Optional[CreditReport]:
        if self.require_consent and not consent_ref:
            raise ConsentRequired("נדרשת הסכמת הלקוח למשיכת נתוני אשראי")
        row = self._register.get(_canonical_id(national_id))
        if row is None:
            return None
        return CreditReport(
            national_id=_canonical_id(national_id),
            source=self.source,
            retrieved_at=_now_iso(),
            consent_ref=consent_ref,
            **row,
        )


class HttpBankOfIsraelClient(CreditBureauClient):
    """Real-integration skeleton for the Bank of Israel credit register.

    Not functional until configured with a real endpoint and credentials
    (via constructor args or environment variables):

        RISK_BUREAU_URL          endpoint that returns a credit report
        RISK_BUREAU_API_KEY      bearer token / API key
        RISK_BUREAU_CLIENT_CERT  client certificate (PEM) for mutual TLS
        RISK_BUREAU_CLIENT_KEY   client private key (PEM)
        RISK_BUREAU_CA_BUNDLE    CA bundle to verify the server

    The response parsing in ``_parse`` is illustrative — map it to the real
    register schema during onboarding.
    """

    source = "bank-of-israel-register"

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 client_cert: Optional[str] = None, client_key: Optional[str] = None,
                 ca_bundle: Optional[str] = None, timeout: float = 10.0):
        self.base_url = base_url or os.environ.get("RISK_BUREAU_URL")
        self.api_key = api_key or os.environ.get("RISK_BUREAU_API_KEY")
        self.client_cert = client_cert or os.environ.get("RISK_BUREAU_CLIENT_CERT")
        self.client_key = client_key or os.environ.get("RISK_BUREAU_CLIENT_KEY")
        self.ca_bundle = ca_bundle or os.environ.get("RISK_BUREAU_CA_BUNDLE")
        self.timeout = timeout

    def fetch(self, national_id: Any,
              consent_ref: Optional[str] = None) -> Optional[CreditReport]:
        if not self.base_url:
            raise BureauNotConfigured(
                "RISK_BUREAU_URL is not set — cannot contact the credit register")
        if not consent_ref:
            raise ConsentRequired(
                "consent_ref is required to pull credit data from the register")

        import json
        import ssl
        import urllib.error
        import urllib.request

        payload = json.dumps({
            "national_id": _canonical_id(national_id),
            "consent_ref": consent_ref,
        }).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        ctx = ssl.create_default_context(cafile=self.ca_bundle) if self.ca_bundle \
            else ssl.create_default_context()
        if self.client_cert:
            ctx.load_cert_chain(certfile=self.client_cert, keyfile=self.client_key)

        req = urllib.request.Request(self.base_url, data=payload,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None  # person not on file
            raise BureauError(f"שגיאת מרשם האשראי: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise BureauError(f"כשל בתקשורת מול מרשם האשראי: {exc.reason}") from exc

        return self._parse(data, national_id, consent_ref)

    def _parse(self, data: Dict[str, Any], national_id: Any,
               consent_ref: Optional[str]) -> CreditReport:
        # Map the register's real field names here during onboarding.
        return CreditReport(
            national_id=_canonical_id(national_id),
            bureau_score=_to_float(data.get("credit_score")),
            num_open_loans=_to_float(data.get("open_loans")),
            total_debt=_to_float(data.get("total_debt")),
            monthly_debt_payments=_to_float(data.get("monthly_obligations")),
            missed_payments_12m=_to_float(data.get("late_payments_12m")),
            defaults=_to_float(data.get("defaults")),
            credit_utilization=_to_float(data.get("utilization")),
            oldest_account_years=_to_float(data.get("credit_history_years")),
            source=self.source,
            retrieved_at=_now_iso(),
            consent_ref=consent_ref,
        )


def get_bureau_client() -> CreditBureauClient:
    """Return the configured bureau client (``RISK_BUREAU``: mock | http)."""
    kind = os.environ.get("RISK_BUREAU", "mock").strip().lower()
    if kind == "http":
        return HttpBankOfIsraelClient()
    return MockBankOfIsraelClient()
