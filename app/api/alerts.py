from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import PageParams, count_of
from app.core.database import get_db
from app.core.enums import AlertStatus, AlertType, Severity
from app.models import Alert, AuditEvent
from app.schemas.common import Page
from app.schemas.entities import AlertDetail, AlertOut, AuditEventOut
from app.schemas.insights import AlertSummary

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=Page[AlertOut])
def list_alerts(
    page: PageParams = Depends(),
    alert_type: AlertType | None = None,
    status: AlertStatus | None = None,
    severity: Severity | None = None,
    customer_id: str | None = None,
    triggered_from: date | None = None,
    triggered_to: date | None = None,
    db: Session = Depends(get_db),
):
    """Newest alerts first."""
    stmt = select(Alert)
    if alert_type:
        stmt = stmt.where(Alert.alert_type == alert_type.value)
    if status:
        stmt = stmt.where(Alert.status == status.value)
    if severity:
        stmt = stmt.where(Alert.severity == severity.value)
    if customer_id:
        stmt = stmt.where(Alert.customer_id == customer_id)
    if triggered_from:
        stmt = stmt.where(Alert.trigger_date >= triggered_from)
    if triggered_to:
        stmt = stmt.where(Alert.trigger_date <= triggered_to)
    total = count_of(db, stmt)
    rows = db.scalars(stmt.order_by(Alert.trigger_date.desc(), Alert.alert_id)
                      .limit(page.limit).offset(page.offset)).all()
    return Page[AlertOut](total=total, limit=page.limit, offset=page.offset,
                          items=[AlertOut.model_validate(a) for a in rows])


# "/summary" before "/{alert_id}" for the same reason as "/expiring" in documents
@router.get("/summary", response_model=AlertSummary)
def alert_summary(db: Session = Depends(get_db)):
    rows = db.execute(select(Alert.alert_type, Alert.status, func.count())
                      .group_by(Alert.alert_type, Alert.status)).all()
    by_type: dict[str, dict[str, int]] = defaultdict(dict)
    for alert_type, status, n in rows:
        by_type[alert_type][status] = n
    return AlertSummary(total=sum(n for _, _, n in rows),
                        by_type={k: dict(sorted(v.items())) for k, v in sorted(by_type.items())})


@router.get("/{alert_id}", response_model=AlertDetail)
def get_alert(alert_id: str, db: Session = Depends(get_db)):
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == "ALERT", AuditEvent.entity_id == alert_id)
        .order_by(AuditEvent.occurred_at, AuditEvent.event_id)).all()
    return AlertDetail(**AlertOut.model_validate(alert).model_dump(),
                       audit_events=[AuditEventOut.model_validate(e) for e in events])