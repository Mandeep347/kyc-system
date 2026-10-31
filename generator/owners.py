from datetime import timedelta
from decimal import Decimal

from app.core.enums import CustomerTypeCode as T
from generator import ids
from generator.config import (COUNTRY_MIX, FOREIGN_OWNER_RATE, OWNER_CHANGE_RATE,
                              OWNER_COUNT_MIX, PEP_RATE, GenConfig)
from generator.master import TYPE_ID
from generator.rng import stage_rng

UNITS_TOTAL = 10_000   # 100.00% written as hundredths of a percent
MIN_UNITS = 500        # every owner holds at least 5.00%


def split_ownership(rng, n: int) -> list[Decimal]:
    """n percentages, each >= 5%, that sum to EXACTLY 100.00 (integer maths, no float drift)."""
    remaining = UNITS_TOTAL - MIN_UNITS * n
    cuts = sorted(rng.sample(range(1, remaining), n - 1))   # n-1 cut points make n slices
    edges = [0, *cuts, remaining]
    units = [MIN_UNITS + edges[i + 1] - edges[i] for i in range(n)]
    return [Decimal(f"{u // 100}.{u % 100:02d}") for u in units]


def _person(rng, fake, cust: dict, is_company: bool, countries, country_weights):
    # a sole proprietor owns their own business, so the owner IS the customer
    name = f"{fake.first_name()} {fake.last_name()}" if is_company else cust["legal_name"]
    if rng.random() < FOREIGN_OWNER_RATE:
        nationality = rng.choices(countries, country_weights)[0]
    else:
        nationality = cust["country_code"]
    return name, nationality, rng.random() < PEP_RATE


def generate_owners(cfg: GenConfig, customers: list[dict]) -> list[dict]:
    rng, fake = stage_rng(cfg.seed, "owners")
    sizes, size_weights = list(OWNER_COUNT_MIX), list(OWNER_COUNT_MIX.values())
    countries, country_weights = list(COUNTRY_MIX), list(COUNTRY_MIX.values())
    rows: list[dict] = []

    def add(cid, person, pct, start, end=None) -> None:
        name, nationality, is_pep = person
        rows.append({
            "owner_id": ids.owner_id(len(rows) + 1),
            "customer_id": cid,
            "owner_name": name,
            "nationality": nationality,
            "ownership_pct": pct,
            "is_pep": is_pep,
            "effective_from": start,
            "effective_to": end,
        })

    for cust in customers:
        type_id = cust["customer_type_id"]
        if type_id == TYPE_ID[T.INDIVIDUAL]:
            continue                                  # individuals have no beneficial owners
        is_company = type_id == TYPE_ID[T.COMPANY]
        cid, onboarded = cust["customer_id"], cust["onboarded_on"]

        n = rng.choices(sizes, size_weights)[0] if is_company else 1
        percentages = split_ownership(rng, n)

        # some companies change one owner after onboarding (history, not overwrite)
        changed_idx = change_date = None
        days_since = (cfg.as_of - onboarded).days
        if is_company and days_since >= 60 and rng.random() < OWNER_CHANGE_RATE:
            changed_idx = rng.randrange(n)
            change_date = onboarded + timedelta(days=rng.randint(30, days_since))

        for k, pct in enumerate(percentages):
            person = _person(rng, fake, cust, is_company, countries, country_weights)
            if k == changed_idx:
                # old owner leaves the day before; the replacement takes the same stake
                add(cid, person, pct, onboarded, change_date - timedelta(days=1))
                replacement = _person(rng, fake, cust, is_company, countries, country_weights)
                add(cid, replacement, pct, change_date)
            else:
                add(cid, person, pct, onboarded)
    return rows