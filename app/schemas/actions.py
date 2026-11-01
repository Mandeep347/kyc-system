from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.core.enums import DocumentCategory, DocumentType, ReviewResolution
from app.schemas.entities import VerificationOut


class DocumentSubmission(BaseModel):
    document_category: DocumentCategory
    document_type: DocumentType
    document_number: str = Field(min_length=4, max_length=40)
    name_on_document: str = Field(min_length=2, max_length=200)
    issue_date: date
    expiry_date: date | None = None


class VerifyRequest(BaseModel):
    decision: Literal["PASS", "FAIL"]    # a name mismatch is decided by the system, not the analyst
    remarks: str | None = Field(None, max_length=500)


class VerifyResult(BaseModel):
    verification: VerificationOut
    document_status_before: str
    document_status_after: str
    note: str | None = None


class ReassessResult(BaseModel):
    customer_id: str
    previous_status: str
    new_status: str
    derived_status: str          # what the evidence says
    changed: bool
    steps: list[str]             # each step is one audit event
    note: str | None = None


class ReviewCloseRequest(BaseModel):
    resolution: ReviewResolution


# ---------- admin ----------

class FailedCheck(BaseModel):
    check_id: str
    description: str
    findings: int
    sample: list[str]


class ValidationOut(BaseModel):
    verdict: str                          # PASS when no expected-zero check failed
    as_of: date
    checks_run: int
    failures: list[FailedCheck]
    anomaly_findings: dict[str, int]      # planted-anomaly detectors: check_id -> findings