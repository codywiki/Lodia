from __future__ import annotations

from dataclasses import asdict
import json
import uuid
from typing import List, Optional, Tuple

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
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
DELIVERY_AUTH_FAILURES = {
    "delivery_grant_not_found",
    "delivery_grant_not_active",
    "delivery_grant_expired",
    "invalid_delivery_token",
}


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


class AssetUploadSessionRequest(BaseModel):
    owner_id: str = Field(default="demo_contributor", max_length=128)
    filename: str = Field(min_length=1, max_length=240)
    media_type: str = Field(default="application/octet-stream", max_length=160)
    byte_size: int = Field(gt=0, le=10_000_000_000)
    allowed_uses: List[str] = Field(default_factory=lambda: ["private_library", "candidate_pool"])
    authorization_snapshot_id: Optional[str] = Field(default=None, max_length=128)
    expires_in_seconds: Optional[int] = Field(default=None, ge=60, le=86_400)


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


class EnterpriseCustomerCreateRequest(BaseModel):
    tenant_id: str = Field(default="default", max_length=80)
    name: str = Field(min_length=1, max_length=160)
    contact_email: str = Field(min_length=3, max_length=255)


class DeliveryGrantCreateRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=160)
    order_id: Optional[str] = Field(default=None, max_length=160)
    purpose: str = Field(default="commercial_dataset", max_length=80)
    terms_version: str = Field(default="enterprise-delivery-2026-05", max_length=80)
    expires_at: Optional[str] = Field(default=None, max_length=80)
    max_reads: int = Field(default=100, ge=1, le=10_000)


class EnterpriseContractCreateRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=160)
    terms_version: str = Field(default="enterprise-contract-2026-05", max_length=80)
    terms: dict = Field(default_factory=dict)
    expires_at: Optional[str] = Field(default=None, max_length=80)


class EnterpriseOrderCreateRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=160)
    dataset_id: str = Field(min_length=1, max_length=160)
    contract_id: str = Field(min_length=1, max_length=160)
    gross_revenue_cents: int = Field(default=100_000, ge=0)
    direct_cost_cents: int = Field(default=20_000, ge=0)
    currency: str = Field(default="CNY", max_length=12)
    max_reads: int = Field(default=100, ge=1, le=10_000)


class TenantQuotaRequest(BaseModel):
    monthly_order_limit: int = Field(default=0, ge=0)
    monthly_delivery_read_limit: int = Field(default=0, ge=0)
    monthly_submission_limit: int = Field(default=0, ge=0)
    monthly_asset_bytes_limit: int = Field(default=0, ge=0)


class DisputeCreateRequest(BaseModel):
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=2_000)
    hold_payouts: bool = True


class DisputeResolveRequest(BaseModel):
    decision: str = Field(pattern="^(release|void)$")
    resolution: str = Field(default="", max_length=2_000)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)


class UserCreateRequest(BaseModel):
    tenant_id: str = Field(default="default", max_length=80)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(default="", max_length=120)
    roles: List[str] = Field(default_factory=lambda: ["contributor"])


class TenantCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=160)


class TokenCreateRequest(BaseModel):
    name: str = Field(default="api-token", min_length=1, max_length=120)
    roles: Optional[List[str]] = None
    expires_at: Optional[str] = Field(default=None, max_length=80)


class RejectRequest(BaseModel):
    reason: str = Field(default="", max_length=2_000)


class ReviewClaimRequest(BaseModel):
    case_id: Optional[str] = Field(default=None, max_length=160)


class PayoutSettleRequest(BaseModel):
    notes: str = Field(default="", max_length=1_000)


class ContributorPayoutProfileRequest(BaseModel):
    country_region: str = Field(default="CN", max_length=40)
    account_type: str = Field(default="bank", max_length=40)
    account_reference: str = Field(min_length=4, max_length=255)


