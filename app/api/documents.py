from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import PageParams, count_of, get_as_of
from app.core.database import get_db
from app.models import Customer, Document, DocumentRequirementLink, Verification
from app.schemas.common import Page
from app.schemas.entities import DocumentDetail, DocumentOut, VerificationOut
from app.services.presenters import to_document_out

router = APIRouter(prefix="/documents", tags=["documents"])


# NOTE: "/expiring" must be declared BEFORE "/{document_id}", or FastAPI would treat
# the word "expiring" as a document ID.
@router.get("/expiring", response_model=Page[DocumentOut])
def expiring_documents(
    page: PageParams = Depends(),
    within_days: int = Query(60, ge=1, le=365, description="Look this many days ahead"),
    as_of: date = Depends(get_as_of),
    db: Session = Depends(get_db),
):
    """VALID documents that expire within the next N days, soonest first."""
    stmt = select(Document).where(
        Document.status == "VALID", Document.expiry_date.is_not(None),
        Document.expiry_date >= as_of, Document.expiry_date <= as_of + timedelta(days=within_days))
    total = count_of(db, stmt)
    rows = db.scalars(stmt.order_by(Document.expiry_date, Document.document_id)
                      .limit(page.limit).offset(page.offset)).all()
    return Page[DocumentOut](total=total, limit=page.limit, offset=page.offset,
                             items=[to_document_out(d, as_of) for d in rows])


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, as_of: date = Depends(get_as_of),
                 db: Session = Depends(get_db)):
    """One document with its full verification history (nothing is ever overwritten)."""
    doc = db.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    verifications = db.scalars(select(Verification).where(Verification.document_id == document_id)
                               .order_by(Verification.verified_at, Verification.verification_id)).all()
    requirement_ids = list(db.scalars(
        select(DocumentRequirementLink.requirement_id)
        .where(DocumentRequirementLink.document_id == document_id)
        .order_by(DocumentRequirementLink.requirement_id)))
    return DocumentDetail(
        **to_document_out(doc, as_of).model_dump(),
        customer_legal_name=db.get(Customer, doc.customer_id).legal_name,
        verifications=[VerificationOut.model_validate(v) for v in verifications],
        requirement_ids=requirement_ids)