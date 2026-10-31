from datetime import date, datetime

from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Index,
                        Integer, String, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import (AlertStatus, AlertType, AuditAction,
                            ReviewResolution, ReviewStatus, ReviewTrigger,
                            Severity)
from app.models._helpers import in_list


class ReviewCase(Base):
    __tablename__ = "review_cases"

    review_id: Mapped[str] = mapped_column(String(25), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )
    review_year: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(20))
    scheduled_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(15), index=True)
    resolution: Mapped[str | None] = mapped_column(String(25))
    assigned_to: Mapped[str | None] = mapped_column(String(40))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("customer_id", "review_year", name="uq_reviews_customer_year"),
        CheckConstraint("review_id ~ '^REV-CUS-[0-9]{6}-[0-9]{4}$'", name="id_format"),
        CheckConstraint(
            "review_id = 'REV-' || customer_id || '-' || CAST(review_year AS text)",
            name="id_matches_customer_year",
        ),
        CheckConstraint(in_list("trigger", ReviewTrigger), name="trigger_valid"),
        CheckConstraint(in_list("status", ReviewStatus), name="status_valid"),
        # NULL passes a CHECK, so NULL resolution is allowed here on purpose
        CheckConstraint(in_list("resolution", ReviewResolution), name="resolution_valid"),
        CheckConstraint("due_date >= scheduled_date", name="due_after_scheduled"),
        # closed_at is set exactly when status is CLOSED
        CheckConstraint(
            "(status = 'CLOSED') = (closed_at IS NOT NULL)", name="closed_at_iff_closed"
        ),
    )


class Alert(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )
    document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.document_id"), index=True
    )
    review_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_cases.review_id"), index=True
    )
    alert_type: Mapped[str] = mapped_column(String(20))
    lead_days: Mapped[int | None] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(15), index=True)
    trigger_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # set by generator, no now()

    __table_args__ = (
        CheckConstraint("alert_id ~ '^ALT-[0-9]{4}-[0-9]{6}$'", name="id_format"),
        CheckConstraint(in_list("alert_type", AlertType), name="type_valid"),
        CheckConstraint(in_list("severity", Severity), name="severity_valid"),
        CheckConstraint(in_list("status", AlertStatus), name="status_valid"),
        CheckConstraint(
            "left(alert_type, 4) <> 'DOC_' OR document_id IS NOT NULL",
            name="doc_alert_needs_document",
        ),
        CheckConstraint(
            "alert_type <> 'REVIEW_OVERDUE' OR review_id IS NOT NULL",
            name="review_alert_needs_review",
        ),
        # lead_days is filled exactly for DOC_EXPIRING alerts
        CheckConstraint(
            "(alert_type = 'DOC_EXPIRING') = (lead_days IS NOT NULL)",
            name="lead_days_iff_expiring",
        ),
        CheckConstraint("lead_days IS NULL OR lead_days > 0", name="lead_days_positive"),
    )


class AuditEvent(Base):
    """Append-only. Protected by a DB trigger (see the 2nd migration)."""
    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(30))  # soft reference, no FK
    action: Mapped[str] = mapped_column(String(20))
    old_value: Mapped[dict | None] = mapped_column(JSONB)
    new_value: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime)

    __table_args__ = (
        CheckConstraint("event_id ~ '^AUD-[0-9]{6}$'", name="id_format"),
        CheckConstraint(in_list("action", AuditAction), name="action_valid"),
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_customer_time", "customer_id", "occurred_at"),
    )