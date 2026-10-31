from datetime import timedelta

from app.core.rules import risk_level_from_score
from generator import ids
from generator.config import RERATE_RATE, REVIEWERS, GenConfig
from generator.rng import stage_rng

INITIAL_REASON = "Initial onboarding assessment"
REASONS_UP = ["Adverse media hit", "Unusual transaction pattern",
              "High-risk jurisdiction exposure"]
REASONS_DOWN = ["Periodic re-assessment", "Enhanced due diligence completed"]


def _different_score(rng, score: int) -> int:
    """An earlier score that is 8-30 points away from the current one."""
    while True:
        prior = max(0, min(100, score + rng.randint(8, 30) * rng.choice([-1, 1])))
        if prior != score:        # clipping at 0 or 100 can land on the same value
            return prior


def generate_risk_ratings(cfg: GenConfig, customers: list[dict],
                          profile: list[dict]) -> list[dict]:
    rng, _ = stage_rng(cfg.seed, "ratings")
    onboarded = {c["customer_id"]: c["onboarded_on"] for c in customers}
    rows: list[dict] = []

    def add(cid, score, reason, rated_by, start, end) -> None:
        rows.append({
            "rating_id": ids.rating_id(len(rows) + 1),
            "customer_id": cid,
            "risk_level": risk_level_from_score(score).value,   # always derived from the score
            "risk_score": score,
            "reason": reason,
            "rated_by": rated_by,
            "effective_from": start,
            "effective_to": end,
        })

    for prof in profile:
        cid, current = prof["customer_id"], prof["risk_score"]
        start = onboarded[cid]
        days_since = (cfg.as_of - start).days

        if days_since >= 60 and rng.random() < RERATE_RATE:
            change = start + timedelta(days=rng.randint(30, days_since))
            prior = _different_score(rng, current)
            add(cid, prior, INITIAL_REASON, "system", start, change - timedelta(days=1))
            reason = rng.choice(REASONS_UP if current > prior else REASONS_DOWN)
            add(cid, current, reason, rng.choice(REVIEWERS), change, None)
        else:
            add(cid, current, INITIAL_REASON, "system", start, None)
    return rows