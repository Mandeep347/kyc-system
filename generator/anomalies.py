"""Stage 3E: plant labelled anomalies into the clean baseline and write the answer key."""
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from app.core.enums import CustomerTypeCode as T
from app.core.enums import IssueType as Issue
from app.core.enums import VerificationResult as V
from app.services.completeness import CompletenessReport, derive_kyc_status
from generator import ids
from generator.config import ANOMALY_BASE_COUNTS, GenConfig
from generator.master import TYPE_ID
from generator.rng import stage_rng
from generator.util import random_date, working_hours

# The rule a detector should use to find each issue (documentation, stored in ground truth)
DETECTION_RULES = {
    Issue.EXPIRED_BUT_VALID: "documents.status = 'VALID' AND documents.expiry_date < as_of_date",
    Issue.APPROVED_MISSING_EVIDENCE:
        "customers.kyc_status = 'APPROVED' AND completeness(customer, as_of_date) < 100%",
    Issue.OWNERSHIP_OVER_100:
        "SUM(ownership_pct) over ACTIVE beneficial_owners (effective_to IS NULL) > 100",
    Issue.REVIEW_CLOSED_NO_RESOLUTION: "review_cases.status = 'CLOSED' AND resolution IS NULL",
    Issue.DUPLICATE_DOCUMENT: "same document_number on documents of 2 or more different customers",
    Issue.MISSING_OWNERSHIP:
        "business customer (requires_beneficial_owner) with no ACTIVE beneficial_owners",
}

# Document types that carry an identity-style number worth duplicating
DUPLICABLE_TYPES = ("DRIVING_LICENSE", "NATIONAL_ID", "PASSPORT", "TAX_CERTIFICATE")


def _count(cfg: GenConfig, issue: Issue) -> int:
    return max(1, round(ANOMALY_BASE_COUNTS[issue] * cfg.scale))


def _take(rng, pool: list[dict], k: int, used: set[str]) -> list[dict]:
    """Up to k items from the pool: at most one per customer, none from customers already used."""
    pool = list(pool)
    rng.shuffle(pool)
    chosen = []
    for item in pool:
        if len(chosen) == k:
            break
        if item["customer_id"] in used:
            continue
        used.add(item["customer_id"])
        chosen.append(item)
    return chosen


def _renumber(rows: list[dict], key: str, make_id) -> list[dict]:
    for n, row in enumerate(rows, start=1):
        row[key] = make_id(n)
    return rows


