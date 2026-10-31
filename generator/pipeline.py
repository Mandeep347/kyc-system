from dataclasses import dataclass, field

from generator.alerts import generate_alerts
from generator.anomalies import expected_decisions, inject_anomalies
from generator.audit import generate_audit_events
from generator.config import GenConfig
from generator.customers import generate_customers
from generator.derive import apply_statuses
from generator.documents import generate_documents, generate_requirement_links
from generator.master import customer_type_rows, requirement_rows
from generator.owners import generate_owners
from generator.ratings import generate_risk_ratings
from generator.reviews import generate_reviews
from generator.verifications import derive_document_status, generate_verifications


@dataclass
class Dataset:
    tables: dict[str, list[dict]] = field(default_factory=dict)  # goes to DB and files
    profile: list[dict] = field(default_factory=list)            # internal helper only
    reports: dict = field(default_factory=dict)                  # internal: completeness per customer
    decisions: list[dict] = field(default_factory=list)          # GROUND TRUTH: expected statuses
    ground_truth: list[dict] = field(default_factory=list)       # GROUND TRUTH: planted anomalies


def build_all(cfg: GenConfig, with_anomalies: bool = False) -> Dataset:
    ds = Dataset()
    ds.tables["customer_types"] = customer_type_rows()
    requirements = ds.tables["kyc_requirements"] = requirement_rows()
    customers, ds.profile = generate_customers(cfg)
    ds.tables["customers"] = customers

    # 3B: evidence
    documents = generate_documents(cfg, customers, ds.profile, requirements)
    verifications = generate_verifications(cfg, documents, customers)
    derive_document_status(documents, verifications)
    links = generate_requirement_links(documents, ds.profile, requirements)
    ds.tables["documents"] = documents
    ds.tables["verifications"] = verifications
    ds.tables["document_requirement_links"] = links

    # 3C: ownership, risk history, reviews
    ds.tables["beneficial_owners"] = generate_owners(cfg, customers)
    ratings = ds.tables["risk_ratings"] = generate_risk_ratings(cfg, customers, ds.profile)
    reviews = ds.tables["review_cases"] = generate_reviews(cfg, customers, ds.profile)

    # 3D: derived values
    reports = apply_statuses(cfg, customers, ds.profile, documents, requirements, reviews)
    alerts, alert_resolved_at = generate_alerts(cfg, documents, verifications, links,
                                                requirements, reviews)
    ds.tables["alerts"] = alerts
    ds.reports = reports
    ds.decisions = expected_decisions(customers, reports)     # computed BEFORE any injection

    # 3E: plant anomalies BEFORE the audit trail, so every anomalous state has a history
    if with_anomalies:
        ds.ground_truth = inject_anomalies(cfg, ds.tables, reports)

    ds.tables["audit_events"] = generate_audit_events(
        cfg, customers, documents, ds.tables["verifications"], ratings, reviews,
        alerts, alert_resolved_at, reports)
    return ds