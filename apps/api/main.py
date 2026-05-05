from __future__ import annotations

from dataclasses import asdict
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lodia.auth import AuthContext, AuthManager, auth_dependency
from lodia.config import LodiaSettings
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

settings = LodiaSettings.from_env()
auth_manager = AuthManager(settings)
store = LodiaStore(settings=settings)
require_contributor = auth_dependency(auth_manager, "contributor", "reviewer", "admin")
require_reviewer = auth_dependency(auth_manager, "reviewer", "admin")
require_admin = auth_dependency(auth_manager, "admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
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
    return {
        "status": "ok",
        "service": "lodia-api",
        "env": settings.env,
        "database": "postgres" if settings.use_postgres else "sqlite",
        "object_storage": settings.object_storage_backend,
        "auth_enabled": auth_manager.enabled,
        "async_processing": settings.async_processing,
    }


@app.post("/api/pipeline/preview")
async def preview_case(payload: PreviewRequest, actor: AuthContext = Depends(require_contributor)):
    processed = process_text_case(
        raw_text=payload.text,
        owner_id=_owner_id(payload.owner_id, actor),
        allowed_uses=payload.allowed_uses,
    )
    return asdict(processed)


@app.post("/api/submissions/text")
async def submit_text(payload: SubmissionRequest, actor: AuthContext = Depends(require_contributor)):
    try:
        return store.submit_text(
            owner_id=_owner_id(payload.owner_id, actor),
            text=payload.text,
            allowed_uses=payload.allowed_uses,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/cases")
async def list_cases(actor: AuthContext = Depends(require_reviewer)):
    return {"items": store.list_cases()}


@app.get("/api/cases/{case_id}")
async def get_case(case_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.get_case(case_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/review/{case_id}/approve")
async def approve_case(case_id: str, payload: ReviewRequest, actor: AuthContext = Depends(require_reviewer)):
    try:
        reviewer_id = actor.subject_id if auth_manager.enabled else payload.reviewer_id
        return store.approve_case(case_id, reviewer_id, payload.notes)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets")
async def create_dataset(payload: DatasetRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_dataset(
            name=payload.name,
            purpose=payload.purpose,
            min_drl=payload.min_drl,
            gross_revenue_cents=payload.gross_revenue_cents,
            direct_cost_cents=payload.direct_cost_cents,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/ledger/usage-events")
async def list_usage_events(actor: AuthContext = Depends(require_admin)):
    return {"items": store.list_usage_events()}


@app.get("/api/ledger/payout-events")
async def list_payout_events(actor: AuthContext = Depends(require_admin)):
    return {"items": store.list_payout_events()}


@app.get("/api/audit/logs")
async def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    entity_id: Optional[str] = Query(default=None, max_length=160),
    actor: AuthContext = Depends(require_admin),
):
    return {"items": store.list_audit_logs(limit=limit, entity_id=entity_id)}


@app.get("/api/admin/jobs")
async def list_jobs(limit: int = Query(default=100, ge=1, le=500), actor: AuthContext = Depends(require_admin)):
    return {"items": store.list_jobs(limit=limit)}


def _owner_id(requested_owner_id: str, actor: AuthContext) -> str:
    if auth_manager.enabled and "admin" not in actor.roles:
        return actor.subject_id
    return requested_owner_id
