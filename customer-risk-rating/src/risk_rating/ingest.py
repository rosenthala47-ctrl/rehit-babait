"""שכבת קליטה חכמה — שואבת אקסלים ענקיים ומגוונים לרשומה קנונית אחת ללקוח.

Ingestion layer for the *real* world: a credit company has many huge Excel
files, and **every file names its columns differently** — "הכנסה", "שכר נטו"
and ``monthly_income`` all mean the same thing; "ת\"ז", "תעודת זהות" and
``national_id`` are the same key. This module maps each file's columns onto a
small set of **canonical scoring fields**, aggregates transaction tables
(many rows per customer) up to one row per customer, and merges everything
into a single canonical record per ת"ז that the scoring engine can consume.

Getting the mapping right — "no mistakes"
-----------------------------------------
No auto-mapper is perfect on the first pass, so this is built the way a
regulated system must be:

1. **AI proposes** (``use_ai=True`` + ``ANTHROPIC_API_KEY``): Claude reads the
   column names and a few sample values and maps each to a canonical field,
   with a **confidence** and a Hebrew reason. Offline, a transparent
   alias/normalisation matcher does the same job deterministically.
2. **Confidence gate**: anything below the threshold is **flagged for a
   one-time human confirmation** (``review_needed``) instead of being guessed.
3. **Persist & reuse**: an approved :class:`SchemaMapping` is saved keyed by
   the file's column signature and **reused deterministically** — the system
   never re-guesses a layout it has already learned.
4. **Validation**: types are coerced and values range-checked, so a bad map
   surfaces as a warning rather than a silently wrong score.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .data import DataStore, _canonical_nid, _clean

# ─────────────────────────────────────────────────────────────────────────────
# Canonical field registry — the vocabulary every messy column is mapped onto.
# ``id`` matches the ``source`` names the scoring model references, so a mapped
# record feeds straight into the engine.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CanonicalField:
    id: str
    label_he: str
    aliases: List[str]                 # human header variants (he + en)
    dtype: str = "number"              # int | number | ratio | text | categorical | bool
    agg: str = "last"                  # how to aggregate a transaction table
    is_key: bool = False
    minimum: Optional[float] = None
    maximum: Optional[float] = None


_FIELDS: List[CanonicalField] = [
    # ── identity / meta ──────────────────────────────────────────────────
    CanonicalField("national_id", "תעודת זהות (ת\"ז)",
                   ["national_id", "nationalid", "id_number", "idnumber", "tz", "teudat_zehut",
                    "תז", "תעודתזהות", "מספרזהות", "מסזהות", "זהות", "תעודת זהות", "ת.ז"],
                   dtype="text", agg="first", is_key=True),
    CanonicalField("customer_id", "מזהה לקוח",
                   ["customer_id", "customerid", "client_id", "clientid", "cust_id", "custid",
                    "מזההלקוח", "קודלקוח", "מספרלקוח", "לקוח"],
                   dtype="text", agg="first"),
    CanonicalField("full_name", "שם הלקוח",
                   ["full_name", "fullname", "name", "customer_name", "customername",
                    "שם", "שממלא", "שםהלקוח", "שם מלא"],
                   dtype="text", agg="first"),
    CanonicalField("city", "עיר",
                   ["city", "town", "עיר", "יישוב", "ישוב"], dtype="text", agg="first"),
    # ── demographics ─────────────────────────────────────────────────────
    CanonicalField("age", "גיל", ["age", "גיל"], dtype="int", agg="last",
                   minimum=16, maximum=120),
    CanonicalField("marital_status", "מצב משפחתי",
                   ["marital_status", "maritalstatus", "מצבמשפחתי", "מצב משפחתי", "סטטוסמשפחתי"],
                   dtype="categorical", agg="last"),
    CanonicalField("dependents", "תלויים / ילדים",
                   ["dependents", "children", "num_children", "תלויים", "ילדים", "מספרילדים"],
                   dtype="int", agg="last", minimum=0, maximum=20),
    # ── capacity (employment / income) ───────────────────────────────────
    CanonicalField("employment_status", "מצב תעסוקתי",
                   ["employment_status", "employmentstatus", "employment", "job_status",
                    "מצבתעסוקתי", "מצב תעסוקתי", "סטטוסתעסוקה", "תעסוקה"],
                   dtype="categorical", agg="last"),
    CanonicalField("monthly_income", "הכנסה חודשית (נטו)",
                   ["monthly_income", "monthlyincome", "income", "salary", "net_income",
                    "netincome", "wage", "הכנסה", "שכר", "שכרנטו", "משכורת", "הכנסהחודשית",
                    "הכנסה חודשית", "שכר נטו", "הכנסהנטו"],
                   dtype="number", agg="mean", minimum=0, maximum=1_000_000),
    CanonicalField("tenure_months", "ותק במקום העבודה (חודשים)",
                   ["tenure_months", "tenuremonths", "seniority", "job_tenure", "ותק",
                    "ותקבעבודה", "ותק בעבודה", "ותקתעסוקתי", "חודשיוותק"],
                   dtype="int", agg="max", minimum=0, maximum=720),
    CanonicalField("employer", "מעסיק",
                   ["employer", "company", "workplace", "מעסיק", "מקוםעבודה", "חברה"],
                   dtype="text", agg="last"),
    # ── capital (banking) ────────────────────────────────────────────────
    CanonicalField("avg_balance", "יתרה ממוצעת בעו\"ש",
                   ["avg_balance", "avgbalance", "balance", "average_balance", "יתרה",
                    "יתרהממוצעת", "יתרתעוש", "יתרת עוש", "יתרהבעוש"],
                   dtype="number", agg="mean", minimum=-1_000_000, maximum=100_000_000),
    CanonicalField("overdraft_days_12m", "ימי חריגה ממסגרת (12ח')",
                   ["overdraft_days_12m", "overdraft_days", "overdraftdays", "ימיחריגה",
                    "ימי חריגה", "חריגהממסגרת", "ימיחריגהממסגרת"],
                   dtype="int", agg="sum", minimum=0, maximum=366),
    CanonicalField("returned_checks_12m", "צ'קים חוזרים (12ח')",
                   ["returned_checks_12m", "returned_checks", "bounced_checks", "צקיםחוזרים",
                    "שיקיםחוזרים", "צ'קיםחוזרים", "צ׳קים חוזרים"],
                   dtype="int", agg="sum", minimum=0, maximum=1000),
    # ── character (credit) — may live in the company's own tables ─────────
    CanonicalField("external_bureau_score", "דירוג אשראי (לשכה/בנק ישראל)",
                   ["external_bureau_score", "bureau_score", "credit_score", "creditscore",
                    "score", "דירוגאשראי", "דירוג אשראי", "ציוןאשראי", "דירוגלשכה"],
                   dtype="number", agg="last", minimum=0, maximum=2000),
    CanonicalField("missed_payments_12m", "פיגורים בתשלום (12ח')",
                   ["missed_payments_12m", "missed_payments", "late_payments", "latepayments",
                    "arrears", "פיגורים", "איחוריםבתשלום", "איחורים", "פיגוריםבתשלום"],
                   dtype="int", agg="sum", minimum=0, maximum=1000),
    CanonicalField("defaults", "כשלי אשראי / הוצל\"פ / כונס",
                   ["defaults", "default", "delinquencies", "enforcement", "כשליאשראי",
                    "כשלאשראי", "הוצאהלפועל", "הוצלפ", "כונס", "חדלותפירעון", "חדלותפרעון"],
                   dtype="int", agg="max", minimum=0, maximum=1000),
    CanonicalField("credit_utilization", "ניצול מסגרות אשראי",
                   ["credit_utilization", "creditutilization", "utilization", "util",
                    "ניצולמסגרת", "ניצולאשראי", "ניצול מסגרות", "אחוזניצול"],
                   dtype="ratio", agg="mean", minimum=0, maximum=1),
    CanonicalField("oldest_account_years", "ותק היסטוריית אשראי (שנים)",
                   ["oldest_account_years", "credit_history_years", "history_years",
                    "ותקאשראי", "ותק אשראי", "שנותהיסטוריה", "ותקהיסטוריה"],
                   dtype="number", agg="max", minimum=0, maximum=80),
    CanonicalField("hard_inquiries_6m", "בקשות אשראי אחרונות (6ח')",
                   ["hard_inquiries_6m", "hard_inquiries", "inquiries", "בקשותאשראי",
                    "בקשות אשראי", "שאילתותאשראי", "התעניינויות"],
                   dtype="int", agg="sum", minimum=0, maximum=1000),
    CanonicalField("total_debt", "סך חוב",
                   ["total_debt", "totaldebt", "debt", "outstanding_debt", "סךחוב",
                    "חובכולל", "חוב", "יתרתחוב"],
                   dtype="number", agg="sum", minimum=0, maximum=100_000_000),
    CanonicalField("monthly_debt_payments", "החזרי חוב חודשיים",
                   ["monthly_debt_payments", "monthly_obligations", "debt_payments",
                    "החזרחודשי", "תשלומיחוב", "החזריחוב", "החזר חודשי"],
                   dtype="number", agg="sum", minimum=0, maximum=10_000_000),
    CanonicalField("restricted_account", "חשבון מוגבל",
                   ["restricted_account", "restricted", "חשבוןמוגבל", "חשבון מוגבל", "מוגבל"],
                   dtype="bool", agg="any"),
    CanonicalField("bankruptcy", "פשיטת רגל / כונס",
                   ["bankruptcy", "insolvency", "פשיטתרגל", "פשיטת רגל", "פשר", "פש\"ר"],
                   dtype="bool", agg="any"),
]

FIELDS_BY_ID: Dict[str, CanonicalField] = {f.id: f for f in _FIELDS}
KEY_FIELDS = ("national_id", "customer_id")


# ─────────────────────────────────────────────────────────────────────────────
# Header normalisation (Hebrew-aware) + deterministic alias matching
# ─────────────────────────────────────────────────────────────────────────────
_HE_FINALS = {"ם": "מ", "ן": "נ", "ץ": "צ", "ף": "פ", "ך": "כ"}


def normalize_header(text: Any) -> str:
    """Fold a column header to a comparable token (niqqud, finals, case, punct)."""
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))  # drop niqqud
    s = s.lower().strip()
    s = "".join(_HE_FINALS.get(ch, ch) for ch in s)
    s = re.sub(r"[\"'`׳״.\-_/\\|()\[\]{}:,;]+", "", s)
    s = re.sub(r"\s+", "", s)
    return s


_ALIAS_INDEX: Dict[str, str] = {}
for _f in _FIELDS:
    for _a in [_f.id, _f.label_he, *_f.aliases]:
        _ALIAS_INDEX.setdefault(normalize_header(_a), _f.id)


@dataclass
class ColumnMap:
    """One column's proposed mapping to a canonical field."""

    column: str
    field: Optional[str]      # canonical field id, or None = ignore
    confidence: float
    reason_he: str
    source: str = "heuristic"  # heuristic | ai | manual | learned

    def to_dict(self) -> Dict[str, Any]:
        return {"column": self.column, "field": self.field,
                "confidence": round(self.confidence, 2),
                "reason_he": self.reason_he, "source": self.source}


