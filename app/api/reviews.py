from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import PageParams, count_of, get_as_of
from app.core.database import get_db
from app.core.enums import RiskLevel
from app.models import AuditEvent, Customer, ReviewCase, RiskRating
from app.schemas.common import Page
from app.schemas.entities import AuditEventOut, OverdueReviewOut, ReviewDetail
from app.services.presenters import to_review_out

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/overdue", response_model=Page[OverdueReviewOut])
def overdue_reviews(
    page: PageParams = Depends(),
    min_days_overdue: int = Query(1, ge=1, description="Only reviews at least this many days late"),
    risk_level: RiskLevel | None = None,
    as_of: date = Depends(get_as_of),
    db: Session = Depends(get_db),
):
    """Overdue is COMPUTED (due_date < as_of and not CLOSED), never stored. Most late first."""
    latest_due = as_of - timedelta(days=min_days_overdue)
    stmt = (
        select(ReviewCase, Customer.legal_name, RiskRating.risk_level)
        .join(Customer, Customer.customer_id == ReviewCase.customer_id)
        .join(RiskRating, and_(RiskRating.customer_id == ReviewCase.customer_id,
                               RiskRating.effective_to.is_(None)))
        .where(ReviewCase.status != "CLOSED", ReviewCase.due_date <= latest_due)
    )
    if risk_level:
        stmt = stmt.where(RiskRating.risk_level == risk_level.value)
    total = count_of(db, stmt)
    rows = db.execute(stmt.order_by(ReviewCase.due_date, ReviewCase.review_id)
                      .limit(page.limit).offset(page.offset)).all()
    items = [OverdueReviewOut(**to_review_out(r, as_of).model_dump(),
                              customer_legal_name=name, risk_level=level)
             for r, name, level in rows]
    return Page[OverdueReviewOut](total=total, limit=page.limit, offset=page.offset, items=items)


@router.get("/{review_id}", response_model=ReviewDetail)
def get_review(review_id: str, as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    review = db.get(ReviewCase, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail=f"Review {review_id} not found")
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == "REVIEW_CASE", AuditEvent.entity_id == review_id)
        .order_by(AuditEvent.occurred_at, AuditEvent.event_id)).all()
    return ReviewDetail(**to_review_out(review, as_of).model_dump(),
                        audit_events=[AuditEventOut.model_validate(e) for e in events])