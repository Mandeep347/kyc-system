from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Document, KYCRequirement, RiskRating
from app.services.completeness import (CompletenessReport, applicable_requirements,
                                       evaluate_completeness)


def load_requirements(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(select(KYCRequirement.__table__)).mappings()]


def current_risk_level(db: Session, customer_id: str) -> str | None:
    return db.scalar(select(RiskRating.risk_level).where(
        RiskRating.customer_id == customer_id, RiskRating.effective_to.is_(None)))


def customer_completeness(db: Session, customer: Customer,
                          as_of: date) -> tuple[CompletenessReport, str | None]:
    """Completeness of ONE customer. Returns (report, current risk level)."""
    risk = current_risk_level(db, customer.customer_id)
    reqs = applicable_requirements(load_requirements(db), customer.customer_type_id, risk, as_of)
    docs = [dict(r) for r in db.execute(
        select(Document.document_category, Document.status, Document.expiry_date)
        .where(Document.customer_id == customer.customer_id)).mappings()]
    return evaluate_completeness(reqs, docs, as_of), risk


@dataclass(frozen=True)
class CustomerEvaluation:
    customer_id: str
    customer_type_id: str
    risk_level: str
    kyc_status: str
    report: CompletenessReport


def evaluate_all_customers(db: Session, as_of: date) -> list[CustomerEvaluation]:
    """Completeness for EVERY customer using 4 queries in total (not 8,000)."""
    requirements = load_requirements(db)
    risk = {r.customer_id: r.risk_level for r in db.execute(
        select(RiskRating.customer_id, RiskRating.risk_level)
        .where(RiskRating.effective_to.is_(None)))}

    docs: dict[str, list[dict]] = defaultdict(list)
    for r in db.execute(select(Document.customer_id, Document.document_category,
                               Document.status, Document.expiry_date)).mappings():
        docs[r["customer_id"]].append(dict(r))

    out = []
    for c in db.execute(select(Customer.customer_id, Customer.customer_type_id,
                               Customer.kyc_status).order_by(Customer.customer_id)):
        level = risk[c.customer_id]
        reqs = applicable_requirements(requirements, c.customer_type_id, level, as_of)
        out.append(CustomerEvaluation(
            c.customer_id, c.customer_type_id, level, c.kyc_status,
            evaluate_completeness(reqs, docs[c.customer_id], as_of)))
    return out