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


class AmbiguousCustomer(Exception):
    def __init__(self, query: str, matches: List[Tuple[str, str]]):
        self.query = query
        self.matches = matches
        listed = ", ".join(f"{name} ({cid})" for cid, name in matches)
        super().__init__(f"'{query}' תואם ליותר מלקוח אחד: {listed}")


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
        try:
            if path.suffix.lower() in (".xlsx", ".xls"):
                return pd.read_excel(path, dtype={"customer_id": str})
            return pd.read_csv(path, dtype={"customer_id": str})
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

    def get_record(self, customer_id: str) -> Dict[str, Any]:
        rec = self._records.get(str(customer_id).strip())
        if rec is None:
            raise CustomerNotFound(f"לא נמצא לקוח עם מזהה '{customer_id}'")
        return dict(rec)

    def resolve(self, query: str) -> str:
        """Resolve a query to a single customer_id (by id or by name)."""
        q = str(query).strip()
        if not q:
            raise CustomerNotFound("שאילתת לקוח ריקה")

        # 1) exact id
        if q in self._records:
            return q

        # 2) exact name (case-insensitive)
        exact = [(cid, str(r.get("full_name", "")))
                 for cid, r in self._records.items()
                 if str(r.get("full_name", "")).strip().lower() == q.lower()]
        if len(exact) == 1:
            return exact[0][0]
        if len(exact) > 1:
            raise AmbiguousCustomer(q, exact)

        # 3) partial name match
        partial = [(cid, str(r.get("full_name", "")))
                   for cid, r in self._records.items()
                   if q.lower() in str(r.get("full_name", "")).lower()]
        if len(partial) == 1:
            return partial[0][0]
        if len(partial) > 1:
            raise AmbiguousCustomer(q, partial)

        raise CustomerNotFound(f"לא נמצא לקוח '{query}'")

    def resolve_record(self, query: str) -> Dict[str, Any]:
        return self.get_record(self.resolve(query))
