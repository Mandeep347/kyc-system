from datetime import date
from decimal import Decimal

from sqlalchemy import func, select, cast, Integer
from sqlalchemy.orm import Session

from app.core import rules
from app.core.enums import AuditAction as A
from app.core.enums import DocumentCategory as C
from app.models import (Alert, Customer, Document, DocumentRequirementLink, ReviewCase,
                        Verification)
from app.schemas.actions import DocumentSubmission, ReassessResult
from app.services.completeness import applicable_requirements, derive_kyc_status
from app.services.customer_data import (current_risk_level, customer_completeness,
                                        load_requirements)
from app.services.writes import Ledger
from generator import ids

ADDRESS_CATEGORIES = (C.ADDRESS_PROOF, C.BUSINESS_ADDRESS_PROOF)


class WorkflowError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code, self.message = status_code, message


def _conflict(msg: str) -> WorkflowError:    # 409: the current state does not allow this
    return WorkflowError(409, msg)


def _invalid(msg: str) -> WorkflowError:     # 422: the input itself is wrong
    return WorkflowError(422, msg)


def _not_found(what: str, key: str) -> WorkflowError:
    return WorkflowError(404, f"{what} {key} not found")


# ====================================================== documents

def submit_document(db: Session, customer: Customer, body: DocumentSubmission,
                    actor: str, as_of: date) -> Document:
    ledger = Ledger(db, as_of)
    cid = customer.customer_id

    # ---- validate everything BEFORE writing anything ----
    validity_by_type = dict(rules.DOCUMENT_SPECS[body.document_category])
    if body.document_type not in validity_by_type:
        raise _invalid(f"{body.document_type.value} is not accepted as {body.document_category.value}")
    validity = validity_by_type[body.document_type]
    if body.issue_date > as_of:
        raise _invalid("issue_date is in the future")
    if validity is not None:
        if body.expiry_date is None:
            raise _invalid("expiry_date is required for this document type")
        if body.expiry_date <= body.issue_date:
            raise _invalid("expiry_date must be after issue_date")
        if body.expiry_date < as_of:
            raise _invalid("the document has already expired")
    elif body.expiry_date is not None:
        raise _invalid("this document type does not expire; leave expiry_date empty")
    if (body.document_category in ADDRESS_CATEGORIES
            and (as_of - body.issue_date).days > rules.ADDRESS_PROOF_MAX_AGE_DAYS):
        raise _invalid(f"address proof must be at most {rules.ADDRESS_PROOF_MAX_AGE_DAYS} days old")
    if db.scalar(select(Document.document_id)
                 .where(Document.document_number == body.document_number).limit(1)):
        raise _conflict("this document number is already registered")
    last_seq = db.scalar(select(func.max(cast(func.right(Document.document_id, 3), Integer)))
                         .where(Document.customer_id == cid)) or 0
    if last_seq >= 999:
        raise _conflict("this customer has reached the maximum number of documents")

    # ---- write ----
    doc = Document(
        document_id=ids.document_id(cid, last_seq + 1), customer_id=cid,
        document_category=body.document_category.value, document_type=body.document_type.value,
        document_number=body.document_number, name_on_document=body.name_on_document,
        issue_date=body.issue_date, expiry_date=body.expiry_date,
        status="PENDING", submitted_on=as_of)
    db.add(doc)

    # link the document to the requirement it counts towards (the N:M bridge table)
    risk = current_risk_level(db, cid)
    requirement = next((r for r in applicable_requirements(
        load_requirements(db), customer.customer_type_id, risk, as_of)
        if r["document_category"] == body.document_category.value), None)
    if requirement is not None:
        db.add(DocumentRequirementLink(
            link_id=ledger.next_id("link"), document_id=doc.document_id,
            requirement_id=requirement["requirement_id"],
            linked_on=max(as_of, requirement["effective_from"])))

    ledger.audit(cid, "DOCUMENT", doc.document_id, A.CREATED, None, {"status": "PENDING"}, actor)
    db.commit()
    return doc


def verify_document(db: Session, document_id: str, decision: str, remarks: str | None,
                    actor: str, as_of: date):
    ledger = Ledger(db, as_of)
    doc = db.get(Document, document_id)
    if doc is None:
        raise _not_found("Document", document_id)
    if doc.expiry_date is not None and doc.expiry_date < as_of:
        raise _conflict(f"the document expired on {doc.expiry_date}; it can no longer be verified")

    customer = db.get(Customer, doc.customer_id)
    score = rules.name_match_score(customer.legal_name, doc.name_on_document)
    note = None
    if score < rules.NAME_MATCH_THRESHOLD:
        result, note = "NAME_MISMATCH", (f"Name on document differs from the customer record "
                                          f"(score {score}); recorded as NAME_MISMATCH")
    else:
        result = decision                     # "PASS" or "FAIL"

    before = doc.status
    after = rules.DOC_STATUS_FROM_RESULT[result].value
    if (before, after) not in rules.ALLOWED_TRANSITIONS["DOCUMENT"]:
        raise _conflict(f"document is {before}: changing it to {after} is not an allowed transition")

    when = ledger.stamp()
    verification = Verification(
        verification_id=ledger.next_id("verification"), document_id=document_id, result=result,
        name_match_score=Decimal(str(score)), verified_by=actor, verified_at=when,
        remarks=remarks or note)
    db.add(verification)
    doc.status = after       # current state; the history is the verification + audit rows
    ledger.audit(doc.customer_id, "DOCUMENT", document_id, A.VERIFIED,
                 {"status": before},
                 {"status": after, "result": result,
                  "verification_id": verification.verification_id},
                 actor, when=when)
    db.commit()
    return verification, before, after, note


# ====================================================== customers

