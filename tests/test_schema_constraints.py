import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal

@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()

def test_invalid_enum_is_rejected(db):
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO customer_types VALUES ('CT-01', 'ALIEN', 'Alien', false, false)"
        ))

def test_foreign_key_is_enforced(db):
    with pytest.raises(IntegrityError):
        db.execute(text(
            "INSERT INTO customers (customer_id, customer_type_id, legal_name, "
            "date_of_birth, country_code, email, onboarded_on, kyc_status) VALUES "
            "('CUS-000001', 'CT-99', 'A B', '1990-01-01', 'IN', 'a@b.com', "
            "'2026-01-01', 'PENDING')"
        ))

