from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

import pytest

from app.core.rules import risk_level_from_score
from generator.config import GenConfig
from generator.pipeline import build_all

AS_OF = date(2026, 10, 30)
INDIVIDUAL, SOLE_PROP = "CT-01", "CT-02"


@pytest.fixture(scope="module")
def full():
    return build_all(GenConfig())


@pytest.fixture(scope="module")
def starter():
    return build_all(GenConfig(scale=0.01))


# ---------- beneficial owners ----------

def test_only_businesses_have_owners_and_all_are_covered(full):
    t = full.tables
    businesses = {c["customer_id"] for c in t["customers"] if c["customer_type_id"] != INDIVIDUAL}
    assert {o["customer_id"] for o in t["beneficial_owners"]} == businesses
    assert len(businesses) == 1000


def test_owner_ids_unique_and_foreign_keys_resolve(full):
    t = full.tables
    customer_ids = {c["customer_id"] for c in t["customers"]}
    owner_ids = [o["owner_id"] for o in t["beneficial_owners"]]
    assert len(owner_ids) == len(set(owner_ids))
    assert all(o["customer_id"] in customer_ids for o in t["beneficial_owners"])


def test_active_ownership_sums_to_exactly_100(full):
    sums = defaultdict(lambda: Decimal(0))
    for o in full.tables["beneficial_owners"]:
        assert Decimal(0) < o["ownership_pct"] <= Decimal(100)
        if o["effective_to"] is None:
            sums[o["customer_id"]] += o["ownership_pct"]
    assert len(sums) == 1000
    assert all(total == Decimal(100) for total in sums.values())


def test_sole_proprietor_is_the_single_100_percent_owner(full):
    t = full.tables
    names = {c["customer_id"]: c["legal_name"] for c in t["customers"]
             if c["customer_type_id"] == SOLE_PROP}
    owners = defaultdict(list)
    for o in t["beneficial_owners"]:
        if o["customer_id"] in names:
            owners[o["customer_id"]].append(o)
    for cid, name in names.items():
        assert len(owners[cid]) == 1
        assert owners[cid][0]["ownership_pct"] == Decimal(100)
        assert owners[cid][0]["owner_name"] == name


def test_owner_dates_and_history_exist(full):
    t = full.tables
    onboarded = {c["customer_id"]: c["onboarded_on"] for c in t["customers"]}
    for o in t["beneficial_owners"]:
        assert o["effective_from"] >= onboarded[o["customer_id"]]
        if o["effective_to"] is not None:
            assert o["effective_from"] <= o["effective_to"] < AS_OF
    assert any(o["effective_to"] is not None for o in t["beneficial_owners"])


# ---------- risk ratings ----------

def test_exactly_one_current_rating_per_customer(full):
    t = full.tables
    current = [r for r in t["risk_ratings"] if r["effective_to"] is None]
    assert len(current) == len(t["customers"])
    assert len({r["customer_id"] for r in current}) == len(t["customers"])


def test_current_rating_matches_profile(full):
    prof = {p["customer_id"]: p for p in full.profile}
    for r in full.tables["risk_ratings"]:
        if r["effective_to"] is None:
            p = prof[r["customer_id"]]
            assert (r["risk_score"], r["risk_level"]) == (p["risk_score"], p["risk_level"])


def test_rating_level_is_derived_from_score(full):
    for r in full.tables["risk_ratings"]:
        assert risk_level_from_score(r["risk_score"]).value == r["risk_level"]


def test_rating_periods_are_contiguous_and_never_overlap(full):
    t = full.tables
    onboarded = {c["customer_id"]: c["onboarded_on"] for c in t["customers"]}
    by_customer = defaultdict(list)
    for r in t["risk_ratings"]:
        by_customer[r["customer_id"]].append(r)
    for cid, ratings in by_customer.items():
        ratings.sort(key=lambda r: r["effective_from"])
        assert ratings[0]["effective_from"] == onboarded[cid]
        for older, newer in zip(ratings, ratings[1:]):
            assert older["effective_to"] is not None
            assert older["effective_to"] < newer["effective_from"]
    assert any(len(rs) > 1 for rs in by_customer.values())   # re-rating history exists


# ---------- review cases ----------

def test_review_volume_ids_and_cohort(full, starter):
    assert len(full.tables["review_cases"]) == 3000
    assert len(starter.tables["review_cases"]) == 30
    cohort = {p["customer_id"] for p in full.profile if p["in_review_cohort"]}
    reviews = full.tables["review_cases"]
    assert {r["customer_id"] for r in reviews} == cohort
    for r in reviews:
        assert r["review_id"] == f"REV-{r['customer_id']}-2026"
        assert r["review_year"] == 2026
        assert r["due_date"] >= r["scheduled_date"]


def test_review_due_date_follows_risk_cadence(full):
    prof = {p["customer_id"]: p for p in full.profile}
    for r in full.tables["review_cases"]:
        assert r["due_date"] == prof[r["customer_id"]]["next_review_due"]


def test_closed_at_is_set_exactly_when_closed(full):
    for r in full.tables["review_cases"]:
        assert (r["status"] == "CLOSED") == (r["closed_at"] is not None)
        if r["closed_at"] is not None:
            assert r["scheduled_date"] <= r["closed_at"].date() <= AS_OF


def test_overdue_reviews_exist_but_are_a_minority(full):
    reviews = full.tables["review_cases"]
    past_due = [r for r in reviews if r["due_date"] < AS_OF]
    overdue = [r for r in past_due if r["status"] != "CLOSED"]
    assert 0 < len(overdue) < 0.30 * len(past_due)


def test_all_review_statuses_appear(full):
    statuses = Counter(r["status"] for r in full.tables["review_cases"])
    for s in ("SCHEDULED", "IN_PROGRESS", "CLOSED"):
        assert statuses[s] > 0, f"no {s} reviews generated"


# ---------- reproducibility ----------

def test_same_seed_gives_identical_stage_c_data():
    cfg = GenConfig(scale=0.01)
    a, b = build_all(cfg).tables, build_all(cfg).tables
    for name in ("beneficial_owners", "risk_ratings", "review_cases"):
        assert a[name] == b[name]