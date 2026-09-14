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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from .data import AmbiguousCustomer, CustomerNotFound
from .explain import ai_available, explain
from .service import (DEFAULT_CONFIG, DEFAULT_DATA_DIR, PROJECT_ROOT,
                      RiskRatingService)

DATA_DIR = os.environ.get("RISK_DATA_DIR", str(DEFAULT_DATA_DIR))
CONFIG_PATH = os.environ.get("RISK_CONFIG", str(DEFAULT_CONFIG))
_PAGE_PATH = PROJECT_ROOT / "web" / "app.html"

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
    return {"customers": [{"id": cid, "name": name}
                          for cid, name in service.list_customers()]}


@app.post("/api/score")
def score(req: ScoreRequest) -> JSONResponse:
    try:
        result = service.assess(req.customer, req.amount, consent=req.consent,
                                purpose=req.purpose, collateral=req.collateral,
                                adjudicate=req.adjudicate,
                                use_ai=req.use_ai and ai_available())
    except AmbiguousCustomer as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except CustomerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    payload = result.to_dict()
    if req.explain or req.use_ai:
        payload["explanation"] = explain(result, use_ai=req.use_ai and ai_available())
    return JSONResponse(content=payload)
