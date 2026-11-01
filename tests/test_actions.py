from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core import rules
from app.core.config import settings
from app.core.database import engine, get_db
from app.core.enums import DocumentCategory as C
from app.core.enums import IssueType
from app.main import app
from generator.config import GenConfig
from generator.loader import load_into
from generator.pipeline import build_all

AS_OF = settings.as_of_date
CFG = GenConfig(scale=0.2, as_of=AS_OF)      # 1,600 customers: every situation below occurs
ANALYST = {"X-Role": "ANALYST", "X-User": "analyst_01"}
REVIEWER = {"X-Role": "REVIEWER", "X-User": "reviewer_01"}
ADMIN = {"X-Role": "ADMIN", "X-User": "admin_01"}


def as_customer(cid: str) -> dict:
    return {"X-Role": "CUSTOMER", "X-User": cid}


# ---------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def dataset():
    return build_all(CFG, with_anomalies=True)


@pytest.fixture(scope="module")
def conn(dataset):
    with engine.connect() as c:
        outer = c.begin()
        try:
            load_into(c, dataset.tables)
            yield c
        finally:
            outer.rollback()                  # your real data is never touched


@pytest.fixture
def db(conn):
    nested = conn.begin_nested()              # per-test savepoint
    session = Session(bind=conn)              # its own commits only release a savepoint
    try:
        yield session
    finally:
        session.close()
        if nested.is_active:
            nested.rollback()