def _heuristic_map_column(column: str, samples: List[Any]) -> ColumnMap:
    """Map a single column by exact/normalised/partial alias match."""
    norm = normalize_header(column)
    if not norm:
        return ColumnMap(column, None, 0.0, "עמודה ללא כותרת", "heuristic")

    # exact normalised alias
    if norm in _ALIAS_INDEX:
        fid = _ALIAS_INDEX[norm]
        return ColumnMap(column, fid, 0.97,
                         f"התאמה מדויקת לכינוי מוכר של '{FIELDS_BY_ID[fid].label_he}'",
                         "heuristic")

    # containment either way (e.g. "הכנסה חודשית נטו" contains "הכנסה")
    best: Tuple[float, Optional[str]] = (0.0, None)
    for alias_norm, fid in _ALIAS_INDEX.items():
        if len(alias_norm) < 3:
            continue
        if alias_norm in norm or norm in alias_norm:
            ratio = min(len(alias_norm), len(norm)) / max(len(alias_norm), len(norm))
            score = 0.6 + 0.3 * ratio
            if score > best[0]:
                best = (score, fid)
    if best[1] is not None:
        fid = best[1]
        return ColumnMap(column, fid, round(best[0], 2),
                         f"התאמה חלקית לכינוי של '{FIELDS_BY_ID[fid].label_he}'", "heuristic")

    return ColumnMap(column, None, 0.0, "לא זוהתה התאמה לשדה ניקוד — יש לבדוק ידנית",
                     "heuristic")


