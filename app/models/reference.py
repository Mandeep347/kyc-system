from datetime import date

from sqlalchemy import (ARRAY, Boolean, CheckConstraint, Date, ForeignKey,
                        Integer, String, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import CustomerTypeCode, DocumentCategory, RiskLevel
from app.models._helpers import in_list


class CustomerType(Base):
    __tablename__ = "customer_types"

    customer_type_id: Mapped[str] = mapped_column(String(10), primary_key=True)
    type_code: Mapped[str] = mapped_column(String(20), unique=True)
    display_name: Mapped[str] = mapped_column(String(60))
    is_business: Mapped[bool] = mapped_column(Boolean)
    requires_beneficial_owner: Mapped[bool] = mapped_column(Boolean)

    __table_args__ = (
        CheckConstraint("customer_type_id ~ '^CT-[0-9]{2}$'", name="id_format"),
        CheckConstraint(in_list("type_code", CustomerTypeCode), name="type_code_valid"),
        CheckConstraint(
            "NOT requires_beneficial_owner OR is_business", name="bo_needs_business"
        ),
    )


class KYCRequirement(Base):
    __tablename__ = "kyc_requirements"

    requirement_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    customer_type_id: Mapped[str] = mapped_column(
        ForeignKey("customer_types.customer_type_id"), index=True
    )
    risk_level: Mapped[str] = mapped_column(String(10))
    document_category: Mapped[str] = mapped_column(String(30))
    is_mandatory: Mapped[bool] = mapped_column(Boolean)
    max_age_days: Mapped[int | None] = mapped_column(Integer)
    expiry_lead_days: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default="{60,30,7}"
    )
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint(
            "customer_type_id", "risk_level", "document_category", "effective_from",
            name="uq_kyc_requirements_rule",
        ),
        CheckConstraint(
            "requirement_id ~ '^KYC-[0-9]{4}-[0-9]{6}$'", name="id_format"
        ),
        CheckConstraint(in_list("risk_level", RiskLevel), name="risk_valid"),
        CheckConstraint(
            in_list("document_category", DocumentCategory), name="category_valid"
        ),
        CheckConstraint(
            "max_age_days IS NULL OR max_age_days > 0", name="max_age_positive"
        ),
        CheckConstraint(
            "cardinality(expiry_lead_days) > 0 AND 0 < ALL (expiry_lead_days)",
            name="lead_days_positive",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="effective_dates_ordered",
        ),
    )