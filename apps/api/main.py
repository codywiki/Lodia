from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lodia.pipeline import process_text_case
from lodia.security import SECURITY_HEADERS
from lodia.store import LodiaStore


class PreviewRequest(BaseModel):
    owner_id: str = Field(default="demo_contributor", max_length=128)
    text: str = Field(min_length=1, max_length=200_000)
    allowed_uses: List[str] = Field(default_factory=lambda: ["private_library", "candidate_pool"])


class SubmissionRequest(PreviewRequest):
    pass


class ReviewRequest(BaseModel):
    reviewer_id: str = Field(default="reviewer_demo", max_length=128)
    notes: str = Field(default="", max_length=2_000)


class DatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    purpose: str = Field(default="commercial_dataset", max_length=80)
    min_drl: str = Field(default="DRL3", pattern="^DRL[0-5]$")
    gross_revenue_cents: int = Field(default=100_000, ge=0)
    direct_cost_cents: int = Field(default=20_000, ge=0)


app = FastAPI(
    title="Lodia API",
    version="0.1.0",
    description="Commercial-grade AI Case-to-Dataset platform API foundation.",
)

store = LodiaStore()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    return response


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "lodia-api"}


@app.post("/api/pipeline/preview")
async def preview_case(payload: PreviewRequest):
    processed = process_text_case(
        raw_text=payload.text,
        owner_id=payload.owner_id,
        allowed_uses=payload.allowed_uses,
    )
    return asdict(processed)


@app.post("/api/submissions/text")
async def submit_text(payload: SubmissionRequest):
    try:
        return store.submit_text(
            owner_id=payload.owner_id,
            text=payload.text,
            allowed_uses=payload.allowed_uses,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/cases")
async def list_cases():
    return {"items": store.list_cases()}


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str):
    try:
        return store.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/review/{case_id}/approve")
async def approve_case(case_id: str, payload: ReviewRequest):
    try:
        return store.approve_case(case_id, payload.reviewer_id, payload.notes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets")
async def create_dataset(payload: DatasetRequest):
    try:
        return store.create_dataset(
            name=payload.name,
            purpose=payload.purpose,
            min_drl=payload.min_drl,
            gross_revenue_cents=payload.gross_revenue_cents,
            direct_cost_cents=payload.direct_cost_cents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/datasets/{dataset_id}")
async def get_dataset(dataset_id: str):
    try:
        return store.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/ledger/usage-events")
async def list_usage_events():
    return {"items": store.list_usage_events()}


@app.get("/api/ledger/payout-events")
async def list_payout_events():
    return {"items": store.list_payout_events()}
