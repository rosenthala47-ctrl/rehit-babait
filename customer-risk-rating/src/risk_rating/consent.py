"""פנקס הסכמות ויומן ביקורת — Consent ledger & audit trail.

Under Israel's Credit Data Law (חוק נתוני אשראי, 2016) a lender may pull a
customer's credit data only with the customer's explicit consent, and must be
able to prove it. This module records:

* **grants** — a customer consented (who / when / purpose / validity), and
* **pulls**  — a credit-data pull that happened under a given consent (who /
  when / which fields / status).

Together these are the **audit trail**: durable proof that every pull was
lawful. The ledger is append-only. It persists to a JSON-Lines file when a
path is given (or via the ``RISK_CONSENT_LOG`` env var); otherwise it lives in
memory for the session (fine for demos and tests).
"""
from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


def _canon_nid(value: Any) -> str:
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(9) if digits else ""


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def mask_nid(nid: str) -> str:
    nid = str(nid)
    return ("•" * max(0, len(nid) - 4)) + nid[-4:] if nid else ""


class ConsentLedger:
    """Append-only ledger of consent grants and credit-data pulls."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else None
        self._events: List[Dict[str, Any]] = []
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    self._events.append(json.loads(line))

    # ── writing ──────────────────────────────────────────────────────────
    def _append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        self._events.append(event)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def grant(self, national_id: Any, customer_id: Optional[str], purpose: Optional[str],
              requested_amount: Optional[float], ttl_days: int = 30,
              via: str = "system") -> Dict[str, Any]:
        """Record that a customer granted consent. Returns the grant record."""
        now = _now()
        return self._append({
            "type": "grant",
            "consent_id": "CONSENT-" + uuid.uuid4().hex[:10].upper(),
            "national_id": _canon_nid(national_id),
            "customer_id": customer_id,
            "purpose": purpose,
            "requested_amount": requested_amount,
            "granted_at": _iso(now),
            "expires_at": _iso(now + datetime.timedelta(days=ttl_days)),
            "granted_via": via,
            "status": "granted",
        })

    def record_pull(self, consent_id: str, national_id: Any, source: str,
                    fields_pulled: List[str], status: str = "ok") -> Dict[str, Any]:
        """Log a credit-data pull performed under a consent."""
        return self._append({
            "type": "pull",
            "consent_id": consent_id,
            "national_id": _canon_nid(national_id),
            "source": source,
            "fields_pulled": fields_pulled,
            "status": status,
            "at": _iso(_now()),
        })

    # ── reading ──────────────────────────────────────────────────────────
    def active_consent(self, national_id: Any) -> Optional[Dict[str, Any]]:
        """The most recent non-expired grant for this person, or None."""
        nid = _canon_nid(national_id)
        now = _now()
        for g in reversed(self._events):
            if g.get("type") != "grant" or g.get("national_id") != nid:
                continue
            try:
                if datetime.datetime.fromisoformat(g["expires_at"]) > now:
                    return g
            except (ValueError, KeyError):
                continue
        return None

    def events(self, national_id: Any = None, limit: int = 200) -> List[Dict[str, Any]]:
        evs = self._events
        if national_id is not None:
            nid = _canon_nid(national_id)
            evs = [e for e in evs if e.get("national_id") == nid]
        return evs[-limit:]

    def audit_view(self, national_id: Any = None, limit: int = 200) -> List[Dict[str, Any]]:
        """Events with the national id masked — safe for display / API."""
        out = []
        for e in self.events(national_id, limit):
            e = dict(e)
            e["national_id"] = mask_nid(e.get("national_id", ""))
            out.append(e)
        return out
