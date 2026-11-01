from collections import Counter
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import engine, get_db
from app.core.enums import IssueType
from app.main import app
from generator.config import GenConfig
from generator.export import export_all
from generator.loader import load_into
from generator.pipeline import build_all

AS_OF = settings.as_of_date
CFG = GenConfig(scale=0.05, as_of=AS_OF)     # 400 customers: fast, contains every anomaly type


@pytest.fixture(scope="module")
def dataset():
    return build_all(CFG, with_anomalies=True)


@pytest.fixture(scope="module")
def client(dataset, tmp_path_factory):
    out = tmp_path_factory.mktemp("export")
    export_all(dataset, CFG, out)                    # writes the answer key for /metrics to read
    with pytest.MonkeyPatch.context() as mp, engine.connect() as conn:
        mp.setattr(settings, "ground_truth_dir", str(out / "ground_truth"))
        trans = conn.begin()
        session = Session(bind=conn)
        try:
            load_into(conn, dataset.tables)

            def override():
                yield session

            app.dependency_overrides[get_db] = override
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            session.close()
            trans.rollback()                         # your real data is never touched


def truth_ids(ds, issue: IssueType) -> list[str]:
    return sorted(g["entity_id"] for g in ds.ground_truth if g["issue_type"] == issue.value)


# ---------- basics ----------

def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_unknown_ids_return_404(client):
    for path in ("/customers/CUS-999999", "/documents/DOC-CUS-999999-001",
                 "/alerts/ALT-2026-999999", "/reviews/REV-CUS-999999-2026"):
        assert client.get(path).status_code == 404


def test_customer_list_filters_and_pagination(client, dataset):
    customers = dataset.tables["customers"]
    body = client.get("/customers", params={"limit": 10}).json()
    assert body["total"] == len(customers) and len(body["items"]) == 10

    approved = client.get("/customers", params={"kyc_status": "APPROVED", "limit": 500}).json()
    assert approved["total"] == sum(c["kyc_status"] == "APPROVED" for c in customers)
    assert all(i["kyc_status"] == "APPROVED" for i in approved["items"])

    assert client.get("/customers", params={"limit": 0}).status_code == 422
    detail = client.get("/customers/CUS-000001").json()
    assert detail["customer_id"] == "CUS-000001" and detail["document_count"] > 0


# ---------- completeness = the decision endpoint ----------

def test_completeness_matches_generator_decisions(client, dataset):
    wrong = set(truth_ids(dataset, IssueType.APPROVED_MISSING_EVIDENCE))
    for row in dataset.decisions[::8]:
        body = client.get(f"/customers/{row['customer_id']}/completeness").json()
        assert body["derived_kyc_status"] == row["expected_kyc_status"]
        assert body["completeness_pct"] == pytest.approx(row["completeness_pct"])
        assert body["status_consistent"] == (row["customer_id"] not in wrong)


def test_wrongly_approved_customers_are_flagged(client, dataset):
    ids = truth_ids(dataset, IssueType.APPROVED_MISSING_EVIDENCE)
    assert ids
    for cid in ids:
        body = client.get(f"/customers/{cid}/completeness").json()
        assert body["stored_kyc_status"] == "APPROVED"
        assert not body["is_complete"] and not body["status_consistent"] and body["missing"]


# ---------- documents ----------

def test_expired_but_valid_document_is_exposed(client, dataset):
    ids = truth_ids(dataset, IssueType.EXPIRED_BUT_VALID)
    assert ids
    for doc_id in ids:
        body = client.get(f"/documents/{doc_id}").json()
        assert body["status"] == "VALID" and body["effective_status"] == "EXPIRED"
        assert body["days_to_expiry"] < 0
        last = body["verifications"][-1]
        assert last["result"] == "PASS" and last["verified_at"][:10] > body["expiry_date"]


def test_expiring_documents_window(client, dataset):
    cutoff = AS_OF + timedelta(days=90)
    expected = sum(d["status"] == "VALID" and d["expiry_date"] is not None
                   and AS_OF <= d["expiry_date"] <= cutoff for d in dataset.tables["documents"])
    body = client.get("/documents/expiring", params={"within_days": 90, "limit": 500}).json()
    assert body["total"] == expected
    assert all(0 <= i["days_to_expiry"] <= 90 and i["effective_status"] == "VALID"
               for i in body["items"])


# ---------- owners ----------

