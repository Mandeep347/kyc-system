from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.api.deps import PageParams, count_of, get_as_of, get_customer_or_404
from app.core import rules
from app.core.database import get_db
from app.core.enums import CustomerTypeCode, KycStatus, RiskLevel
from app.models import (Alert, AuditEvent, BeneficialOwner, Customer, CustomerType, Document,
                        ReviewCase, RiskRating)
from app.schemas.common import Page
from app.schemas.entities import (AuditEventOut, CustomerDetail, CustomerSummary, DocumentOut,
                                  OwnerOut, OwnersOut, ReviewOut)
from app.schemas.insights import CompletenessOut, MissingItem
from app.services.completeness import derive_kyc_status
from app.services.customer_data import customer_completeness
from app.services.presenters import to_document_out, to_review_out

router = APIRouter(prefix="/customers", tags=["customers"])


def _count(db: Session, model, *conditions) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0


@router.get("", response_model=Page[CustomerSummary])
def list_customers(
    page: PageParams = Depends(),
    kyc_status: KycStatus | None = None,
    customer_type: CustomerTypeCode | None = None,
    risk_level: RiskLevel | None = None,
    q: str | None = Query(None, min_length=2, description="Search in name or customer ID"),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Customer, CustomerType.type_code, RiskRating.risk_level, RiskRating.risk_score)
        .join(CustomerType, CustomerType.customer_type_id == Customer.customer_type_id)
        .join(RiskRating, and_(RiskRating.customer_id == Customer.customer_id,
                               RiskRating.effective_to.is_(None)))
    )
    if kyc_status:
        stmt = stmt.where(Customer.kyc_status == kyc_status.value)
    if customer_type:
        stmt = stmt.where(CustomerType.type_code == customer_type.value)
    if risk_level:
        stmt = stmt.where(RiskRating.risk_level == risk_level.value)
    if q:
        stmt = stmt.where(Customer.legal_name.ilike(f"%{q}%") | Customer.customer_id.ilike(f"%{q}%"))

    total = count_of(db, stmt)
    rows = db.execute(stmt.order_by(Customer.customer_id)
                      .limit(page.limit).offset(page.offset)).all()
    items = [CustomerSummary(
        customer_id=c.customer_id, legal_name=c.legal_name, customer_type=type_code,
        country_code=c.country_code, kyc_status=c.kyc_status, risk_level=level,
        risk_score=score, onboarded_on=c.onboarded_on)
        for c, type_code, level, score in rows]
    return Page[CustomerSummary](total=total, limit=page.limit, offset=page.offset, items=items)


@router.get("/{customer_id}", response_model=CustomerDetail)
def get_customer(customer: Customer = Depends(get_customer_or_404),
                 as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    cid = customer.customer_id
    ctype = db.get(CustomerType, customer.customer_type_id)
    rating = db.scalar(select(RiskRating).where(
        RiskRating.customer_id == cid, RiskRating.effective_to.is_(None)))
    report, _ = customer_completeness(db, customer, as_of)
    return CustomerDetail(
        customer_id=cid, legal_name=customer.legal_name, customer_type=ctype.type_code,
        country_code=customer.country_code, kyc_status=customer.kyc_status,
        risk_level=rating.risk_level, risk_score=rating.risk_score,
        onboarded_on=customer.onboarded_on, email=customer.email,
        date_of_birth=customer.date_of_birth, incorporation_date=customer.incorporation_date,
        document_count=_count(db, Document, Document.customer_id == cid),
        active_owner_count=_count(db, BeneficialOwner, BeneficialOwner.customer_id == cid,
                                  BeneficialOwner.effective_to.is_(None)),
        unresolved_alert_count=_count(db, Alert, Alert.customer_id == cid,
                                      Alert.status != "RESOLVED"),
        completeness_pct=report.pct, is_complete=report.is_complete)


@router.get("/{customer_id}/completeness", response_model=CompletenessOut)
def get_completeness(customer: Customer = Depends(get_customer_or_404),
                     as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    """The decision endpoint: which mandatory evidence is missing, and which KYC
    status the rules derive from it (compare with the stored status)."""
    report, risk = customer_completeness(db, customer, as_of)
    derived = derive_kyc_status(report)
    return CompletenessOut(
        customer_id=customer.customer_id, as_of=as_of,
        customer_type=db.get(CustomerType, customer.customer_type_id).type_code,
        risk_level=risk, stored_kyc_status=customer.kyc_status,
        derived_kyc_status=derived.value,
        status_consistent=customer.kyc_status == derived.value,
        is_complete=report.is_complete, mandatory_total=report.total,
        satisfied=report.satisfied, completeness_pct=report.pct,
        satisfied_categories=[c for c in report.mandatory if c not in report.missing],
        missing=[MissingItem(category=c, reason=r) for c, r in sorted(report.missing.items())])


@router.get("/{customer_id}/documents", response_model=list[DocumentOut])
def customer_documents(customer: Customer = Depends(get_customer_or_404),
                       as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    docs = db.scalars(select(Document).where(Document.customer_id == customer.customer_id)
                      .order_by(Document.document_id)).all()
    return [to_document_out(d, as_of) for d in docs]


@router.get("/{customer_id}/owners", response_model=OwnersOut)
def customer_owners(customer: Customer = Depends(get_customer_or_404),
                    db: Session = Depends(get_db)):
    """All owner rows (history included). Only ACTIVE rows must total 100%."""
    rows = db.scalars(select(BeneficialOwner)
                      .where(BeneficialOwner.customer_id == customer.customer_id)
                      .order_by(BeneficialOwner.effective_from, BeneficialOwner.owner_id)).all()
    required = db.get(CustomerType, customer.customer_type_id).requires_beneficial_owner
    active_total = sum((o.ownership_pct for o in rows if o.effective_to is None), Decimal(0))
    return OwnersOut(
        customer_id=customer.customer_id, owners_required=required, active_total=active_total,
        reconciles=(active_total == rules.OWNERSHIP_TOTAL) if required else None,
        owners=[OwnerOut.model_validate(o).model_copy(update={"is_active": o.effective_to is None})
                for o in rows])


@router.get("/{customer_id}/reviews", response_model=list[ReviewOut])
def customer_reviews(customer: Customer = Depends(get_customer_or_404),
                     as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    rows = db.scalars(select(ReviewCase).where(ReviewCase.customer_id == customer.customer_id)
                      .order_by(ReviewCase.review_year)).all()
    return [to_review_out(r, as_of) for r in rows]


@router.get("/{customer_id}/audit-trail", response_model=Page[AuditEventOut])
def customer_audit_trail(
    customer: Customer = Depends(get_customer_or_404),
    page: PageParams = Depends(),
    entity_type: Literal["CUSTOMER", "DOCUMENT", "REVIEW_CASE", "ALERT", "RISK_RATING"] | None = None,
    db: Session = Depends(get_db),
):
    """Everything that ever happened to this customer, oldest first."""
    stmt = select(AuditEvent).where(AuditEvent.customer_id == customer.customer_id)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    total = count_of(db, stmt)
    rows = db.scalars(stmt.order_by(AuditEvent.occurred_at, AuditEvent.event_id)
                      .limit(page.limit).offset(page.offset)).all()
    return Page[AuditEventOut](total=total, limit=page.limit, offset=page.offset,
                               items=[AuditEventOut.model_validate(e) for e in rows])