from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import Principal, Role, require_roles
from app.schemas.actions import FailedCheck, ValidationOut
from validation.report import build_report
from validation.runner import run_checks

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/validation", response_model=ValidationOut)
def run_validation(
    principal: Principal = Depends(require_roles(Role.ADMIN)),
    db: Session = Depends(get_db),
):
    """Runs the whole Phase 4 validation suite against the live database.
    The ground truth is NOT used: this is a pure data-integrity check."""
    as_of = settings.as_of_date
    results = run_checks(db.connection(), as_of)
    rep = build_report(results, None, as_of, {}, {}, "")
    return ValidationOut(
        verdict=rep["verdict"], as_of=as_of, checks_run=rep["totals"]["checks"],
        failures=[FailedCheck(check_id=e["check_id"], description=e["description"],
                              findings=e["findings"], sample=e["sample"])
                  for e in rep["failures"]],
        anomaly_findings={e["check_id"]: e["findings"] for e in rep["reconciliation"]})