# ─────────────────────────────────────────────────────────────────────────────
# AI mapper (Claude) — proposes a mapping, falls back to the heuristic on error
# ─────────────────────────────────────────────────────────────────────────────
AI_MODEL = os.environ.get("RISK_INGEST_MODEL",
                          os.environ.get("RISK_EXPLAIN_MODEL", "claude-opus-5"))


def ai_available() -> bool:
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _field_catalog() -> str:
    return "\n".join(f"- {f.id}: {f.label_he}" for f in _FIELDS)


_AI_SYSTEM = (
    "אתה מומחה לאינטגרציית נתונים בחברת אשראי. קיבלת שמות עמודות מקובץ אקסל "
    "(עברית/אנגלית, שמות לא אחידים) עם דוגמאות ערכים. מפה כל עמודה לשדה קנוני "
    "אחד מתוך הרשימה, או ל-null אם אינה רלוונטית לניקוד. אל תנחש — אם אינך בטוח, "
    "החזר confidence נמוך כדי שהעמודה תעבור לבדיקה אנושית. אסור למפות שתי עמודות "
    "לאותו שדה אלא אם באמת זהות. החזר אך ורק JSON תקין: "
    "{\"mappings\":[{\"column\":str,\"field\":str|null,\"confidence\":0..1,"
    "\"reason_he\":str}]}."
)


