from datetime import date

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import Customer


class PageParams:
    def __init__(self,
                 limit: int = Query(50, ge=1, le=500, description="Rows per page"),
                 offset: int = Query(0, ge=0, description="Rows to skip")):
        self.limit = limit
        self.offset = offset


def get_as_of(as_of: date | None = Query(
        None, description="Evaluate as of this date. Default: the configured AS_OF_DATE")) -> date:
    return as_of or settings.as_of_date


def get_customer_or_404(customer_id: str, db: Session = Depends(get_db)) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail=f"Customer {customer_id} not found")
    return customer


def count_of(db: Session, stmt) -> int:
    """Number of rows a SELECT would return (used for Page.total)."""
    return db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0