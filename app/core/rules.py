from difflib import SequenceMatcher

from app.core.enums import DocumentCategory as C
from app.core.enums import DocumentStatus
from app.core.enums import DocumentType as D
from app.core.enums import RiskLevel, VerificationResult

# Score bands. Must match the CHECK constraint on risk_ratings.
RISK_BANDS = {
    RiskLevel.LOW: (0, 34),
    RiskLevel.MEDIUM: (35, 69),
    RiskLevel.HIGH: (70, 100),
}

# How often each risk level is reviewed
REVIEW_CADENCE_MONTHS = {
    RiskLevel.LOW: 36,
    RiskLevel.MEDIUM: 24,
    RiskLevel.HIGH: 12,
}

# Expiry alerts fire this many days BEFORE a document expires
DEFAULT_LEAD_DAYS = [60, 30, 7]
HIGH_RISK_LEAD_DAYS = [90, 60, 30, 7]

ADDRESS_PROOF_MAX_AGE_DAYS = 90   # address proof must be issued this recently
NAME_MATCH_THRESHOLD = 85         # name_match_score below this = NAME_MISMATCH
OWNERSHIP_TOTAL = 100             # active ownership_pct must sum to this

# category -> allowed (document_type, validity_in_days). None = never expires.
DOCUMENT_SPECS = {
    C.ID_PROOF: [(D.PASSPORT, 3650), (D.NATIONAL_ID, None), (D.DRIVING_LICENSE, 7300)],
    C.ADDRESS_PROOF: [(D.UTILITY_BILL, None), (D.BANK_STATEMENT, None)],
    C.TAX_ID: [(D.TAX_CERTIFICATE, None)],
    C.INCORPORATION_CERT: [(D.CERTIFICATE_OF_INCORPORATION, None)],
    C.BUSINESS_ADDRESS_PROOF: [(D.UTILITY_BILL, None), (D.BANK_STATEMENT, None)],
    C.OWNERSHIP_PROOF: [(D.SHAREHOLDER_REGISTER, 730)],
    C.SOURCE_OF_FUNDS: [(D.BANK_STATEMENT, None), (D.SALARY_SLIP, None)],
}

# The latest verification result decides the document status
DOC_STATUS_FROM_RESULT = {
    VerificationResult.PASS: DocumentStatus.VALID,
    VerificationResult.FAIL: DocumentStatus.INVALID,
    VerificationResult.NAME_MISMATCH: DocumentStatus.INVALID,
    VerificationResult.EXPIRED_DOCUMENT: DocumentStatus.EXPIRED,
}


def risk_level_from_score(score: int) -> RiskLevel:
    for level, (low, high) in RISK_BANDS.items():
        if low <= score <= high:
            return level
    raise ValueError(f"risk score out of range: {score}")


def name_match_score(name_a: str, name_b: str) -> float:
    """Similarity of two names on a 0-100 scale (case-insensitive)."""
    ratio = SequenceMatcher(None, name_a.lower(), name_b.lower()).ratio()
    return round(100 * ratio, 2)

# Allowed lifecycle transitions per entity type, as (old_state, new_state).
# Used by the validation suite now and by the API later.
ALLOWED_TRANSITIONS = {
    "CUSTOMER": {("PENDING", "UNDER_REVIEW"), ("UNDER_REVIEW", "APPROVED"),
                 ("UNDER_REVIEW", "REJECTED"), ("APPROVED", "SUSPENDED"),
                 ("SUSPENDED", "APPROVED"),        # documents were renewed
                 ("REJECTED", "UNDER_REVIEW")},    # customer resubmitted evidence
    "DOCUMENT": {("PENDING", "VALID"), ("PENDING", "INVALID"),
                 ("INVALID", "VALID"), ("VALID", "EXPIRED")},
    "REVIEW_CASE": {("SCHEDULED", "IN_PROGRESS"), ("IN_PROGRESS", "CLOSED")},
    "ALERT": {("OPEN", "ACKNOWLEDGED"), ("OPEN", "RESOLVED"),
              ("ACKNOWLEDGED", "RESOLVED")},
}