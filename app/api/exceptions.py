from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import PageParams, get_as_of
from app.core.database import get_db
from app.core.enums import IssueType
from app.schemas.common import Page
from app.schemas.insights import ExceptionOut, ExceptionSummary
from app.services import exceptions as svc

router = APIRouter(prefix="/exceptions", tags=["exceptions"])


@router.get("", response_model=Page[ExceptionOut])
def list_exceptions(
    page: PageParams = Depends(),
    issue_type: IssueType | None = None,
    customer_id: str | None = None,
    as_of: date = Depends(get_as_of),
    db: Session = Depends(get_db),
):
    """Detected exceptions. Found by rules over the data, never by reading the answer key.
    Each row links to the customer and audit trail so it can be traced."""
    found = svc.detect(db, as_of, [issue_type] if issue_type else None)
    items = []
    for issue, entity_ids in found.items():
        for entity_id in entity_ids:
            cid = svc.customer_of(entity_id)
            if customer_id and cid != customer_id:
                continue
            items.append(ExceptionOut(
                issue_type=issue.value, entity_type=svc.ENTITY_TYPE[issue], entity_id=entity_id,
                customer_id=cid, rule=svc.rule_for(issue),
                customer_url=f"/customers/{cid}", audit_trail_url=f"/customers/{cid}/audit-trail"))
    return Page[ExceptionOut](total=len(items), limit=page.limit, offset=page.offset,
                              items=items[page.offset:page.offset + page.limit])


@router.get("/summary", response_model=ExceptionSummary)
def exception_summary(as_of: date = Depends(get_as_of), db: Session = Depends(get_db)):
    found = svc.detect(db, as_of)
    return ExceptionSummary(as_of=as_of, total=sum(len(v) for v in found.values()),
                            by_issue_type={i.value: len(ids) for i, ids in found.items()})