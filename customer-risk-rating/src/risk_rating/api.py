"""שרת Web (FastAPI) למנוע דירוג הסיכון.

Run it with:

    pip install fastapi uvicorn
    uvicorn risk_rating.api:app --reload

Then open http://127.0.0.1:8000 — pick a customer, enter an amount, and the
system pulls the customer's data from all tables and returns a score in
under a second (the "AI inside the credit company's system").

Endpoints
---------
    GET  /                 → the Hebrew web UI
    GET  /api/customers    → list of customers
    POST /api/score        → {customer, amount, explain?, use_ai?} → full result
    GET  /api/health       → health check
"""
from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .data import AmbiguousCustomer, CustomerNotFound
from .explain import ai_available, explain
from .ingest import (FIELDS, MappingBook, ai_available as ingest_ai_available,
                     apply_and_ingest, propose_tables)
from .service import (DEFAULT_CONFIG, DEFAULT_DATA_DIR, PROJECT_ROOT,
                      RiskRatingService)

DATA_DIR = os.environ.get("RISK_DATA_DIR", str(DEFAULT_DATA_DIR))
CONFIG_PATH = os.environ.get("RISK_CONFIG", str(DEFAULT_CONFIG))
_PAGE_PATH = PROJECT_ROOT / "web" / "app.html"
_INGEST_PAGE = PROJECT_ROOT / "web" / "ingest_app.html"
UPLOAD_ROOT = Path(os.environ.get("RISK_UPLOAD_DIR", Path(tempfile.gettempdir()) / "risk_uploads"))
_ALLOWED_EXT = (".csv", ".xlsx", ".xls")

app = FastAPI(title="דירוג סיכון לקוח", version="0.1.0")
service = RiskRatingService(data_dir=DATA_DIR, config_path=CONFIG_PATH)


class ScoreRequest(BaseModel):
    customer: str = Field(..., description="שם הלקוח או מזהה")
    amount: float = Field(..., gt=0, description="סכום ההלוואה המבוקש")
    consent: bool = Field(True, description="הלקוח נתן הסכמה למשיכת נתוני אשראי מבנק ישראל")
    purpose: Optional[str] = Field(None, description="מטרת ההלוואה")
    collateral: Optional[str] = Field(None, description="בטוחה להלוואה")
    adjudicate: bool = Field(False, description="הפעלת שיקול דעת (יכול לאשר חריגה)")
    explain: bool = Field(True, description="הוספת הסבר מילולי")
    use_ai: bool = Field(False, description="שימוש ב-Claude לשיקול דעת ולחוות דעת (דורש מפתח API)")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if _PAGE_PATH.exists():
        return _PAGE_PATH.read_text(encoding="utf-8")
    return "<h1>דירוג סיכון לקוח</h1><p>קובץ הממשק web/app.html לא נמצא.</p>"


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "customers": len(service.list_customers()),
        "tables": service.store.tables,
        "credit_bureau": service.bureau.source,
        "ai_available": ai_available(),
        "model_version": service.model.version,
    }


@app.get("/api/customers")
def customers() -> dict:
    # include distinguishing details so same-named customers are told apart
    return {"customers": [
        {"id": c["customer_id"], "name": c["full_name"],
         "city": c.get("city"), "national_id_masked": c.get("national_id_masked")}
        for c in service.store.list_customer_details()]}


@app.post("/api/score")
def score(req: ScoreRequest) -> JSONResponse:
    try:
        result = service.assess(req.customer, req.amount, consent=req.consent,
                                purpose=req.purpose, collateral=req.collateral,
                                adjudicate=req.adjudicate,
                                use_ai=req.use_ai and ai_available())
    except AmbiguousCustomer as exc:
        # same name matches several people — hand back the candidates to choose from
        raise HTTPException(status_code=409,
                            detail={"message": str(exc), "candidates": exc.candidates})
    except CustomerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = result.to_dict()
    if req.explain or req.use_ai:
        payload["explanation"] = explain(result, use_ai=req.use_ai and ai_available())
    return JSONResponse(content=payload)


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion server: upload messy Excels → AI column mapping → review → score
# ─────────────────────────────────────────────────────────────────────────────
class ApprovedColumn(BaseModel):
    column: str
    field: Optional[str] = None


