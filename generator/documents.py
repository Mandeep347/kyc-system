import string
from collections import defaultdict
from datetime import date, timedelta
from itertools import count

from app.core import rules
from app.core.enums import DocumentCategory as C
from app.core.enums import DocumentType as D
from generator import ids
from generator.config import (ADDRESS_BOUNDARY_RATE, NAME_VARIANT_RATE,
                              OPTIONAL_SUBMISSION_RATE, RENEWAL_RATE, GenConfig)
from generator.rng import stage_rng
from generator.util import add_months

ADDRESS_CATEGORIES = (C.ADDRESS_PROOF, C.BUSINESS_ADDRESS_PROOF)


# ---------- small helpers ----------

def _letters(rng, k: int) -> str:
    return "".join(rng.choices(string.ascii_uppercase, k=k))


def _digits(rng, k: int) -> str:
    return "".join(rng.choices(string.digits, k=k))


NUMBER_MAKERS = {
    D.PASSPORT: lambda r: _letters(r, 1) + _digits(r, 7),
    D.NATIONAL_ID: lambda r: _digits(r, 12),
    D.DRIVING_LICENSE: lambda r: "DL-" + _digits(r, 13),
    D.UTILITY_BILL: lambda r: "UB-" + _digits(r, 10),
    D.BANK_STATEMENT: lambda r: "BS-" + _digits(r, 10),
    D.TAX_CERTIFICATE: lambda r: _letters(r, 5) + _digits(r, 4) + _letters(r, 1),
    D.CERTIFICATE_OF_INCORPORATION:
        lambda r: "U" + _digits(r, 5) + _letters(r, 2) + _digits(r, 4) + "PTC" + _digits(r, 6),
    D.SHAREHOLDER_REGISTER: lambda r: "SR-" + _digits(r, 8),
    D.SALARY_SLIP: lambda r: "SS-" + _digits(r, 10),
}


def _unique_number(rng, doc_type: D, taken: set[str]) -> str:
    """Document numbers are unique in the baseline. Duplicates are injected
    on purpose in stage 3E, so accidental ones would be false 'anomalies'."""
    while True:
        number = NUMBER_MAKERS[doc_type](rng)
        if number not in taken:
            taken.add(number)
            return number


def _is_business(cust: dict) -> bool:
    return cust["date_of_birth"] is None


def _name_on_document(rng, legal_name: str) -> str:
    """Usually the exact name; sometimes a realistic variant."""
    if rng.random() >= NAME_VARIANT_RATE:
        return legal_name
    kind = rng.choice(["typo", "initial", "reversed", "truncated"])
    parts = legal_name.split()
    if kind == "typo" and len(legal_name) > 3:
        i = rng.randrange(len(legal_name) - 1)
        chars = list(legal_name)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]   # swap two neighbours
        return "".join(chars)
    if kind == "initial" and len(parts) > 1:
        return f"{parts[0][0]}. {' '.join(parts[1:])}"
    if kind == "reversed" and len(parts) > 1:
        return " ".join(reversed(parts))
    if kind == "truncated" and len(parts) > 1:
        return " ".join(parts[:-1])
    return legal_name


def _earliest_issue(cust: dict, category: C) -> date | None:
    """A document cannot be issued before the person is 18 or the business exists."""
    if cust["date_of_birth"] is not None:
        return add_months(cust["date_of_birth"], 18 * 12)
    if category != C.ID_PROOF:       # a sole proprietor's personal ID may predate the business
        return cust["incorporation_date"]
    return None


def _original(rng, cust: dict, category: C, as_of: date):
    """Pick a document type and the submission/issue dates for a first submission."""
    specs = rules.DOCUMENT_SPECS[category]
    if _is_business(cust):
        specs = [s for s in specs if s[0] != D.SALARY_SLIP]
    doc_type, validity = rng.choice(specs)
    submitted = min(as_of, cust["onboarded_on"] + timedelta(days=rng.randint(0, 14)))

    if category == C.INCORPORATION_CERT:
        return doc_type, validity, submitted, cust["incorporation_date"]

    if category in ADDRESS_CATEGORIES:
        limit = rules.ADDRESS_PROOF_MAX_AGE_DAYS
        age = limit if rng.random() < ADDRESS_BOUNDARY_RATE else rng.randint(0, limit - 1)
    elif category == C.ID_PROOF:
        # at least 90 days of validity must remain on the day it is submitted
        age = rng.randint(0, (validity - 90) if validity else 3650)
    elif category == C.TAX_ID:
        age = rng.randint(0, 1825)
    else:  # ownership proof, source of funds
        age = rng.randint(0, 180)
    issue = submitted - timedelta(days=age)

    earliest = _earliest_issue(cust, category)
    if earliest is not None and issue < earliest:
        issue = earliest
    return doc_type, validity, submitted, issue


def _build_row(rng, cust, category: C, doc_type: D, validity, submitted, issue, taken) -> dict:
    return {
        "document_id": None,   # assigned at the end, once the order is final
        "customer_id": cust["customer_id"],
        "document_category": category.value,
        "document_type": doc_type.value,
        "document_number": _unique_number(rng, doc_type, taken),
        "name_on_document": _name_on_document(rng, cust["legal_name"]),
        "issue_date": issue,
        "expiry_date": issue + timedelta(days=validity) if validity else None,
        "status": "PENDING",   # PLACEHOLDER: derived from verifications afterwards
        "submitted_on": submitted,
    }


