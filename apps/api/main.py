from __future__ import annotations

from dataclasses import asdict
import json
import uuid
from typing import List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.requests import Request

from lodia.auth import AuthContext, AuthManager, auth_dependency
from lodia.config import LodiaSettings
from lodia.limits import FixedWindowRateLimiter, rate_limit_key
from lodia.pipeline import process_text_case
from lodia.security import SECURITY_HEADERS
from lodia.signing import verify_request_signature
from lodia.store import LodiaStore

RATE_LIMIT_BYPASS_PATHS = {"/api/health", "/api/ready"}
SIGNATURE_BYPASS_PATHS = {"/api/health", "/api/ready", "/api/auth/login"}


class PreviewRequest(BaseModel):
    owner_id: str = Field(default="demo_contributor", max_length=128)
    text: str = Field(min_length=1, max_length=200_000)
    allowed_uses: List[str] = Field(default_factory=lambda: ["private_library", "candidate_pool"])


class SubmissionRequest(PreviewRequest):
    authorization_snapshot_id: Optional[str] = Field(default=None, max_length=128)


class ConsentCreateRequest(BaseModel):
    owner_id: str = Field(default="demo_contributor", max_length=128)
    allowed_uses: List[str] = Field(default_factory=lambda: ["private_library", "candidate_pool"])
    policy_version: str = Field(default="cn-pipl-2026-05", max_length=80)
    terms_version: str = Field(default="contributor-2026-05", max_length=80)
    source: str = Field(default="api", max_length=80)
    consent_text: str = Field(default="", max_length=10_000)


class ConsentWithdrawRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class ReviewRequest(BaseModel):
    reviewer_id: str = Field(default="reviewer_demo", max_length=128)
    notes: str = Field(default="", max_length=2_000)


class AdvancedReviewRequest(ReviewRequest):
    score: float = Field(default=1.0, ge=0, le=1)
    rubric: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)


class DatasetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    purpose: str = Field(default="commercial_dataset", max_length=80)
    min_drl: str = Field(default="DRL3", pattern="^DRL[0-5]$")
    gross_revenue_cents: int = Field(default=100_000, ge=0)
    direct_cost_cents: int = Field(default=20_000, ge=0)
    max_cases: Optional[int] = Field(default=None, ge=1, le=100_000)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)


class UserCreateRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)
    roles: List[str] = Field(default_factory=lambda: ["contributor"])


class TokenCreateRequest(BaseModel):
    name: str = Field(default="api-token", min_length=1, max_length=120)
    roles: Optional[List[str]] = None
    expires_at: Optional[str] = Field(default=None, max_length=80)


class RejectRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class PayoutSettleRequest(BaseModel):
    notes: str = Field(default="", max_length=1_000)


class PayoutBatchCreateRequest(BaseModel):
    contributor_id: Optional[str] = Field(default=None, max_length=128)
    min_amount_cents: int = Field(default=1, ge=1)
    max_events: int = Field(default=1000, ge=1, le=10_000)


class PayoutBatchSettleRequest(BaseModel):
    external_reference: str = Field(default="", max_length=160)
    notes: str = Field(default="", max_length=1_000)


class ApprovalRequestPayload(BaseModel):
    operation_type: str = Field(min_length=1, max_length=120)
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=2_000)
    payload: dict = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    notes: str = Field(default="", max_length=2_000)


app = FastAPI(
    title="Lodia API",
    version="0.1.0",
    description="Commercial-grade AI Case-to-Dataset platform API foundation.",
)

settings = LodiaSettings.from_env()
store = LodiaStore(settings=settings)
auth_manager = AuthManager(settings, token_resolver=store.lookup_api_token)
rate_limiter = FixedWindowRateLimiter(
    requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
    enabled=settings.rate_limit_enabled,
)
require_contributor = auth_dependency(auth_manager, "contributor", "reviewer", "admin")
require_reviewer = auth_dependency(auth_manager, "reviewer", "admin")
require_admin = auth_dependency(auth_manager, "admin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "Idempotency-Key",
        "X-Lodia-Signature",
        "X-Lodia-Timestamp",
        "X-Request-ID",
    ],
)


