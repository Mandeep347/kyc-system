from datetime import date

from app.core.rules import risk_level_from_score
from generator.config import GenConfig
from generator.pipeline import build_all


def test_same_seed_gives_identical_data():
    cfg = GenConfig(scale=0.01)
    assert build_all(cfg).tables == build_all(cfg).tables


def test_different_seed_gives_different_data():
    a = build_all(GenConfig(scale=0.01, seed=1)).tables["customers"]
    b = build_all(GenConfig(scale=0.01, seed=2)).tables["customers"]
    assert a != b


def test_starter_volumes():
    ds = build_all(GenConfig(scale=0.01))
    assert len(ds.tables["customers"]) == 80
    assert len(ds.tables["kyc_requirements"]) == 31
    assert sum(c["customer_type_id"] != "CT-01" for c in ds.tables["customers"]) == 10


def test_full_volumes():
    ds = build_all(GenConfig())
    customers = ds.tables["customers"]
    assert len(customers) == 8000
    assert sum(c["customer_type_id"] != "CT-01" for c in customers) == 1000
    assert len({c["email"] for c in customers}) == 8000  # emails unique


def test_review_cohort_is_derived_from_cadence():
    ds = build_all(GenConfig())
    cohort = [p for p in ds.profile if p["in_review_cohort"]]
    assert len(cohort) == 3000
    assert all(p["next_review_due"].year == 2026 for p in cohort)
    assert all(p["next_review_due"].year > 2026 for p in ds.profile if not p["in_review_cohort"])


def test_derived_values_are_consistent():
    ds = build_all(GenConfig())
    for p in ds.profile:
        assert risk_level_from_score(p["risk_score"]).value == p["risk_level"]
    for c in ds.tables["customers"]:
        assert c["onboarded_on"] <= date(2026, 10, 30)
        assert (c["date_of_birth"] is None) != (c["incorporation_date"] is None)