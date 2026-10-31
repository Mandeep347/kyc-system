from datetime import date

from sqlalchemy import (Boolean, CheckConstraint, Date, ForeignKey, Index,
                        Integer, Numeric, String, text)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import KycStatus, RiskLevel
from app.models._helpers import in_list


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    customer_type_id: Mapped[str] = mapped_column(
        ForeignKey("customer_types.customer_type_id"), index=True
    )
    legal_name: Mapped[str] = mapped_column(String(200))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    incorporation_date: Mapped[date | None] = mapped_column(Date)
    country_code: Mapped[str] = mapped_column(String(2))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    onboarded_on: Mapped[date] = mapped_column(Date)
    kyc_status: Mapped[str] = mapped_column(String(15), index=True)

    __table_args__ = (
        CheckConstraint("customer_id ~ '^CUS-[0-9]{6}$'", name="id_format"),
        CheckConstraint(in_list("kyc_status", KycStatus), name="status_valid"),
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_format"),
        # exactly one of the two must be filled: person XOR business
        CheckConstraint(
            "(date_of_birth IS NULL) <> (incorporation_date IS NULL)",
            name="person_xor_business_date",
        ),
    )


class BeneficialOwner(Base):
    __tablename__ = "beneficial_owners"

    owner_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )
    owner_name: Mapped[str] = mapped_column(String(200))
    nationality: Mapped[str] = mapped_column(String(2))
    ownership_pct: Mapped[float] = mapped_column(Numeric(5, 2))
    is_pep: Mapped[bool] = mapped_column(Boolean)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("owner_id ~ '^BO-[0-9]{6}$'", name="id_format"),
        CheckConstraint("nationality ~ '^[A-Z]{2}$'", name="nationality_format"),
        # each ROW is 0-100. The SUM per customer is checked by validation.
        CheckConstraint(
            "ownership_pct > 0 AND ownership_pct <= 100", name="pct_range"
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_dates_ordered",
        ),
    )


class RiskRating(Base):
    __tablename__ = "risk_ratings"

    rating_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )
    risk_level: Mapped[str] = mapped_column(String(10))
    risk_score: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(200))
    rated_by: Mapped[str] = mapped_column(String(40))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("rating_id ~ '^RSK-[0-9]{6}$'", name="id_format"),
        CheckConstraint(in_list("risk_level", RiskLevel), name="level_valid"),
        CheckConstraint("risk_score BETWEEN 0 AND 100", name="score_range"),
        # level must be DERIVED from score: LOW <35, MEDIUM 35-69, HIGH >=70
        CheckConstraint(
            "(risk_score < 35 AND risk_level = 'LOW') OR "
            "(risk_score BETWEEN 35 AND 69 AND risk_level = 'MEDIUM') OR "
            "(risk_score >= 70 AND risk_level = 'HIGH')",
            name="level_matches_score",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_dates_ordered",
        ),
        # at most ONE current rating (effective_to IS NULL) per customer
        Index(
            "uq_risk_ratings_one_current",
            "customer_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
        ),
    )