class AdminPayoutProfileRequest(ContributorPayoutProfileRequest):
    kyc_status: str = Field(default="pending", max_length=40)
    tax_status: str = Field(default="pending", max_length=40)
    risk_status: str = Field(default="clear", max_length=40)
    withholding_rate_bps: int = Field(default=0, ge=0, le=10_000)


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
        "X-Lodia-Delivery-Token",
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
    return {"subject_id": actor.subject_id, "tenant_id": actor.tenant_id, "roles": sorted(actor.roles), "auth_mode": actor.auth_mode}


@app.get("/api/contributor/dashboard")
async def contributor_dashboard(actor: AuthContext = Depends(require_contributor)):
    return store.contributor_dashboard(actor.subject_id)


@app.get("/api/contributor/cases")
async def contributor_cases(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_contributor),
):
    return _page(store.list_cases(limit=limit, offset=offset, status=status, owner_id=actor.subject_id), limit, offset)


@app.get("/api/contributor/ledger")
async def contributor_self_ledger(actor: AuthContext = Depends(require_contributor)):
    return store.contributor_ledger(actor.subject_id)


@app.get("/api/contributor/payout-profile")
async def contributor_payout_profile(actor: AuthContext = Depends(require_contributor)):
    try:
        return store.get_payout_profile(actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/contributor/payout-profile")
async def upsert_contributor_payout_profile(payload: ContributorPayoutProfileRequest, actor: AuthContext = Depends(require_contributor)):
    try:
        return store.upsert_payout_profile(
            contributor_id=actor.subject_id,
            country_region=payload.country_region,
            account_type=payload.account_type,
            account_reference=payload.account_reference,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/users")
async def create_user(payload: UserCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
            roles=payload.roles,
            tenant_id=payload.tenant_id,
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


@app.post("/api/admin/tenants")
async def create_tenant(payload: TenantCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_tenant(payload.id, payload.name, actor_id=actor.subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/tenants")
async def list_tenants(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_tenants(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/tenant-quotas/{tenant_id}")
async def upsert_tenant_quota(tenant_id: str, payload: TenantQuotaRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.upsert_tenant_quota(
            tenant_id=tenant_id,
            monthly_order_limit=payload.monthly_order_limit,
            monthly_delivery_read_limit=payload.monthly_delivery_read_limit,
            monthly_submission_limit=payload.monthly_submission_limit,
            monthly_asset_bytes_limit=payload.monthly_asset_bytes_limit,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/tenant-quotas")
async def list_tenant_quotas(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_tenant_quotas(limit=limit, offset=offset), limit, offset)


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


@app.post("/api/assets/upload-sessions")
async def create_asset_upload_session(payload: AssetUploadSessionRequest, actor: AuthContext = Depends(require_contributor)):
    try:
        result = store.create_asset_upload_session(
            owner_id=_owner_id(payload.owner_id, actor),
            filename=payload.filename,
            media_type=payload.media_type,
            byte_size=payload.byte_size,
            allowed_uses=payload.allowed_uses,
            actor_id=actor.subject_id,
            authorization_snapshot_id=payload.authorization_snapshot_id,
            expires_in_seconds=payload.expires_in_seconds,
        )
        return {
            "session": _public_upload_session(result["session"]),
            "upload": _public_upload_instruction(result["upload"]),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/assets/upload-sessions/{session_id}/complete")
async def complete_asset_upload_session(session_id: str, actor: AuthContext = Depends(require_contributor)):
    try:
        session = store.get_asset_upload_session(session_id)
        _assert_readable_owner(session["owner_id"], actor)
        result = store.complete_asset_upload_session(session_id, actor_id=actor.subject_id)
        return {**result, "asset": _public_asset(result["asset"])}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.post("/api/review/claim")
async def claim_review(payload: ReviewClaimRequest, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.claim_review_case(actor.subject_id, case_id=payload.case_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{case_id}/release")
async def release_review(case_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return store.release_review_case(case_id, actor.subject_id, force="admin" in actor.roles)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/datasets")
async def create_dataset(payload: DatasetRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return _public_dataset(store.create_dataset(
            name=payload.name,
            purpose=payload.purpose,
            min_drl=payload.min_drl,
            gross_revenue_cents=payload.gross_revenue_cents,
            direct_cost_cents=payload.direct_cost_cents,
            actor_id=actor.subject_id,
            max_cases=payload.max_cases,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/enterprise/customers")
async def create_enterprise_customer(payload: EnterpriseCustomerCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_enterprise_customer(
            name=payload.name,
            contact_email=payload.contact_email,
            tenant_id=payload.tenant_id,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/enterprise/customers")
async def list_enterprise_customers(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_enterprise_customers(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/enterprise/contracts")
async def create_enterprise_contract(payload: EnterpriseContractCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_enterprise_contract(
            customer_id=payload.customer_id,
            terms_version=payload.terms_version,
            terms=payload.terms,
            expires_at=payload.expires_at,
            actor_id=actor.subject_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/enterprise/contracts")
async def list_enterprise_contracts(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    customer_id: Optional[str] = Query(default=None, max_length=160),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_enterprise_contracts(limit=limit, offset=offset, customer_id=customer_id, status=status), limit, offset)


@app.post("/api/admin/enterprise/orders")
async def create_enterprise_order(payload: EnterpriseOrderCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_enterprise_order(
            customer_id=payload.customer_id,
            dataset_id=payload.dataset_id,
            contract_id=payload.contract_id,
            gross_revenue_cents=payload.gross_revenue_cents,
            direct_cost_cents=payload.direct_cost_cents,
            currency=payload.currency,
            max_reads=payload.max_reads,
            actor_id=actor.subject_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/enterprise/orders")
async def list_enterprise_orders(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    customer_id: Optional[str] = Query(default=None, max_length=160),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_enterprise_orders(limit=limit, offset=offset, customer_id=customer_id, status=status), limit, offset)


@app.post("/api/admin/enterprise/orders/{order_id}/recognize-usage")
async def recognize_enterprise_order_usage(order_id: str, actor: AuthContext = Depends(require_admin)):
    try:
        return store.recognize_enterprise_order_usage(order_id, actor_id=actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/admin/datasets/{dataset_id}/delivery-grants")
async def create_delivery_grant(dataset_id: str, payload: DeliveryGrantCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_dataset_delivery_grant(
            dataset_id=dataset_id,
            customer_id=payload.customer_id,
            purpose=payload.purpose,
            terms_version=payload.terms_version,
            expires_at=payload.expires_at,
            max_reads=payload.max_reads,
            order_id=payload.order_id,
            actor_id=actor.subject_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/delivery-grants")
async def list_delivery_grants(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    dataset_id: Optional[str] = Query(default=None, max_length=160),
    customer_id: Optional[str] = Query(default=None, max_length=160),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(
        store.list_dataset_delivery_grants(limit=limit, offset=offset, dataset_id=dataset_id, customer_id=customer_id, status=status),
        limit,
        offset,
    )


@app.post("/api/admin/delivery-grants/{grant_id}/revoke")
async def revoke_delivery_grant(grant_id: str, actor: AuthContext = Depends(require_admin)):
    try:
        return store.revoke_dataset_delivery_grant(grant_id, actor_id=actor.subject_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/datasets")
async def list_datasets(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    purpose: Optional[str] = Query(default=None, max_length=80),
    status: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_reviewer),
):
    return _page([_public_dataset(item) for item in store.list_datasets(limit=limit, offset=offset, purpose=purpose, status=status)], limit, offset)


@app.get("/api/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, actor: AuthContext = Depends(require_reviewer)):
    try:
        return _public_dataset(store.get_dataset(dataset_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/datasets/{dataset_id}/artifacts/{artifact}")
async def get_dataset_artifact(dataset_id: str, artifact: str, actor: AuthContext = Depends(require_admin)):
    try:
        payload = store.read_dataset_artifact(dataset_id, artifact, actor_id=actor.subject_id)
        return PlainTextResponse(
            payload["content"],
            media_type=payload["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{payload["filename"]}"'},
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/delivery-grants/{grant_id}/artifacts/{artifact}")
async def get_delivery_grant_artifact(
    grant_id: str,
    artifact: str,
    delivery_token: Optional[str] = Header(default=None, alias="X-Lodia-Delivery-Token"),
):
    if not delivery_token:
        raise HTTPException(status_code=401, detail="missing_delivery_token")
    try:
        payload = store.read_delivery_grant_artifact(grant_id, delivery_token, artifact)
        return PlainTextResponse(
            payload["content"],
            media_type=payload["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{payload["filename"]}"'},
        )
    except KeyError as exc:
        raise HTTPException(status_code=401, detail="invalid_delivery_token") from exc
    except ValueError as exc:
        reason = str(exc)
        if reason in DELIVERY_AUTH_FAILURES:
            raise HTTPException(status_code=401, detail="invalid_delivery_token") from exc
        if reason == "delivery_grant_read_limit_exceeded":
            raise HTTPException(status_code=429, detail=reason) from exc
        raise HTTPException(status_code=400, detail=reason) from exc


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


@app.get("/api/admin/payout-profiles")
async def list_payout_profiles(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_payout_profiles(limit=limit, offset=offset, status=status), limit, offset)


@app.post("/api/admin/payout-profiles/{contributor_id}")
async def upsert_admin_payout_profile(contributor_id: str, payload: AdminPayoutProfileRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.upsert_payout_profile(
            contributor_id=contributor_id,
            country_region=payload.country_region,
            account_type=payload.account_type,
            account_reference=payload.account_reference,
            kyc_status=payload.kyc_status,
            tax_status=payload.tax_status,
            risk_status=payload.risk_status,
            withholding_rate_bps=payload.withholding_rate_bps,
            actor_id=actor.subject_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.get("/api/admin/vendor-processing")
async def list_vendor_processing(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    entity_id: Optional[str] = Query(default=None, max_length=160),
    provider: Optional[str] = Query(default=None, max_length=80),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_vendor_processing_records(limit=limit, offset=offset, entity_id=entity_id, provider=provider), limit, offset)


@app.get("/api/admin/metrics/prometheus")
async def prometheus_metrics(actor: AuthContext = Depends(require_admin)):
    return PlainTextResponse(store.prometheus_metrics(), media_type="text/plain; version=0.0.4")


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


@app.post("/api/admin/disputes")
async def create_dispute(payload: DisputeCreateRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.create_dispute(
            entity_type=payload.entity_type,
            entity_id=payload.entity_id,
            reason=payload.reason,
            hold_payouts=payload.hold_payouts,
            actor_id=actor.subject_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/admin/disputes")
async def list_disputes(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None, max_length=40),
    entity_id: Optional[str] = Query(default=None, max_length=160),
    actor: AuthContext = Depends(require_admin),
):
    return _page(store.list_disputes(limit=limit, offset=offset, status=status, entity_id=entity_id), limit, offset)


@app.post("/api/admin/disputes/{dispute_id}/resolve")
async def resolve_dispute(dispute_id: str, payload: DisputeResolveRequest, actor: AuthContext = Depends(require_admin)):
    try:
        return store.resolve_dispute(dispute_id, payload.decision, payload.resolution, actor_id=actor.subject_id)
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


def _public_upload_session(session: dict) -> dict:
    return {key: value for key, value in session.items() if key not in {"object_key", "object_uri"}}


def _public_upload_instruction(upload: dict) -> dict:
    return {key: value for key, value in upload.items() if key not in {"object_key", "object_uri"}}


def _public_dataset(dataset: dict) -> dict:
    hidden = {"manifest_path", "quality_report_path", "data_path", "data_contract_path"}
    return {key: value for key, value in dataset.items() if key not in hidden}


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
