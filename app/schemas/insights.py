from datetime import date

from pydantic import BaseModel


# ---------- completeness ----------

class MissingItem(BaseModel):
    category: str
    reason: str      # PENDING / EXPIRED / INVALID / NONE


class CompletenessOut(BaseModel):
    customer_id: str
    as_of: date
    customer_type: str
    risk_level: str
    stored_kyc_status: str
    derived_kyc_status: str          # what the rules say, from the evidence
    status_consistent: bool          # stored == derived
    is_complete: bool
    mandatory_total: int
    satisfied: int
    completeness_pct: float
    satisfied_categories: list[str]
    missing: list[MissingItem]


# ---------- exceptions ----------

class ExceptionOut(BaseModel):
    issue_type: str
    entity_type: str
    entity_id: str
    customer_id: str
    rule: str                  # the detection rule that fired
    customer_url: str          # follow these two links to trace the exception
    audit_trail_url: str


class ExceptionSummary(BaseModel):
    as_of: date
    total: int
    by_issue_type: dict[str, int]


class AlertSummary(BaseModel):
    total: int
    by_type: dict[str, dict[str, int]]    # alert_type -> status -> count


# ---------- metrics ----------

class CompletenessMetrics(BaseModel):
    customers_evaluated: int
    complete_customers: int
    incomplete_customers: int
    complete_pct: float
    average_completeness_pct: float
    average_completeness_by_type: dict[str, float]
    missing_by_category: dict[str, int]
    missing_by_reason: dict[str, int]
    approved_but_incomplete: int
    status_disagrees_with_evidence: int


class AlertMetrics(BaseModel):
    total: int
    by_type: dict[str, int]
    by_status: dict[str, int]
    by_severity: dict[str, int]


class ReviewMetrics(BaseModel):
    total: int
    by_status: dict[str, int]
    past_due: int
    overdue: int
    overdue_pct_of_past_due: float


class IssueMetrics(BaseModel):
    issue_type: str
    detector: str
    expected: int
    found: int
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    missed_sample: list[str]
    unexpected_sample: list[str]


class DetectionMetrics(BaseModel):
    ground_truth_available: bool
    issues: list[IssueMetrics] = []
    overall_precision: float | None = None
    overall_recall: float | None = None
    overall_f1: float | None = None


class DecisionMetrics(BaseModel):
    ground_truth_available: bool
    customers_compared: int = 0
    agreeing: int = 0
    accuracy: float | None = None
    disagreeing_sample: list[str] = []


class MetricsOut(BaseModel):
    as_of: date
    row_counts: dict[str, int]
    customers_by_status: dict[str, int]
    customers_by_type: dict[str, int]
    customers_by_risk: dict[str, int]
    documents_by_status: dict[str, int]
    completeness: CompletenessMetrics
    alerts: AlertMetrics
    reviews: ReviewMetrics
    detection: DetectionMetrics
    decisions: DecisionMetrics