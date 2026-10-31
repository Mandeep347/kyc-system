from collections import defaultdict
from datetime import date, datetime, time, timedelta

from app.core.rules import DEFAULT_LEAD_DAYS
from generator import ids
from generator.config import ALERT_ACK_AFTER_DAYS, GenConfig

ALERT_TIME = time(6, 0)   # the nightly alert job runs at 06:00


def _doc_severity(kind: str, lead: int | None) -> str:
    if kind == "DOC_EXPIRED" or lead <= 7:
        return "HIGH"
    return "MEDIUM" if lead <= 30 else "LOW"


def _review_severity(days_overdue: int) -> str:
    if days_overdue >= 90:
        return "HIGH"
    return "MEDIUM" if days_overdue >= 30 else "LOW"


def _verification_times(documents: list[dict], verifications: list[dict]):
    """first_pass: doc_id -> when it first passed.
    renewed_at: doc_id -> when a LATER document of the same customer and category first passed."""
    first_pass: dict[str, datetime] = {}
    for v in verifications:
        if v["result"] == "PASS":
            current = first_pass.get(v["document_id"])
            if current is None or v["verified_at"] < current:
                first_pass[v["document_id"]] = v["verified_at"]

    groups = defaultdict(list)
    for d in documents:
        groups[(d["customer_id"], d["document_category"])].append(d)
    renewed_at: dict[str, datetime] = {}
    for group in groups.values():
        for d in group:
            times = [first_pass[o["document_id"]] for o in group
                     if o["issue_date"] > d["issue_date"] and o["document_id"] in first_pass]
            if times:
                renewed_at[d["document_id"]] = min(times)
    return first_pass, renewed_at


def generate_alerts(cfg: GenConfig, documents: list[dict], verifications: list[dict],
                    links: list[dict], requirements: list[dict], reviews: list[dict]):
    """Returns (alert rows, {alert_id: when it was resolved}). No randomness is used."""
    as_of = cfg.as_of
    window_start = date(as_of.year, 1, 1)
    first_pass, renewed_at = _verification_times(documents, verifications)
    req_by_id = {r["requirement_id"]: r for r in requirements}
    leads_by_doc = {l["document_id"]: req_by_id[l["requirement_id"]]["expiry_lead_days"]
                    for l in links}

    pending: list[dict] = []

    # ---- document alerts ----
    for d in documents:
        expiry = d["expiry_date"]
        passed_at = first_pass.get(d["document_id"])
        if expiry is None or passed_at is None:
            continue                       # never verified valid -> nothing to track
        renewed = renewed_at.get(d["document_id"])
        leads = leads_by_doc.get(d["document_id"], DEFAULT_LEAD_DAYS)

        candidates = [(expiry - timedelta(days=lead), "DOC_EXPIRING", lead) for lead in leads]
        if expiry < as_of:
            candidates.append((expiry + timedelta(days=1), "DOC_EXPIRED", None))

        for trigger, kind, lead in candidates:
            created = datetime.combine(trigger, ALERT_TIME)
            if not (window_start <= trigger <= as_of):
                continue
            if created < passed_at:
                continue                   # the document was not verified yet on that day
            if renewed is not None and renewed <= created:
                continue                   # already replaced before the alert would fire
            pending.append({
                "trigger_date": trigger, "alert_type": kind,
                "customer_id": d["customer_id"], "document_id": d["document_id"],
                "review_id": None, "lead_days": lead,
                "severity": _doc_severity(kind, lead), "resolved_at": renewed,
            })

    # ---- review alerts ----
    for r in reviews:
        due, closed_at = r["due_date"], r["closed_at"]
        if due >= as_of:
            continue
        if closed_at is not None and closed_at.date() <= due:
            continue                       # closed on time: never overdue
        trigger = due + timedelta(days=1)
        if trigger < window_start:
            continue
        end = closed_at.date() if closed_at is not None else as_of
        pending.append({
            "trigger_date": trigger, "alert_type": "REVIEW_OVERDUE",
            "customer_id": r["customer_id"], "document_id": None,
            "review_id": r["review_id"], "lead_days": None,
            "severity": _review_severity((end - due).days), "resolved_at": closed_at,
        })

    # ---- deterministic order, then IDs and statuses ----
    pending.sort(key=lambda a: (a["trigger_date"], a["alert_type"], a["customer_id"],
                                a["document_id"] or "", a["review_id"] or "",
                                a["lead_days"] or 0))
    rows, resolved_at = [], {}
    for n, a in enumerate(pending, start=1):
        alert_id = ids.alert_id(n, as_of.year)
        if a["resolved_at"] is not None:
            status = "RESOLVED"
            resolved_at[alert_id] = a["resolved_at"]
        elif (as_of - a["trigger_date"]).days >= ALERT_ACK_AFTER_DAYS:
            status = "ACKNOWLEDGED"
        else:
            status = "OPEN"
        rows.append({
            "alert_id": alert_id,
            "customer_id": a["customer_id"],
            "document_id": a["document_id"],
            "review_id": a["review_id"],
            "alert_type": a["alert_type"],
            "lead_days": a["lead_days"],
            "severity": a["severity"],
            "status": status,
            "trigger_date": a["trigger_date"],
            "created_at": datetime.combine(a["trigger_date"], ALERT_TIME),
        })
    return rows, resolved_at