def inject_anomalies(cfg: GenConfig, tables: dict[str, list[dict]],
                     reports: dict[str, CompletenessReport]) -> list[dict]:
    """Edits `tables` in place and returns the ground-truth rows."""
    rng, fake = stage_rng(cfg.seed, "anomalies")
    as_of = cfg.as_of
    customers, documents = tables["customers"], tables["documents"]
    verifications, reviews = tables["verifications"], tables["review_cases"]
    owners = tables["beneficial_owners"]

    docs_by_customer = defaultdict(list)
    for d in documents:
        docs_by_customer[d["customer_id"]].append(d)
    businesses = [c for c in customers if c["customer_type_id"] != TYPE_ID[T.INDIVIDUAL]]

    used: set[str] = set()      # customers already carrying an anomaly
    truth: list[dict] = []

    def label(issue: Issue, entity_type: str, entity_id: str, customer_id: str, details: str):
        truth.append({
            "ground_truth_id": ids.ground_truth_id(len(truth) + 1),
            "issue_type": issue.value,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "customer_id": customer_id,
            "expected_detection": DETECTION_RULES[issue],
            "details": details,
        })

    # ---- 1) customer APPROVED although mandatory evidence is missing ----
    closed_review_customers = {r["customer_id"] for r in reviews if r["status"] == "CLOSED"}
    pool = [c for c in customers
            if c["kyc_status"] in ("UNDER_REVIEW", "SUSPENDED", "REJECTED")
            and docs_by_customer[c["customer_id"]]
            and c["customer_id"] not in closed_review_customers]
    for c in _take(rng, pool, _count(cfg, Issue.APPROVED_MISSING_EVIDENCE), used):
        report = reports[c["customer_id"]]
        assert not report.is_complete
        missing = "; ".join(f"{cat}={why}" for cat, why in sorted(report.missing.items()))
        label(Issue.APPROVED_MISSING_EVIDENCE, "CUSTOMER", c["customer_id"], c["customer_id"],
              f"status {c['kyc_status']} -> APPROVED while missing: {missing}")
        c["kyc_status"] = "APPROVED"

    # ---- 2) expired document marked VALID (an analyst re-passes it after expiry) ----
    score_of = {v["document_id"]: v["name_match_score"] for v in verifications}
    cutoff = as_of - timedelta(days=2)
    pool = [d for d in documents if d["status"] == "EXPIRED" and d["expiry_date"] <= cutoff]
    new_verifications = []
    for d in _take(rng, pool, _count(cfg, Issue.EXPIRED_BUT_VALID), used):
        day = random_date(rng, d["expiry_date"] + timedelta(days=2), as_of)
        new_verifications.append({
            "verification_id": None,   # assigned below
            "document_id": d["document_id"],
            "result": V.PASS.value,
            "name_match_score": score_of[d["document_id"]],
            "verified_by": f"analyst_{rng.randint(1, 10):02d}",
            "verified_at": working_hours(rng, day),
            "remarks": "Manual re-verification",
        })
        label(Issue.EXPIRED_BUT_VALID, "DOCUMENT", d["document_id"], d["customer_id"],
              f"{d['document_type']} expired on {d['expiry_date']} but status is VALID")
        d["status"] = "VALID"
    # keep each document's rows together, then renumber so injected rows don't stand out by ID
    tables["verifications"] = _renumber(
        sorted(verifications + new_verifications, key=lambda v: v["document_id"]),
        "verification_id", ids.verification_id)

    # ---- 3) ownership over 100%: an extra active owner is ADDED (history is not edited) ----
    active_total = defaultdict(lambda: Decimal(0))
    owner_rows = Counter(o["customer_id"] for o in owners)
    for o in owners:
        if o["effective_to"] is None:
            active_total[o["customer_id"]] += o["ownership_pct"]
    extras = []
    for c in _take(rng, businesses, _count(cfg, Issue.OWNERSHIP_OVER_100), used):
        units = rng.randint(500, 3000)                       # +5.00 to +30.00 percentage points
        extra = Decimal(f"{units // 100}.{units % 100:02d}")
        extras.append({
            "owner_id": None,                                # assigned below
            "customer_id": c["customer_id"],
            "owner_name": f"{fake.first_name()} {fake.last_name()}",
            "nationality": c["country_code"],
            "ownership_pct": extra,
            "is_pep": False,
            "effective_from": random_date(rng, c["onboarded_on"], as_of),
            "effective_to": None,
        })
        label(Issue.OWNERSHIP_OVER_100, "CUSTOMER", c["customer_id"], c["customer_id"],
              f"active ownership totals {active_total[c['customer_id']] + extra}% "
              f"(extra owner holding {extra}%)")

    # ---- 4) review closed without a resolution ----
    pool = [r for r in reviews if r["status"] == "CLOSED"]
    for r in _take(rng, pool, _count(cfg, Issue.REVIEW_CLOSED_NO_RESOLUTION), used):
        label(Issue.REVIEW_CLOSED_NO_RESOLUTION, "REVIEW_CASE", r["review_id"], r["customer_id"],
              f"closed on {r['closed_at'].date()}, resolution was {r['resolution']}, now empty")
        r["resolution"] = None

    # ---- 5) the same document number used by two unrelated customers ----
    by_type = defaultdict(list)
    for d in documents:
        if d["document_type"] in DUPLICABLE_TYPES:
            by_type[d["document_type"]].append(d)
    for name in sorted(by_type):
        rng.shuffle(by_type[name])
    pairs = 0
    while pairs < _count(cfg, Issue.DUPLICATE_DOCUMENT):
        options = {}
        for name in sorted(by_type):
            free = [d for d in by_type[name] if d["customer_id"] not in used]
            if len({d["customer_id"] for d in free}) >= 2:
                options[name] = free
        if not options:
            break
        free = options[rng.choice(sorted(options))]
        a = free[0]
        b = next(d for d in free if d["customer_id"] != a["customer_id"])
        b["document_number"] = a["document_number"]
        used.update((a["customer_id"], b["customer_id"]))
        for me, other in ((a, b), (b, a)):
            label(Issue.DUPLICATE_DOCUMENT, "DOCUMENT", me["document_id"], me["customer_id"],
                  f"document_number {a['document_number']} also used by "
                  f"{other['document_id']} ({other['customer_id']})")
        pairs += 1

    # ---- 6) business with no ownership records at all ----
    gone = set()
    for c in _take(rng, businesses, _count(cfg, Issue.MISSING_OWNERSHIP), used):
        gone.add(c["customer_id"])
        label(Issue.MISSING_OWNERSHIP, "CUSTOMER", c["customer_id"], c["customer_id"],
              f"{owner_rows[c['customer_id']]} ownership row(s) removed")

    # rebuild owners: drop the removed ones, slot extras in after their customer, renumber
    rebuilt = sorted([o for o in owners if o["customer_id"] not in gone] + extras,
                     key=lambda o: o["customer_id"])      # stable: baseline order is kept
    tables["beneficial_owners"] = _renumber(rebuilt, "owner_id", ids.owner_id)
    return truth


def expected_decisions(customers: list[dict],
                       reports: dict[str, CompletenessReport]) -> list[dict]:
    """Ground truth: the kyc_status the RULES say each customer should have."""
    rows = []
    for c in customers:
        rep = reports[c["customer_id"]]
        rows.append({
            "customer_id": c["customer_id"],
            "expected_kyc_status": derive_kyc_status(rep).value,
            "mandatory_total": rep.total,
            "mandatory_satisfied": rep.satisfied,
            "completeness_pct": rep.pct,
            "missing_categories": ";".join(f"{k}={v}" for k, v in sorted(rep.missing.items())),
        })
    return rows