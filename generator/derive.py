from collections import defaultdict

from app.services.completeness import (CompletenessReport, applicable_requirements,
                                       derive_kyc_status, derive_review_resolution,
                                       evaluate_completeness)
from generator.config import GenConfig


def apply_statuses(cfg: GenConfig, customers: list[dict], profile: list[dict],
                   documents: list[dict], requirements: list[dict],
                   reviews: list[dict]) -> dict[str, CompletenessReport]:
    """Fills in customers.kyc_status and review_cases.resolution (edits the dicts in place)."""
    docs_by_customer = defaultdict(list)
    for d in documents:
        docs_by_customer[d["customer_id"]].append(d)
    prof = {p["customer_id"]: p for p in profile}

    reports, status = {}, {}
    for c in customers:
        cid = c["customer_id"]
        p = prof[cid]
        reqs = applicable_requirements(requirements, p["customer_type_id"],
                                       p["risk_level"], cfg.as_of)
        reports[cid] = evaluate_completeness(reqs, docs_by_customer[cid], cfg.as_of)
        status[cid] = derive_kyc_status(reports[cid])
        c["kyc_status"] = status[cid].value

    for r in reviews:
        if r["status"] == "CLOSED":
            cid = r["customer_id"]
            r["resolution"] = derive_review_resolution(
                reports[cid], prof[cid]["risk_level"], status[cid]).value
    return reports