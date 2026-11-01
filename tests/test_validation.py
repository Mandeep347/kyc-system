from collections import defaultdict
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import text

from app.core.database import engine
from app.core.enums import IssueType
from generator.config import GenConfig
from generator.loader import load_into
from generator.pipeline import build_all
from validation.checks import ALL_CHECKS
from validation.runner import run_checks

AS_OF = date(2026, 10, 30)
SCALE = 0.05     # 400 customers: fast, but large enough to contain every anomaly type


@contextmanager
def loaded(tables):
    """Load data inside a transaction and ALWAYS roll back: your real data is never touched."""
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            load_into(conn, tables)
            yield conn
        finally:
            trans.rollback()


@pytest.fixture(scope="module")
def clean():
    return build_all(GenConfig(scale=SCALE))


@pytest.fixture(scope="module")
def dirty():
    return build_all(GenConfig(scale=SCALE), with_anomalies=True)


def test_check_catalogue_is_well_formed():
    ids = [c.check_id for c in ALL_CHECKS]
    assert len(ids) == len(set(ids))
    assert all("entity_id" in c.sql for c in ALL_CHECKS)
    assert len(ALL_CHECKS) > 60


def test_clean_baseline_passes_every_check(clean):
    with loaded(clean.tables) as conn:
        results = run_checks(conn, AS_OF)
    findings = {r.check.check_id: r.found[:5] for r in results if r.found}
    assert findings == {}


def test_planted_anomalies_are_found_exactly(dirty):
    truth = defaultdict(set)
    for g in dirty.ground_truth:
        truth[g["issue_type"]].add(g["entity_id"])
    with loaded(dirty.tables) as conn:
        results = run_checks(conn, AS_OF)
    for r in results:
        c = r.check
        expected = truth[c.issue_type.value] if c.issue_type else set()
        assert set(r.found) == expected, f"{c.check_id}: found {r.found[:5]}"
    assert {r.check.issue_type for r in results if r.check.issue_type} == set(IssueType)


def test_checks_catch_deliberate_corruption(clean):
    with loaded(clean.tables) as conn:
        try:   # turns off FK and append-only triggers for this transaction (needs a superuser)
            conn.execute(text("SET LOCAL session_replication_role = replica"))
        except Exception:
            pytest.skip("needs a superuser to switch off triggers")
        # 1) orphan foreign key
        conn.execute(text("UPDATE risk_ratings SET customer_id = 'CUS-999999' "
                          "WHERE rating_id = 'RSK-000001'"))
        # 2) verification dated before the document was even submitted
        conn.execute(text("UPDATE verifications SET verified_at = verified_at - INTERVAL '400 days' "
                          "WHERE verification_id = 'VER-000001'"))
        # 3) customer status that disagrees with its audit trail
        conn.execute(text("UPDATE customers SET kyc_status = CASE WHEN kyc_status = 'REJECTED' "
                          "THEN 'APPROVED' ELSE 'REJECTED' END WHERE customer_id = 'CUS-000001'"))
        found = {r.check.check_id: r.found for r in run_checks(conn, AS_OF)}
    assert found["FK.risk_ratings.customer_id"] == ("RSK-000001",)
    assert "VER-000001" in found["DATE.verification_after_submission"]
    assert found["REC.audit_state_customers"] == ("CUS-000001",)