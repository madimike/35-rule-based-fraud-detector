
"""FastAPI application for the rule-based fraud detector.

Run locally with: uvicorn app.main:app --reload
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from app.body_limit import RequestBodyLimitMiddleware
from collections.abc import Awaitable, Callable
from app.security import InMemoryRateLimiter
from dotenv import load_dotenv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from app.models.transaction import Transaction
from app.persistence.database import Database
from app.persistence.repository import (
    DuplicateTransactionError,
    PersistenceRepository,
)
from app.rules.config import RuleConfig
from app.rules.engine import RuleEngine


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CaseStatus = Literal["open", "in_review", "closed"]
RiskBandName = Literal["low", "medium", "high"]


class TransactionIngestResponse(BaseModel):
    transaction: Transaction
    created: bool = Field(
        description="False when this is an identical retry of an existing transaction"
    )


class CaseCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(min_length=1, max_length=128)
    assessment_id: int | None = Field(default=None, ge=1)
    notes: str = Field(default="", max_length=5000)


class CaseUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CaseStatus | None = None
    notes: str | None = Field(default=None, max_length=5000)


class CaseResponse(BaseModel):
    case_id: int
    transaction_id: str
    assessment_id: int | None
    status: CaseStatus
    notes: str
    created_at: datetime
    updated_at: datetime


class AssessmentResponse(BaseModel):
    assessment_id: int
    transaction_id: str
    risk_score: int
    risk_band: RiskBandName
    created_at: datetime
    rule_results: list[dict[str, Any]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()



async def _authenticate_request(request: Request, call_next):
    """Require an API key for protected API endpoints."""
    path = request.url.path

    # Allow the health check and frontend pages/assets to load.
    if (
        path == "/health"
        or path == "/"
        or path == "/favicon.ico"
        or path == "/assets"
        or path.startswith("/assets/")
    ):
        return await call_next(request)

    # The frontend is served only when a production build exists.
    frontend_dist = (
        Path(__file__).resolve().parent.parent / "frontend" / "dist"
    )
    if (
        frontend_dist.is_dir()
        and not path.startswith("/api/")
        and path not in {
            "/transactions",
            "/assessments",
            "/cases",
        }
        and not path.startswith(("/transactions/", "/assessments/", "/cases/"))
        and request.method in {"GET", "HEAD"}
    ):
        requested_file = (frontend_dist / path.lstrip("/")).resolve()
        if (
            requested_file.is_relative_to(frontend_dist.resolve())
            and requested_file.is_file()
        ):
            return await call_next(request)

    # Public demo mode is opt-in and should only be enabled on deployment.
    demo_mode = (
        os.getenv("FRAUD_DEMO_MODE", "").strip().lower() == "true"
    )

    if not demo_mode:
        expected_key = os.getenv("FRAUD_API_KEY")

        if not expected_key:
            return Response(
                content='{"detail":"API authentication is not configured"}',
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                media_type="application/json",
            )

        provided_key = request.headers.get("X-API-Key")

        if not provided_key or not secrets.compare_digest(
            provided_key, expected_key
        ):
            return Response(
                content='{"detail":"Invalid or missing API key"}',
                status_code=status.HTTP_401_UNAUTHORIZED,
                media_type="application/json",
                headers={"WWW-Authenticate": "ApiKey"},
            )

    # Apply abuse protections to API routes in public demo mode.
    if demo_mode and path.startswith(
        ("/transactions", "/assessments", "/cases")
    ):
        client_id = (
            request.client.host if request.client else "unknown"
        )

        is_write = request.method in {"POST", "PATCH", "PUT", "DELETE"}
        category = "write" if is_write else "read"
        limit = 20 if is_write else 120

        limiter: InMemoryRateLimiter = request.app.state.rate_limiter

        if not limiter.allow(client_id, category, limit):
            return Response(
                content='{"detail":"Rate limit exceeded. Please try again shortly."}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )

    return await call_next(request)



def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Create an app instance; tests can provide a temporary SQLite file."""
    db_path = database_path or os.getenv(
        "FRAUD_DATABASE_PATH",
        "data/fraud_detector.sqlite3",
    )
    database = Database(db_path)
    repository = PersistenceRepository(database)
    repository.save_rules(RuleConfig())
    engine = RuleEngine()

    api = FastAPI(
        title="Rule-Based Fraud Detector API",
        version="0.1.0",
        description=(
            "An explainable, rule-based transaction risk monitoring API. Risk scores are "
            "rule-weight totals, not calibrated probabilities of fraud."
        ),
    )
    api.middleware("http")(_authenticate_request)

    api.state.rate_limiter = InMemoryRateLimiter()
    api.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=65_536,
    )

    api.state.database = database
    api.state.repository = repository
    api.state.rule_engine = engine

    @api.exception_handler(DuplicateTransactionError)
    async def duplicate_transaction_handler(
        _request, exc: DuplicateTransactionError
    ):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "detail": str(exc),
                "code": "duplicate_transaction_conflict",
            },
        )

    @api.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.post(
        "/transactions",
        response_model=TransactionIngestResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["transactions"],
    )
    def ingest_transaction(
        payload: Transaction, response: Response
    ) -> TransactionIngestResponse:
        try:
            created = repository.save_transaction(payload)
        except DuplicateTransactionError:
            raise

        if not created:
            response.status_code = status.HTTP_200_OK

        return TransactionIngestResponse(
            transaction=payload,
            created=created,
        )

    @api.get(
        "/transactions",
        response_model=list[Transaction],
        tags=["transactions"],
    )
    def list_transactions(
        customer_id: str | None = Query(
            default=None, min_length=1, max_length=128
        ),
        currency: str | None = Query(
            default=None,
            min_length=3,
            max_length=3,
            pattern="^[A-Z]{3}$",
        ),
        channel: Literal["card", "online", "transfer"] | None = None,
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[Transaction]:
        transactions = repository.list_transactions(
            customer_id=customer_id,
            limit=limit,
        )

        if currency is not None:
            transactions = [
                item for item in transactions
                if item.currency == currency
            ]

        if channel is not None:
            transactions = [
                item for item in transactions
                if item.channel == channel
            ]

        return transactions

    @api.get(
        "/transactions/{transaction_id}",
        response_model=Transaction,
        tags=["transactions"],
    )
    def get_transaction(transaction_id: str) -> Transaction:
        transaction = repository.get_transaction(transaction_id)

        if transaction is None:
            raise HTTPException(
                status_code=404,
                detail="Transaction not found",
            )

        return transaction

    @api.post(
        "/transactions/{transaction_id}/evaluate",
        tags=["assessments"],
    )
    def evaluate_transaction(transaction_id: str) -> dict[str, Any]:
        transaction = repository.get_transaction(transaction_id)

        if transaction is None:
            raise HTTPException(
                status_code=404,
                detail="Transaction not found",
            )

        history = repository.get_customer_history(
            customer_id=transaction.customer_id,
            before=transaction.timestamp,
        )

        assessment = api.state.rule_engine.evaluate(
            transaction,
            history,
        )

        try:
            assessment_id = repository.save_assessment(assessment)
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail=str(exc),
            ) from exc

        stored = repository.get_assessment(transaction_id)

        if stored is None:
            raise HTTPException(
                status_code=500,
                detail="Assessment was saved but could not be retrieved",
            )

        stored["assessment_id"] = assessment_id
        stored["rule_results"] = [
            {**item, "score": int(item["score"])}
            for item in stored["rule_results"]
        ]

        return stored

    @api.get("/assessments", tags=["assessments"])
    def list_assessments(
        transaction_id: str | None = Query(
            default=None, min_length=1, max_length=128
        ),
        risk_band: RiskBandName | None = None,
        min_score: int | None = Query(default=None, ge=0, le=100),
        max_score: int | None = Query(default=None, ge=0, le=100),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        if (
            min_score is not None
            and max_score is not None
            and min_score > max_score
        ):
            raise HTTPException(
                status_code=422,
                detail="min_score must be less than or equal to max_score",
            )

        clauses: list[str] = []
        parameters: list[Any] = []

        if transaction_id is not None:
            clauses.append("transaction_id = ?")
            parameters.append(transaction_id)

        if risk_band is not None:
            clauses.append("risk_band = ?")
            parameters.append(risk_band)

        if min_score is not None:
            clauses.append("risk_score >= ?")
            parameters.append(min_score)

        if max_score is not None:
            clauses.append("risk_score <= ?")
            parameters.append(max_score)

        where = " WHERE " + " AND ".join(clauses) if clauses else ""

        with database.connect() as connection:
            rows = connection.execute(
                f"SELECT assessment_id, transaction_id, risk_score, risk_band, created_at "
                f"FROM assessments{where} "
                f"ORDER BY created_at DESC, assessment_id DESC LIMIT ?",
                (*parameters, limit),
            ).fetchall()

        output: list[dict[str, Any]] = []

        for row in rows:
            item = dict(row)
            details = repository.get_assessment(item["transaction_id"])
            item["rule_results"] = (
                details["rule_results"] if details else []
            )
            output.append(item)

        return output

    @api.get("/assessments/{transaction_id}", tags=["assessments"])
    def get_assessment(transaction_id: str) -> dict[str, Any]:
        assessment = repository.get_assessment(transaction_id)

        if assessment is None:
            raise HTTPException(
                status_code=404,
                detail="Assessment not found",
            )

        return assessment

    @api.get("/cases", tags=["cases"])
    def list_cases(
        case_status: CaseStatus | None = Query(
            default=None,
            alias="status",
        ),
        transaction_id: str | None = Query(
            default=None,
            min_length=1,
            max_length=128,
        ),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        cases = repository.list_cases(
            status=case_status,
            limit=1000 if transaction_id else limit,
        )

        if transaction_id is not None:
            cases = [
                item for item in cases
                if item["transaction_id"] == transaction_id
            ]

        return cases[:limit]

    @api.post(
        "/cases",
        response_model=CaseResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["cases"],
    )
    def create_case(payload: CaseCreateRequest) -> dict[str, Any]:
        if repository.get_transaction(payload.transaction_id) is None:
            raise HTTPException(
                status_code=404,
                detail="Transaction not found",
            )

        if payload.assessment_id is not None:
            assessment = repository.get_assessment(
                payload.transaction_id
            )

            if (
                assessment is None
                or int(assessment["assessment_id"]) != payload.assessment_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="assessment_id must belong to this transaction",
                )

        try:
            case_id = repository.create_case(
                transaction_id=payload.transaction_id,
                assessment_id=payload.assessment_id,
                notes=payload.notes,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=422,
                detail="Case references an invalid transaction or assessment",
            ) from exc

        case = repository.get_case(case_id)

        if case is None:
            raise HTTPException(
                status_code=500,
                detail="Case was created but could not be retrieved",
            )

        return case

    @api.patch(
        "/cases/{case_id}",
        response_model=CaseResponse,
        tags=["cases"],
    )
    def update_case(
        case_id: int,
        payload: CaseUpdateRequest,
    ) -> dict[str, Any]:
        existing = repository.get_case(case_id)

        if existing is None:
            raise HTTPException(
                status_code=404,
                detail="Case not found",
            )

        changes = payload.model_dump(exclude_unset=True)

        if not changes:
            raise HTTPException(
                status_code=422,
                detail="Provide at least one field to update",
            )

        if all(value is None for value in changes.values()):
            raise HTTPException(
                status_code=422,
                detail="At least one non-null update value is required",
            )

        new_status = changes.get("status", existing["status"])
        new_notes = changes.get("notes", existing["notes"])

        with database.connect() as connection:
            connection.execute(
                "UPDATE cases SET status = ?, notes = ?, updated_at = ? "
                "WHERE case_id = ?",
                (new_status, new_notes, _utc_now(), case_id),
            )

        updated = repository.get_case(case_id)

        if updated is None:
            raise HTTPException(
                status_code=500,
                detail="Case was updated but could not be retrieved",
            )

        return updated

    # Serve the built React frontend when production assets are available.
    frontend_dist = (
        Path(__file__).resolve().parent.parent / "frontend" / "dist"
    )
    frontend_index = frontend_dist / "index.html"
    frontend_assets = frontend_dist / "assets"

    if frontend_index.is_file():
        if frontend_assets.is_dir():
            api.mount(
                "/assets",
                StaticFiles(directory=frontend_assets),
                name="frontend-assets",
            )

        @api.get("/", include_in_schema=False)
        def serve_frontend_root() -> FileResponse:
            return FileResponse(frontend_index)

        @api.get("/{full_path:path}", include_in_schema=False)
        def serve_frontend_page(full_path: str) -> FileResponse:
            # Preserve proper 404 responses for unknown API routes.
            if full_path.startswith("api/"):
                raise HTTPException(
                    status_code=404,
                    detail="Not found",
                )

            requested_file = (frontend_dist / full_path).resolve()

            # Prevent path traversal and serve files only inside frontend/dist.
            if (
                requested_file.is_relative_to(frontend_dist.resolve())
                and requested_file.is_file()
            ):
                return FileResponse(requested_file)

            # Support React client-side routes.
            return FileResponse(frontend_index)

    return api


app = create_app()