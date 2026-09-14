"""בדיקות לשכבת הנתונים — טעינה, מיזוג וזיהוי לקוח."""
import pytest

from risk_rating.data import AmbiguousCustomer, CustomerNotFound, DataStore
from risk_rating.service import DEFAULT_DATA_DIR


@pytest.fixture(scope="module")
def store():
    return DataStore.from_directory(DEFAULT_DATA_DIR)


def test_loads_all_customers(store):
    customers = store.list_customers()
    assert len(customers) == 9
    ids = {cid for cid, _ in customers}
    assert {"C1001", "C1008", "C1009"} <= ids


def test_record_merges_all_tables(store):
    """One customer's record must combine columns from every company table.

    Credit fields (missed_payments_12m etc.) are NOT here — they come from the
    Bank of Israel register at assess time, not from the company's own tables.
    """
    rec = store.get_record("C1001")
    assert rec["full_name"] == "יוסי כהן"          # demographics.csv
    assert rec["monthly_income"] == 18000           # employment.csv
    assert rec["overdraft_days_12m"] == 3           # banking.csv
    assert "missed_payments_12m" not in rec         # comes from the credit bureau


def test_missing_row_leaves_field_absent(store):
    # Noa (C1006) has no banking.csv row.
    rec = store.get_record("C1006")
    assert "overdraft_days_12m" not in rec
    assert rec["full_name"] == "נועה שפירא"


def test_resolve_by_id(store):
    assert store.resolve("C1003") == "C1003"


def test_resolve_by_unique_name(store):
    assert store.resolve("שרה לוי") == "C1002"


def test_resolve_by_partial_name(store):
    assert store.resolve("מזרחי") == "C1003"


def test_resolve_by_national_id(store):
    # two customers are named "יוסי כהן" — the ת"ז disambiguates them
    assert store.resolve("034512789") == "C1001"
    assert store.resolve("311478502") == "C1009"
    # leading zero may be lost on input; resolution restores it
    assert store.resolve("34512789") == "C1001"


def test_duplicate_name_is_ambiguous(store):
    with pytest.raises(AmbiguousCustomer) as exc:
        store.resolve("יוסי כהן")
    cands = exc.value.candidates
    assert {c["customer_id"] for c in cands} == {"C1001", "C1009"}
    # candidates carry distinguishing details (masked ת"ז, city)
    assert all(c["national_id_masked"] for c in cands)
    assert {c["city"] for c in cands} == {"תל אביב", "חיפה"}


def test_resolve_unknown_raises(store):
    with pytest.raises(CustomerNotFound):
        store.resolve("לא קיים בכלל")


def test_numeric_fields_are_native_python(store):
    """Values must be native ints/floats, not numpy scalars (JSON-safe)."""
    rec = store.get_record("C1001")
    assert type(rec["monthly_income"]) in (int, float)
    assert type(rec["age"]) in (int, float)
