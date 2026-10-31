from datetime import timedelta

from app.core.enums import ReviewStatus as S
from app.core.enums import ReviewTrigger, RiskLevel
from generator import ids
from generator.config import (LATE_CLOSE_MAX_DAYS, REVIEW_IN_PROGRESS_SHARE,
                              REVIEW_LEAD_DAYS, REVIEW_OPEN_PAST_DUE_RATE,
                              REVIEWERS, GenConfig)
from generator.rng import stage_rng
from generator.util import random_date, working_hours


def generate_reviews(cfg: GenConfig, customers: list[dict], profile: list[dict]) -> list[dict]:
    """One periodic review per customer in the 2026 cohort (3,000 at full scale)."""
    rng, _ = stage_rng(cfg.seed, "reviews")
    as_of = cfg.as_of
    rows: list[dict] = []

    for prof in profile:
        if not prof["in_review_cohort"]:
            continue
        cid = prof["customer_id"]
        due = prof["next_review_due"]                       # onboarded + cadence, from 3A
        scheduled = due - timedelta(days=REVIEW_LEAD_DAYS)
        level = RiskLevel(prof["risk_level"])
        closed_at = None

        if due < as_of:
            if rng.random() >= REVIEW_OPEN_PAST_DUE_RATE[level]:
                status = S.CLOSED
                latest_close = min(due + timedelta(days=LATE_CLOSE_MAX_DAYS), as_of)
                closed_at = working_hours(rng, random_date(rng, scheduled, latest_close))
            else:                                           # past due and still open = OVERDUE
                status = S.IN_PROGRESS if rng.random() < REVIEW_IN_PROGRESS_SHARE else S.SCHEDULED
        elif scheduled <= as_of and rng.random() < REVIEW_IN_PROGRESS_SHARE:
            status = S.IN_PROGRESS                          # opened, not due yet
        else:
            status = S.SCHEDULED

        rows.append({
            "review_id": ids.review_id(cid, due.year),
            "customer_id": cid,
            "review_year": due.year,
            "trigger": ReviewTrigger.PERIODIC.value,
            "scheduled_date": scheduled,
            "due_date": due,
            "status": status.value,
            "resolution": None,   # PLACEHOLDER: stage 3D derives it from evidence completeness
            "assigned_to": None if status == S.SCHEDULED else rng.choice(REVIEWERS),
            "closed_at": closed_at,
        })
    return rows