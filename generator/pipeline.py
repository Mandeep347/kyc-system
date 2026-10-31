from dataclasses import dataclass, field

from generator.config import GenConfig
from generator.customers import generate_customers
from generator.master import customer_type_rows, requirement_rows


@dataclass
class Dataset:
    tables: dict[str, list[dict]] = field(default_factory=dict)  # goes to DB and files
    profile: list[dict] = field(default_factory=list)            # internal helper only


def build_all(cfg: GenConfig) -> Dataset:
    ds = Dataset()
    ds.tables["customer_types"] = customer_type_rows()
    ds.tables["kyc_requirements"] = requirement_rows()
    ds.tables["customers"], ds.profile = generate_customers(cfg)
    # 3B adds: documents, verifications, links
    # 3C adds: beneficial_owners, risk_ratings, review_cases
    # 3D adds: derived statuses, alerts, audit_events
    # 3E adds: anomaly injection + ground truth
    return ds