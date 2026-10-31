from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.core.enums import AuditAction as A
from app.core.enums import VerificationResult as V
from app.core.rules import DOC_STATUS_FROM_RESULT
from app.services.completeness import CompletenessReport
from generator import ids
from generator.config import ALERT_ACK_AFTER_DAYS, GenConfig
from generator.verifications import ANALYSTS


def _analyst(customer_id: str) -> str:
    """Deterministic analyst for a customer (no randomness)."""
    return ANALYSTS[int(customer_id[-6:]) % len(ANALYSTS)]


def _status_changes(cust: dict, report: CompletenessReport, docs: list[dict], vers_by_doc: dict):
    """[(when, old_status, new_status, actor)]: how this customer reached its final kyc_status."""
    final = cust["kyc_status"]
    if final == "PENDING" or not docs:
        return []
    review_start = datetime.combine(min(d["submitted_on"] for d in docs), time(8, 30))
    changes = [(review_start, "PENDING", "UNDER_REVIEW", "system")]
    if final == "UNDER_REVIEW":
        return changes
    actor = _analyst(cust["customer_id"])

    verifs = [(d, v) for d in docs for v in vers_by_doc[d["document_id"]]]
    first_pass: dict[str, datetime] = {}
    for d, v in verifs:
        if v["result"] == V.PASS:
            cat = d["document_category"]
            first_pass[cat] = min(first_pass.get(cat, v["verified_at"]), v["verified_at"])
    # approved once EVERY mandatory category had passed at least once
    approved_at = max([review_start] + [first_pass[cat] for cat in report.mandatory
                                        if cat in first_pass])

    if final == "REJECTED":
        bad = [v["verified_at"] for d, v in verifs
               if report.missing.get(d["document_category"]) == "INVALID"
               and d["status"] == "INVALID"]
        return changes + [(max([review_start] + bad), "UNDER_REVIEW", "REJECTED", actor)]

    changes.append((approved_at, "UNDER_REVIEW", "APPROVED", actor))
    if final == "SUSPENDED":
        lapses = []
        for cat in report.missing:
            times = [v["verified_at"] for d, v in verifs
                     if d["document_category"] == cat and v["result"] == V.EXPIRED_DOCUMENT]
            if times:
                lapses.append(max(times))
        when = max(min(lapses, default=approved_at), approved_at + timedelta(minutes=1))
        changes.append((when, "APPROVED", "SUSPENDED", "system"))
    return changes


def generate_audit_events(cfg: GenConfig, customers, documents, verifications, ratings,
                          reviews, alerts, alert_resolved_at,
                          reports: dict[str, CompletenessReport]) -> list[dict]:
    """Append-only log. No randomness: every event is derived from the records themselves."""
    events: list[tuple] = []

    def emit(when, cid, etype, eid, action, old, new, actor):
        events.append((when, len(events), cid, etype, eid, action.value, old, new, actor))

    docs_by_cust = defaultdict(list)
    for d in documents:
        docs_by_cust[d["customer_id"]].append(d)
    vers_by_doc = defaultdict(list)
    for v in verifications:
        vers_by_doc[v["document_id"]].append(v)
    for vs in vers_by_doc.values():
        vs.sort(key=lambda v: v["verified_at"])

    # customers: creation, then the status chain
    for c in customers:
        cid = c["customer_id"]
        emit(datetime.combine(c["onboarded_on"], time(8, 0)), cid, "CUSTOMER", cid,
             A.CREATED, None, {"kyc_status": "PENDING"}, "system")
        for when, old, new, actor in _status_changes(c, reports[cid], docs_by_cust[cid], vers_by_doc):
            emit(when, cid, "CUSTOMER", cid, A.STATUS_CHANGED,
                 {"kyc_status": old}, {"kyc_status": new}, actor)

    # documents: creation, then one VERIFIED event per verification row
    for d in documents:
        did, cid = d["document_id"], d["customer_id"]
        emit(datetime.combine(d["submitted_on"], time(8, 0)), cid, "DOCUMENT", did,
             A.CREATED, None, {"status": "PENDING"}, "system")
        status = "PENDING"
        for v in vers_by_doc[did]:
            new = DOC_STATUS_FROM_RESULT[V(v["result"])].value
            emit(v["verified_at"], cid, "DOCUMENT", did, A.VERIFIED,
                 {"status": status},
                 {"status": new, "result": v["result"], "verification_id": v["verification_id"]},
                 v["verified_by"])
            status = new

    # risk ratings: one event per rating row (generation order = oldest first)
    previous: dict[str, dict] = {}
    for r in ratings:
        cid = r["customer_id"]
        old = None
        if cid in previous:
            old = {"risk_level": previous[cid]["risk_level"], "risk_score": previous[cid]["risk_score"]}
        emit(datetime.combine(r["effective_from"], time(10, 0)), cid, "RISK_RATING",
             r["rating_id"], A.RISK_RATED, old,
             {"risk_level": r["risk_level"], "risk_score": r["risk_score"]}, r["rated_by"])
        previous[cid] = r

    # reviews: created by the yearly scheduler, started, closed
    jan_first = date(cfg.as_of.year, 1, 1)
    for r in reviews:
        rid, cid = r["review_id"], r["customer_id"]
        emit(datetime.combine(min(r["scheduled_date"], jan_first), time(2, 0)), cid,
             "REVIEW_CASE", rid, A.CREATED, None,
             {"status": "SCHEDULED", "due_date": r["due_date"].isoformat()}, "system")
        if r["status"] in ("IN_PROGRESS", "CLOSED"):
            emit(datetime.combine(r["scheduled_date"], time(8, 30)), cid, "REVIEW_CASE", rid,
                 A.STATUS_CHANGED, {"status": "SCHEDULED"}, {"status": "IN_PROGRESS"},
                 r["assigned_to"])
        if r["status"] == "CLOSED":
            emit(r["closed_at"], cid, "REVIEW_CASE", rid, A.REVIEW_CLOSED,
                 {"status": "IN_PROGRESS"},
                 {"status": "CLOSED", "resolution": r["resolution"]}, r["assigned_to"])

    # alerts: raised, then acknowledged or resolved
    for a in alerts:
        aid, cid = a["alert_id"], a["customer_id"]
        emit(a["created_at"], cid, "ALERT", aid, A.ALERT_RAISED, None,
             {"status": "OPEN", "alert_type": a["alert_type"], "severity": a["severity"]}, "system")
        if aid in alert_resolved_at:
            emit(alert_resolved_at[aid], cid, "ALERT", aid, A.STATUS_CHANGED,
                 {"status": "OPEN"}, {"status": "RESOLVED"}, "system")
        elif a["status"] == "ACKNOWLEDGED":
            emit(a["created_at"] + timedelta(days=ALERT_ACK_AFTER_DAYS), cid, "ALERT", aid,
                 A.STATUS_CHANGED, {"status": "OPEN"}, {"status": "ACKNOWLEDGED"}, _analyst(cid))

    # chronological order; insertion order breaks ties. IDs are numbered AFTER sorting.
    events.sort(key=lambda e: (e[0], e[1]))
    return [{
        "event_id": ids.audit_id(n),
        "customer_id": cid, "entity_type": etype, "entity_id": eid, "action": action,
        "old_value": old, "new_value": new, "actor": actor, "occurred_at": when,
    } for n, (when, _, cid, etype, eid, action, old, new, actor) in enumerate(events, start=1)]