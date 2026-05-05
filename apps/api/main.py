from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from lodia.pipeline import process_text_case
from lodia.security import SECURITY_HEADERS


class PreviewRequest(BaseModel):
    owner_id: str = Field(default="demo_contributor", max_length=128)
    text: str = Field(min_length=1, max_length=200_000)
    allowed_uses: List[str] = Field(default_factory=lambda: ["private_library", "candidate_pool"])


app = FastAPI(
    title="Lodia API",
    version="0.1.0",
    description="Commercial-grade AI Case-to-Dataset platform API foundation.",
)

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
