from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from app.core.enums import DocumentStatus, KycStatus, ReviewResolution, RiskLevel

# Why a mandatory category is NOT satisfied (see the table in the explanation)
PENDING, EXPIRED, INVALID, NONE = "PENDING", "EXPIRED", "INVALID", "NONE"


def applicable_requirements(requirements: list[dict], customer_type_id: str,
                            risk_level: str, as_of: date) -> list[dict]:
    """Requirements that apply to this customer type and risk level on the as-of date."""
    return [
        r for r in requirements
        if r["customer_type_id"] == customer_type_id
        and r["risk_level"] == risk_level
        and r["effective_from"] <= as_of
        and (r["effective_to"] is None or r["effective_to"] >= as_of)
    ]


def effective_state(doc: dict, as_of: date) -> str:
    """The status the DATES say a document has. A VALID document past its expiry is EXPIRED."""
    if (doc["status"] == DocumentStatus.VALID
            and doc["expiry_date"] is not None and doc["expiry_date"] < as_of):
        return EXPIRED
    return str(doc["status"])


@dataclass(frozen=True)
class CompletenessReport:
    mandatory: tuple[str, ...]      # mandatory document categories for this customer
    missing: dict[str, str]         # category -> why it is not satisfied

    @property
    def total(self) -> int:
        return len(self.mandatory)

    @property
    def satisfied(self) -> int:
        return self.total - len(self.missing)

    @property
    def pct(self) -> float:
        return 100.0 if not self.mandatory else round(100 * self.satisfied / self.total, 2)

    @property
    def is_complete(self) -> bool:
        return not self.missing


def evaluate_completeness(requirements: list[dict], documents: list[dict],
                          as_of: date) -> CompletenessReport:
    """requirements = this customer's applicable requirements; documents = this customer's documents."""
    mandatory = tuple(sorted({r["document_category"] for r in requirements if r["is_mandatory"]}))
    by_category = defaultdict(list)
    for d in documents:
        by_category[d["document_category"]].append(d)

    missing: dict[str, str] = {}
    for category in mandatory:
        states = {effective_state(d, as_of) for d in by_category[category]}
        if "VALID" in states:
            continue                                   # satisfied
        for state in (PENDING, EXPIRED, INVALID):      # priority order
            if state in states:
                missing[category] = state
                break
        else:
            missing[category] = NONE
    return CompletenessReport(mandatory, missing)


def derive_kyc_status(report: CompletenessReport) -> KycStatus:
    if report.is_complete:
        return KycStatus.APPROVED
    states = set(report.missing.values())
    if states == {EXPIRED}:                    # everything was fine once, then documents lapsed
        return KycStatus.SUSPENDED
    if INVALID in states:
        return KycStatus.REJECTED
    if states & {PENDING, EXPIRED}:
        return KycStatus.UNDER_REVIEW
    return KycStatus.PENDING


def derive_review_resolution(report: CompletenessReport, risk_level: str,
                             kyc_status: KycStatus) -> ReviewResolution:
    if report.is_complete:
        return ReviewResolution.NO_ISSUES
    if kyc_status == KycStatus.REJECTED:
        return ReviewResolution.RELATIONSHIP_EXITED
    if risk_level == RiskLevel.HIGH:
        return ReviewResolution.ESCALATED
    return ReviewResolution.REMEDIATION_REQUIRED