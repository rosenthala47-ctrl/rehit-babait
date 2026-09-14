"""שכבת הנתונים — טוענת וממזגת את כל טבלאות הלקוחות.

Simulates the credit company's data layer: many tables (the "Excel files"),
each keyed by ``customer_id``. The store reads every CSV/Excel file in a
directory, merges them into one record per customer, and resolves a customer
by id or by name — exactly what the AI needs before it can score anyone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd

_TABLE_GLOBS = ("*.csv", "*.xlsx", "*.xls")


class CustomerNotFound(Exception):
    pass


def _canonical_nid(value: Any) -> str:
    """Normalize an Israeli national id to 9 digits (restores lost leading zeros)."""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(9) if digits else ""


class AmbiguousCustomer(Exception):
    """Raised when a name matches more than one customer.

    ``candidates`` lists each match with distinguishing details so the caller
    (or a UI) can pick the right person — names are not unique, the national id
    (ת"ז) is.
    """

    def __init__(self, query: str, candidates: List[Dict[str, Any]]):
        self.query = query
        self.candidates = candidates
        lines = []
        for c in candidates:
            bits = [f"{c['full_name']} ({c['customer_id']})"]
            if c.get("national_id_masked"):
                bits.append(f"ת\"ז {c['national_id_masked']}")
            if c.get("city"):
                bits.append(str(c["city"]))
            if c.get("age") is not None:
                bits.append(f"גיל {c['age']}")
            lines.append("  • " + " · ".join(bits))
        super().__init__(
            f"'{query}' תואם ל-{len(candidates)} לקוחות. ציין ת\"ז או מזהה לקוח:\n"
            + "\n".join(lines))


def _clean(value: Any) -> Any:
    """Drop NaN/empty values and normalize numpy scalars to native Python.

    Values coming out of pandas are often numpy scalars (int64/float64), which
    are not JSON-serializable — convert them so downstream output stays clean.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass  # not a scalar pandas can test (e.g. a list) — leave as-is
    if hasattr(value, "item") and type(value).__module__.split(".")[0] == "numpy":
        value = value.item()
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class DataStore:
    """Loads customer tables from a directory and serves merged records."""

    def __init__(self, records: Dict[str, Dict[str, Any]], tables: List[str]):
        self._records = records
        self.tables = tables

    # ── loading ──────────────────────────────────────────────────────────
    @classmethod
    def from_directory(cls, directory: str | Path) -> "DataStore":
        directory = Path(directory)
        if not directory.exists():
            raise FileNotFoundError(f"data directory not found: {directory}")

        files: List[Path] = []
        for pattern in _TABLE_GLOBS:
            files.extend(sorted(directory.glob(pattern)))
        # De-dupe while keeping order (a file could match twice in theory).
        files = list(dict.fromkeys(files))
        if not files:
            raise FileNotFoundError(
                f"no CSV/Excel tables found in {directory}")

        records: Dict[str, Dict[str, Any]] = {}
        loaded: List[str] = []
        for path in files:
            df = cls._read(path)
            if df is None:
                continue
            if "customer_id" not in df.columns:
                # Not a customer table — skip it silently.
                continue
            loaded.append(path.name)
            for _, row in df.iterrows():
                cid = _clean(row["customer_id"])
                if cid is None:
                    continue
                cid = str(cid).strip()
                rec = records.setdefault(cid, {"customer_id": cid})
                for col in df.columns:
                    if col == "customer_id":
                        continue
                    val = _clean(row[col])
                    if val is not None:
                        rec[col] = val

        if not records:
            raise ValueError(
                f"tables in {directory} contain no 'customer_id' column")

        return cls(records=records, tables=loaded)

    @staticmethod
    def _read(path: Path):
        # keep ids as strings so national-id leading zeros survive
        dtypes = {"customer_id": str, "national_id": str}
        try:
            if path.suffix.lower() in (".xlsx", ".xls"):
                return pd.read_excel(path, dtype=dtypes)
            return pd.read_csv(path, dtype=dtypes)
        except Exception as exc:  # noqa: BLE001 - surface as a warning, keep going
            print(f"⚠️  לא ניתן לקרוא את {path.name}: {exc}")
            return None

    # ── access ───────────────────────────────────────────────────────────
    def list_customers(self) -> List[Tuple[str, str]]:
        """Return (customer_id, full_name) for every known customer."""
        return sorted(
            ((cid, str(rec.get("full_name", ""))) for cid, rec in self._records.items()),
            key=lambda t: t[0],
        )

    def _candidate(self, cid: str) -> Dict[str, Any]:
        """Distinguishing details for a customer (for pickers / ambiguity)."""
        r = self._records[cid]
        nid = _canonical_nid(r.get("national_id", ""))
        masked = ("•" * max(0, len(nid) - 4)) + nid[-4:] if nid else ""
        return {
            "customer_id": cid,
            "full_name": str(r.get("full_name", "")),
            "national_id_masked": masked,
            "city": r.get("city"),
            "age": r.get("age"),
        }

    def list_customer_details(self) -> List[Dict[str, Any]]:
        return [self._candidate(cid) for cid, _ in self.list_customers()]

    def get_record(self, customer_id: str) -> Dict[str, Any]:
        rec = self._records.get(str(customer_id).strip())
        if rec is None:
            raise CustomerNotFound(f"לא נמצא לקוח עם מזהה '{customer_id}'")
        return dict(rec)

    def resolve(self, query: str) -> str:
        """Resolve a query to a single customer_id.

        Resolution order: exact customer_id → national id (ת"ז) → exact name →
        partial name. Names are not unique, so a name matching more than one
        person raises :class:`AmbiguousCustomer` with the candidates' details —
        pass a ת"ז or a customer id to disambiguate.
        """
        q = str(query).strip()
        if not q:
            raise CustomerNotFound("שאילתת לקוח ריקה")

        # 1) exact customer id
        if q in self._records:
            return q

        # 2) national id (ת"ז) — the unique key, as the credit register uses
        qd = "".join(ch for ch in q if ch.isdigit())
        if qd and len(qd) <= 9 and qd == q.replace("-", "").replace(" ", ""):
            canon = _canonical_nid(q)
            by_nid = [cid for cid, r in self._records.items()
                      if _canonical_nid(r.get("national_id", "")) == canon]
            if len(by_nid) == 1:
                return by_nid[0]
            if len(by_nid) > 1:  # national ids should be unique; guard anyway
                raise AmbiguousCustomer(q, [self._candidate(c) for c in by_nid])

        # 3) exact name (case-insensitive)
        exact = [cid for cid, r in self._records.items()
                 if str(r.get("full_name", "")).strip().lower() == q.lower()]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            raise AmbiguousCustomer(q, [self._candidate(c) for c in exact])

        # 4) partial name match
        partial = [cid for cid, r in self._records.items()
                   if q.lower() in str(r.get("full_name", "")).lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            raise AmbiguousCustomer(q, [self._candidate(c) for c in partial])

        raise CustomerNotFound(f"לא נמצא לקוח '{query}'")

    def resolve_record(self, query: str) -> Dict[str, Any]:
        return self.get_record(self.resolve(query))
