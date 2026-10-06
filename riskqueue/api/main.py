from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from riskqueue.api.cases import actor, configured_analysts, database
from riskqueue.api.cases import router as cases_router
from riskqueue.api.schemas import BatchRequest, QueueRequest, ScoreResponse, Transaction
from riskqueue.db.session import build_session_factory
from riskqueue.replay import ReplayRequest, replay
from riskqueue.scoring import risk_band, runtime_scorer
from riskqueue.scoring_pipeline import score_transactions

app = FastAPI(
    title="RiskQueue API",
    version="0.1.0",
    description="Auditable demo scoring and cost-aware analyst queue prioritization.",
)
app.include_router(cases_router)


def preview_auth_required() -> bool:
    setting = os.getenv("API_AUTH_REQUIRED", "false").lower()
    if setting not in {"true", "false"}:
        raise ValueError("API authentication is misconfigured")
    return setting == "true"


def preview_access(authorization: Annotated[str | None, Header()] = None) -> None:
    """Keep local previews open while allowing hosted deployments to protect compute routes."""
    try:
        required = preview_auth_required()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if required:
        actor(authorization)


@app.get("/v1/me")
def current_analyst(analyst: Annotated[str, Depends(actor)]) -> dict[str, str]:
    return {"analyst": analyst}


@app.get("/operator", include_in_schema=False)
def operator_workspace() -> FileResponse:
    return FileResponse(Path(__file__).with_name("operator.html"), media_type="text/html")


def _score_batch(
    transactions: list[Transaction], loss_fraction: float = 1.0, manual_review_cost: float = 4.0
) -> list[ScoreResponse]:
    frame = pd.DataFrame([tx.model_dump() for tx in transactions])
    try:
        if os.getenv("DATABASE_URL"):
            with build_session_factory()() as session:
                _, result = score_transactions(frame, scorer=runtime_scorer(), session=session)
        else:
            _, result = score_transactions(frame, scorer=runtime_scorer())
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return [
        ScoreResponse(
            transaction_id=tx.transaction_id,
            fraud_probability=round(float(result.probabilities[index]), 6),
            expected_loss=round(float(result.probabilities[index]) * tx.amount * loss_fraction, 2),
            risk_band=risk_band(float(result.probabilities[index])),
            decision=(
                "review"
                if float(result.probabilities[index]) * tx.amount * loss_fraction
                > manual_review_cost
                else "approve"
            ),
            model_version=result.model_version,
            feature_version=result.feature_version,
            scored_at=result.scored_at.isoformat(),
            score_kind=result.score_kind,
            policy_version="api-preview-v1",
        )
        for index, tx in enumerate(transactions)
    ]


@app.get("/health")
def health() -> dict[str, str]:
    try:
        model = runtime_scorer()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"status": "ok", "model": model.model_version}


@app.get("/ready", include_in_schema=False)
def ready() -> dict[str, str]:
    """Require a usable scorer, analyst authentication, and database before routing traffic."""
    try:
        model = runtime_scorer()
        preview_auth_required()
        configured_analysts()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, "Operator configuration is unavailable") from exc
    if not os.getenv("DATABASE_URL"):
        raise HTTPException(503, "Operational database is unavailable")
    try:
        with build_session_factory()() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise HTTPException(503, "Operational database is unavailable") from exc
    return {"status": "ready", "model": model.model_version}


@app.post("/v1/score", response_model=ScoreResponse)
def score(
    transaction: Transaction, _access: Annotated[None, Depends(preview_access)]
) -> ScoreResponse:
    return _score_batch([transaction])[0]


@app.post("/v1/score/batch", response_model=list[ScoreResponse])
def score_batch(
    request: BatchRequest, _access: Annotated[None, Depends(preview_access)]
) -> list[ScoreResponse]:
    return _score_batch(request.transactions)


@app.post("/v1/review-queue")
def review_queue(
    request: QueueRequest, _access: Annotated[None, Depends(preview_access)]
) -> list[dict]:
    scored = [
        row.model_dump() | {"amount": tx.amount, "type": tx.type}
        for row, tx in zip(
            _score_batch(request.transactions, request.loss_fraction, request.manual_review_cost),
            request.transactions,
            strict=True,
        )
    ]
    key = {
        "probability": "fraud_probability",
        "expected_loss": "expected_loss",
        "expected_review_value": "expected_loss",
    }[request.strategy]
    selected = sorted(scored, key=lambda row: (row[key], row["amount"]), reverse=True)[
        : request.capacity
    ]
    for rank, row in enumerate(selected, 1):
        row["rank"] = rank
        if request.strategy == "expected_review_value":
            row["priority_score"] = round(row["expected_loss"] - request.manual_review_cost, 2)
        else:
            row["priority_score"] = row[key]
    return selected


@app.get("/v1/model/metrics")
def model_metrics(_access: Annotated[None, Depends(preview_access)]) -> dict:
    try:
        model = runtime_scorer()
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "model_version": model.model_version,
        "feature_version": model.feature_version,
        "status": "development_heuristic" if model.model is None else "configured_model",
        "notice": "Measured metrics must come from the evaluation artifact for this exact model version.",
    }


@app.post("/v1/replay")
def replay_policies(
    request: ReplayRequest,
    _analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    try:
        return replay(session, request)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
