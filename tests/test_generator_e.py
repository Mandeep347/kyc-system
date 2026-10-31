import csv
import hashlib
from collections import Counter, defaultdict
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from app.core.enums import IssueType, VerificationResult
from app.core.rules import DOC_STATUS_FROM_RESULT, NAME_MATCH_THRESHOLD
from app.services.completeness import applicable_requirements, evaluate_completeness
from generator.config import ANOMALY_BASE_COUNTS, GenConfig
from generator.export import export_all
from generator.pipeline import build_all

AS_OF = date(2026, 10, 30)
END_OF_AS_OF = datetime.combine(AS_OF, time.max)
INDIVIDUAL = "CT-01"


@pytest.fixture(scope="module")
def clean():
    return build_all(GenConfig())


@pytest.fixture(scope="module")
def dirty():
    return build_all(GenConfig(), with_anomalies=True)


@pytest.fixture(scope="module")
def starter():
    return build_all(GenConfig(scale=0.01), with_anomalies=True)


def labelled(ds, issue):
    return {g["entity_id"] for g in ds.ground_truth if g["issue_type"] == issue.value}


# ---------- detectors: they see ONLY table data, never the ground truth ----------

def detect_expired_but_valid(t):
    return {d["document_id"] for d in t["documents"]
            if d["status"] == "VALID" and d["expiry_date"] is not None and d["expiry_date"] < AS_OF}


def detect_over_100(t):
    totals = defaultdict(lambda: Decimal(0))
    for o in t["beneficial_owners"]:
        if o["effective_to"] is None:
            totals[o["customer_id"]] += o["ownership_pct"]
    return {cid for cid, total in totals.items() if total > 100}


def detect_missing_ownership(t):
    business = {c["customer_id"] for c in t["customers"] if c["customer_type_id"] != INDIVIDUAL}
    active = {o["customer_id"] for o in t["beneficial_owners"] if o["effective_to"] is None}
    return business - active


def detect_closed_without_resolution(t):
    return {r["review_id"] for r in t["review_cases"]
            if r["status"] == "CLOSED" and r["resolution"] is None}


def detect_duplicates(t):
    holders = defaultdict(set)
    for d in t["documents"]:
        holders[d["document_number"]].add(d["customer_id"])
    shared = {n for n, customers in holders.items() if len(customers) > 1}
    return {d["document_id"] for d in t["documents"] if d["document_number"] in shared}


def detect_approved_missing_evidence(t):
    risk = {r["customer_id"]: r["risk_level"] for r in t["risk_ratings"] if r["effective_to"] is None}
    docs = defaultdict(list)
    for d in t["documents"]:
        docs[d["customer_id"]].append(d)
    found = set()
    for c in t["customers"]:
        if c["kyc_status"] != "APPROVED":
            continue
        reqs = applicable_requirements(t["kyc_requirements"], c["customer_type_id"],
                                       risk[c["customer_id"]], AS_OF)
        if not evaluate_completeness(reqs, docs[c["customer_id"]], AS_OF).is_complete:
            found.add(c["customer_id"])
    return found


DETECTORS = {
    IssueType.EXPIRED_BUT_VALID: detect_expired_but_valid,
    IssueType.APPROVED_MISSING_EVIDENCE: detect_approved_missing_evidence,
    IssueType.OWNERSHIP_OVER_100: detect_over_100,
    IssueType.REVIEW_CLOSED_NO_RESOLUTION: detect_closed_without_resolution,
    IssueType.DUPLICATE_DOCUMENT: detect_duplicates,
    IssueType.MISSING_OWNERSHIP: detect_missing_ownership,
}


@pytest.mark.parametrize("issue", list(DETECTORS))
def test_detector_finds_exactly_the_planted_records(dirty, issue):
    assert DETECTORS[issue](dirty.tables) == labelled(dirty, issue)


@pytest.mark.parametrize("issue", list(DETECTORS))
def test_clean_baseline_has_no_accidental_anomalies(clean, issue):
    assert DETECTORS[issue](clean.tables) == set()


# ---------- ground truth ----------

def test_full_scale_label_counts(dirty):
    expected = {k.value: v for k, v in ANOMALY_BASE_COUNTS.items()}
    expected[IssueType.DUPLICATE_DOCUMENT.value] *= 2      # both documents of a pair are labelled
    assert dict(Counter(g["issue_type"] for g in dirty.ground_truth)) == expected