def _ai_map_columns(columns: List[str], samples: Dict[str, List[Any]]
                    ) -> Optional[List[ColumnMap]]:
    try:
        import anthropic

        client = anthropic.Anthropic()
        cols_payload = [{"column": c, "samples": [str(s) for s in samples.get(c, [])[:5]]}
                        for c in columns]
        user = ("השדות הקנוניים האפשריים:\n" + _field_catalog()
                + "\n\nהעמודות בקובץ (עם דוגמאות):\n"
                + json.dumps(cols_payload, ensure_ascii=False)
                + "\n\nהחזר JSON בלבד.")
        msg = client.messages.create(
            model=AI_MODEL, max_tokens=2000, system=_AI_SYSTEM,
            messages=[{"role": "user", "content": user}])
        if getattr(msg, "stop_reason", None) == "refusal":
            return None
        text = "".join(b.text for b in msg.content
                       if getattr(b, "type", None) == "text").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        data = json.loads(text[start:end + 1])
        out: List[ColumnMap] = []
        for m in data.get("mappings", []):
            fid = m.get("field")
            if fid not in FIELDS_BY_ID:
                fid = None
            out.append(ColumnMap(
                column=str(m.get("column")), field=fid,
                confidence=float(m.get("confidence", 0.0)),
                reason_he=str(m.get("reason_he", "")).strip(), source="ai"))
        # make sure every column is represented
        seen = {c.column for c in out}
        for c in columns:
            if c not in seen:
                out.append(_heuristic_map_column(c, samples.get(c, [])))
        return out
    except Exception:  # noqa: BLE001 - never let the AI layer break ingestion
        return None


def map_columns(columns: List[str], samples: Dict[str, List[Any]],
                use_ai: bool = False) -> List[ColumnMap]:
    """Propose a mapping for a table's columns.

    Heuristic always runs. When ``use_ai`` and a key are present, Claude's
    proposal is reconciled with it: agreement raises confidence; disagreement
    is flagged for review by lowering it.
    """
    heur = {c.column: c for c in (_heuristic_map_column(c, samples.get(c, [])) for c in columns)}
    if not (use_ai and ai_available()):
        return [heur[c] for c in columns]

    ai = _ai_map_columns(columns, samples)
    if ai is None:
        return [heur[c] for c in columns]
    ai_by_col = {m.column: m for m in ai}

    reconciled: List[ColumnMap] = []
    for c in columns:
        h, a = heur[c], ai_by_col.get(c)
        if a is None:
            reconciled.append(h)
        elif a.field == h.field and a.field is not None:
            reconciled.append(ColumnMap(c, a.field, max(a.confidence, h.confidence, 0.95),
                                        a.reason_he or h.reason_he, "ai"))
        elif h.confidence >= 0.97 and a.field != h.field:
            # a rock-solid alias beats the model, but flag the disagreement
            reconciled.append(ColumnMap(c, h.field, 0.75,
                                        f"{h.reason_he} (ה-AI הציע אחרת — לבדיקה)", "ai"))
        else:
            reconciled.append(a)
    return reconciled


