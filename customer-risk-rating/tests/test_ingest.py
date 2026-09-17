"""בדיקות לשכבת הקליטה החכמה (ingest) — מיפוי, צבירה, ולידציה, מיזוג."""
import pandas as pd
import pytest

from risk_rating.config import ScoringModel
from risk_rating.engine import RiskEngine
from risk_rating.ingest import (FIELDS_BY_ID, MappingBook, SchemaMapping,
                                _aggregate, _coerce, detect_grain,
                                ingest_directory, map_columns, normalize_header,
                                table_signature)
from risk_rating.service import DEFAULT_CONFIG, PROJECT_ROOT

MESSY_DIR = PROJECT_ROOT / "data" / "messy_demo"


# ── header normalisation ─────────────────────────────────────────────────────
def test_normalize_folds_quotes_finals_case():
    assert normalize_header('ת"ז') == normalize_header("תז")
    assert normalize_header("Monthly_Income") == "monthlyincome"
    # final letters fold to their base form (ם→מ)
    assert normalize_header("שלום") == normalize_header("שלומ")


# ── the AI-independent heuristic mapper ──────────────────────────────────────
def _fields(names):
    samples = {n: [] for n in names}
    return {m.column: m.field for m in map_columns(names, samples, use_ai=False)}


def test_income_aliases_all_map_to_one_field():
    got = _fields(["הכנסה", "שכר נטו", "monthly_income", "משכורת"])
    assert set(got.values()) == {"monthly_income"}


def test_national_id_spellings_all_map_to_key():
    got = _fields(["תעודת זהות", "תז", "ת.ז", "national_id"])
    assert set(got.values()) == {"national_id"}


def test_unknown_column_is_not_mapped():
    [m] = map_columns(["צבע עיניים"], {"צבע עיניים": ["חום"]}, use_ai=False)
    assert m.field is None
    assert m.confidence < 0.8


# ── grain detection + aggregation ────────────────────────────────────────────
def test_detect_grain():
    tx = pd.DataFrame({"k": ["a", "a", "b"], "v": [1, 2, 3]})
    cust = pd.DataFrame({"k": ["a", "b", "c"], "v": [1, 2, 3]})
    assert detect_grain(tx, "k") == "transaction"
    assert detect_grain(cust, "k") == "customer"


def test_aggregate_sums_and_means():
    df = pd.DataFrame({
        "kid": ["1", "1", "1"],
        "bal": [10, 20, 30],       # avg_balance → mean = 20
        "ovd": [1, 2, 3],          # overdraft_days_12m → sum = 6
    })
    fmap = {"bal": "avg_balance", "ovd": "overdraft_days_12m"}
    agg = _aggregate(df, "kid", fmap)["1"]
    assert agg["avg_balance"] == pytest.approx(20)
    assert agg["overdraft_days_12m"] == pytest.approx(6)


# ── coercion + validation ────────────────────────────────────────────────────
def test_coerce_percentage_to_ratio():
    v, warn = _coerce(FIELDS_BY_ID["credit_utilization"], "95")
    assert v == pytest.approx(0.95)
    assert warn is None


def test_coerce_currency_and_int():
    v, _ = _coerce(FIELDS_BY_ID["monthly_income"], "18,000 ₪")
    assert v == pytest.approx(18000)
    v2, _ = _coerce(FIELDS_BY_ID["age"], "34.0")
    assert v2 == 34 and isinstance(v2, int)


def test_coerce_bool_hebrew():
    assert _coerce(FIELDS_BY_ID["restricted_account"], "מוגבל")[0] == 1
    assert _coerce(FIELDS_BY_ID["restricted_account"], "0")[0] == 0


def test_coerce_out_of_range_warns():
    _, warn = _coerce(FIELDS_BY_ID["age"], "200")
    assert warn is not None


# ── end-to-end on the messy demo files ───────────────────────────────────────
@pytest.fixture(scope="module")
def result():
    return ingest_directory(MESSY_DIR, use_ai=False)


def test_ingests_four_customers_from_messy_files(result):
    assert result.stats["customers_out"] == 4
    assert result.stats["tables"] == 4


def test_transaction_table_is_aggregated(result):
    # Yosef's 3 monthly rows collapse: balance→mean 3000, overdraft→sum 20
    rec = result.store.resolve_record("358024917")
    assert rec["avg_balance"] == pytest.approx(3000)
    assert rec["overdraft_days_12m"] == pytest.approx(20)


def test_fields_from_different_files_are_merged(result):
    rec = result.store.resolve_record("034512789")   # by ת"ז
    assert rec["full_name"] == "יוסי כהן"             # 01_לקוחות
    assert rec["monthly_income"] == pytest.approx(18000)   # 02_payroll
    assert rec["external_bureau_score"] == pytest.approx(720)  # 04_credit_report
    assert rec["credit_utilization"] == pytest.approx(0.35)    # 35% → 0.35


def test_low_confidence_column_is_flagged_not_applied(result):
    # "חודש" leans toward tenure by partial match — it must be flagged...
    flagged = {r["column"] for r in result.review_needed}
    assert "חודש" in flagged
    # ...and must NOT have corrupted anyone's tenure (that comes from payroll only)
    rec = result.store.resolve_record("034512789")
    assert rec["tenure_months"] == pytest.approx(40)


def test_ingested_records_score_and_knockout(result):
    engine = RiskEngine(ScoringModel.load(DEFAULT_CONFIG))
    yosef = engine.score(result.store.resolve_record("358024917"), 100000)
    assert yosef.knockouts                       # defaults + restricted account
    assert yosef.decision_id == "reject"
    yossi = engine.score(result.store.resolve_record("034512789"), 100000)
    assert yossi.decision_id == "approve"


# ── learned-mapping persistence ──────────────────────────────────────────────
def test_signature_is_order_independent():
    assert table_signature(["a", "b", "c"]) == table_signature(["c", "a", "b"])


def test_mapping_book_roundtrip_and_reuse(tmp_path):
    from risk_rating.ingest import ColumnMap
    path = tmp_path / "mappings.yaml"
    book = MappingBook(path)
    sig = table_signature(["ת\"ז", "הכנסה"])
    book.put(SchemaMapping(signature=sig, approved=True, columns=[
        ColumnMap("ת\"ז", "national_id", 1.0, "", "manual"),
        ColumnMap("הכנסה", "monthly_income", 1.0, "", "manual"),
    ]))
    reloaded = MappingBook(path)
    got = reloaded.get(sig)
    assert got is not None and got.approved
    assert got.field_map() == {"ת\"ז": "national_id", "הכנסה": "monthly_income"}