@app.middleware("http")
async def production_guardrails(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:16]}"
    content_length = request.headers.get("content-length")
    if content_length and _exceeds_body_limit(content_length):
        response = JSONResponse(
            status_code=413,
            content={"detail": "request_body_too_large", "request_id": request_id},
        )
        _apply_response_headers(response, request_id)
        return response

    body: Optional[bytes] = None
    should_read_body = _should_read_body(request, content_length)
    if should_read_body:
        body, oversized = await _read_limited_body(request)
        if oversized:
            response = JSONResponse(
                status_code=413,
                content={"detail": "request_body_too_large", "request_id": request_id},
            )
            _apply_response_headers(response, request_id)
            return response
        _replay_body(request, body)

    if _signature_required(request):
        body = body if body is not None else await request.body()
        _replay_body(request, body)
        signature_result = verify_request_signature(
            secret=settings.request_signature_secret,
            method=request.method,
            path=request.url.path,
            body=body,
            timestamp=request.headers.get("X-Lodia-Timestamp"),
            signature=request.headers.get("X-Lodia-Signature"),
        )
        if not signature_result.ok:
            response = JSONResponse(
                status_code=401,
                content={"detail": signature_result.reason, "request_id": request_id},
            )
            _apply_response_headers(response, request_id)
            return response

    rate_result = None
    if request.url.path not in RATE_LIMIT_BYPASS_PATHS:
        rate_result = rate_limiter.check(rate_limit_key(request, trust_proxy_headers=settings.trust_proxy_headers))
        if not rate_result.allowed:
            response = JSONResponse(
                status_code=429,
                content={"detail": "rate_limit_exceeded", "request_id": request_id},
                headers={"Retry-After": str(rate_result.retry_after)},
            )
            _apply_response_headers(response, request_id, rate_result)
            return response

    response = await call_next(request)
    _apply_response_headers(response, request_id, rate_result)
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


@app.get("/api/ready")
async def ready():
    readiness = store.readiness_check()
    return JSONResponse(status_code=200 if readiness["ok"] else 503, content=readiness)