# ─────────────────────────────────────────────────────────────────────────────
# Schema mapping (persist + reuse), grain detection, aggregation
# ─────────────────────────────────────────────────────────────────────────────
def table_signature(columns: List[str]) -> str:
    """Stable id for a table layout (order-independent, normalised)."""
    norm = sorted(normalize_header(c) for c in columns)
    return hashlib.sha1("|".join(norm).encode("utf-8")).hexdigest()[:12]


@dataclass
class SchemaMapping:
    """A reviewed, reusable mapping for one table layout."""

    signature: str
    columns: List[ColumnMap]
    grain: str = "customer"           # customer | transaction
    approved: bool = False

    def field_map(self, threshold: float = 0.0) -> Dict[str, str]:
        """{source_column: canonical_field} for columns confident enough to use.

        Below ``threshold`` a column is held out (it still appears in
        :meth:`review_needed`) so an uncertain guess never reaches a score.
        An approved mapping passes ``threshold=0`` to apply everything.
        """
        seen: Dict[str, str] = {}
        out: Dict[str, str] = {}
        # highest-confidence column wins a field if two map to the same one
        for c in sorted(self.columns, key=lambda x: -x.confidence):
            if not c.field or c.confidence < threshold:
                continue
            if c.field in seen:
                continue
            seen[c.field] = c.column
            out[c.column] = c.field
        return out

    def review_needed(self, threshold: float = 0.8) -> List[ColumnMap]:
        return [c for c in self.columns
                if c.field is None or c.confidence < threshold]

    def key_column(self) -> Optional[str]:
        # prefer national_id over customer_id
        for want in KEY_FIELDS:
            for c in self.columns:
                if c.field == want:
                    return c.column
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"signature": self.signature, "grain": self.grain,
                "approved": self.approved,
                "columns": [c.to_dict() for c in self.columns]}


class MappingBook:
    """A YAML book of learned mappings keyed by table signature."""

    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else None
        self._book: Dict[str, SchemaMapping] = {}
        if self.path and self.path.exists():
            import yaml
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            for sig, m in raw.items():
                self._book[sig] = SchemaMapping(
                    signature=sig, grain=m.get("grain", "customer"),
                    approved=bool(m.get("approved", False)),
                    columns=[ColumnMap(column=c["column"], field=c.get("field"),
                                       confidence=float(c.get("confidence", 1.0)),
                                       reason_he=c.get("reason_he", ""),
                                       source=c.get("source", "learned"))
                             for c in m.get("columns", [])])

    def get(self, signature: str) -> Optional[SchemaMapping]:
        return self._book.get(signature)

    def put(self, mapping: SchemaMapping) -> None:
        self._book[mapping.signature] = mapping
        if self.path:
            import yaml
            data = {sig: m.to_dict() for sig, m in self._book.items()}
            self.path.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def detect_grain(df: pd.DataFrame, key_column: Optional[str]) -> str:
    """A table is 'transaction' grain if a customer key repeats across rows."""
    if not key_column or key_column not in df.columns:
        return "customer"
    keys = df[key_column].map(_clean).dropna()
    if len(keys) == 0:
        return "customer"
    return "transaction" if keys.duplicated().any() else "customer"


_AGG_FUNCS = {
    "sum": lambda s: s.sum(),
    "mean": lambda s: s.mean(),
    "max": lambda s: s.max(),
    "min": lambda s: s.min(),
    "count": lambda s: s.count(),
    "any": lambda s: 1 if (s.fillna(0).astype(float) > 0).any() else 0,
    "first": lambda s: s.dropna().iloc[0] if s.notna().any() else None,
    "last": lambda s: s.dropna().iloc[-1] if s.notna().any() else None,
}


