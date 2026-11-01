from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_customer_or_404
from app.core.config import settings
from app.core.database import get_db
from app.core.security import Principal, Role, require_roles
from app.models import Customer
from app.schemas.actions import (DocumentSubmission, ReassessResult, ReviewCloseRequest,
                                 VerifyRequest, VerifyResult)
from app.schemas.entities import AlertOut, DocumentOut, ReviewOut, VerificationOut
from app.services import workflows
from app.services.presenters import to_document_out, to_review_out

router = APIRouter(tags=["actions (write)"])

AS_OF = settings.as_of_date    # writes always happen "on" the configured as-of date


@router.post("/customers/{customer_id}/documents", response_model=DocumentOut, status_code=201)
def submit_document(
    body: DocumentSubmission,
    customer: Customer = Depends(get_customer_or_404),
    principal: Principal = Depends(require_roles(Role.CUSTOMER, Role.ANALYST, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """A customer (or an analyst on their behalf) submits a document. It starts as PENDING."""
    if principal.role == Role.CUSTOMER and principal.user != customer.customer_id:
        raise HTTPException(status_code=403, detail="Customers may only submit their own documents")
    doc = workflows.submit_document(db, customer, body, principal.user, AS_OF)
    return to_document_out(doc, AS_OF)


@router.post("/documents/{document_id}/verify", response_model=VerifyResult, status_code=201)
def verify_document(
    document_id: str,
    body: VerifyRequest,
    principal: Principal = Depends(require_roles(Role.ANALYST, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Adds a verification row (history is kept) and updates the document status from it."""
    verification, before, after, note = workflows.verify_document(
        db, document_id, body.decision, body.remarks, principal.user, AS_OF)
    return VerifyResult(verification=VerificationOut.model_validate(verification),
                        document_status_before=before, document_status_after=after, note=note)


@router.post("/customers/{customer_id}/reassess", response_model=ReassessResult)
def reassess_customer(
    customer: Customer = Depends(get_customer_or_404),
    principal: Principal = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Recomputes the KYC status from the evidence and applies the allowed steps, one audit event each."""
    return workflows.reassess_customer(db, customer, principal.user, AS_OF)


@router.post("/reviews/{review_id}/start", response_model=ReviewOut)
def start_review(
    review_id: str,
    principal: Principal = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return to_review_out(workflows.start_review(db, review_id, principal.user, AS_OF), AS_OF)


@router.post("/reviews/{review_id}/close", response_model=ReviewOut)
def close_review(
    review_id: str,
    body: ReviewCloseRequest,
    principal: Principal = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """A resolution is mandatory, so a review can never be closed without one."""
    review = workflows.close_review(db, review_id, body.resolution, principal.user, AS_OF)
    return to_review_out(review, AS_OF)


@router.post("/alerts/{alert_id}/acknowledge", response_model=AlertOut)
def acknowledge_alert(
    alert_id: str,
    principal: Principal = Depends(require_roles(Role.ANALYST, Role.REVIEWER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return AlertOut.model_validate(workflows.acknowledge_alert(db, alert_id, principal.user, AS_OF))


@router.post("/alerts/{alert_id}/resolve", response_model=AlertOut)
def resolve_alert(
    alert_id: str,
    principal: Principal = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    db: Session = Depends(get_db),
):
    return AlertOut.model_validate(workflows.resolve_alert(db, alert_id, principal.user, AS_OF))