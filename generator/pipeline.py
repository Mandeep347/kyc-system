from dataclasses import dataclass, field

from generator.config import GenConfig
from generator.customers import generate_customers
from generator.documents import generate_documents, generate_requirement_links
from generator.master import customer_type_rows, requirement_rows
from generator.verifications import derive_document_status, generate_verifications


@dataclass
class Dataset:
    tables: dict[str, list[dict]] = field(default_factory=dict)  # goes to DB and files
    profile: list[dict] = field(default_factory=list)            # internal helper only


def build_all(cfg: GenConfig) -> Dataset:
    ds = Dataset()
    ds.tables["customer_types"] = customer_type_rows()
    ds.tables["kyc_requirements"] = requirement_rows()
    customers, ds.profile = generate_customers(cfg)
    ds.tables["customers"] = customers

    # 3B: evidence
    documents = generate_documents(cfg, customers, ds.profile, ds.tables["kyc_requirements"])
    verifications = generate_verifications(cfg, documents, customers)
    derive_document_status(documents, verifications)       # status comes from verifications
    ds.tables["documents"] = documents
    ds.tables["verifications"] = verifications
    ds.tables["document_requirement_links"] = generate_requirement_links(
        documents, ds.profile, ds.tables["kyc_requirements"])

    # 3C adds: beneficial_owners, risk_ratings, review_cases
    # 3D adds: derived kyc_status, alerts, audit_events
    # 3E adds: anomaly injection + ground truth
    return ds