def _coerce(field: CanonicalField, value: Any) -> Tuple[Any, Optional[str]]:
    """Coerce to the field's dtype and range-check. Returns (value, warning)."""
    value = _clean(value)
    if value is None:
        return None, None
    warn = None
    if field.dtype in ("number", "ratio", "int"):
        try:
            num = float(str(value).replace(",", "").replace("₪", "").replace("%", "").strip())
        except (TypeError, ValueError):
            return None, f"ערך לא-מספרי בשדה '{field.label_he}': {value!r}"
        if field.dtype == "ratio" and num > 1.5:      # looks like a percentage
            num = num / 100.0
        if field.dtype == "int":
            num = int(round(num))
        if field.minimum is not None and num < field.minimum:
            warn = f"ערך נמוך מהצפוי בשדה '{field.label_he}': {num}"
        if field.maximum is not None and num > field.maximum:
            warn = f"ערך גבוה מהצפוי בשדה '{field.label_he}': {num}"
        return num, warn
    if field.dtype == "bool":
        s = str(value).strip().lower()
        truthy = s in ("1", "true", "yes", "y", "כן", "נכון", "מוגבל", "פעיל")
        return (1 if truthy else 0), None
    if field.dtype == "categorical":
        return str(value).strip().lower(), None
    return str(value).strip(), None


# ─────────────────────────────────────────────────────────────────────────────
# The pipeline: files → canonical records → DataStore + report
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class IngestResult:
    store: DataStore
    mappings: Dict[str, SchemaMapping] = field(default_factory=dict)      # filename → mapping
    review_needed: List[Dict[str, Any]] = field(default_factory=list)
    validation: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stats": self.stats,
            "mappings": {fn: m.to_dict() for fn, m in self.mappings.items()},
            "review_needed": self.review_needed,
            "validation": self.validation,
        }


def _read_frames(path: Path) -> List[Tuple[str, pd.DataFrame]]:
    """Read a file into (sheet_label, DataFrame) pairs (Excel = one per sheet)."""
    if path.suffix.lower() in (".xlsx", ".xls"):
        book = pd.read_excel(path, sheet_name=None, dtype=object)
        return [(f"{path.name}:{sheet}", df) for sheet, df in book.items()]
    return [(path.name, pd.read_csv(path, dtype=object))]


def _samples(df: pd.DataFrame, col: str, n: int = 5) -> List[Any]:
    vals = [v for v in (_clean(x) for x in df[col].tolist()) if v is not None]
    return vals[:n]


