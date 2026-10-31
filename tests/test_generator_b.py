from collections import Counter
from datetime import date, datetime, time

import pytest

from app.core import rules
from generator.config import GenConfig
from generator.pipeline import build_all

AS_OF = date(2026, 10, 30)


@pytest.fixture(scope="module")
def full():
    return build_all(GenConfig())


@pytest.fixture(scope="module")
def starter():
    return build_all(GenConfig(scale=0.01))


def test_document_count_is_exact(full, starter):
    assert len(full.tables["documents"]) == 20000
    assert len(starter.tables["documents"]) == 200


def test_ids_and_foreign_keys_resolve(full):
    t = full.tables
    customer_ids = {c["customer_id"] for c in t["customers"]}
    doc_ids = [d["document_id"] for d in t["documents"]]
    assert len(doc_ids) == len(set(doc_ids))
    for d in t["documents"]:
        assert d["customer_id"] in customer_ids
        assert d["document_id"].startswith(f"DOC-{d['customer_id']}-")

    doc_id_set = set(doc_ids)
    assert len({v["verification_id"] for v in t["verifications"]}) == len(t["verifications"])
    assert all(v["document_id"] in doc_id_set for v in t["verifications"])

    req_ids = {r["requirement_id"] for r in t["kyc_requirements"]}
    pairs = [(l["document_id"], l["requirement_id"]) for l in t["document_requirement_links"]]
    assert len(pairs) == len(set(pairs))
    assert all(d in doc_id_set and r in req_ids for d, r in pairs)


def test_link_category_matches_requirement(full):
    t = full.tables
    docs = {d["document_id"]: d for d in t["documents"]}
    reqs = {r["requirement_id"]: r for r in t["kyc_requirements"]}
    for l in t["document_requirement_links"]:
        assert docs[l["document_id"]]["document_category"] == reqs[l["requirement_id"]]["document_category"]


def test_document_dates_are_ordered(full):
    for d in full.tables["documents"]:
        assert d["submitted_on"] >= d["issue_date"]
        assert d["submitted_on"] <= AS_OF
        if d["expiry_date"] is not None:
            assert d["expiry_date"] > d["issue_date"]


def test_document_numbers_are_unique_in_baseline(full):
    numbers = [d["document_number"] for d in full.tables["documents"]]
    assert len(numbers) == len(set(numbers))


def test_address_proof_age_rule_and_boundary(full):
    ages = [(d["submitted_on"] - d["issue_date"]).days
            for d in full.tables["documents"]
            if d["document_category"] in ("ADDRESS_PROOF", "BUSINESS_ADDRESS_PROOF")]
    assert max(ages) <= rules.ADDRESS_PROOF_MAX_AGE_DAYS
    assert rules.ADDRESS_PROOF_MAX_AGE_DAYS in ages        # boundary case exists


def test_name_match_invariant(full):
    for v in full.tables["verifications"]:
        score = float(v["name_match_score"])
        if v["result"] == "NAME_MISMATCH":
            assert score < rules.NAME_MATCH_THRESHOLD
        else:
            assert score >= rules.NAME_MATCH_THRESHOLD


def test_verification_never_before_submission(full):
    submitted = {d["document_id"]: d["submitted_on"] for d in full.tables["documents"]}
    for v in full.tables["verifications"]:
        assert v["verified_at"] >= datetime.combine(submitted[v["document_id"]], time.min)


def test_baseline_status_is_consistent_with_dates(full):
    for d in full.tables["documents"]:
        if d["status"] == "VALID" and d["expiry_date"] is not None:
            assert d["expiry_date"] >= AS_OF          # no "expired but VALID" in the baseline
        if d["status"] == "EXPIRED":
            assert d["expiry_date"] < AS_OF


def test_all_statuses_appear(full):
    statuses = Counter(d["status"] for d in full.tables["documents"])
    for s in ("VALID", "EXPIRED", "INVALID", "PENDING"):
        assert statuses[s] > 0, f"no {s} documents generated"


def test_same_seed_gives_identical_documents():
    cfg = GenConfig(scale=0.01)
    a, b = build_all(cfg).tables, build_all(cfg).tables
    for name in ("documents", "verifications", "document_requirement_links"):
        assert a[name] == b[name]