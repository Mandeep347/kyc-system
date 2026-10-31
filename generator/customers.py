import re
from datetime import date, timedelta

from app.core.enums import CustomerTypeCode as T
from app.core.rules import REVIEW_CADENCE_MONTHS, RISK_BANDS, risk_level_from_score
from generator import ids
from generator.config import COUNTRY_MIX, RISK_MIX, GenConfig
from generator.master import TYPE_ID
from generator.rng import stage_rng
from generator.util import add_months, random_date


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")[:60]


def generate_customers(cfg: GenConfig) -> tuple[list[dict], list[dict]]:
    """Returns (customers, profile). The profile is internal and never exported."""
    rng, fake = stage_rng(cfg.seed, "customers")
    n = cfg.n_customers

    # Exact counts of each type, then shuffled so businesses are spread out
    type_codes = (
        [T.COMPANY] * cfg.n_companies
        + [T.SOLE_PROPRIETOR] * cfg.n_sole_props
        + [T.INDIVIDUAL] * (n - cfg.n_businesses)
    )
    rng.shuffle(type_codes)

    # Which customers have their periodic review due in 2026
    cohort = set(rng.sample(range(n), cfg.n_reviews))

    levels = list(RISK_MIX)
    level_weights = list(RISK_MIX.values())
    countries = list(COUNTRY_MIX)
    country_weights = list(COUNTRY_MIX.values())

    customers, profile = [], []
    for i, type_code in enumerate(type_codes):
        cid = ids.customer_id(i + 1)

        # Risk: pick a band, pick a score inside it, then DERIVE the level from the score
        band_level = rng.choices(levels, level_weights)[0]
        score = rng.randint(*RISK_BANDS[band_level])
        level = risk_level_from_score(score)
        cadence = REVIEW_CADENCE_MONTHS[level]
        years = cadence // 12

        # Onboarding date: cohort customers are due for review in 2026, others later
        if i in cohort:
            year = cfg.as_of.year - years
            onboarded = random_date(rng, date(year, 1, 1), date(year, 12, 31))
        else:
            start = date(cfg.as_of.year - years + 1, 1, 1)
            end = cfg.as_of - timedelta(days=7)
            assert start <= end, "as_of date is too early in the year for this logic"
            onboarded = random_date(rng, start, end)

        # Name and the person-vs-business date fields (exactly one is filled)
        if type_code == T.INDIVIDUAL:
            name = f"{fake.first_name()} {fake.last_name()}"
            age = int(rng.triangular(18, 80, 35))
            dob = add_months(onboarded, -12 * age) - timedelta(days=rng.randint(0, 364))
            incorporated = None
        else:
            dob = None
            if type_code == T.COMPANY:
                name = fake.company()
                incorporated = onboarded - timedelta(days=rng.randint(365, 30 * 365))
            else:  # sole proprietor: named after the person
                name = f"{fake.first_name()} {fake.last_name()}"
                incorporated = onboarded - timedelta(days=rng.randint(180, 15 * 365))

        customers.append({
            "customer_id": cid,
            "customer_type_id": TYPE_ID[type_code],
            "legal_name": name,
            "date_of_birth": dob,
            "incorporation_date": incorporated,
            "country_code": rng.choices(countries, country_weights)[0],
            "email": f"{_slug(name)}.{i + 1}@example.com",   # example.com is reserved: no real mailbox
            "onboarded_on": onboarded,
            "kyc_status": "PENDING",   # PLACEHOLDER: stage 3D recomputes it from completeness
        })
        profile.append({
            "customer_id": cid,
            "customer_type_id": TYPE_ID[type_code],
            "risk_score": score,
            "risk_level": level.value,
            "review_cadence_months": cadence,
            "in_review_cohort": i in cohort,
            "next_review_due": add_months(onboarded, cadence),
        })
    return customers, profile