def test_ground_truth_ids_and_references_resolve(dirty):
    t = dirty.tables
    targets = {"CUSTOMER": {c["customer_id"] for c in t["customers"]},
               "DOCUMENT": {d["document_id"] for d in t["documents"]},
               "REVIEW_CASE": {r["review_id"] for r in t["review_cases"]}}
    gt = dirty.ground_truth
    assert [g["ground_truth_id"] for g in gt] == [f"GT-{n:06d}" for n in range(1, len(gt) + 1)]
    for g in gt:
        IssueType(g["issue_type"])
        assert g["entity_id"] in targets[g["entity_type"]]
        assert g["customer_id"] in targets["CUSTOMER"]


def test_each_customer_carries_at_most_one_anomaly(dirty):
    per_customer = defaultdict(set)
    for g in dirty.ground_truth:
        per_customer[g["customer_id"]].add(g["issue_type"])
    assert all(len(kinds) == 1 for kinds in per_customer.values())


def test_ground_truth_never_enters_the_database_tables(dirty):
    forbidden = {"issue_type", "ground_truth_id", "expected_detection", "details"}
    assert "ground_truth" not in dirty.tables
    for rows in dirty.tables.values():
        if rows:
            assert forbidden.isdisjoint(rows[0])


def test_expected_decisions_differ_from_stored_status_only_for_wrongly_approved(dirty):
    expected = {d["customer_id"]: d["expected_kyc_status"] for d in dirty.decisions}
    stored = {c["customer_id"]: c["kyc_status"] for c in dirty.tables["customers"]}
    wrong = {cid for cid in stored if stored[cid] != expected[cid]}
    assert wrong == labelled(dirty, IssueType.APPROVED_MISSING_EVIDENCE)


# ---------- no collateral damage ----------

def test_only_labelled_rows_changed(clean, dirty):
    c, d = clean.tables, dirty.tables
    for name in ("customer_types", "kyc_requirements", "document_requirement_links",
                 "alerts", "risk_ratings"):
        assert c[name] == d[name], f"{name} should be untouched"

    changed_customers = {a["customer_id"] for a, b in zip(c["customers"], d["customers"]) if a != b}
    assert changed_customers == labelled(dirty, IssueType.APPROVED_MISSING_EVIDENCE)

    changed_reviews = {a["review_id"] for a, b in zip(c["review_cases"], d["review_cases"]) if a != b}
    assert changed_reviews == labelled(dirty, IssueType.REVIEW_CLOSED_NO_RESOLUTION)

    n_expired = len(labelled(dirty, IssueType.EXPIRED_BUT_VALID))
    n_pairs = len(labelled(dirty, IssueType.DUPLICATE_DOCUMENT)) // 2
    changed_docs = {a["document_id"] for a, b in zip(c["documents"], d["documents"]) if a != b}
    assert changed_docs <= labelled(dirty, IssueType.EXPIRED_BUT_VALID) | labelled(
        dirty, IssueType.DUPLICATE_DOCUMENT)
    assert len(changed_docs) == n_expired + n_pairs

    assert len(d["verifications"]) == len(c["verifications"]) + n_expired
    gone = labelled(dirty, IssueType.MISSING_OWNERSHIP)
    removed = sum(1 for o in c["beneficial_owners"] if o["customer_id"] in gone)
    added = len(labelled(dirty, IssueType.OWNERSHIP_OVER_100))
    assert len(d["beneficial_owners"]) == len(c["beneficial_owners"]) + added - removed


# ---------- integrity after injection ----------

def test_verifications_stay_consistent(dirty):
    t = dirty.tables
    vs = t["verifications"]
    assert [v["verification_id"] for v in vs] == [f"VER-{n:06d}" for n in range(1, len(vs) + 1)]
    docs = {d["document_id"]: d for d in t["documents"]}
    for v in vs:
        d = docs[v["document_id"]]
        assert datetime.combine(d["submitted_on"], time.min) <= v["verified_at"] <= END_OF_AS_OF
        assert (v["result"] == "NAME_MISMATCH") == (float(v["name_match_score"]) < NAME_MATCH_THRESHOLD)


def test_document_status_always_equals_latest_verification(dirty):
    latest = {}
    for v in dirty.tables["verifications"]:
        cur = latest.get(v["document_id"])
        if cur is None or v["verified_at"] >= cur["verified_at"]:
            latest[v["document_id"]] = v
    for d in dirty.tables["documents"]:
        v = latest.get(d["document_id"])
        expected = "PENDING" if v is None else DOC_STATUS_FROM_RESULT[VerificationResult(v["result"])].value
        assert d["status"] == expected