@pytest.fixture
def client(db):
    def override():
        yield db
    app.dependency_overrides[get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- helpers

def need(value, what):
    if value is None:
        pytest.skip(f"the generated test data has no {what}")
    return value


def an_individual(dataset):
    return next(c for c in dataset.tables["customers"] if c["customer_type_id"] == "CT-01")


def payload(customer, category, **override):
    doc_type, validity = rules.DOCUMENT_SPECS[C(category)][0]
    issue = AS_OF - timedelta(days=1)
    body = {"document_category": str(category), "document_type": doc_type.value,
            "document_number": f"TST-{customer['customer_id']}-{category}",
            "name_on_document": customer["legal_name"], "issue_date": issue.isoformat(),
            "expiry_date": (issue + timedelta(days=validity)).isoformat() if validity else None}
    body.update(override)
    return body


def post_doc(client, customer, body, headers=ANALYST):
    return client.post(f"/customers/{customer['customer_id']}/documents", json=body, headers=headers)


def submit(client, customer, category, **override):
    r = post_doc(client, customer, payload(customer, category, **override))
    assert r.status_code == 201, r.text
    return r.json()


def verify(client, document_id, decision="PASS", headers=ANALYST):
    return client.post(f"/documents/{document_id}/verify", json={"decision": decision}, headers=headers)


def trail(client, customer_id, entity_type, entity_id):
    body = client.get(f"/customers/{customer_id}/audit-trail",
                      params={"entity_type": entity_type, "limit": 500}).json()
    return [e for e in body["items"] if e["entity_id"] == entity_id]


def remediate_suspended_customer(client, dataset):
    """Finds a SUSPENDED customer whose documents merely expired, and replaces them."""
    for c in dataset.tables["customers"]:
        report = dataset.reports[c["customer_id"]]
        if c["kyc_status"] == "SUSPENDED" and report.missing \
                and set(report.missing.values()) == {"EXPIRED"}:
            for category in report.missing:
                doc = submit(client, c, category)
                assert verify(client, doc["document_id"]).status_code == 201
            return c
    pytest.skip("the generated test data has no suspended customer")


def first_review(dataset, predicate):
    return next((r for r in dataset.tables["review_cases"] if predicate(r)), None)


def first_alert(dataset, status):
    return next((a for a in dataset.tables["alerts"] if a["status"] == status), None)


# ---------------------------------------------------------------- roles

def test_roles_are_enforced(client):
    url = "/documents/DOC-CUS-000001-001/verify"
    assert client.post(url, json={"decision": "PASS"}).status_code == 401                       # no headers
    assert client.post(url, json={"decision": "PASS"},
                       headers={"X-Role": "BOSS", "X-User": "x"}).status_code == 401           # unknown role
    assert client.post(url, json={"decision": "PASS"},
                       headers=as_customer("CUS-000001")).status_code == 403                   # wrong role
    assert client.post("/reviews/REV-CUS-000001-2026/close", json={"resolution": "NO_ISSUES"},
                       headers=ANALYST).status_code == 403


# ---------------------------------------------------------------- documents

def test_customer_submits_own_document_only(client, dataset):
    c = an_individual(dataset)
    cid = c["customer_id"]
    existing = sum(d["customer_id"] == cid for d in dataset.tables["documents"])

    r = post_doc(client, c, payload(c, "ID_PROOF"), headers=as_customer(cid))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "PENDING"
    assert body["document_id"] == f"DOC-{cid}-{existing + 1:03d}"        # next number, no gaps

    assert post_doc(client, c, payload(c, "ID_PROOF"),
                    headers=as_customer("CUS-999999")).status_code == 403   # not their record

    detail = client.get(f"/documents/{body['document_id']}").json()
    assert detail["requirement_ids"]                                         # bridge row was created
    events = trail(client, cid, "DOCUMENT", body["document_id"])
    assert [e["action"] for e in events] == ["CREATED"]
    assert events[0]["actor"] == cid


def test_submission_rules_reject_bad_input(client, dataset):
    c = an_individual(dataset)
    p, addr = payload(c, "ID_PROOF"), payload(c, "ADDRESS_PROOF")
    day = lambda n: (AS_OF + timedelta(days=n)).isoformat()                  # noqa: E731

    assert post_doc(client, c, {**p, "expiry_date": day(-1)}).status_code == 422          # already expired
    assert post_doc(client, c, {**p, "expiry_date": None}).status_code == 422             # passport needs expiry
    assert post_doc(client, c, {**p, "document_type": "UTILITY_BILL"}).status_code == 422 # wrong category
    assert post_doc(client, c, {**p, "issue_date": day(5)}).status_code == 422            # future issue
    assert post_doc(client, c, {**addr, "issue_date": day(-91)}).status_code == 422       # too old
    assert post_doc(client, c, {**addr, "expiry_date": day(100)}).status_code == 422      # bills never expire
    taken = next(d for d in dataset.tables["documents"] if d["customer_id"] != c["customer_id"])
    assert post_doc(client, c, {**p, "document_number": taken["document_number"]}).status_code == 409
    assert post_doc(client, c, {**addr, "issue_date": day(-90)}).status_code == 201       # boundary: 90 days is OK


def test_verify_pass_makes_the_document_valid(client, dataset):
    c = an_individual(dataset)
    doc = submit(client, c, "ID_PROOF")
    r = verify(client, doc["document_id"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert (body["document_status_before"], body["document_status_after"]) == ("PENDING", "VALID")
    assert body["verification"]["result"] == "PASS" and body["verification"]["verified_by"] == "analyst_01"
    assert body["verification"]["name_match_score"] == 100

    detail = client.get(f"/documents/{doc['document_id']}").json()
    assert detail["status"] == "VALID" and len(detail["verifications"]) == 1
    assert [e["action"] for e in trail(client, c["customer_id"], "DOCUMENT", doc["document_id"])] \
        == ["CREATED", "VERIFIED"]

    assert verify(client, doc["document_id"], headers=as_customer(c["customer_id"])).status_code == 403
    assert verify(client, doc["document_id"]).status_code == 409             # VALID -> VALID is not allowed


def test_name_mismatch_is_decided_by_the_system(client, dataset):
    c = an_individual(dataset)
    doc = submit(client, c, "ID_PROOF", name_on_document="Completely Different Name")
    body = verify(client, doc["document_id"], "PASS").json()                 # the analyst says PASS...
    assert body["verification"]["result"] == "NAME_MISMATCH"                 # ...the system disagrees
    assert body["document_status_after"] == "INVALID" and body["note"]
    assert body["verification"]["name_match_score"] < rules.NAME_MATCH_THRESHOLD
    assert verify(client, doc["document_id"], "PASS").status_code == 409     # can never flip to VALID


def test_failed_scan_can_be_redone_and_history_is_kept(client, dataset):
    c = an_individual(dataset)
    doc = submit(client, c, "ID_PROOF")
    assert verify(client, doc["document_id"], "FAIL").json()["document_status_after"] == "INVALID"
    assert verify(client, doc["document_id"], "PASS").json()["document_status_after"] == "VALID"

    detail = client.get(f"/documents/{doc['document_id']}").json()
    assert [v["result"] for v in detail["verifications"]] == ["FAIL", "PASS"]   # nothing overwritten
    events = trail(client, c["customer_id"], "DOCUMENT", doc["document_id"])
    assert [e["new_value"]["status"] for e in events] == ["PENDING", "INVALID", "VALID"]


def test_expired_document_cannot_be_passed(client, dataset):
    expired = next(d for d in dataset.tables["documents"]
                   if d["status"] == "EXPIRED" and d["expiry_date"] < AS_OF)
    assert verify(client, expired["document_id"]).status_code == 409


# ---------------------------------------------------------------- customers

def test_reassess_suspends_a_wrongly_approved_customer(client, dataset):
    wrong = [g["entity_id"] for g in dataset.ground_truth
             if g["issue_type"] == IssueType.APPROVED_MISSING_EVIDENCE.value]
    cid = wrong[0]
    before = client.get("/exceptions", params={"issue_type": "APPROVED_MISSING_EVIDENCE",
                                               "limit": 500}).json()["total"]

    assert client.post(f"/customers/{cid}/reassess", headers=ANALYST).status_code == 403
    body = client.post(f"/customers/{cid}/reassess", headers=REVIEWER).json()
    assert body["changed"] is True and body["steps"] == ["SUSPENDED"]
    assert (body["previous_status"], body["new_status"]) == ("APPROVED", "SUSPENDED")

    last = trail(client, cid, "CUSTOMER", cid)[-1]
    assert last["action"] == "STATUS_CHANGED" and last["actor"] == "reviewer_01"
    assert (last["old_value"]["kyc_status"], last["new_value"]["kyc_status"]) == ("APPROVED", "SUSPENDED")

    after = client.get("/exceptions", params={"issue_type": "APPROVED_MISSING_EVIDENCE",
                                              "limit": 500}).json()["total"]
    assert after == before - 1                                               # the exception is remediated
    assert client.post(f"/customers/{cid}/reassess", headers=REVIEWER).json()["changed"] is False


def test_fixing_the_evidence_gets_a_suspended_customer_approved(client, dataset):
    c = remediate_suspended_customer(client, dataset)
    cid = c["customer_id"]
    body = client.post(f"/customers/{cid}/reassess", headers=REVIEWER).json()
    assert (body["previous_status"], body["new_status"], body["steps"]) == ("SUSPENDED", "APPROVED", ["APPROVED"])

    comp = client.get(f"/customers/{cid}/completeness").json()
    assert comp["is_complete"] and comp["status_consistent"] and comp["stored_kyc_status"] == "APPROVED"
    last = trail(client, cid, "CUSTOMER", cid)[-1]
    assert (last["old_value"]["kyc_status"], last["new_value"]["kyc_status"]) == ("SUSPENDED", "APPROVED")


# ---------------------------------------------------------------- reviews

def test_start_review_rules(client, dataset):
    startable = need(first_review(dataset, lambda r: r["status"] == "SCHEDULED"
                                  and r["scheduled_date"] <= AS_OF), "startable review")
    future = need(first_review(dataset, lambda r: r["status"] == "SCHEDULED"
                               and r["scheduled_date"] > AS_OF), "review that opens later")
    rid = startable["review_id"]

    assert client.post(f"/reviews/{rid}/start", headers=ANALYST).status_code == 403
    body = client.post(f"/reviews/{rid}/start", headers=REVIEWER).json()
    assert body["status"] == "IN_PROGRESS" and body["assigned_to"] == "reviewer_01"
    assert client.post(f"/reviews/{rid}/start", headers=REVIEWER).status_code == 409
    assert client.post(f"/reviews/{future['review_id']}/start", headers=REVIEWER).status_code == 409


def test_close_review_needs_a_resolution_that_matches_the_evidence(client, dataset):
    in_progress = [r for r in dataset.tables["review_cases"] if r["status"] == "IN_PROGRESS"]
    complete = need(next((r for r in in_progress
                          if dataset.reports[r["customer_id"]].is_complete), None), "complete in-progress review")
    incomplete = need(next((r for r in in_progress
                            if not dataset.reports[r["customer_id"]].is_complete), None), "incomplete in-progress review")
    scheduled = need(first_review(dataset, lambda r: r["status"] == "SCHEDULED"), "scheduled review")

    close = lambda rid, body, h=REVIEWER: client.post(f"/reviews/{rid}/close", json=body, headers=h)  # noqa: E731
    assert close(complete["review_id"], {}).status_code == 422                       # resolution is mandatory
    assert close(scheduled["review_id"], {"resolution": "NO_ISSUES"}).status_code == 409   # never started
    assert close(incomplete["review_id"], {"resolution": "NO_ISSUES"}).status_code == 409  # evidence missing

    ok = close(incomplete["review_id"], {"resolution": "REMEDIATION_REQUIRED"})
    assert ok.status_code == 200 and ok.json()["status"] == "CLOSED"
    body = close(complete["review_id"], {"resolution": "NO_ISSUES"}).json()
    assert body["resolution"] == "NO_ISSUES" and body["closed_at"] and not body["is_overdue"]

    last = client.get(f"/reviews/{complete['review_id']}").json()["audit_events"][-1]
    assert last["action"] == "REVIEW_CLOSED" and last["actor"] == "reviewer_01"
    assert close(complete["review_id"], {"resolution": "NO_ISSUES"}).status_code == 409   # already closed


def test_closing_an_overdue_review_resolves_its_alert(client, dataset):
    review_status = {r["review_id"]: r for r in dataset.tables["review_cases"]}
    alert = need(next((a for a in dataset.tables["alerts"]
                       if a["alert_type"] == "REVIEW_OVERDUE" and a["status"] != "RESOLVED"
                       and review_status[a["review_id"]]["status"] == "IN_PROGRESS"), None),
                 "overdue in-progress review with an open alert")
    complete = dataset.reports[alert["customer_id"]].is_complete
    resolution = "NO_ISSUES" if complete else "REMEDIATION_REQUIRED"

    r = client.post(f"/reviews/{alert['review_id']}/close", json={"resolution": resolution}, headers=REVIEWER)
    assert r.status_code == 200, r.text
    detail = client.get(f"/alerts/{alert['alert_id']}").json()
    assert detail["status"] == "RESOLVED"
    assert detail["audit_events"][-1]["new_value"] == {"status": "RESOLVED"}


# ---------------------------------------------------------------- alerts

def test_alert_cannot_be_acknowledged_twice(client, dataset):
    ackd = need(first_alert(dataset, "ACKNOWLEDGED"), "acknowledged alert")
    assert client.post(f"/alerts/{ackd['alert_id']}/acknowledge", headers=ANALYST).status_code == 409


def test_acknowledge_an_open_alert(client, dataset):
    open_alert = need(first_alert(dataset, "OPEN"), "open alert")
    aid = open_alert["alert_id"]
    assert client.post(f"/alerts/{aid}/acknowledge", headers=as_customer("CUS-000001")).status_code == 403
    body = client.post(f"/alerts/{aid}/acknowledge", headers=ANALYST).json()
    assert body["status"] == "ACKNOWLEDGED"
    last = client.get(f"/alerts/{aid}").json()["audit_events"][-1]
    assert last["actor"] == "analyst_01"


def test_resolve_alert(client, dataset):
    ackd = need(first_alert(dataset, "ACKNOWLEDGED"), "acknowledged alert")
    aid = ackd["alert_id"]
    assert client.post(f"/alerts/{aid}/resolve", headers=ANALYST).status_code == 403
    assert client.post(f"/alerts/{aid}/resolve", headers=REVIEWER).json()["status"] == "RESOLVED"
    last = client.get(f"/alerts/{aid}").json()["audit_events"][-1]
    assert (last["old_value"]["status"], last["new_value"]["status"]) == ("ACKNOWLEDGED", "RESOLVED")
    assert client.post(f"/alerts/{aid}/resolve", headers=REVIEWER).status_code == 409


# ---------------------------------------------------------------- history is never rewritten

def test_no_endpoint_edits_or_deletes_history(client, dataset):
    doc = dataset.tables["documents"][0]["document_id"]
    paths = [f"/documents/{doc}", "/customers/CUS-000001", "/customers/CUS-000001/audit-trail",
             f"/alerts/{dataset.tables['alerts'][0]['alert_id']}"]
    for path in paths:
        for method in ("put", "patch", "delete"):
            assert getattr(client, method)(path).status_code == 405, f"{method.upper()} {path}"


def test_database_itself_refuses_to_rewrite_history(db):
    for sql in ("UPDATE verifications SET remarks = 'edited'", "DELETE FROM verifications",
                "UPDATE audit_events SET actor = 'someone'", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError):
            db.execute(text(sql))
        db.rollback()


# ---------------------------------------------------------------- the big one

def test_validation_suite_still_passes_after_a_work_session(client, dataset):
    """Do a realistic mix of writes, then run all ~100 Phase 4 checks. Zero failures expected."""
    # 1) a customer's expired documents are replaced, verified, and the status recovers
    c = remediate_suspended_customer(client, dataset)
    assert client.post(f"/customers/{c['customer_id']}/reassess", headers=REVIEWER).json()["changed"]

    # 2) a failed scan is redone; a name mismatch is recorded
    other = an_individual(dataset)
    redo = submit(client, other, "ADDRESS_PROOF")
    verify(client, redo["document_id"], "FAIL")
    verify(client, redo["document_id"], "PASS")
    bad = submit(client, other, "TAX_ID", name_on_document="Completely Different Name")
    verify(client, bad["document_id"], "PASS")

    # 3) a review is started, then another one closed (resolving its overdue alert)
    startable = first_review(dataset, lambda r: r["status"] == "SCHEDULED" and r["scheduled_date"] <= AS_OF)
    if startable:
        assert client.post(f"/reviews/{startable['review_id']}/start", headers=REVIEWER).status_code == 200
    reviews = {r["review_id"]: r for r in dataset.tables["review_cases"]}
    alert = next((a for a in dataset.tables["alerts"]
                  if a["alert_type"] == "REVIEW_OVERDUE" and a["status"] != "RESOLVED"
                  and reviews[a["review_id"]]["status"] == "IN_PROGRESS"), None)
    if alert:
        resolution = "NO_ISSUES" if dataset.reports[alert["customer_id"]].is_complete else "REMEDIATION_REQUIRED"
        assert client.post(f"/reviews/{alert['review_id']}/close", json={"resolution": resolution},
                           headers=REVIEWER).status_code == 200

    # 4) an alert is resolved (a DIFFERENT one from the alert that step 3 already resolved)
    ackd = next((a for a in dataset.tables["alerts"]
                 if a["status"] == "ACKNOWLEDGED"
                 and (alert is None or a["alert_id"] != alert["alert_id"])), None)
    if ackd:
        assert client.post(f"/alerts/{ackd['alert_id']}/resolve", headers=REVIEWER).status_code == 200

    # now: every expected-zero check must still find nothing
    assert client.get("/admin/validation", headers=as_customer("CUS-000001")).status_code == 403
    rep = client.get("/admin/validation", headers=ADMIN).json()
    assert rep["failures"] == [], rep["failures"]
    assert rep["verdict"] == "PASS" and rep["checks_run"] > 60
    assert "ANOM.expired_but_valid" in rep["anomaly_findings"]