# ---------- the stage ----------

def generate_documents(cfg: GenConfig, customers: list[dict], profile: list[dict],
                       requirements: list[dict]) -> list[dict]:
    rng, _ = stage_rng(cfg.seed, "documents")
    as_of = cfg.as_of
    by_id = {c["customer_id"]: c for c in customers}

    reqs = defaultdict(list)                       # (customer type, risk) -> requirements
    for r in requirements:
        reqs[(r["customer_type_id"], r["risk_level"])].append(r)

    taken: set[str] = set()
    plans: list[dict] = []                         # every planned document, with its tier
    seq = count()

    def add_plan(tier: str, row: dict, validity) -> None:
        plans.append({"seq": next(seq), "tier": tier, "validity": validity, "row": row})

    # 1) first submissions: mandatory always, optional sometimes
    for cust, prof in zip(customers, profile):
        for req in reqs[(prof["customer_type_id"], prof["risk_level"])]:
            if not req["is_mandatory"] and rng.random() >= OPTIONAL_SUBMISSION_RATE:
                continue
            tier = "mandatory" if req["is_mandatory"] else "optional"
            category = C(req["document_category"])
            doc_type, validity, submitted, issue = _original(rng, cust, category, as_of)
            row = _build_row(rng, cust, category, doc_type, validity, submitted, issue, taken)
            add_plan(tier, row, validity)

    # 2) renewals of documents that expired before the as-of date
    for p in list(plans):                          # snapshot: we add to plans inside the loop
        row = p["row"]
        expiry = row["expiry_date"]
        if expiry is None or expiry >= as_of or rng.random() >= RENEWAL_RATE:
            continue
        cust = by_id[row["customer_id"]]
        issue = min(as_of, expiry + timedelta(days=rng.randint(-14, 30)))
        submitted = min(as_of, issue + timedelta(days=rng.randint(0, 10)))
        new_row = _build_row(rng, cust, C(row["document_category"]),
                             D(row["document_type"]), p["validity"], submitted, issue, taken)
        add_plan("renewal", new_row, p["validity"])

    # 3a) too many documents? drop optional first, then renewals, then mandatory
    excess = len(plans) - cfg.n_documents
    for tier in ("optional", "renewal", "mandatory"):
        if excess <= 0:
            break
        pool = [i for i, p in enumerate(plans) if p["tier"] == tier]
        rng.shuffle(pool)
        drop = set(pool[:excess])
        excess -= len(drop)
        plans[:] = [p for i, p in enumerate(plans) if i not in drop]

    # 3b) too few? customers volunteer an extra source-of-funds document
    shortfall = cfg.n_documents - len(plans)
    if shortfall > 0:
        has_sof = {p["row"]["customer_id"] for p in plans
                   if p["row"]["document_category"] == C.SOURCE_OF_FUNDS.value}
        candidates = [i for i, c in enumerate(customers) if c["customer_id"] not in has_sof]
        if shortfall > len(candidates):
            raise ValueError(f"cannot reach {cfg.n_documents} documents: "
                             f"{shortfall} short, only {len(candidates)} candidates")
        for i in rng.sample(candidates, shortfall):
            cust = customers[i]
            doc_type, validity, submitted, issue = _original(rng, cust, C.SOURCE_OF_FUNDS, as_of)
            row = _build_row(rng, cust, C.SOURCE_OF_FUNDS, doc_type, validity,
                             submitted, issue, taken)
            add_plan("extra", row, validity)

    # 4) final order and IDs: per customer, oldest submission first -> DOC-<cust>-001, 002...
    plans.sort(key=lambda p: (p["row"]["customer_id"], p["row"]["submitted_on"], p["seq"]))
    counters: dict[str, int] = defaultdict(int)
    documents = []
    for p in plans:
        row = p["row"]
        counters[row["customer_id"]] += 1
        row["document_id"] = ids.document_id(row["customer_id"], counters[row["customer_id"]])
        documents.append(row)
    return documents


def generate_requirement_links(documents: list[dict], profile: list[dict],
                               requirements: list[dict]) -> list[dict]:
    """Bridge table: which requirement does each document count towards?"""
    prof_by_cust = {p["customer_id"]: p for p in profile}
    req_by_key = {(r["customer_type_id"], r["risk_level"], r["document_category"]): r
                  for r in requirements}
    rows = []
    for doc in documents:
        prof = prof_by_cust[doc["customer_id"]]
        req = req_by_key.get((prof["customer_type_id"], prof["risk_level"],
                              doc["document_category"]))
        if req is None:
            continue   # a voluntary extra document that no rule asks for
        rows.append({
            "link_id": ids.link_id(len(rows) + 1),
            "document_id": doc["document_id"],
            "requirement_id": req["requirement_id"],
            "linked_on": max(doc["submitted_on"], req["effective_from"]),
        })
    return rows