def test_owner_ids_and_percentages_stay_valid(dirty):
    owners = dirty.tables["beneficial_owners"]
    assert [o["owner_id"] for o in owners] == [f"BO-{n:06d}" for n in range(1, len(owners) + 1)]
    assert all(0 < o["ownership_pct"] <= 100 for o in owners)


# ---------- audit trail ----------

STATE_KEY = {"CUSTOMER": "kyc_status", "DOCUMENT": "status", "REVIEW_CASE": "status", "ALERT": "status"}
ALLOWED = {
    "CUSTOMER": {("PENDING", "UNDER_REVIEW"), ("UNDER_REVIEW", "APPROVED"),
                 ("UNDER_REVIEW", "REJECTED"), ("APPROVED", "SUSPENDED")},
    "DOCUMENT": {("PENDING", "VALID"), ("PENDING", "INVALID"), ("INVALID", "VALID"),
                 ("VALID", "EXPIRED")},
    "REVIEW_CASE": {("SCHEDULED", "IN_PROGRESS"), ("IN_PROGRESS", "CLOSED")},
    "ALERT": {("OPEN", "ACKNOWLEDGED"), ("OPEN", "RESOLVED"), ("ACKNOWLEDGED", "RESOLVED")},
}


def test_audit_replays_and_only_expired_but_valid_breaks_the_lifecycle(dirty):
    t = dirty.tables
    events = t["audit_events"]
    assert [e["event_id"] for e in events] == [f"AUD-{n:06d}" for n in range(1, len(events) + 1)]
    times = [e["occurred_at"] for e in events]
    assert times == sorted(times) and times[-1] <= END_OF_AS_OF

    current = {
        "CUSTOMER": {c["customer_id"]: c["kyc_status"] for c in t["customers"]},
        "DOCUMENT": {d["document_id"]: d["status"] for d in t["documents"]},
        "REVIEW_CASE": {r["review_id"]: r["status"] for r in t["review_cases"]},
        "ALERT": {a["alert_id"]: a["status"] for a in t["alerts"]},
    }
    timelines = defaultdict(list)
    for e in events:
        timelines[(e["entity_type"], e["entity_id"])].append(e)

    odd = set()
    for etype, finals in current.items():
        key = STATE_KEY[etype]
        for eid, final in finals.items():
            trail = timelines[(etype, eid)]
            assert trail, f"no audit trail for {etype} {eid}"
            state = trail[0]["new_value"][key]
            for e in trail[1:]:
                assert e["old_value"][key] == state
                if (state, e["new_value"][key]) not in ALLOWED[etype]:
                    odd.add((etype, eid))
                state = e["new_value"][key]
            assert state == final, f"{etype} {eid}: trail ends at {state}, record says {final}"
    assert odd == {("DOCUMENT", d) for d in labelled(dirty, IssueType.EXPIRED_BUT_VALID)}


# ---------- reproducibility and export ----------

def test_same_seed_gives_identical_anomalies(starter):
    again = build_all(GenConfig(scale=0.01), with_anomalies=True)
    assert again.tables == starter.tables
    assert again.ground_truth == starter.ground_truth


def test_export_checksums_match_and_ground_truth_is_separate(starter, tmp_path):
    cfg = GenConfig(scale=0.01)
    manifest = export_all(starter, cfg, tmp_path / "a")
    for rel, info in manifest["files"].items():
        data = (tmp_path / "a" / rel).read_bytes()
        assert hashlib.sha256(data).hexdigest() == info["sha256"]
        assert b"\r\n" not in data                               # same bytes on Windows and Linux

    for rel, info in manifest["files"].items():
        if rel.startswith("csv/"):
            with open(tmp_path / "a" / rel, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
            assert len(rows) - 1 == info["rows"]
            assert "issue_type" not in rows[0]                   # no label leaks into input files
    assert "ground_truth/ground_truth.csv" in manifest["files"]
    assert "INSERT INTO customers" in (tmp_path / "a" / "seed.sql").read_text(encoding="utf-8")


def test_two_exports_are_byte_identical(starter, tmp_path):
    cfg = GenConfig(scale=0.01)
    a = export_all(starter, cfg, tmp_path / "a")["files"]
    b = export_all(build_all(cfg, with_anomalies=True), cfg, tmp_path / "b")["files"]
    assert a == b