def ingest_files(paths: List[str | Path], use_ai: bool = False,
                 mapping_book: Optional[MappingBook] = None,
                 review_threshold: float = 0.8) -> IngestResult:
    """Map, aggregate and merge many messy tables into canonical records.

    Records are keyed by ת"ז where available; a synthetic ``customer_id`` is
    generated when a table has only a national id. Every field remembers which
    file it came from (data lineage).
    """
    records: Dict[str, Dict[str, Any]] = {}
    sources: Dict[str, Dict[str, str]] = {}
    mappings: Dict[str, SchemaMapping] = {}
    review: List[Dict[str, Any]] = []
    validation: List[str] = []
    tables_loaded: List[str] = []
    rows_in = 0

    frames: List[Tuple[str, pd.DataFrame]] = []
    for p in paths:
        p = Path(p)
        try:
            frames.extend(_read_frames(p))
        except Exception as exc:  # noqa: BLE001
            validation.append(f"⚠️ לא ניתן לקרוא את {p.name}: {exc}")

    for label, df in frames:
        if df is None or df.empty:
            continue
        df = df.dropna(axis=1, how="all")
        columns = [str(c) for c in df.columns]
        df.columns = columns
        sig = table_signature(columns)

        # 1) reuse a learned+approved mapping, else propose one
        learned = mapping_book.get(sig) if mapping_book else None
        if learned and learned.approved:
            col_maps = learned.columns
            grain = learned.grain
        else:
            samples = {c: _samples(df, c) for c in columns}
            col_maps = map_columns(columns, samples, use_ai=use_ai)
        key_col = _key_column(col_maps)
        grain = learned.grain if (learned and learned.approved) else detect_grain(df, key_col)
        mapping = SchemaMapping(signature=sig, columns=col_maps, grain=grain,
                                approved=bool(learned and learned.approved))
        mappings[label] = mapping
        if mapping_book is not None and not mapping.approved:
            mapping_book.put(mapping)      # remember the proposal for review

        # collect review items
        for c in mapping.review_needed(review_threshold):
            review.append({"table": label, **c.to_dict()})

        # apply only confident columns (approved mappings apply everything)
        fmap = mapping.field_map(0.0 if mapping.approved else review_threshold)
        if key_col is None:
            validation.append(
                f"⚠️ בטבלה {label} לא זוהתה עמודת מפתח (ת\"ז/מזהה) — דילוג.")
            continue
        tables_loaded.append(label)
        rows_in += len(df)

        # 2) build per-key canonical dicts (aggregate transaction tables)
        if grain == "transaction":
            per_key = _aggregate(df, key_col, fmap)
        else:
            per_key = _rows_to_dicts(df, key_col, fmap)

        # 3) merge into the master records, coercing + validating
        for raw_key, canon in per_key.items():
            nid = _canonical_nid(canon.get("national_id", raw_key))
            key = nid if nid else str(raw_key).strip()
            if not key:
                continue
            rec = records.setdefault(key, {})
            src = sources.setdefault(key, {})
            for fid, value in canon.items():
                fdef = FIELDS_BY_ID.get(fid)
                if fdef is None:
                    continue
                coerced, warn = _coerce(fdef, value)
                if warn:
                    validation.append(f"[{label}] {warn}")
                if coerced is None:
                    continue
                if fid not in rec:                    # first non-null wins; keep lineage
                    rec[fid] = coerced
                    src[fid] = label
                elif rec[fid] != coerced and fdef.dtype != "text":
                    validation.append(
                        f"קונפליקט בשדה '{fdef.label_he}' ללקוח {_mask(key)}: "
                        f"{rec[fid]} (מ-{src.get(fid)}) מול {coerced} (מ-{label})")

    # 4) finalise identity: synthesize customer_id / national_id where missing
    final: Dict[str, Dict[str, Any]] = {}
    final_src: Dict[str, Dict[str, str]] = {}
    for key, rec in records.items():
        nid = _canonical_nid(rec.get("national_id", key))
        if nid and "national_id" not in rec:
            rec["national_id"] = nid
        cid = rec.get("customer_id") or (f"C{nid[-6:]}" if nid else key)
        rec["customer_id"] = str(cid)
        final[rec["customer_id"]] = rec
        final_src[rec["customer_id"]] = sources.get(key, {})

    store = DataStore(records=final, tables=tables_loaded, sources=final_src)
    stats = {"files": len({f.split(':')[0] for f in mappings}),
             "tables": len(mappings), "rows_in": rows_in,
             "customers_out": len(final),
             "review_items": len(review)}
    return IngestResult(store=store, mappings=mappings, review_needed=review,
                        validation=validation, stats=stats)


def _key_column(col_maps: List[ColumnMap]) -> Optional[str]:
    for want in KEY_FIELDS:
        for c in col_maps:
            if c.field == want:
                return c.column
    return None


