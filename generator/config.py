from dataclasses import dataclass
from datetime import date

from app.core.enums import IssueType, RiskLevel

# these volumes were defined in problem statement
BASE_CUSTOMERS = 8_000
BASE_BUSINESSES = 1_000
BASE_DOCUMENTS = 20_000
BASE_REVIEWS = 3_000

# generation paramteres (i chose myself)
COMPANY_SHARE_OF_BUSINESSES = 0.70   # rest are sole proprietors
RISK_MIX = {RiskLevel.LOW: 0.60, RiskLevel.MEDIUM: 0.30, RiskLevel.HIGH: 0.10}
COUNTRY_MIX = {"IN": 85, "US": 5, "GB": 5, "AE": 3, "SG": 3}
OPTIONAL_SUBMISSION_RATE = 0.40    # share of customers who submit an optional doc
RENEWAL_RATE = 0.75                # share of expired renewable docs that get renewed
NAME_VARIANT_RATE = 0.06           # share of docs printed with a variant of the name
ADDRESS_BOUNDARY_RATE = 0.03       # share of address proofs issued exactly 90 days before submission
VERIFICATION_FAIL_RATE = 0.02      # share of first checks that FAIL (bad scan etc.)
FAIL_RESCAN_PASS_RATE = 0.50       # share of FAILs that pass after a re-scan
# Ownership parameters
OWNER_COUNT_MIX = {1: 15, 2: 35, 3: 30, 4: 20}   # owners per company (weights)
PEP_RATE = 0.03                                  # share of owners flagged as PEP
FOREIGN_OWNER_RATE = 0.30                        # owner nationality differs from customer country
OWNER_CHANGE_RATE = 0.08                         # share of companies with an ownership change

# Risk rating parameters
RERATE_RATE = 0.20                               # share of customers re-rated after onboarding

# Review parameters
REVIEW_LEAD_DAYS = 30                            # a review case opens this many days before it is due
LATE_CLOSE_MAX_DAYS = 20                         # a closed review may close this long after its due date
REVIEW_IN_PROGRESS_SHARE = 0.60                  # of open reviews, share already started
REVIEW_OPEN_PAST_DUE_RATE = {                    # share of past-due reviews still not closed
    RiskLevel.HIGH: 0.08,
    RiskLevel.MEDIUM: 0.12,
    RiskLevel.LOW: 0.16,
}

REVIEWERS = [f"reviewer_{i:02d}" for i in range(1, 7)]   # compliance reviewers (synthetic)
# Alert parameters
ALERT_ACK_AFTER_DAYS = 14    # an unresolved alert counts as acknowledged after this many days


@dataclass(frozen=True)
class GenConfig:
    seed: int = 20261022
    as_of: date = date(2026, 10, 30) # the fixed today for all derived dates
    scale: float = 1.0

    def _scaled(self, base: int) -> int:
        return max(1, round(base * self.scale))

    @property
    def n_customers(self) -> int:
        return self._scaled(BASE_CUSTOMERS)

    @property
    def n_businesses(self) -> int:
        return min(self._scaled(BASE_BUSINESSES), self.n_customers)

    @property
    def n_companies(self) -> int:
        return round(self.n_businesses * COMPANY_SHARE_OF_BUSINESSES)

    @property
    def n_sole_props(self) -> int:
        return self.n_businesses - self.n_companies

    @property
    def n_documents(self) -> int:
        return self._scaled(BASE_DOCUMENTS)

    @property
    def n_reviews(self) -> int:
        return min(self._scaled(BASE_REVIEWS), self.n_customers)

# Anomaly injection (stage 3E): how many of each kind at scale = 1.0
ANOMALY_BASE_COUNTS = {
    IssueType.APPROVED_MISSING_EVIDENCE: 80,    # customers
    IssueType.EXPIRED_BUT_VALID: 60,            # documents
    IssueType.OWNERSHIP_OVER_100: 25,           # businesses
    IssueType.REVIEW_CLOSED_NO_RESOLUTION: 40,  # review cases
    IssueType.DUPLICATE_DOCUMENT: 30,           # PAIRS of documents (= 60 documents)
    IssueType.MISSING_OWNERSHIP: 15,            # businesses
}