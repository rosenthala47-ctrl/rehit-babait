"""בדיקות לשרת הקליטה (FastAPI): העלאה → הצעת מיפוי → אישור → ניקוד."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("multipart")   # python-multipart, required for uploads
from fastapi.testclient import TestClient   # noqa: E402

from risk_rating.api import app             # noqa: E402
from risk_rating.service import PROJECT_ROOT  # noqa: E402

MESSY = PROJECT_ROOT / "data" / "messy_demo"
client = TestClient(app)


def _upload():
    files = [("files", (p.name, p.read_bytes(), "text/csv"))
             for p in sorted(MESSY.glob("*.csv"))]
    r = client.post("/api/ingest/propose", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_fields_endpoint_lists_canonical_fields():
    r = client.get("/api/ingest/fields")
    assert r.status_code == 200
    ids = {f["id"] for f in r.json()["fields"]}
    assert {"national_id", "monthly_income", "defaults"} <= ids


def test_propose_maps_columns_and_detects_transaction_grain():
    data = _upload()
    assert data["upload_id"]
    assert len(data["tables"]) == 4
    # every table found a national_id column (spelled differently each file)
    for t in data["tables"]:
        assert any(c["field"] == "national_id" for c in t["columns"])
    # the monthly banking file is detected as a transaction table
    bank = next(t for t in data["tables"] if "בנק" in t["label"])
    assert bank["grain"] == "transaction"
    # samples are included for the review UI
    assert all("samples" in c for t in data["tables"] for c in t["columns"])


def test_apply_scores_every_customer_with_knockout():
    data = _upload()
    approved = {}
    for t in data["tables"]:
        approved[t["signature"]] = {
            "grain": t["grain"],
            # mirror the UI: confident columns kept, uncertain ones held out
            "columns": [{"column": c["column"],
                         "field": c["field"] if (c["confidence"] or 0) >= 0.8 else None}
                        for c in t["columns"]],
        }
    r = client.post("/api/ingest/apply",
                    json={"upload_id": data["upload_id"], "amount": 100000, "approved": approved})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stats"]["customers_out"] == 4
    by_name = {c["full_name"]: c for c in body["customers"]}
    yosef = by_name["יוסף שמעוני"]
    assert yosef["decision"] == "reject"
    assert yosef["knockouts"]                       # default + restricted account
    assert yosef["pulled"]["avg_balance"] == pytest.approx(3000)   # transaction mean
    assert by_name["יוסי כהן"]["decision"] == "approve"


def test_propose_rejects_non_tabular_upload():
    r = client.post("/api/ingest/propose",
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 400


def test_apply_unknown_upload_id_is_404():
    r = client.post("/api/ingest/apply",
                    json={"upload_id": "deadbeef", "amount": 1000, "approved": {}})
    assert r.status_code == 404
