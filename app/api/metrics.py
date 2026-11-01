from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.schemas.insights import MetricsOut
from app.services.metrics import compute_metrics

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricsOut)
def metrics(db: Session = Depends(get_db)):
    """Dashboard numbers plus detection quality versus the ground truth.
    Always evaluated at the configured AS_OF_DATE (the date the ground truth was built for)."""
    return compute_metrics(db, settings.as_of_date)