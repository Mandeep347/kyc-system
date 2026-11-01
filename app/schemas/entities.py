from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.common import Number, ORMModel


# ---------- customers ----------

class CustomerSummary(BaseModel):
    customer_id: str
    legal_name: str
    customer_type: str          # INDIVIDUAL / SOLE_PROPRIETOR / COMPANY
    country_code: str
    kyc_status: str
    risk_level: str             # the CURRENT rating
    risk_score: int
    onboarded_on: date


class CustomerDetail(CustomerSummary):
    email: str
    date_of_birth: date | None
    incorporation_date: date | None
    document_count: int
    active_owner_count: int
    unresolved_alert_count: int
    completeness_pct: float
    is_complete: bool


# ---------- documents ----------

class VerificationOut(ORMModel):
    verification_id: str
    result: str
    name_match_score: Number
    verified_by: str
    verified_at: datetime
    remarks: str | None


class DocumentOut(ORMModel):
    document_id: str
    customer_id: str
    document_category: str
    document_type: str
    document_number: str
    name_on_document: str
    issue_date: date
    expiry_date: date | None
    status: str                          # as stored
    submitted_on: date
    effective_status: str | None = None  # what the DATES say (VALID but past expiry -> EXPIRED)
    days_to_expiry: int | None = None    # negative = already expired


class DocumentDetail(DocumentOut):
    customer_legal_name: str
    verifications: list[VerificationOut]
    requirement_ids: list[str]


# ---------- owners ----------

class OwnerOut(ORMModel):
    owner_id: str
    owner_name: str
    nationality: str
    ownership_pct: Number
    is_pep: bool
    effective_from: date
    effective_to: date | None
    is_active: bool = True


class OwnersOut(BaseModel):
    customer_id: str
    owners_required: bool
    active_total: Number
    reconciles: bool | None     # active total == 100? None when owners are not required
    owners: list[OwnerOut]


# ---------- audit ----------

class AuditEventOut(ORMModel):
    event_id: str
    customer_id: str
    entity_type: str
    entity_id: str
    action: str
    old_value: dict[str, Any] | None
    new_value: dict[str, Any] | None
    actor: str
    occurred_at: datetime


# ---------- reviews ----------

class ReviewOut(ORMModel):
    review_id: str
    customer_id: str
    review_year: int
    trigger: str
    scheduled_date: date
    due_date: date
    status: str
    resolution: str | None
    assigned_to: str | None
    closed_at: datetime | None
    is_overdue: bool = False     # computed: due_date < as_of AND status != CLOSED
    days_overdue: int = 0


class OverdueReviewOut(ReviewOut):
    customer_legal_name: str
    risk_level: str


class ReviewDetail(ReviewOut):
    audit_events: list[AuditEventOut]


# ---------- alerts ----------

class AlertOut(ORMModel):
    alert_id: str
    customer_id: str
    document_id: str | None
    review_id: str | None
    alert_type: str
    lead_days: int | None
    severity: str
    status: str
    trigger_date: date
    created_at: datetime


class AlertDetail(AlertOut):
    audit_events: list[AuditEventOut]