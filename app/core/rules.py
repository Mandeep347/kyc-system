from app.core.enums import RiskLevel

# Score Bands.
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

# Expiry alerts fire this many days before a document expires
DEFAULT_LEAD_DAYS = [60, 30, 7]
HIGH_RISK_LEAD_DAYS = [90, 60, 30, 7]

ADDRESS_PROOF_MAX_AGE_DAYS = 90 # address proof must be issued this recently
NAME_MATCH_THRESHOLS = 85 # name match score below this will be name mismatch
OWNERSHIP_TOTAl = 100 # active ownership percentage must be equal to this

def risk_level_from_score(score: int) -> RiskLevel:
    for level, (low, high) in RISK_BANDS.items():
        if low <= score <= high:
            return level
    raise ValueError(f"risk score out of range: {score}")

