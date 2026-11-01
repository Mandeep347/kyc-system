from datetime import date

from app.models import Document, ReviewCase
from app.schemas.entities import DocumentOut, ReviewOut
from app.services.completeness import effective_state


def to_document_out(d: Document, as_of: date) -> DocumentOut:
    days = (d.expiry_date - as_of).days if d.expiry_date is not None else None
    state = effective_state({"status": d.status, "expiry_date": d.expiry_date}, as_of)
    return DocumentOut.model_validate(d).model_copy(
        update={"effective_status": state, "days_to_expiry": days})


def to_review_out(r: ReviewCase, as_of: date) -> ReviewOut:
    overdue = r.status != "CLOSED" and r.due_date < as_of
    return ReviewOut.model_validate(r).model_copy(
        update={"is_overdue": overdue,
                "days_overdue": (as_of - r.due_date).days if overdue else 0})