# (stored status, derived status) -> the steps to take. Every step is an allowed transition.
_STATUS_PATHS = {
    ("PENDING", "UNDER_REVIEW"): ["UNDER_REVIEW"],
    ("PENDING", "APPROVED"): ["UNDER_REVIEW", "APPROVED"],
    ("PENDING", "REJECTED"): ["UNDER_REVIEW", "REJECTED"],
    ("UNDER_REVIEW", "APPROVED"): ["APPROVED"],
    ("UNDER_REVIEW", "REJECTED"): ["REJECTED"],
    ("APPROVED", "SUSPENDED"): ["SUSPENDED"],
    ("SUSPENDED", "APPROVED"): ["APPROVED"],
    ("REJECTED", "UNDER_REVIEW"): ["UNDER_REVIEW"],
    ("REJECTED", "APPROVED"): ["UNDER_REVIEW", "APPROVED"],
}


def reassess_customer(db: Session, customer: Customer, actor: str, as_of: date) -> ReassessResult:
    ledger = Ledger(db, as_of)
    db.refresh(customer)
    report, _ = customer_completeness(db, customer, as_of)
    derived = derive_kyc_status(report).value
    stored = customer.kyc_status

    if stored == derived:
        steps, note = [], "Stored status already matches the evidence."
    elif stored == "APPROVED" and not report.is_complete:
        steps = ["SUSPENDED"]
        note = ("Approved customer is missing mandatory evidence "
                f"({', '.join(sorted(report.missing))}); suspended until it is fixed.")
    else:
        steps = list(_STATUS_PATHS.get((stored, derived), []))
        note = None if steps else (f"No automatic transition from {stored} to {derived}; "
                                   "needs manual review.")

    state = stored
    for target in steps:
        if (state, target) not in rules.ALLOWED_TRANSITIONS["CUSTOMER"]:
            raise _conflict(f"{state} -> {target} is not an allowed transition")
        ledger.audit(customer.customer_id, "CUSTOMER", customer.customer_id, A.STATUS_CHANGED,
                     {"kyc_status": state}, {"kyc_status": target}, actor)
        state = target
    if steps:
        customer.kyc_status = state
        db.commit()
    return ReassessResult(customer_id=customer.customer_id, previous_status=stored,
                          new_status=state, derived_status=derived, changed=bool(steps),
                          steps=steps, note=note)


# ====================================================== reviews

def start_review(db: Session, review_id: str, actor: str, as_of: date) -> ReviewCase:
    ledger = Ledger(db, as_of)
    review = db.get(ReviewCase, review_id)
    if review is None:
        raise _not_found("Review", review_id)
    if review.status != "SCHEDULED":
        raise _conflict(f"review is {review.status}; only a SCHEDULED review can be started")
    if review.scheduled_date > as_of:
        raise _conflict(f"review opens on {review.scheduled_date}; it cannot be started yet")
    review.status = "IN_PROGRESS"
    review.assigned_to = review.assigned_to or actor
    ledger.audit(review.customer_id, "REVIEW_CASE", review_id, A.STATUS_CHANGED,
                 {"status": "SCHEDULED"}, {"status": "IN_PROGRESS"}, actor)
    db.commit()
    return review


def close_review(db: Session, review_id: str, resolution, actor: str, as_of: date) -> ReviewCase:
    ledger = Ledger(db, as_of)
    review = db.get(ReviewCase, review_id)
    if review is None:
        raise _not_found("Review", review_id)
    if review.status == "CLOSED":
        raise _conflict("review is already closed")
    if review.status != "IN_PROGRESS":
        raise _conflict("start the review before closing it")

    customer = db.get(Customer, review.customer_id)
    report, _ = customer_completeness(db, customer, as_of)
    if resolution.value == "NO_ISSUES" and not report.is_complete:
        missing = ", ".join(sorted(report.missing))
        raise _conflict(f"cannot close with NO_ISSUES: mandatory evidence is missing ({missing})")

    when = ledger.stamp()
    review.status, review.resolution, review.closed_at = "CLOSED", resolution.value, when
    ledger.audit(review.customer_id, "REVIEW_CASE", review_id, A.REVIEW_CLOSED,
                 {"status": "IN_PROGRESS"}, {"status": "CLOSED", "resolution": resolution.value},
                 actor, when=when)

    # the overdue alert for this review is now resolved
    for alert in db.scalars(select(Alert).where(Alert.review_id == review_id,
                                                Alert.status != "RESOLVED")):
        ledger.audit(alert.customer_id, "ALERT", alert.alert_id, A.STATUS_CHANGED,
                     {"status": alert.status}, {"status": "RESOLVED"}, "system")
        alert.status = "RESOLVED"
    db.commit()
    return review


# ====================================================== alerts

def _alert_transition(db: Session, alert_id: str, allowed_from: tuple[str, ...], target: str,
                      actor: str, as_of: date) -> Alert:
    ledger = Ledger(db, as_of)
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise _not_found("Alert", alert_id)
    if alert.status not in allowed_from:
        raise _conflict(f"alert is {alert.status}; it cannot become {target}")
    ledger.audit(alert.customer_id, "ALERT", alert_id, A.STATUS_CHANGED,
                 {"status": alert.status}, {"status": target}, actor)
    alert.status = target
    db.commit()
    return alert


def acknowledge_alert(db: Session, alert_id: str, actor: str, as_of: date) -> Alert:
    return _alert_transition(db, alert_id, ("OPEN",), "ACKNOWLEDGED", actor, as_of)


def resolve_alert(db: Session, alert_id: str, actor: str, as_of: date) -> Alert:
    return _alert_transition(db, alert_id, ("OPEN", "ACKNOWLEDGED"), "RESOLVED", actor, as_of)