class ApprovedTable(BaseModel):
    grain: str = "customer"
    columns: List[ApprovedColumn] = Field(default_factory=list)


class ApplyRequest(BaseModel):
    upload_id: str
    amount: float = Field(100000, gt=0)
    approved: Dict[str, ApprovedTable]


def _upload_dir(upload_id: str) -> Path:
    # upload_id is server-generated (hex); reject anything else to avoid traversal
    if not upload_id.isalnum():
        raise HTTPException(status_code=400, detail="מזהה העלאה לא תקין")
    return UPLOAD_ROOT / upload_id


def _upload_files(d: Path) -> List[Path]:
    return sorted(p for p in d.iterdir() if p.suffix.lower() in _ALLOWED_EXT)


@app.get("/ingest", response_class=HTMLResponse)
def ingest_page() -> str:
    if _INGEST_PAGE.exists():
        return _INGEST_PAGE.read_text(encoding="utf-8")
    return "<h1>קליטת נתונים</h1><p>web/ingest_app.html לא נמצא.</p>"


@app.get("/api/ingest/fields")
def ingest_fields() -> dict:
    return {"fields": [{"id": f.id, "label_he": f.label_he} for f in FIELDS]}


@app.post("/api/ingest/propose")
async def ingest_propose(files: List[UploadFile] = File(...)) -> dict:
    """Upload company files; return the proposed (AI/heuristic) column mapping."""
    uid = uuid.uuid4().hex[:16]
    d = _upload_dir(uid)
    d.mkdir(parents=True, exist_ok=True)
    saved = 0
    for f in files:
        name = Path(f.filename or "").name          # strip any path components
        if not name or Path(name).suffix.lower() not in _ALLOWED_EXT:
            continue
        (d / name).write_bytes(await f.read())
        saved += 1
    if not saved:
        raise HTTPException(status_code=400, detail="לא הועלו קבצי CSV/Excel תקינים")
    book = MappingBook(d / "mappings.yaml")
    tables = propose_tables(_upload_files(d), use_ai=ingest_ai_available(), mapping_book=book)
    return {"upload_id": uid, "ai_used": ingest_ai_available(),
            "tables": tables, "files": saved}


@app.post("/api/ingest/apply")
def ingest_apply(req: ApplyRequest) -> JSONResponse:
    """Persist the analyst-approved mapping, re-ingest, and score every customer."""
    d = _upload_dir(req.upload_id)
    if not d.exists():
        raise HTTPException(status_code=404, detail="ההעלאה פגה — יש להעלות את הקבצים מחדש")
    approved = {sig: {"grain": t.grain,
                      "columns": [c.model_dump() for c in t.columns]}
                for sig, t in req.approved.items()}
    book = MappingBook(d / "mappings.yaml")
    result = apply_and_ingest(_upload_files(d), approved, book)

    customers = []
    for cid, name in result.store.list_customers():
        rec = result.store.get_record(cid)
        r = service.engine.score(rec, req.amount)
        pulled = {k: rec[k] for k in (
            "national_id", "monthly_income", "avg_balance", "external_bureau_score",
            "credit_utilization", "defaults", "restricted_account") if k in rec}
        customers.append({
            "customer_id": cid, "full_name": name,
            "score": round(r.score, 1), "decision": r.decision_id,
            "decision_label_he": r.decision_label_he,
            "knockouts": [k["label_he"] for k in r.knockouts],
            "pulled": pulled,
            "sources": result.store.get_sources(cid),
        })
    customers.sort(key=lambda c: c["score"])
    return JSONResponse(content={
        "customers": customers, "stats": result.stats,
        "validation": result.validation})


@app.get("/api/audit")
def audit(customer: Optional[str] = None) -> dict:
    """Consent + credit-pull audit trail (national id masked)."""
    try:
        return {"events": service.audit_trail(customer)}
    except AmbiguousCustomer as exc:
        raise HTTPException(status_code=409,
                            detail={"message": str(exc), "candidates": exc.candidates})
    except CustomerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
