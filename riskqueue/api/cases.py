"""Authenticated, persistent analyst investigation routes."""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from riskqueue.db.models import Case, CaseAudit, CaseNote, CaseOutcome, OperationalTransaction
from riskqueue.db.session import build_session_factory

router = APIRouter(prefix="/v1/cases", tags=["cases"])


def configured_analysts() -> dict[str, str]:
    try:
        credentials = json.loads(os.getenv("ANALYST_TOKENS", "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError("Analyst authentication is misconfigured") from exc
    if (
        not isinstance(credentials, dict)
        or not credentials
        or not all(
            isinstance(token, str) and token and isinstance(analyst, str) and analyst
            for token, analyst in credentials.items()
        )
    ):
        raise ValueError("Analyst authentication is not configured")
    if os.getenv("API_AUTH_REQUIRED", "false").lower() == "true" and any(
        len(token) < 32 for token in credentials
    ):
        raise ValueError("Hosted analyst tokens must contain at least 32 characters")
    return credentials


def actor(authorization: Annotated[str | None, Header()] = None) -> str:
    try:
        credentials = configured_analysts()
    except ValueError as exc:
        raise HTTPException(503, str(exc)) from exc
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    supplied = authorization[7:]
    for token, analyst in credentials.items():
        if (
            isinstance(token, str)
            and isinstance(analyst, str)
            and secrets.compare_digest(supplied, token)
        ):
            return analyst
    raise HTTPException(401, "Invalid bearer token")


def database():
    if not os.getenv("DATABASE_URL"):
        raise HTTPException(503, "DATABASE_URL is not configured")
    with build_session_factory()() as session:
        yield session


def find_case(session: Session, case_id: int, *, lock: bool = False) -> Case:
    query = select(Case).where(Case.case_id == case_id)
    if lock:
        query = query.with_for_update()
    case = session.scalar(query)
    if case is None:
        raise HTTPException(404, "Case not found")
    return case


def require_assignee(case: Case, analyst: str) -> None:
    if case.assigned_to != analyst:
        raise HTTPException(403, "Case is assigned to another analyst or unclaimed")


def audit(
    session: Session, case: Case, analyst: str, action: str, before: dict | None, after: dict
):
    transaction = session.get(OperationalTransaction, case.transaction_id)
    session.add(
        CaseAudit(
            case_id=case.case_id,
            actor=analyst,
            action=action,
            happened_at=datetime.now(UTC),
            previous_value=before,
            new_value=after,
            model_version=transaction.model_version,
            feature_version=transaction.feature_version,
            policy_version=transaction.policy_version,
        )
    )
    case.updated_at = datetime.now(UTC)


def case_summary(case: Case, transaction: OperationalTransaction) -> dict:
    return {
        "case_id": case.case_id,
        "transaction_id": case.transaction_id,
        "status": case.status,
        "assigned_to": case.assigned_to,
        "disposition": case.disposition,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
        "amount": transaction.amount,
        "model_output": transaction.fraud_probability,
        "score_kind": transaction.score_kind,
        "expected_loss": transaction.expected_loss,
        "model_version": transaction.model_version,
        "feature_version": transaction.feature_version,
        "policy_version": transaction.policy_version,
        "review_cost": transaction.review_cost,
        "loss_fraction": transaction.loss_fraction,
        "scored_at": transaction.scored_at,
    }


@router.get("")
def list_cases(
    _analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
    status: Literal["queued", "investigating", "escalated", "resolved"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    query = select(Case, OperationalTransaction).join(
        OperationalTransaction, Case.transaction_id == OperationalTransaction.transaction_id
    )
    if status:
        query = query.where(Case.status == status)
    query = query.order_by(Case.created_at.desc(), Case.case_id.desc()).limit(limit)
    return [case_summary(case, tx) for case, tx in session.execute(query)]


@router.get("/{case_id}")
def get_case(
    case_id: int,
    _analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    case = find_case(session, case_id)
    transaction = session.get(OperationalTransaction, case.transaction_id)
    result = case_summary(case, transaction)
    result["features"] = transaction.feature_snapshot
    result["evidence_note"] = "Feature values are model inputs, not causal attributions."
    result["notes"] = [
        {
            "note_id": note.note_id,
            "actor": note.actor,
            "created_at": note.created_at,
            "text": note.text,
        }
        for note in session.scalars(
            select(CaseNote).where(CaseNote.case_id == case_id).order_by(CaseNote.note_id)
        )
    ]
    result["audit"] = [
        {
            "actor": entry.actor,
            "action": entry.action,
            "happened_at": entry.happened_at,
            "previous_value": entry.previous_value,
            "new_value": entry.new_value,
            "model_version": entry.model_version,
            "feature_version": entry.feature_version,
            "policy_version": entry.policy_version,
        }
        for entry in session.scalars(
            select(CaseAudit).where(CaseAudit.case_id == case_id).order_by(CaseAudit.audit_id)
        )
    ]
    outcome = session.get(CaseOutcome, case_id)
    result["outcome"] = (
        None
        if outcome is None
        else {
            "finding": outcome.finding,
            "observed_at": outcome.observed_at,
            "recorded_at": outcome.recorded_at,
            "investigation_minutes": outcome.investigation_minutes,
            "recovered_amount": outcome.recovered_amount,
            "prevented_amount": outcome.prevented_amount,
            "reason": outcome.reason,
        }
    )
    return result


@router.post("/{case_id}/claim")
def claim_case(
    case_id: int,
    analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    case = find_case(session, case_id, lock=True)
    if case.assigned_to and case.assigned_to != analyst:
        raise HTTPException(409, "Case already assigned")
    if case.status == "resolved":
        raise HTTPException(409, "Resolved case cannot be claimed")
    if case.assigned_to is None:
        before = {"assigned_to": None, "status": case.status}
        case.assigned_to = analyst
        case.status = "investigating"
        audit(
            session,
            case,
            analyst,
            "claimed",
            before,
            {"assigned_to": analyst, "status": case.status},
        )
        session.commit()
    return {"case_id": case_id, "assigned_to": case.assigned_to, "status": case.status}


class NoteInput(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


@router.post("/{case_id}/notes", status_code=201)
def add_note(
    case_id: int,
    body: NoteInput,
    analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    case = find_case(session, case_id, lock=True)
    require_assignee(case, analyst)
    if case.status == "resolved":
        raise HTTPException(409, "Resolved case is read-only")
    note = CaseNote(case_id=case_id, actor=analyst, created_at=datetime.now(UTC), text=body.text)
    session.add(note)
    session.flush()
    audit(session, case, analyst, "note_added", None, {"note_id": note.note_id})
    session.commit()
    return {"note_id": note.note_id, "case_id": case_id}


class TransitionInput(BaseModel):
    status: Literal["investigating", "escalated", "resolved"]
    disposition: Literal["confirmed_fraud", "legitimate", "unresolved"] | None = None
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/{case_id}/transition")
def transition_case(
    case_id: int,
    body: TransitionInput,
    analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    case = find_case(session, case_id, lock=True)
    require_assignee(case, analyst)
    if case.status == "resolved" or (body.status == "resolved") != (body.disposition is not None):
        raise HTTPException(409, "Invalid case transition or missing disposition")
    before = {"status": case.status, "disposition": case.disposition}
    case.status = body.status
    case.disposition = body.disposition
    audit(
        session,
        case,
        analyst,
        "transition",
        before,
        {"status": case.status, "disposition": case.disposition, "reason": body.reason},
    )
    session.commit()
    return {"case_id": case_id, "status": case.status, "disposition": case.disposition}


class OutcomeInput(BaseModel):
    finding: Literal["confirmed_fraud", "legitimate", "unresolved"]
    observed_at: datetime
    investigation_minutes: int | None = Field(default=None, ge=0, le=1_000_000)
    recovered_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    prevented_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=2000)


@router.post("/{case_id}/outcome", status_code=201)
def record_outcome(
    case_id: int,
    body: OutcomeInput,
    analyst: Annotated[str, Depends(actor)],
    session: Annotated[Session, Depends(database)],
) -> dict:
    case = find_case(session, case_id, lock=True)
    require_assignee(case, analyst)
    if case.status != "resolved" or session.get(CaseOutcome, case_id):
        raise HTTPException(409, "Case must be resolved and outcome must be new")
    if body.observed_at.tzinfo is None or body.observed_at > datetime.now(UTC):
        raise HTTPException(422, "observed_at must be a nonfuture timezone-aware time")
    outcome = CaseOutcome(
        case_id=case_id, recorded_at=datetime.now(UTC), actor=analyst, **body.model_dump()
    )
    session.add(outcome)
    audit(
        session,
        case,
        analyst,
        "outcome_recorded",
        None,
        {
            "finding": body.finding,
            "observed_at": body.observed_at.isoformat(),
            "investigation_minutes": body.investigation_minutes,
            "recovered_amount": body.recovered_amount,
            "prevented_amount": body.prevented_amount,
            "reason": body.reason,
        },
    )
    session.commit()
    return {"case_id": case_id, "finding": body.finding}
