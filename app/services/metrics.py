from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import Base
from app.models import Alert, Customer, CustomerType, Document, ReviewCase, RiskRating
from app.schemas.insights import (AlertMetrics, CompletenessMetrics, DecisionMetrics,
                                  DetectionMetrics, IssueMetrics, MetricsOut, ReviewMetrics)
from app.services import exceptions as exc_service
from app.services.completeness import derive_kyc_status
from app.services.customer_data import evaluate_all_customers
from app.services.ground_truth import load_expected_decisions, load_truth
from validation.reconcile import compare


def _counts(db: Session, column, *conditions) -> dict[str, int]:
    stmt = select(column, func.count()).group_by(column).order_by(column)
    for cond in conditions:
        stmt = stmt.where(cond)
    return {row[0]: row[1] for row in db.execute(stmt).all()}


def _completeness(db: Session, as_of: date) -> CompletenessMetrics:
    evals = evaluate_all_customers(db, as_of)
    type_code = {r.customer_type_id: r.type_code for r in db.execute(
        select(CustomerType.customer_type_id, CustomerType.type_code))}

    pct_by_type: dict[str, list[float]] = defaultdict(list)
    by_category: Counter = Counter()
    by_reason: Counter = Counter()
    for e in evals:
        pct_by_type[type_code[e.customer_type_id]].append(e.report.pct)
        for category, reason in e.report.missing.items():
            by_category[category] += 1
            by_reason[reason] += 1

    n = len(evals)
    complete = sum(e.report.is_complete for e in evals)
    return CompletenessMetrics(
        customers_evaluated=n,
        complete_customers=complete,
        incomplete_customers=n - complete,
        complete_pct=round(100 * complete / n, 2) if n else 0.0,
        average_completeness_pct=round(sum(e.report.pct for e in evals) / n, 2) if n else 0.0,
        average_completeness_by_type={k: round(sum(v) / len(v), 2)
                                      for k, v in sorted(pct_by_type.items())},
        missing_by_category=dict(sorted(by_category.items())),
        missing_by_reason=dict(sorted(by_reason.items())),
        # both computed from the DATA alone (no ground truth):
        approved_but_incomplete=sum(e.kyc_status == "APPROVED" and not e.report.is_complete
                                    for e in evals),
        status_disagrees_with_evidence=sum(derive_kyc_status(e.report).value != e.kyc_status
                                           for e in evals),
    )


def _reviews(db: Session, as_of: date) -> ReviewMetrics:
    total = db.scalar(select(func.count()).select_from(ReviewCase)) or 0
    past_due = db.scalar(select(func.count()).select_from(ReviewCase)
                         .where(ReviewCase.due_date < as_of)) or 0
    overdue = db.scalar(select(func.count()).select_from(ReviewCase)
                        .where(ReviewCase.due_date < as_of, ReviewCase.status != "CLOSED")) or 0
    return ReviewMetrics(
        total=total, by_status=_counts(db, ReviewCase.status), past_due=past_due, overdue=overdue,
        overdue_pct_of_past_due=round(100 * overdue / past_due, 2) if past_due else 0.0)


def _detection(db: Session, as_of: date) -> DetectionMetrics:
    truth = load_truth()
    if truth is None:
        return DetectionMetrics(ground_truth_available=False)

    issues, tp, fp, fn = [], 0, 0, 0
    for issue, found in exc_service.detect(db, as_of).items():
        rec = compare(set(found), truth.get(issue.value, set()))
        issues.append(IssueMetrics(issue_type=issue.value,
                                   detector=exc_service.PRIMARY_DETECTOR[issue], **rec))
        tp += rec["true_positives"]
        fp += rec["false_positives"]
        fn += rec["false_negatives"]

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return DetectionMetrics(ground_truth_available=True, issues=issues,
                            overall_precision=round(precision, 4),
                            overall_recall=round(recall, 4), overall_f1=round(f1, 4))


def _decisions(db: Session) -> DecisionMetrics:
    expected = load_expected_decisions()
    if expected is None:
        return DecisionMetrics(ground_truth_available=False)
    stored = {cid: status for cid, status in db.execute(
        select(Customer.customer_id, Customer.kyc_status))}
    disagree = sorted(cid for cid, status in expected.items() if stored.get(cid) != status)
    n = len(expected)
    return DecisionMetrics(
        ground_truth_available=True, customers_compared=n, agreeing=n - len(disagree),
        accuracy=round((n - len(disagree)) / n, 4) if n else None,
        disagreeing_sample=disagree[:10])


def compute_metrics(db: Session, as_of: date) -> MetricsOut:
    return MetricsOut(
        as_of=as_of,
        row_counts={t.name: db.scalar(select(func.count()).select_from(t)) or 0
                    for t in Base.metadata.sorted_tables},
        customers_by_status=_counts(db, Customer.kyc_status),
        customers_by_type={row[0]: row[1] for row in db.execute(
            select(CustomerType.type_code, func.count(Customer.customer_id))
            .join(Customer, Customer.customer_type_id == CustomerType.customer_type_id)
            .group_by(CustomerType.type_code).order_by(CustomerType.type_code))},
        customers_by_risk=_counts(db, RiskRating.risk_level, RiskRating.effective_to.is_(None)),
        documents_by_status=_counts(db, Document.status),
        completeness=_completeness(db, as_of),
        alerts=AlertMetrics(
            total=db.scalar(select(func.count()).select_from(Alert)) or 0,
            by_type=_counts(db, Alert.alert_type),
            by_status=_counts(db, Alert.status),
            by_severity=_counts(db, Alert.severity)),
        reviews=_reviews(db, as_of),
        detection=_detection(db, as_of),
        decisions=_decisions(db),
    )