def _rows_to_dicts(df: pd.DataFrame, key_col: str,
                   fmap: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        key = _clean(row[key_col])
        if key is None:
            continue
        canon = {fmap[col]: _clean(row[col]) for col in fmap if col in df.columns}
        out[str(key).strip()] = {k: v for k, v in canon.items() if v is not None}
    return out


def _aggregate(df: pd.DataFrame, key_col: str,
               fmap: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Collapse a transaction table to one canonical dict per customer."""
    out: Dict[str, Dict[str, Any]] = {}
    df = df.copy()
    df["_k"] = df[key_col].map(lambda v: (_clean(v) or ""))
    for key, grp in df.groupby("_k"):
        if not key:
            continue
        canon: Dict[str, Any] = {}
        for col, fid in fmap.items():
            if col not in df.columns:
                continue
            fdef = FIELDS_BY_ID[fid]
            series = grp[col]
            if fdef.dtype in ("number", "ratio", "int") and fdef.agg in (
                    "sum", "mean", "max", "min", "count"):
                series = pd.to_numeric(
                    series.map(lambda v: _clean(v)), errors="coerce").dropna()
                if series.empty and fdef.agg != "count":
                    continue
            fn = _AGG_FUNCS.get(fdef.agg, _AGG_FUNCS["last"])
            try:
                canon[fid] = fn(series)
            except Exception:  # noqa: BLE001
                canon[fid] = _AGG_FUNCS["last"](series)
        canon["__txn_count__"] = len(grp)      # informational
        out[str(key)] = {k: v for k, v in canon.items()
                         if not k.startswith("__") and v is not None}
    return out


def _mask(key: str) -> str:
    return ("•" * max(0, len(key) - 4)) + key[-4:] if key else ""


def ingest_directory(directory: str | Path, **kwargs) -> IngestResult:
    """Ingest every CSV/Excel file in a directory."""
    directory = Path(directory)
    files: List[Path] = []
    for pattern in ("*.csv", "*.xlsx", "*.xls"):
        files.extend(sorted(directory.glob(pattern)))
    files = list(dict.fromkeys(files))
    if not files:
        raise FileNotFoundError(f"לא נמצאו קבצי CSV/Excel בתיקייה {directory}")
    return ingest_files(files, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def _fmt_mapping(result: "IngestResult") -> str:
    out: List[str] = ["מיפוי עמודות שזוהה (לכל טבלה):"]
    for label, m in result.mappings.items():
        out.append(f"\n■ {label}   [{'תנועה→צבירה' if m.grain == 'transaction' else 'רמת-לקוח'}]")
        for c in m.columns:
            if c.field:
                tick = "✓" if c.confidence >= 0.8 else "?"
                out.append(f"   {tick} {c.column}  →  {FIELDS_BY_ID[c.field].label_he}"
                           f"   ({c.confidence:.0%} · {c.source})")
            else:
                out.append(f"   ✗ {c.column}  →  (מתעלם / לבדיקה)")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    from .config import ScoringModel
    from .engine import RiskEngine
    from .service import DEFAULT_CONFIG

    p = argparse.ArgumentParser(
        prog="risk-ingest",
        description="קליטת אקסלים מגוונים → רשומה קנונית אחת ללקוח → ניקוד.")
    p.add_argument("--data", required=True, help="תיקייה עם קבצי CSV/Excel של החברה")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="מודל הניקוד (YAML)")
    p.add_argument("--mapping", default=None,
                   help="קובץ מיפויים נלמדים (YAML) — נשמר ומשמש חוזר")
    p.add_argument("--amount", type=float, default=100000, help="סכום הלוואה לבדיקה")
    p.add_argument("--ai", action="store_true",
                   help="שימוש ב-Claude לזיהוי עמודות (דורש ANTHROPIC_API_KEY)")
    p.add_argument("--json", action="store_true", help="פלט JSON")
    p.add_argument("--score", action="store_true", help="ניקוד כל הלקוחות שנקלטו")
    args = p.parse_args(argv)

    book = MappingBook(args.mapping) if args.mapping else None
    result = ingest_directory(args.data, use_ai=args.ai, mapping_book=book)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        s = result.stats
        print(f"נקלטו {s['tables']} טבלאות מתוך {s['files']} קבצים · "
              f"{s['rows_in']} שורות → {s['customers_out']} לקוחות קנוניים.")
        print()
        print(_fmt_mapping(result))
        if result.review_needed:
            print(f"\n⚠ {len(result.review_needed)} עמודות לאישור אנושי (confidence נמוך / לא זוהו):")
            for r in result.review_needed:
                print(f"   • [{r['table']}] {r['column']}  ({r['confidence']:.0%})")
        if result.validation:
            print(f"\n⚠ {len(result.validation)} התראות ולידציה:")
            for v in result.validation[:20]:
                print(f"   • {v}")

    if args.score:
        model = ScoringModel.load(args.config)
        engine = RiskEngine(model)
        print("\nניקוד הלקוחות שנקלטו:")
        for cid, name in result.store.list_customers():
            rec = result.store.get_record(cid)
            res = engine.score(rec, args.amount)
            ko = " ⛔נוק-אאוט" if res.knockouts else ""
            print(f"   {cid}  ·  {name or '—':<12}  ציון {res.score:.1f}/10  "
                  f"→ {res.decision_label_he}{ko}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