@app.post("/api/auth/login")
async def login(payload: LoginRequest):
    try:
        return store.authenticate_user(payload.email, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.get("/api/auth/me")
async def me(actor: AuthContext = Depends(require_contributor)):
    return {"subject_id": actor.subject_id, "roles": sorted(actor.roles), "auth_mode": actor.auth_mode}


@app.post("/api/admin/users")
async def create_user(payload: UserCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            roles=payload.roles,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/users")
async def list_users(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_users(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/users/{user_id}/tokens")
async def create_user_token(user_id: str, payload: TokenCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_api_token(
            user_id=user_id,
            name=payload.name,
            roles=payload.roles,
            expires_at=payload.expires_at,
            actor_id=actor.subject_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/tokens/{token_id}/revoke")
async def revoke_token(token_id: str, actor: AuthContext = Depends(require_admin)):
    try:
        return store.revoke_api_token(token_id, actor_id=actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
            authorization_snapshot_id=payload.authorization_snapshot_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/assets")
async def upload_asset(
    file: UploadFile = File(...),
    owner_id: str = Form(default="demo_contributor", max_length=128),
    allowed_uses: str = Form(default='["private_library","candidate_pool"]'),
    authorization_snapshot_id: Optional[str] = Form(default=None, max_length=128),
    actor: AuthContext = Depends(require_contributor),
):
    content = await file.read(settings.max_asset_bytes + 1)
    try:
        result = store.submit_asset(
            owner_id=_owner_id(owner_id, actor),
            filename=file.filename or "asset.bin",
            media_type=file.content_type or "application/octet-stream",
            content=content,
            allowed_uses=_parse_allowed_uses(allowed_uses),
            actor_id=actor.subject_id,
            authorization_snapshot_id=authorization_snapshot_id,
        )
        return {**result, "asset": _public_asset(result["asset"])}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/assets")
async def list_assets(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    owner_id: Optional[str] = Query(default=None, max_length=128),
    status: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_contributor),
):
    scoped_owner = _scoped_owner_id(owner_id, actor)
    items = [_public_asset(item) for item in store.list_assets(owner_id=scoped_owner, status=status, limit=limit, offset=offset)]
    return _page(items, limit, offset)


@app.get("/api/assets/{asset_id}")
async def get_asset(asset_id: str, actor: AuthContext = Depends(require_contributor)):
    try:
        asset = store.get_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _assert_readable_owner(asset["owner_id"], actor)
    return _public_asset(asset)


@app.post("/api/assets/{asset_id}/extract")
async def request_asset_extraction(asset_id: str, actor: AuthContext = Depends(require_contributor)):
    try:
        asset = store.get_asset(asset_id)
        _assert_readable_owner(asset["owner_id"], actor)
        return store.request_asset_extraction(asset_id, actor_id=actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/authorizations")
async def create_authorization(payload: ConsentCreateRequest, actor: AuthContext = Depends(require_contributor)):
    try:
        return store.create_authorization_snapshot(
            owner_id=_owner_id(payload.owner_id, actor),
            allowed_uses=payload.allowed_uses,
            policy_version=payload.policy_version,
            terms_version=payload.terms_version,
            source=payload.source,
            consent_text=payload.consent_text,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/authorizations")
async def list_authorizations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    owner_id: Optional[str] = Query(default=None, max_length=128),
    status: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_contributor),
):
    scoped_owner = _scoped_owner_id(owner_id, actor)
    return _page(store.list_authorization_snapshots(owner_id=scoped_owner, status=status, limit=limit, offset=offset), limit, offset)


@app.post("/api/authorizations/{authorization_id}/withdraw")
async def withdraw_authorization(
    authorization_id: str,
    payload: ConsentWithdrawRequest,
    actor: AuthContext = Depends(require_contributor),
):
    try:
        authorization = store.get_authorization_snapshot(authorization_id)
        _assert_writable_owner(authorization["owner_id"], actor)
        return store.withdraw_authorization_snapshot(authorization_id, reason=payload.reason, actor_id=actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/cases")
async def list_cases(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=80),
    owner_id: Optional[str] = Query(default=None, max_length=128),
    actor: AuthContext = Depends(require_reviewer),
):
    items = store.list_cases(limit=limit, offset=offset, status=status, owner_id=owner_id)
    return _page(items, limit, offset)


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


@app.post("/api/review/{case_id}/expert-verify")
async def expert_verify_case(case_id: str, payload: AdvancedReviewRequest, actor: AuthContext = Depends(require_reviewer)):
    try:
        reviewer_id = actor.subject_id if auth_manager.enabled else payload.reviewer_id
        return store.expert_verify_case(
            case_id,
            reviewer_id=reviewer_id,
            notes=payload.notes,
            rubric=payload.rubric,
            evidence=payload.evidence,
            score=payload.score,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{case_id}/gold-review")
async def gold_review_case(case_id: str, payload: AdvancedReviewRequest, actor: AuthContext = Depends(require_reviewer)):
    try:
        reviewer_id = actor.subject_id if auth_manager.enabled else payload.reviewer_id
        return store.gold_review_case(
            case_id,
            reviewer_id=reviewer_id,
            notes=payload.notes,
            rubric=payload.rubric,
            evidence=payload.evidence,
            score=payload.score,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{case_id}/reject")
async def reject_case(case_id: str, payload: RejectRequest, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.reject_case(case_id, actor.subject_id, payload.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/reviews")
async def list_reviews(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    case_id: Optional[str] = Query(default=None, max_length=160),
    review_type: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_reviewer),
):
    return _page(store.list_reviews(case_id=case_id, review_type=review_type, limit=limit, offset=offset), limit, offset)


@app.get("/api/review/queue")
async def review_queue(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: AuthContext = Depends(require_reviewer),
):
    return _page(store.list_review_queue(limit=limit, offset=offset), limit, offset)


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
            max_cases=payload.max_cases,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.get_dataset(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/datasets/{dataset_id}/contract")
async def get_data_contract(dataset_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.get_data_contract(dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/ledger/usage-events")
async def list_usage_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_usage_events(limit=limit, offset=offset), limit, offset)


@app.get("/api/ledger/payout-events")
async def list_payout_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    contributor_id: Optional[str] = Query(default=None, max_length=128),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_payout_events(limit=limit, offset=offset, contributor_id=contributor_id, status=status), limit, offset)


@app.get("/api/ledger/contributors/{contributor_id}")
async def contributor_ledger(contributor_id: str, actor: AuthContext = Depends(require_admin)):
    return store.contributor_ledger(contributor_id)


@app.post("/api/ledger/payout-batches")
async def create_payout_batch(payload: PayoutBatchCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_payout_batch(
            contributor_id=payload.contributor_id,
            min_amount_cents=payload.min_amount_cents,
            max_events=payload.max_events,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/ledger/payout-batches")
async def list_payout_batches(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_payout_batches(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/ledger/payout-batches/{batch_id}/settle")
async def settle_payout_batch(batch_id: str, payload: PayoutBatchSettleRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.settle_payout_batch(
            batch_id,
            actor_id=actor.subject_id,
            external_reference=payload.external_reference,
            notes=payload.notes,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/ledger/payout-events/{payout_id}/settle")
async def settle_payout(payout_id: str, payload: PayoutSettleRequest, actor: AuthContext = Depends(require_admin)):
    try:
        settled = store.settle_payout_event(payout_id, actor_id=actor.subject_id)
        return {**settled, "notes": payload.notes}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/audit/logs")
async def list_audit_logs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    entity_id: Optional[str] = Query(default=None, max_length=160),
    event_type: Optional[str] = Query(default=None, max_length=120),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_audit_logs(limit=limit, offset=offset, entity_id=entity_id, event_type=event_type), limit, offset)


@app.get("/api/admin/jobs")
async def list_jobs(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_jobs(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/raw/purge-expired")
async def purge_expired_raw(limit: int = Query(default=100, ge=1, le=500), actor: AuthContext = Depends(require_admin)):
    return store.purge_expired_raw_objects(limit=limit, actor_id=actor.subject_id)


@app.get("/api/admin/metrics")
async def metrics(actor: AuthContext = Depends(require_admin)):
    return store.metrics_snapshot()


@app.get("/api/admin/observability")
async def observability(actor: AuthContext = Depends(require_admin)):
    return store.observability_snapshot()


@app.get("/api/admin/model-invocations")
async def list_model_invocations(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    entity_id: Optional[str] = Query(default=None, max_length=160),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_model_invocations(limit=limit, offset=offset, entity_id=entity_id, status=status), limit, offset)


@app.post("/api/admin/approvals")
async def create_approval(payload: ApprovalRequestPayload, actor: AuthContext = Depends(require_admin)):
    return store.create_approval_request(
        operation_type=payload.operation_type,
        entity_type=payload.entity_type,
        entity_id=payload.entity_id,
        reason=payload.reason,
        payload=payload.payload,
        actor_id=actor.subject_id,
    )


@app.get("/api/admin/approvals")
async def list_approvals(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_approval_requests(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/approvals/{approval_id}/decision")
async def decide_approval(approval_id: str, payload: ApprovalDecisionRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.decide_approval_request(approval_id, payload.decision, payload.notes, actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _owner_id(requested_owner_id: str, actor: AuthContext) -> str:
    if auth_manager.enabled and "admin" not in actor.roles:
        return actor.subject_id
    return requested_owner_id


def _scoped_owner_id(requested_owner_id: Optional[str], actor: AuthContext) -> Optional[str]:
    if auth_manager.enabled and not actor.roles.intersection({"admin", "reviewer"}):
        return actor.subject_id
    return requested_owner_id


def _assert_readable_owner(owner_id: str, actor: AuthContext) -> None:
    if auth_manager.enabled and not actor.roles.intersection({"admin", "reviewer"}) and owner_id != actor.subject_id:
        raise HTTPException(status_code=403, detail="insufficient_owner_scope")


def _assert_writable_owner(owner_id: str, actor: AuthContext) -> None:
    if auth_manager.enabled and "admin" not in actor.roles and owner_id != actor.subject_id:
        raise HTTPException(status_code=403, detail="insufficient_owner_scope")


def _parse_allowed_uses(value: str) -> List[str]:
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _public_asset(asset: dict) -> dict:
    return {key: value for key, value in asset.items() if key not in {"raw_path", "extracted_text_path"}}


def _exceeds_body_limit(content_length: str) -> bool:
    try:
        return int(content_length) > settings.max_request_body_bytes
    except ValueError:
        return False


def _should_read_body(request: Request, content_length: Optional[str]) -> bool:
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if _signature_required(request):
        return True
    if content_length is None:
        return True
    try:
        return int(content_length) < 0
    except ValueError:
        return True


async def _read_limited_body(request: Request) -> Tuple[bytes, bool]:
    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > settings.max_request_body_bytes:
            return b"", True
        chunks.append(chunk)
    return b"".join(chunks), False


def _replay_body(request: Request, body: bytes) -> None:
    async def receive_body():
        return {"type": "http.request", "body": body, "more_body": False}

    request._body = body
    request._receive = receive_body


def _apply_response_headers(response, request_id: str, rate_result=None) -> None:
    for key, value in SECURITY_HEADERS.items():
        response.headers[key] = value
    response.headers["X-Request-ID"] = request_id
    if rate_result is not None and settings.rate_limit_enabled:
        response.headers["X-RateLimit-Limit"] = str(rate_result.limit)
        response.headers["X-RateLimit-Remaining"] = str(rate_result.remaining)
        response.headers["X-RateLimit-Reset"] = str(rate_result.reset_at)


def _signature_required(request: Request) -> bool:
    if not settings.require_request_signature:
        return False
    if request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    return request.url.path not in SIGNATURE_BYPASS_PATHS


def _page(items, limit: int, offset: int):
    return {
        "items": items,
        "page": {
            "limit": limit,
            "offset": offset,
            "count": len(items),
        },
    }
