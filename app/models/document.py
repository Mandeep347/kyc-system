from datetime import date, datetime

from sqlalchemy import (CheckConstraint, Date, DateTime, ForeignKey, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import (DocumentCategory, DocumentStatus, DocumentType,
                            VerificationResult)
from app.models._helpers import in_list


class Document(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(25), primary_key=True)
    customer_id: Mapped[str] = mapped_column(
        ForeignKey("customers.customer_id"), index=True
    )
    document_category: Mapped[str] = mapped_column(String(30))
    document_type: Mapped[str] = mapped_column(String(40))
    # NOT unique on purpose: the "duplicate across customers" anomaly needs it
    document_number: Mapped[str] = mapped_column(String(40), index=True)
    name_on_document: Mapped[str] = mapped_column(String(200))
    issue_date: Mapped[date] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(10), index=True)
    submitted_on: Mapped[date] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint("document_id ~ '^DOC-CUS-[0-9]{6}-[0-9]{3}$'", name="id_format"),
        # the ID must embed its own customer's ID
        CheckConstraint(
            "starts_with(document_id, 'DOC-' || customer_id || '-')",
            name="id_matches_customer",
        ),
        CheckConstraint(
            in_list("document_category", DocumentCategory), name="category_valid"
        ),
        CheckConstraint(in_list("document_type", DocumentType), name="type_valid"),
        CheckConstraint(in_list("status", DocumentStatus), name="status_valid"),
        CheckConstraint(
            "expiry_date IS NULL OR expiry_date > issue_date",
            name="expiry_after_issue",
        ),
        CheckConstraint("submitted_on >= issue_date", name="submitted_after_issue"),
    )


class Verification(Base):
    """Append-only. A re-check is a NEW row; old rows are never edited."""
    __tablename__ = "verifications"

    verification_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id"), index=True
    )
    result: Mapped[str] = mapped_column(String(20))
    name_match_score: Mapped[float] = mapped_column(Numeric(5, 2))
    verified_by: Mapped[str] = mapped_column(String(40))
    verified_at: Mapped[datetime] = mapped_column(DateTime)
    remarks: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("verification_id ~ '^VER-[0-9]{6}$'", name="id_format"),
        CheckConstraint(in_list("result", VerificationResult), name="result_valid"),
        CheckConstraint("name_match_score BETWEEN 0 AND 100", name="score_range"),
    )


class DocumentRequirementLink(Base):
    """Bridge table for the N:M between Document and KYCRequirement."""
    __tablename__ = "document_requirement_links"

    link_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.document_id"), index=True
    )
    requirement_id: Mapped[str] = mapped_column(
        ForeignKey("kyc_requirements.requirement_id"), index=True
    )
    linked_on: Mapped[date] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("document_id", "requirement_id", name="uq_links_pair"),
        CheckConstraint("link_id ~ '^LNK-[0-9]{6}$'", name="id_format"),
    )