def test_owner_reconciliation(client, dataset):
    over = truth_ids(dataset, IssueType.OWNERSHIP_OVER_100)
    missing = truth_ids(dataset, IssueType.MISSING_OWNERSHIP)
    for cid in over:
        body = client.get(f"/customers/{cid}/owners").json()
        assert body["reconciles"] is False and body["active_total"] > 100
    for cid in missing:
        body = client.get(f"/customers/{cid}/owners").json()
        assert body["owners"] == [] and body["reconciles"] is False

    flagged = set(over) | set(missing)
    business = next(c for c in dataset.tables["customers"]
                    if c["customer_type_id"] != "CT-01" and c["customer_id"] not in flagged)
    body = client.get(f"/customers/{business['customer_id']}/owners").json()
    assert body["reconciles"] is True and body["active_total"] == 100

    individual = next(c for c in dataset.tables["customers"] if c["customer_type_id"] == "CT-01")
    body = client.get(f"/customers/{individual['customer_id']}/owners").json()
    assert body["owners_required"] is False and body["reconciles"] is None


# ---------- reviews and alerts ----------

def test_overdue_reviews(client, dataset):
    expected = sum(r["status"] != "CLOSED" and r["due_date"] < AS_OF
                   for r in dataset.tables["review_cases"])
    body = client.get("/reviews/overdue", params={"limit": 500}).json()
    assert body["total"] == expected
    assert all(i["is_overdue"] and i["days_overdue"] > 0 for i in body["items"])
    days = [i["days_overdue"] for i in body["items"]]
    assert days == sorted(days, reverse=True)


def test_alerts_list_summary_and_detail(client, dataset):
    alerts = dataset.tables["alerts"]
    assert client.get("/alerts", params={"limit": 1}).json()["total"] == len(alerts)
    assert client.get("/alerts/summary").json()["total"] == len(alerts)

    expired = client.get("/alerts", params={"alert_type": "DOC_EXPIRED", "limit": 500}).json()
    assert expired["total"] == sum(a["alert_type"] == "DOC_EXPIRED" for a in alerts)
    assert all(i["alert_type"] == "DOC_EXPIRED" for i in expired["items"])

    detail = client.get(f"/alerts/{alerts[0]['alert_id']}").json()
    assert detail["audit_events"][0]["action"] == "ALERT_RAISED"


# ---------- audit trail ----------

def test_audit_trail_is_complete_and_ordered(client, dataset):
    expected = sum(e["customer_id"] == "CUS-000001" for e in dataset.tables["audit_events"])
    body = client.get("/customers/CUS-000001/audit-trail", params={"limit": 500}).json()
    assert body["total"] == expected
    times = [e["occurred_at"] for e in body["items"]]
    assert times == sorted(times)
    assert body["items"][0]["entity_type"] == "CUSTOMER" and body["items"][0]["action"] == "CREATED"


# ---------- exceptions and metrics ----------

def test_exceptions_equal_the_planted_anomalies(client, dataset):
    summary = client.get("/exceptions/summary").json()
    expected = Counter(g["issue_type"] for g in dataset.ground_truth)
    assert {k: v for k, v in summary["by_issue_type"].items() if v} == dict(expected)

    body = client.get("/exceptions", params={"limit": 500}).json()
    assert body["total"] == len(dataset.ground_truth)
    assert {(i["issue_type"], i["entity_id"], i["customer_id"]) for i in body["items"]} == {
        (g["issue_type"], g["entity_id"], g["customer_id"]) for g in dataset.ground_truth}

    only = client.get("/exceptions", params={"issue_type": "OWNERSHIP_OVER_100"}).json()
    assert {i["issue_type"] for i in only["items"]} <= {"OWNERSHIP_OVER_100"}


def test_metrics_report_perfect_detection(client, dataset):
    m = client.get("/metrics").json()
    wrong = len(truth_ids(dataset, IssueType.APPROVED_MISSING_EVIDENCE))
    assert m["row_counts"]["customers"] == len(dataset.tables["customers"])

    det = m["detection"]
    assert det["ground_truth_available"] is True
    assert det["overall_precision"] == 1.0 and det["overall_recall"] == 1.0
    assert all(i["false_positives"] == 0 and i["false_negatives"] == 0 for i in det["issues"])

    assert m["completeness"]["approved_but_incomplete"] == wrong
    assert m["completeness"]["status_disagrees_with_evidence"] == wrong
    assert m["decisions"]["customers_compared"] == len(dataset.tables["customers"])
    assert len(m["decisions"]["disagreeing_sample"]) == min(wrong, 10)