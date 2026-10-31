from datetime import date, datetime, time, timedelta

from app.core import rules
from app.core.enums import DocumentStatus
from app.core.enums import VerificationResult as V
from generator import ids
from generator.config import (FAIL_RESCAN_PASS_RATE, VERIFICATION_FAIL_RATE, GenConfig)
from generator.rng import stage_rng

ANALYSTS = [f"analyst_{i:02d}" for i in range(1, 11)]


def _at(rng, day: date) -> datetime:
    """A working-hours timestamp on the given day."""
    return datetime.combine(day, time(rng.randint(9, 17), rng.randint(0, 59)))


def generate_verifications(cfg: GenConfig, documents: list[dict],
                           customers: list[dict]) -> list[dict]:
    rng, _ = stage_rng(cfg.seed, "verifications")
    legal_names = {c["customer_id"]: c["legal_name"] for c in customers}
    rows: list[dict] = []

    def add(doc_id, result: V, score, by, when, remarks=None) -> None:
        rows.append({
            "verification_id": ids.verification_id(len(rows) + 1),
            "document_id": doc_id,
            "result": result.value,
            "name_match_score": score,
            "verified_by": by,
            "verified_at": when,
            "remarks": remarks,
        })

    for doc in documents:
        score = rules.name_match_score(legal_names[doc["customer_id"]], doc["name_on_document"])

        # analysts check a document 0-5 days after submission
        first_day = doc["submitted_on"] + timedelta(days=rng.randint(0, 5))
        if first_day > cfg.as_of:
            continue   # not checked yet -> the document stays PENDING

        # first check: the outcome follows from the data, not a coin flip
        if score < rules.NAME_MATCH_THRESHOLD:
            result, remarks = V.NAME_MISMATCH, f"Name differs from customer record (score {score})"
        elif rng.random() < VERIFICATION_FAIL_RATE:
            result, remarks = V.FAIL, "Scan illegible or suspected alteration"
        else:
            result, remarks = V.PASS, None
        add(doc["document_id"], result, score, rng.choice(ANALYSTS), _at(rng, first_day), remarks)

        # a FAILed scan can be redone and pass; a NAME_MISMATCH never flips to PASS
        latest = result
        if result == V.FAIL and rng.random() < FAIL_RESCAN_PASS_RATE:
            second_day = first_day + timedelta(days=rng.randint(1, 10))
            if second_day <= cfg.as_of:
                add(doc["document_id"], V.PASS, score, rng.choice(ANALYSTS),
                    _at(rng, second_day), "Re-scan accepted")
                latest = V.PASS

        # the system records the moment a previously valid document expires
        expiry = doc["expiry_date"]
        if latest == V.PASS and expiry is not None and expiry < cfg.as_of:
            add(doc["document_id"], V.EXPIRED_DOCUMENT, score, "system",
                datetime.combine(expiry + timedelta(days=1), time(0, 5)),
                "Automatic expiry check")
    return rows


def derive_document_status(documents: list[dict], verifications: list[dict]) -> None:
    """Sets every document's status from its LATEST verification (edits documents in place)."""
    latest: dict[str, dict] = {}
    for v in verifications:
        current = latest.get(v["document_id"])
        if current is None or v["verified_at"] >= current["verified_at"]:
            latest[v["document_id"]] = v
    for doc in documents:
        v = latest.get(doc["document_id"])
        if v is None:
            doc["status"] = DocumentStatus.PENDING.value
        else:
            doc["status"] = rules.DOC_STATUS_FROM_RESULT[V(v["result"])].value