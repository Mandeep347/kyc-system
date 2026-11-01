import re
from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import IssueType
from validation.checks import ALL_CHECKS
from validation.runner import run_checks

# One detector (a Phase 4 SQL check) per exception type
PRIMARY_DETECTOR = {
    IssueType.EXPIRED_BUT_VALID: "ANOM.expired_but_valid",
    IssueType.APPROVED_MISSING_EVIDENCE: "ANOM.approved_missing_evidence",
    IssueType.OWNERSHIP_OVER_100: "ANOM.ownership_over_100",
    IssueType.REVIEW_CLOSED_NO_RESOLUTION: "ANOM.review_closed_no_resolution",
    IssueType.DUPLICATE_DOCUMENT: "DUP.document_number_across_customers",
    IssueType.MISSING_OWNERSHIP: "ANOM.missing_ownership",
}
ENTITY_TYPE = {
    IssueType.EXPIRED_BUT_VALID: "DOCUMENT",
    IssueType.APPROVED_MISSING_EVIDENCE: "CUSTOMER",
    IssueType.OWNERSHIP_OVER_100: "CUSTOMER",
    IssueType.REVIEW_CLOSED_NO_RESOLUTION: "REVIEW_CASE",
    IssueType.DUPLICATE_DOCUMENT: "DOCUMENT",
    IssueType.MISSING_OWNERSHIP: "CUSTOMER",
}
assert set(PRIMARY_DETECTOR) == set(IssueType) == set(ENTITY_TYPE), "every issue needs a detector"

_CHECKS = {c.check_id: c for c in ALL_CHECKS}
_CUSTOMER_ID = re.compile(r"CUS-\d{6}")


def rule_for(issue: IssueType) -> str:
    return _CHECKS[PRIMARY_DETECTOR[issue]].description


def customer_of(entity_id: str) -> str:
    """Customer, document and review IDs all contain the customer ID."""
    match = _CUSTOMER_ID.search(entity_id)
    if match is None:
        raise ValueError(f"no customer ID inside {entity_id!r}")
    return match.group(0)


def detect(db: Session, as_of: date,
           issues: list[IssueType] | None = None) -> dict[IssueType, tuple[str, ...]]:
    """Run the detectors and return the sorted IDs of the records each one flags.
    Reads ONLY the data tables, never the ground truth."""
    wanted = list(issues) if issues else list(IssueType)
    checks = [_CHECKS[PRIMARY_DETECTOR[i]] for i in wanted]
    results = run_checks(db.connection(), as_of, checks)
    return {issue: result.found for issue, result in zip(wanted, results)}